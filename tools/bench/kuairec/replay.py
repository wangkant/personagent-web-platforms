"""Play sampled KuaiRec people forward through the feed's own ranking, one card at a time.

    python -I tools/bench/kuairec/replay.py --data <prepared> --out <dir> [--arms app,random,popular,bandit,oracle]
        [--people 300] [--cards 300] [--workers 8]

Each app arm runs the app's code in this process with the clock moved: a fresh runtime per person
holding the library videos that person has a reaction to as library items, `recommend.candidates`
(the ranking's best unseen, kept apart from the last one's source and topic) taking its first row,
as the app does with no model or an answer it cannot use, every reaction written as the pet's card
writes it (`feed.remember`), and the taste model refit after each card (`taste.train`; the app refits
at most once a minute, after the reactions). The other arms pick from the same pool. One JSON per
arm and person goes to <out>/cards/<arm>/<uid>.json, so a run that stops resumes where it was.

App arms (what differs from the app as shipped is the measurement, not the product):
  app          the app as shipped: source = a feed per first-level category, Thompson draws at ALPHA 0.5
  app_greedy   ALPHA 0 (the posterior mean, no exploration)
  app_explore  ALPHA 1 (draws at the posterior's full spread)
  app_site     no source: every video is a find from one site, so origin carries nothing
  app_author   source = the uploader instead of the category
  app_topics   the person types the three categories they watch through most in the other half
               of the videos as their first-run topics
  app_tags     the second and third category levels as the character's tags, with the model's
               waiting "tags" family switched on
  app_open05   the reaction map with "opened" from watch ratio 0.5 (default: 1.0)
  app_pop      the crowd's popularity (the popular arm's, quartiles 0-3) put in the slot of the
               character's own judgement (love), the only slot the model has for a prior opinion
"""
from __future__ import annotations

import argparse
import copy
import functools
import json
import random
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 11, 19, 8, 0).timestamp()
# Twelve minutes between cards (pacing's gap while browsing); 300 cards stay inside the 7 days a
# find can be brought. At the app's 10 a day, 300 cards are a month.
GAP_S = 12 * 60
# How long a card is out before the person opens it.
LOOK_S = 2.0
QUICK_PLAY_S = 3.0
APP_ARMS = ("app", "app_greedy", "app_explore", "app_site", "app_author", "app_topics", "app_tags", "app_open05", "app_pop")
BASE_ARMS = ("random", "popular", "bandit", "oracle", "cat_oracle", "cat_oracle_alt")


def event_of(wr: float, play_s: float, open_at: float) -> str:
    """The card's ending a reaction maps to: open (opened and played play_s), quick (closed at once),
    close (closed after a look) or timeout (left alone until it went)."""
    if wr >= open_at:
        return "open"
    if play_s < QUICK_PLAY_S:
        return "quick"
    if wr < 0.5:
        return "close"
    return "timeout"


def positive(wr: float, open_at: float) -> int:
    return int(wr >= open_at)


def title_of(v: dict, vid: str) -> str:
    return v["caption"] or v["cover"] or " ".join("#" + t for t in v["hashtags"]) or f"快手视频 {vid}"


def excerpt_of(v: dict) -> str:
    cats = "/".join(c for c in (v["c1"], v["c2"], v["c3"]) if c)
    tags = " ".join("#" + t for t in v["hashtags"])
    parts = [v["cover"] if v["caption"] else "", tags if v["caption"] else "", "分类：" + cats if cats else ""]
    return " ".join(p for p in parts if p)


def item_row(vid: str, v: dict, arm: str, seen_at: int, love: int = -1) -> dict:
    source = ("" if arm == "app_site" else "ks-author-" + v["author"] if arm == "app_author"
              else "ks-" + (v["c1"] or "其他"))
    return {"url": f"https://www.kuaishou.com/short-video/{vid}", "id": "ks" + vid, "site": "快手", "kind": "video",
            "title": title_of(v, vid), "description": excerpt_of(v), "length": int(round(v["dur_s"])),
            # Kuaishou's player is not on the app's list: a listed player address stands in, so each counts
            # as the video it is (a real Kuaishou link would be drawn as a text card).
            "embed": f"https://open.douyin.com/player/video?vid={vid}&autoplay=0",
            "seen_at": seen_at, "source": source, "state": "", "told": False,
            "tags": [t for t in (v["c2"], v["c3"]) if t] if arm == "app_tags" else [],
            "love": love, "judged": love >= 0}


# --------------------------------------------------------------------------- app arm --

_app = {}


def _load_app():
    if _app:
        return _app
    sys.path.insert(0, str(ROOT))
    from mibo import life, recommend, situation, taste
    from mibo.library import Item, Library
    from mibo.panels import feed
    clock = [T0]
    life.clock = lambda: clock[0]
    # Refit by hand once per card, after its reactions, as the app's minute timer would.
    taste.fit = lambda runtime, soon=False: None
    # The library never changes during a replay: read it once per runtime.
    cache = {}
    original = Library.items

    def items(self, newest_first=True, since=0):
        key = (str(self.root), newest_first, since)
        if key not in cache:
            cache[key] = original(self, newest_first, since)
        return list(cache[key])

    Library.items = items
    # Two pure functions the ranking calls for every item on every card, kept: the same answers, sooner.
    cards_seen = {}
    clean = life.clean_card

    def clean_card(card):
        key = (card.get("url"), card.get("title"), card.get("excerpt"), card.get("embed"), card.get("length"))
        if key not in cards_seen:
            cards_seen[key] = clean(card)
        got = cards_seen[key]
        return None if got is None else dict(got)

    life.clean_card = clean_card
    taste._host = functools.lru_cache(maxsize=None)(taste._host)
    # What a replay never changes once it starts (no chat, no profile, no hidden source, topics set
    # before the first card), read from the runtime's files once per runtime instead of on every call.
    reader, priors, topics_of = taste.Reader, taste.Priors, feed.load_topics

    def kept(make, key):
        if key not in cache:
            cache[key] = make()
        return cache[key]

    def read(runtime, topics=None, events=None):
        # What the reader takes from the runtime's files is kept; what was brought lately moves with every card.
        fresh = copy.copy(kept(lambda: reader(runtime, topics, []),
                               ("reader", str(runtime), None if topics is None else tuple(topics))))
        fresh.brought = reader.lately(feed.events(runtime) if events is None else events)
        return fresh

    taste.Reader = read
    taste.Priors = lambda runtime, hidden=None, rules=True: kept(
        lambda: priors(runtime, hidden, rules),
        ("priors", str(runtime), None if hidden is None else frozenset(hidden), rules))
    feed.load_topics = lambda path: list(kept(lambda: topics_of(path), ("topics", str(path))))
    _app.update(topics_of=topics_of)
    _app.update(life=life, recommend=recommend, situation=situation, taste=taste, Library=Library, Item=Item, feed=feed,
                clock=clock, cache=cache, vector=taste.vector, alpha=taste.ALPHA)
    return _app


def run_app(arm: str, person: dict, reactions: dict, videos: dict, library: list, cards: int, work: Path,
            popularity: dict) -> dict:
    a = _load_app()
    taste, feed, recommend = a["taste"], a["feed"], a["recommend"]
    taste.ALPHA = {"app_greedy": 0.0, "app_explore": 1.0}.get(arm, a["alpha"])
    if arm == "app_tags":
        base = a["vector"]
        taste.vector = lambda snap, read, families=(): base(snap, read, tuple(families) + ("tags",))
    else:
        taste.vector = a["vector"]
    open_at = 0.5 if arm == "app_open05" else 1.0
    uid = person["uid"]
    home = work / f"{arm}-{uid}"
    shutil.rmtree(home, ignore_errors=True)
    rt = home / "runtime"
    seen_at = int(T0)
    pool = [vid for vid in library if vid in reactions]
    love = {}
    if arm == "app_pop":
        ordered = sorted(library, key=lambda v: popularity.get(v, {"mean_wr": 0})["mean_wr"])
        love = {vid: k * 4 // len(ordered) for k, vid in enumerate(ordered)}
    lib = a["Library"](rt)
    for vid in pool:
        lib.add(a["Item"](**item_row(vid, videos[vid], arm, seen_at, love.get(vid, -1))))
    a["situation"].set_value(rt, "readable", ["zh"])
    topics = list(person["topics"]) if arm == "app_topics" else []
    if topics:
        feed.keep_topics(rt, topics)
    # The model's draws are seeded by the clock: each person starts at their own moment of the first
    # day, else every person with the same history would get the same draws.
    now = T0 + 3600 + int(uid) * 7919 % 86400
    out = []
    try:
        for step in range(cards):
            a["clock"][0] = now
            rows = recommend.candidates(lib, feed.events(rt), now, topics, set())
            if not rows:
                break
            shaped, item = rows[0]
            vid = item.id[2:]
            wr, play_s = reactions[vid]
            ev = event_of(wr, play_s, open_at)
            feed.remember(rt, item, "seen", now, surface="card")
            if ev == "open":
                at = now + LOOK_S
                feed.remember(rt, item, "end", at, surface="card", end="open", shown_s=LOOK_S)
                feed.remember(rt, item, "open", at, surface="card")
                if play_s > 0:
                    at += play_s
                    feed.remember(rt, item, "play", at, surface="card", play_s=play_s)
            else:
                at = now + max(play_s, 0.5)
                feed.remember(rt, item, "end", at, surface="card", end=ev, shown_s=at - now)
                if ev in ("quick", "close"):
                    feed.remember(rt, item, "skip", at, surface="card")
            a["clock"][0] = at
            taste.train(rt, at)
            out.append([vid, wr, play_s, videos[vid]["c1"], ev, shaped.get("slot", ""), shaped.get("reason_term", "")])
            now = max(now + GAP_S, at + 60)
        model = taste.load(rt)["m"]
    finally:
        a["cache"].clear()
        shutil.rmtree(home, ignore_errors=True)
    top = sorted(model.items(), key=lambda kv: -abs(kv[1]))[:25]
    return {"arm": arm, "uid": uid, "cards": out, "model": dict(top), "pool": len(pool)}


# ----------------------------------------------------------------------- baselines --

def run_base(arm: str, person: dict, reactions: dict, videos: dict, library: list, cards: int,
             popularity: dict) -> dict:
    uid = person["uid"]
    rng = random.Random(f"{arm}:{uid}")
    pool = [vid for vid in library if vid in reactions]
    if arm == "random":
        rng.shuffle(pool)
        order = pool[:cards]
    elif arm == "popular":
        order = sorted(pool, key=lambda v: (-popularity.get(v, {"mean_wr": 0})["mean_wr"], int(v)))[:cards]
    elif arm == "oracle":
        order = sorted(pool, key=lambda v: (-reactions[v][0], int(v)))[:cards]
    elif arm in ("cat_oracle", "cat_oracle_alt"):
        # The best a model that knows only the category could do: the person's true watched-through rate
        # per category, in random order inside one; "_alt" never two of one category in a row, as the
        # app's candidates keep a card apart from the last one's source.
        rate = {}
        for vid in pool:
            rate.setdefault(videos[vid]["c1"] or "?", []).append(reactions[vid][0] >= 1)
        rate = {c: sum(x) / len(x) for c, x in rate.items()}
        rng.shuffle(pool)
        ranked = sorted(pool, key=lambda v: -rate[videos[v]["c1"] or "?"])
        if arm == "cat_oracle":
            order = ranked[:cards]
        else:
            order, last = [], None
            while ranked and len(order) < cards:
                vid = next((v for v in ranked if (videos[v]["c1"] or "?") != last), ranked[0])
                ranked.remove(vid)
                order.append(vid)
                last = videos[vid]["c1"] or "?"
    else:
        # Beta-Bernoulli Thompson sampling over first-level categories, a random video inside the one drawn.
        left = {}
        for vid in pool:
            left.setdefault(videos[vid]["c1"] or "?", []).append(vid)
        for vids in left.values():
            rng.shuffle(vids)
        wins = {c: [1, 1] for c in left}
        order = []
        while len(order) < cards and left:
            c = max(left, key=lambda c: rng.betavariate(*wins[c]))
            vid = left[c].pop()
            if not left[c]:
                del left[c]
            order.append(vid)
            wins[c][0 if positive(reactions[vid][0], 1.0) else 1] += 1
    out = [[vid, reactions[vid][0], reactions[vid][1], videos[vid]["c1"],
            event_of(reactions[vid][0], reactions[vid][1], 1.0), "", ""] for vid in order]
    return {"arm": arm, "uid": uid, "cards": out, "pool": len(pool)}


# --------------------------------------------------------------------------- driver --

_shared = {}


def _init(data: str) -> None:
    d = Path(data)
    for name in ("videos", "library", "reactions", "popularity"):
        _shared[name] = json.loads((d / f"{name}.json").read_text(encoding="utf-8"))


def job(arm: str, person: dict, cards: int, work: str, out: str) -> str:
    s = _shared
    reactions = s["reactions"][person["uid"]]
    started = time.perf_counter()
    if arm in APP_ARMS:
        got = run_app(arm, person, reactions, s["videos"], s["library"], cards, Path(work), s["popularity"])
    else:
        got = run_base(arm, person, reactions, s["videos"], s["library"], cards, s["popularity"])
    got["seconds"] = round(time.perf_counter() - started, 1)
    target = Path(out) / "cards" / arm / f"{person['uid']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
    return f"{arm} {person['uid']} {len(got['cards'])} cards {got['seconds']} s"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--work", type=Path, default=None, help="where the runtimes live while they run (default <out>/work)")
    ap.add_argument("--arms", default=",".join(("app",) + BASE_ARMS))
    ap.add_argument("--people", type=int, default=0, help="the first N/5 of each stratum's people (default all)")
    ap.add_argument("--cards", type=int, default=300)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    people = json.loads((args.data / "people.json").read_text(encoding="utf-8"))
    if args.people:
        per = args.people // 5
        people = [p for s in range(5) for p in [q for q in people if q["stratum"] == s][:per]]
    work = args.work or args.out / "work"
    jobs = [(arm, p) for arm in args.arms.split(",") for p in people
            if not (args.out / "cards" / arm / f"{p['uid']}.json").exists()]
    print(f"{len(jobs)} jobs to run", flush=True)
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(str(args.data),)) as pool:
        futures = [pool.submit(job, arm, p, args.cards, str(work), str(args.out)) for arm, p in jobs]
        for n, f in enumerate(as_completed(futures), 1):
            try:
                line = f.result()
            except Exception as exc:
                line = f"FAILED {type(exc).__name__}: {exc}"
            if n % 25 == 0 or line.startswith("FAILED") or n <= 3:
                print(f"[{n}/{len(jobs)}] {line}", flush=True)


if __name__ == "__main__":
    main()

"""Thirty simulated days of one person, through the app's own path or one of three others.

    python tools/recsim/sim.py --snapshot <dir> --env <.env> --work <dir> --person <name> [--days 30]

The app arm runs the app's code in this process with the clock moved, not waited for: the first
run's topics and round (feed.keep_topics, feed.first_round), the pacing rule each minute against
the person's simulated activity (pacing.see / decide / outcome, the day cap), the six candidates
(recommend.candidates), the model's choice and sentence (recommend.prompt, profile.ask,
recommend.parse), every reaction written back as the card and the page write it (feed.remember),
the taste model refit as it refits (taste.train), and what the person says in the chat reaching
the profile the way memory._tell_profile and welcome._chat_finds pass it on. The others use the
same days, pacing and person: at random, the most popular, and an oracle that knows the truth.
One JSON of every card per arm goes to <work>/<person>.json.

With --moments <dir>, the rank1 arm also writes every fifth card's candidates, each one's true worth
and the prompt the app would have sent, to <dir>/<person>.jsonl: picks.py asks the model at those
moments, so two prompts or two ways of choosing the candidates can be compared on the same ones for
a few cents.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from dataclasses import fields
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mibo import life, pacing, person as person_rows, profile, recommend, situation, taste  # noqa: E402
from mibo.library import Item, Library  # noqa: E402
from mibo.panels import feed  # noqa: E402
from llm import env_values  # noqa: E402
from people import DAY, PEOPLE, OFFERED, WORK, Person, activity, top  # noqa: E402
from people2 import FRESH, Fresh  # noqa: E402

# The builders' twelve and the twelve written to prove them (people2.py).
EVERYONE = PEOPLE + FRESH


def person(truth, labels: dict, seed: int) -> Person:
    return (Fresh if truth in FRESH else Person)(truth, labels, seed)


def os_langs(truth) -> list[str]:
    """What the person's computer says they read: all the app is told. For the first twelve it is the
    truth itself, which hands the app a fact a random choice does not get; `random_read` gets it too."""
    return list(getattr(truth, "os_langs", None) or (("zh", "en") if truth.english else ("zh",)))


# random_read: at random among things in a language their computer says they read, so the app is not
# credited for a fact it was simply handed.
ARMS = ("app", "random", "random_read", "popular", "oracle")
# The app's path with one part changed, to see what each part is worth; none calls the model.
# rank1: the ranking's first of the six instead of the model's pick. rank1_mixed: the ranking's first
# unseen thing, without the rule that keeps it apart from the last card's source and topic or the
# swap that puts text and video both among the six. rank1_mean: rank1 without the Thompson draw.
# rank1_tags: rank1 with the character's tags as features (taste's "tags" family, off in the app).
# rank1_subject: rank1 with the true subject as a feature, a ceiling for what knowing what a thing is
# about could give; it reads the labels, so it is a diagnosis, not a fair arm.
# rank1_half: rank1 with old reactions fading in 14 days, not 45. rank1_soft: rank1 with a weaker belief that
# a language the computer does not list is unread. rank1_warm: rank1 with one more feature, how the person
# took the things on this subject brought in the last week (opened or liked against closed).
# rank1_nolang: rank1 without the language of a thing as a learned feature (taste's "lang" family).
# rank1_clicks: rank1 learning as it did while an open counted as a yes by itself, however short the stay.
VARIANTS = ("rank1", "rank1_mixed", "rank1_mean", "rank1_tags", "rank1_subject", "rank1_half", "rank1_soft",
            "rank1_warm", "rank1_nolang", "rank1_clicks")
APP = ("app",) + VARIANTS
# Day 0 is Thursday 19 November 2026, local midnight.
T0 = datetime(2026, 11, 19).timestamp()
NEW_SHARE, ROUND_HOURS = 0.25, (8, 12, 16, 20)
TELL_S = life.TELL_DAYS * DAY
TYPING = {"coding", "writing", "homework", "spreadsheet", "chatting"}
CHAT_HOUR = 21.25

ALPHA, HALF, UNREAD = taste.ALPHA, taste.HALF_DAYS, situation.UNREAD_LANG
VECTOR, SNAPSHOT, READER = taste.vector, taste.Reader.snapshot, taste.Reader.__init__
LABEL = taste.label


def clicks_label(h: dict, until: float):
    """taste.label as it was: a thing opened and not played for real is a yes of confidence 1."""
    if not (h["hide"] or h["like"] or h["skip"] or taste._played(h)) and (h["open"] or h["play"] > 0):
        return 1, 1.0, h["acted"]
    return LABEL(h, until)


def features(name: str, labels: dict) -> None:
    """Put the taste model's features back as the app has them, or change them for a variant."""
    taste.ALPHA = 0.0 if name == "rank1_mean" else ALPHA
    taste.label = clicks_label if name == "rank1_clicks" else LABEL
    taste.vector, taste.Reader.snapshot, taste.Reader.__init__ = VECTOR, SNAPSHOT, READER
    taste.HALF_DAYS = 14 if name == "rank1_half" else HALF
    situation.UNREAD_LANG = (-1.0, 1.0) if name == "rank1_soft" else UNREAD
    taste.PRIORS.pop("warm", None)
    if name == "rank1_warm":
        taste.PRIORS["warm"] = (0.5, 1.0)

        def reader(self, runtime, topics=None, events=None):
            READER(self, runtime, topics, events)
            marks = feed._marks(feed.events(self.runtime) if events is None else events)[0]
            self.did = {}
            for _, ident, _ in self.brought:
                mark = marks.get(ident) or {}
                self.did[ident] = (1 if mark.get("like") or mark.get("open") or mark.get("play")
                                   else -1 if mark.get("skip") or mark.get("end") in ("quick", "close") else 0)

        def warm_snapshot(self, item, shaped, now):
            snap = SNAPSHOT(self, item, shaped, now)
            on = set(snap["subj"])
            snap["warm"] = sum(self.did.get(ident, 0) for at, ident, about in self.brought
                               if now - 7 * DAY <= at < now and ident != item.id and about & on)
            return snap

        def warm_vector(snap, read, families=()):
            x = VECTOR(snap, read, families)
            if snap.get("warm"):
                x["warm"] = max(-3, min(3, snap["warm"])) / 3
            return x
        taste.vector, taste.Reader.snapshot, taste.Reader.__init__ = warm_vector, warm_snapshot, reader
    if name == "rank1_tags":
        taste.vector = lambda snap, read, families=(): VECTOR(snap, read, ("tags",))
    elif name == "rank1_subject":
        def snapshot(self, item, shaped, now):
            snap = SNAPSHOT(self, item, shaped, now)
            snap["subject"] = list(labels.get(item.id, {}).get("subjects", {}))
            return snap

        def vector(snap, read, families=()):
            x = VECTOR(snap, read, families)
            x.update({"subject:" + s: 1.0 for s in snap.get("subject", ())})
            return x
        taste.vector, taste.Reader.snapshot = vector, snapshot
    elif name == "rank1_nolang":
        taste.vector = lambda snap, read, families=(): {k: v for k, v in VECTOR(snap, read, families).items()
                                                        if not k.startswith("lang:")}


CLOCK = [T0]
life.clock = lambda: CLOCK[0]
# The app refits at most once a minute in a timer thread; here at once, on the moved clock.
taste.fit = lambda runtime, soon=False: taste.train(runtime)


def arrivals(items: list[dict]) -> dict[str, float]:
    """When each item reaches the library: a quarter before the first day, the rest in the sources'
    rounds of the next 29 days. The same for every person and arm."""
    rng = random.Random(1)
    order = sorted(items, key=lambda it: it["id"])
    rng.shuffle(order)
    first = int(len(order) * NEW_SHARE)
    at = {it["id"]: T0 + 7 * 3600 for it in order[:first]}
    rest = order[first:]
    for n, it in enumerate(rest):
        day = 1 + n * 29 // len(rest)
        at[it["id"]] = T0 + day * DAY + rng.choice(ROUND_HOURS) * 3600 + rng.randint(0, 1800)
    return at


class Minutes:
    """The person's keys and mouse each minute: idle seconds and the last key, from what they do."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.away_since = None
        self.pause = False

    def look(self, now: float, doing: str) -> tuple[float, float]:
        r = self.rng.random
        if doing in ("away", "sleep"):
            self.away_since = self.away_since or now
            return now - self.away_since + 1, now - 3600
        self.away_since = None
        self.pause = r() < (0.12 if doing in WORK else 0.15)
        idle = self.rng.uniform(95, 250) if self.pause else self.rng.uniform(0, 20 if doing in TYPING else 70)
        return idle, (now - idle if doing in TYPING and not self.pause else now - 600)


class Arm:
    """The choice of one arm; the app's keeps the app's files under `home`."""

    def __init__(self, name: str, who: Person, items: dict, at: dict, home: Path, values: dict, pop: dict):
        self.name, self.who, self.items, self.at, self.pop = name, who, items, at, pop
        self.rng = random.Random(f"{name}:{who.t.name}")
        self.shown: set[str] = set()
        self.home, self.runtime, self.values = home, home / "runtime", values
        self.lang = "zh"
        self.cost = 0.0
        self.calls = 0
        self.moments: Path | None = None
        self.asked = 0
        if name in APP:
            home.mkdir(parents=True, exist_ok=True)
            (home / ".env").write_text("AGENT_LANG=zh\nPERSONA_NAME=Mibo\nPERSONA_TZ_OFFSET_HOURS=-8\n",
                                       encoding="utf-8")
            self.runtime.mkdir(parents=True, exist_ok=True)
            self.library = Library(self.runtime)
            known = {f.name for f in fields(Item)}
            for ident, it in items.items():
                self.library.add(Item(**{**{k: v for k, v in it.items() if k in known},
                                         "seen_at": int(at[ident]), "state": "", "told": False}))
            situation.set_value(self.runtime, "readable", os_langs(who.t))
            self.name_, self.persona, _ = profile.character(home, profile.values_of(home))

    def available(self, now: float) -> list[dict]:
        pool = [it for i, it in self.items.items() if now - TELL_S <= self.at[i] <= now and i not in self.shown]
        if self.name == "random_read":
            reads = os_langs(self.who.t)
            pool = [it for it in pool if self.who.labels[it["id"]]["lang"] in reads]
        return pool

    # -- the first run

    def topics(self) -> list[str]:
        t = self.who.t
        if t in FRESH:
            return list(t.ticks or ()) + list(t.typed)
        if not t.picks:
            return []
        strong = sorted((s for s, w in t.interests.items() if w >= 0.5), key=lambda s: -t.interests[s])
        picked = list(dict.fromkeys(OFFERED[s] for s in strong if s in OFFERED))[:4]
        return picked + list(t.typed)

    def first(self, now: float) -> list[dict]:
        pool = self.available(now)
        if self.name in APP:
            feed.keep_topics(self.runtime, self.topics())
            shaped = feed.first_round(self.library, feed.events(self.runtime), now, feed.load_topics(self.runtime))
            got = [self.items[s["id"]] for s in shaped]
        elif self.name in ("random", "random_read"):
            got = self.rng.sample(pool, min(10, len(pool)))
        elif self.name == "popular":
            got = sorted(pool, key=lambda it: -self.pop[it["id"]])[:10]
        else:
            got = sorted(pool, key=lambda it: -self.who.worth(it, now, 0))[:10]
        return got

    # -- a card

    async def choose(self, now: float) -> tuple[dict | None, str, list]:
        pool = self.available(now)
        if self.name in ("random", "random_read"):
            return (self.rng.choice(pool) if pool else None), "", []
        if self.name == "popular":
            return (max(pool, key=lambda it: self.pop[it["id"]]) if pool else None), "", []
        if self.name == "oracle":
            day = (now - T0) / DAY
            return (max(pool, key=lambda it: self.who.worth(it, now, day)) if pool else None), "", []
        topics = feed.load_topics(self.runtime)
        if self.name == "rank1_mixed":
            for shaped, item in feed.ranked(self.library, feed.events(self.runtime), now, topics, seed=int(now * 1000)):
                if not shaped["seen"] and item.seen_at >= now - TELL_S:
                    return self.items[item.id], "", [item.id]
            return None, "", []
        rows = recommend.candidates(self.library, feed.events(self.runtime), now, topics, set())
        if not rows:
            return None, "", []
        shaped, item, line = rows[0][0], rows[0][1], ""
        if self.name != "app":
            self.asked += 1
            if self.moments and self.name == "rank1" and self.asked % 5 == 0:
                self.note(rows, topics, now)
            return self.items[item.id], "", [r[1].id for r in rows]
        found = recommend.grounds(self.runtime, topics, rows, now, self.lang)
        system, user = recommend.prompt(self.runtime, self.name_, self.persona, self.lang, topics, rows, now, found)
        try:
            answer = await profile.ask(self.runtime, self.home, self.values, "pick", system, user,
                                       max_tokens=recommend.MAX_TOKENS, now=now)
        except Exception as exc:
            print(f"pick call failed: {type(exc).__name__}", file=sys.stderr)
            answer = ""
        self.calls += 1
        picked = recommend.parse(answer, rows, self.lang, found)
        # What the sentence may rest on: the topics, what was said, and what the model was shown of the
        # last things brought.
        self.knew = topics + profile.told(self.runtime) + recommend.lately(self.runtime, now).splitlines()
        if picked:
            shaped, item, line = picked
        return self.items[item.id], line, [r[1].id for r in rows]

    def note(self, rows: list, topics: list, now: float) -> None:
        """One moment for picks.py: the candidates in the ranking's order, what each is truly worth to the
        person now, the best they could have been brought, and the prompt the app would send."""
        day = (now - T0) / DAY
        found = recommend.grounds(self.runtime, topics, rows, now, self.lang)
        system, user = recommend.prompt(self.runtime, self.name_, self.persona, self.lang, topics, rows, now, found)
        row = {"person": self.who.t.name, "at": now, "ids": [item.id for _, item in rows], "found": found,
               "worth": [round(self.who.worth(self.items[item.id], now, day), 3) for _, item in rows],
               "best": round(max(self.who.worth(it, now, day) for it in self.available(now)), 3),
               "knew": topics + profile.told(self.runtime) + recommend.lately(self.runtime, now).splitlines(),
               "interests": self.who.interests(day),
               "system": system, "user": user}
        self.moments.mkdir(parents=True, exist_ok=True)
        with (self.moments / f"{self.who.t.name}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def tell(self, it: dict, surface: str, got: dict, now: float, first: bool = False) -> None:
        """Write a reaction back the way the card (card.js) or the first run (onboard.js) sends it."""
        self.shown.add(it["id"])
        if self.name not in APP:
            return
        item = self.library.get(it["id"])
        rt = self.runtime
        if first:
            # Someone who never reacts leaves the round as it is.
            if not getattr(self.who.t, "mute", False):
                feed.remember(rt, item, "like" if got["like"] else "skip", now, surface="first")
            return
        feed.remember(rt, item, "seen", now, surface=surface)
        shaped = feed.shape(item, {})
        at = now + got["shown_s"]
        if got["like"]:
            feed.remember(rt, item, "like", at, surface=surface)
        if got["open"]:
            feed.remember(rt, item, "end", at, surface=surface, end="like" if got["like"] else "open",
                          shown_s=got["shown_s"])
            feed.remember(rt, item, "open", at, surface=surface)
            if got["seconds"]:
                feed.remember(rt, item, "play" if shaped["kind"] == "video" else "read", at + got["seconds"],
                              surface=surface, play_s=got["seconds"])
        elif got["end"] == "close":
            feed.remember(rt, item, "end", at, surface=surface,
                          end="quick" if got["shown_s"] < feed.QUICK_S else "close", shown_s=got["shown_s"])
            feed.remember(rt, item, "skip", at, surface=surface)
        else:
            feed.remember(rt, item, "end", at, surface=surface, end="timeout", shown_s=got["shown_s"])

    def chat(self, said: list[tuple[str, str]], now: float) -> None:
        """What the person said, as memory._tell_profile and welcome._chat_finds pass it to the app,
        taking the reply model's reading of each row as right."""
        if self.name not in APP:
            return
        rt = self.runtime
        for kind, text in said:
            if kind == "like":
                profile.like(rt, person_rows.topic_of(text), now, words=text)
            elif kind == "dislike":
                profile.dislike(rt, person_rows.topic_of(text), person_rows.about_of("", text), now, words=text)
            elif kind == "form":
                short, form_kind = person_rows.form_of(text)
                profile.form(rt, short, form_kind, now, words=text)
            else:
                item = self.library.get(kind.split(":", 1)[1])
                liked = text == "liked"
                words = {"about": "thing" if liked else "topic", "words": "这个不错" if liked else "不想看这类"}
                feed.remember(rt, item, "like" if liked else "skip", now, surface="chat", said=words)


async def run(arm: Arm, days: int, minutes_seed: int) -> dict:
    who = arm.who
    rows = {s: pacing.new_row() for s in pacing.SURFACES}
    seen = pacing.fresh_seen()
    keys = Minutes(minutes_seed)
    cards, first = [], []
    start = T0 + 8.5 * 3600
    CLOCK[0] = start
    for rank, it in enumerate(arm.first(start)):
        w = who.worth(it, start, 0)
        like = who.rng.random() < w and not getattr(who.t, "mute", False)
        who.shown.append((start, top(who.labels[it["id"]])))
        arm.tell(it, "first", {"like": like}, start, first=True)
        first.append({"id": it["id"], "worth": round(w, 3), "like": like})
    out_until = 0.0
    for minute in range(int(8.6 * 60), days * 1440):
        now = T0 + minute * 60
        CLOCK[0] = now
        day, hour = minute // 1440, (minute % 1440) / 60
        doing = activity(who.t, day, hour)
        idle, typed = keys.look(now, doing)
        kind = "" if doing in ("away", "sleep") else doing
        pacing.see(seen, now, kind, idle, False, doing == "video", typed)
        if abs(hour - CHAT_HOUR) < 1 / 120 and doing not in ("sleep", "away"):
            recent = [c for c in cards if now - c["at"] < 2 * DAY]
            arm.chat(who.says(day, recent), now)
        if now < out_until:
            continue
        for surface in pacing.SURFACES:
            pacing.today(rows[surface], now)
            pacing.settle(rows[surface], surface, now)
        # The pace and quiet hours the person set, read as Pacer.judge reads them.
        go, why = pacing.decide(pacing.view(rows, "card", **(pacing.wants(arm.runtime) if arm.name in APP else {})),
                                seen, now, True)
        if not go:
            continue
        it, line, offered = await arm.choose(now)
        if it is None:
            continue
        d = day + hour / 24
        # The best of everything that has arrived, whatever the arm itself draws from.
        pool = [x for i, x in arm.items.items() if now - TELL_S <= arm.at[i] <= now and i not in arm.shown]
        best = max(who.worth(x, now, d) for x in pool)
        w = who.worth(it, now, d)
        # Weighed before the reaction, which the person remembers for tiredness.
        six = [round(who.worth(arm.items[i], now, d), 3) for i in offered]
        got = who.react(it, now, d, doing)
        arm.tell(it, "card", got, now)
        row = rows["card"]
        row.update(last=now, id=it["id"], done="", count=row["count"] + 1)
        end = "quick" if got["end"] == "close" and got["shown_s"] < feed.QUICK_S else got["end"]
        pacing.outcome(row, "like" if got["like"] and not got["open"] else end)
        out_until = now + got["shown_s"]
        lab = who.labels[it["id"]]
        cards.append({"at": now, "day": day, "hour": round(hour, 2), "id": it["id"], "worth": round(w, 3),
                      "best": round(best, 3), "open": got["open"], "liked": got["like"], "end": end,
                      "seconds": got["seconds"], "subject": top(lab), "origin": it.get("source") or "site:" + it["site"],
                      "kind": it["kind"], "doing": doing, "pause": keys.pause, "why": why, "line": line,
                      "offered": offered, "six": six, "pool": len(pool),
                      "knew": getattr(arm, "knew", []) if line else [],
                      "interests": who.interests(d)})
    present = [d for d in range(days) if any(activity(who.t, d, h / 4) not in ("away", "sleep")
                                             for h in range(int(8.6 * 4) if d == 0 else 0, 96))]
    extra = {}
    if arm.name in APP:
        extra = {"told": profile.told(arm.runtime), "topics": feed.load_topics(arm.runtime),
                 "calls": profile.calls(arm.runtime), "model": {k: v for k, v in taste.load(arm.runtime)["m"].items()
                                                                if abs(v) > 0.3}}
    return {"arm": arm.name, "person": who.t.name, "first": first, "cards": cards, "present": present,
            "pick_calls": arm.calls, **extra}


def popularity(items: dict, labels: dict) -> dict[str, float]:
    """What everyone would open, on average, at a calm moment: the most popular list with every
    reaction counted."""
    crowd = [person(t, labels, 0) for t in EVERYONE]
    return {i: sum(p.worth(it, T0, 0) for p in crowd) / len(crowd) for i, it in items.items()}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--person", required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--arms", default=",".join(ARMS + VARIANTS))
    parser.add_argument("--seed", type=int, default=0, help="another draw of the same people's days")
    parser.add_argument("--moments", type=Path, default=None, help="where the rank1 arm writes moments for picks.py")
    args = parser.parse_args()
    labels = json.loads((args.snapshot / "labels.json").read_text(encoding="utf-8"))
    items = {}
    for line in (args.snapshot / "items.jsonl").read_text(encoding="utf-8").splitlines():
        it = json.loads(line)
        if it["id"] in labels:
            items[it["id"]] = it
    at = arrivals(list(items.values()))
    pop = popularity(items, labels)
    values = {**profile.values_of(args.env.parent), **{k: v for k, v in env_values(args.env).items()
                                                        if k.startswith("LLM_")}}
    truth = next(t for t in EVERYONE if t.name == args.person)
    seed = EVERYONE.index(truth) + 1000 * args.seed
    results = []
    for name in args.arms.split(","):
        who = person(truth, labels, seed * 100 + 1)
        arm = Arm(name, who, items, at, args.work / name / truth.name, values, pop)
        arm.moments = args.moments
        features(name, labels)
        results.append(await run(arm, args.days, seed * 100 + 2))
    args.work.mkdir(parents=True, exist_ok=True)
    (args.work / f"{truth.name}.json").write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())

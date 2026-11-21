"""The feed: what the character found on its walks, ranked for its person by
what they do with it.

Every reaction from the pet's card, the chat or the 推荐记录 list is kept as an event in
`<runtime>/feed.json`. Taste is read back from those events: the sites and
kinds of the things the person opened and liked count for a thing, the ones
they skipped count against it. The topics the person named on the first run
are kept in the same file and count before there is any behaviour to go on.
A thing a public source brought also counts what was learned of that source
(`sources.weights`, fed from `remember`), and no source fills more than
SOURCE_CAP of any PAGE_WINDOW things in a row.

The ranking itself is the person's model in `taste` (a Bayesian logistic
regression with Thompson draws, and the character's pick slot); `score` is the
old point sum, kept for the FEED_MODEL switch and for the replay to compare
against. Each event is [at, id, action, site, kind, extra], extra holding the
model's snapshot of the thing at that moment (older events have five).
Things whose host cannot be reached now (`situation.gate`) are never shown.
"""
from __future__ import annotations

import asyncio
import functools
import json
import re
import threading
from collections import Counter, deque
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlsplit

from fastapi import Request

from .. import activity, browsed, langs, life, paths, profile, situation, subjects
from .. import taste as model
from ..library import DISMISSED, KEPT, Item, Library
from ..sources import weights
from ..storage import atomic_write_text
from ..web import signin

TASTE_FILE = "feed.json"
# "hide_source" is "less like this source": the thing is skipped and its source read and ranked far less.
# "play" carries the seconds the app's own player ran, sent when it stops or the card closes; "read" the
# seconds a find stayed in front in the app's own browser. "end" is how the pet's card went (ENDS), with
# the seconds it was out.
# "talk": the person answered the thing in the chat (welcome.py keeps it; the page does not send it).
ACTIONS = ("seen", "open", "like", "unlike", "skip", "hide_source", "play", "read", "end", "talk")
# Opened, liked, closed by the person (within QUICK_S it is "quick"), gone by itself, or put away with the
# pet (a game came to the front). One gone by itself while the person was away is kept as "away".
# "next": passed over for another at once (the card's "Another"). "known": the person had seen it already,
# which says nothing of their taste.
ENDS = ("open", "like", "close", "timeout", "hushed", "next", "known")
# What a no on a card or in the chat's menu was about: this thing, or its kind of topic (then the topic's
# feature leans to less, profile "leans"). "Less from this source" is the action "hide_source".
SAID_ABOUT = ("thing", "topic")
QUICK_S = 3
PLAY_MAX_S = 24 * 3600
# The first run's round of sample finds, at most FIRST_PER_SOURCE from one source, and how long it waits for a
# sources round still out (onboard.js says so while it waits).
FIRST_MAX = 10
FIRST_PER_SOURCE = 2
FIRST_WAIT_S = 30
# A sample find's summary is at least this wide, a Chinese character counting two.
SUMMARY_MIN = 60
# A title that is only a name and a version number ("llm-gemini 0.38"): a release, not a thing to read.
RELEASE = re.compile(r"[\w.-]+ v?\d+(\.\d+)+[a-z]*\d*", re.I)
# Shops and booking sites by their name: their pages are for buying and booking, not for reading.
ERRAND_SITES = frozenset({"taobao", "tmall", "jd", "pinduoduo", "yangkeduo", "suning", "amazon", "ebay", "aliexpress",
                          "etsy", "temu", "shein", "walmart", "bestbuy", "rakuten", "mercari", "coupang", "ozon",
                          "wildberries", "zalando", "mercadolivre", "mercadolibre", "meituan", "ele", "ctrip", "trip",
                          "fliggy", "ly", "qunar", "tuniu", "12306", "booking", "expedia", "agoda", "airbnb",
                          "skyscanner", "kayak", "hotels"})
# Query keys that only say where a visitor came from: a front page with them is still the front page.
TRACKING = ("utm_", "ref", "spm", "from", "source", "share")
# One event per item and action at most, so this holds a few thousand things.
EVENTS_MAX = 8000
LIMIT = 20
LIMIT_MAX = 100
# Points a site or kind earns for each thing of it liked, opened or skipped, and their bound.
LIKE, OPEN, SKIP = 2, 1, -2
TASTE_MAX = 6
# Worth this much when just found, half of it a day later, a quarter two days later.
FRESH = 4.0
# The character itself thought this one fits its person.
FITS = 2.0
# A thing on a topic the person named: above a fresh one from yesterday at first,
# half of that once they have reacted to TOPIC_HALF things, since by then what they do says more.
TOPIC = 5.0
TOPIC_HALF = 40
TOPICS_MAX = 30
TOPIC_LEN = 40
# Points per unit of a source's learned exponent (-3 to 2), and taken off for each "less like this source".
SOURCE = 1.0
SOURCE_HIDDEN = 12.0
# At most this many things from one source in any this many in a row.
SOURCE_CAP, PAGE_WINDOW = 3, LIMIT
# An earlier find's title in a reason, at most this many characters.
REASON_TITLE = 32

# The topics offered on the first run, with their names and the words that find them in a title,
# an excerpt or a site's name (subjects.py holds them, each with the finer subjects under it). A topic the
# person typed is found as itself.
SUGGESTED = {key: (subjects.SUBJECTS[key][1], subjects.words(key)) for key in subjects.OFFERED}

ERRORS: frozenset[str] = frozenset({"feed_action", "feed_cursor", "feed_topics", "feed_no_source",
                                    "situation_invalid", "feed_format", "feed_snooze"})

# The page answers reactions on worker threads; one writer at a time.
_lock = threading.Lock()


# ------------------------------------------------------------------ events --

def _read(path: Path) -> dict:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def _load(path: Path) -> list:
    events = _read(path).get("events")
    return [e for e in events if isinstance(e, list) and (len(e) == 5 or len(e) == 6 and isinstance(e[5], dict))]\
        if isinstance(events, list) else []


def load_topics(path: Path) -> list[str]:
    topics = _read(path).get("topics")
    return [t for t in topics if isinstance(t, str)] if isinstance(topics, list) else []


def _save(path: Path, events: list, topics: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps({"events": events[-EVENTS_MAX:], "topics": topics}, ensure_ascii=False))


def _marks(events: list, until: Optional[float] = None) -> tuple[dict, float]:
    """What the person had done with each thing by `until`, and when the
    desktop last showed something."""
    marks: dict[str, dict] = {}
    shown = 0.0
    for at, ident, action, site, kind, *extra in events:
        if until is not None and at > until:
            continue
        mark = marks.setdefault(ident, {"seen": False, "open": False, "like": False, "skip": False,
                                        "hide": False, "play": 0.0, "end": "", "site": site, "kind": kind,
                                        "walled": False})
        mark["seen"] = True
        if action == "seen":
            shown = max(shown, at)
        elif action == "walled":
            mark["walled"] = True
        elif action == "open":
            mark["open"] = True
        elif action == "like":
            mark["like"], mark["skip"] = True, False
        elif action == "unlike":
            mark["like"] = False
        elif action == "skip":
            mark["skip"] = True
        elif action == "hide_source":
            mark["like"], mark["skip"], mark["hide"] = False, True, True
        elif action in ("play", "read"):
            mark["play"] = max(mark["play"], float(extra[0].get(action + "_s") or 0))
        elif action == "end":
            mark["end"] = extra[0].get("end") or ""
    return marks, shown


def _taste(marks: dict) -> dict[str, float]:
    """Points per "site:<name>" and "kind:<name>", each kept within TASTE_MAX."""
    points: dict[str, float] = {}
    for mark in marks.values():
        earned = LIKE * mark["like"] + OPEN * mark["open"] + SKIP * mark["skip"]
        if earned:
            for key in ("site:" + mark["site"], "kind:" + mark["kind"]):
                points[key] = points.get(key, 0) + earned
    return {key: max(-TASTE_MAX, min(TASTE_MAX, value)) for key, value in points.items()}


def site_taste(runtime: Path) -> dict[str, float]:
    """Points per site from the person's reactions, for the walks to lean on."""
    taste = _taste(_marks(_load(Path(runtime) / TASTE_FILE))[0])
    return {key[5:]: value for key, value in taste.items() if key.startswith("site:")}


def _by_source(marks: dict, sources: dict[str, str]) -> dict[str, dict]:
    """What `weights` learns of each source, read from these marks: the same points, as of the marks' moment."""
    rows: dict[str, dict] = {}
    for ident, mark in marks.items():
        key = sources.get(ident)
        if key:
            row = rows.setdefault(key, {"items": 0, "points": 0.0, "hides": 0})
            row["items"] += 1
            row["points"] += weights.POINTS["seen"] + sum(weights.POINTS[a] for a in ("open", "like", "skip") if mark[a])
            row["hides"] += mark["hide"]
    return rows


def source_points(row: Optional[dict]) -> float:
    """What was learned of the source that brought a thing, as points; 0 for a walk's find."""
    return SOURCE * weights.learned(row) - SOURCE_HIDDEN * (row["hides"] if row else 0)


def score(shaped: dict, found_at: int, fits: bool, taste: dict, now: float, topical: float = 0.0,
          source: float = 0.0) -> float:
    """Its site's points, half its kind's, freshness, FITS when the character said it fits,
    `topical` when it is on a topic the person named, and its source's points."""
    days = max(0.0, now - found_at) / 86400
    return (taste.get("site:" + shaped["site"], 0) + taste.get("kind:" + shaped["kind"], 0) / 2
            + FRESH * 0.5 ** days + (FITS if fits else 0) + topical + source)


def spread(rows: list, origin) -> list:
    """`rows` in their order, except that a row whose origin already has SOURCE_CAP of the
    last PAGE_WINDOW placed waits for the next row that may go; when none may, the best goes."""
    queues: dict = {}
    for n, row in enumerate(rows):
        queues.setdefault(origin(row), deque()).append((n, row))
    placed, recent, counts = [], deque(), {}
    while queues:
        allowed = [key for key in queues if counts.get(key, 0) < SOURCE_CAP]
        key = min(allowed or queues, key=lambda k: queues[k][0][0])
        placed.append(queues[key].popleft()[1])
        if not queues[key]:
            del queues[key]
        recent.append(key)
        counts[key] = counts.get(key, 0) + 1
        if len(recent) > PAGE_WINDOW:
            gone = recent.popleft()
            counts[gone] -= 1
    return placed


# ------------------------------------------------------------------ topics --

def _names(lang: str) -> int:
    return 1 if str(lang).startswith("zh") else 0


def suggested(lang: str) -> list[dict]:
    zh = _names(lang)
    return [{"key": key, "name": names[zh]} for key, (names, _) in SUGGESTED.items()]


def clean_topics(raw) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError("feed_topics")
    topics, seen = [], set()
    for value in raw:
        topic = " ".join(str(value or "").split())[:TOPIC_LEN]
        if topic and topic.casefold() not in seen:
            seen.add(topic.casefold())
            topics.append(topic)
    return topics[:TOPICS_MAX]


def _word(word: str) -> str:
    # A Latin word is found whole, so "ai" is not found in "said"; Chinese has no spaces to look for.
    if word.isascii():
        return r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])"
    return re.escape(word)


def topic_matcher(topics: list[str]) -> Optional[re.Pattern]:
    """One pattern for every word the person's topics stand for, or None without topics."""
    named = {}
    for key, (names, words) in SUGGESTED.items():
        for name in (key, *names):
            named[name.casefold()] = words
    words = set()
    for topic in topics:
        words.update(named.get(topic.casefold(), (topic,)))
    if not words:
        return None
    # Longest first, so "open source" is tried before "source" would be.
    return re.compile("|".join(subjects._word(w.casefold()) for w in sorted(words, key=len, reverse=True)))


def on_topic(shaped: dict, matcher: Optional[re.Pattern]) -> bool:
    return bool(matcher and matcher.search(" ".join((shaped["title"], shaped["text"], shaped["site"])).casefold()))


@functools.lru_cache(maxsize=8)
def _matchers(topics: tuple) -> tuple:
    # The person's own topics that no subject stands for: found as themselves.
    return tuple((t.casefold(), topic_matcher([t])) for t in topics if not subjects.named(t))


class Veins:
    """A thing's source (its site for a walk's find) and what it is about, its subjects and the person's
    own topics: no two in a row share either where there is a choice."""

    def __init__(self, topics: list[str]):
        self.matchers = _matchers(tuple(topics))

    def of(self, shaped: dict, item: Item) -> tuple[str, frozenset]:
        text = " ".join((shaped["title"], shaped["text"], shaped["site"])).casefold()
        about = subjects.of(shaped["title"], " ".join((shaped["text"], shaped["site"], *map(str, item.tags))))
        return (item.source or "site:" + shaped["site"],
                frozenset(about) | frozenset(name for name, matcher in self.matchers if matcher.search(text)))

    @staticmethod
    def same(a: Optional[tuple], b: Optional[tuple], topics: bool = True) -> bool:
        return bool(a and b and (a[0] == b[0] or topics and a[1] & b[1]))

    def last(self, library: Library, events: list) -> Optional[tuple]:
        """The vein of the last thing the pet's card or the main chat brought."""
        for _at, ident, action, *rest in reversed(events):
            extra = rest[2] if len(rest) > 2 else {}
            if action == "seen" and extra.get("s") in ("card", "chat"):
                item = library.get(ident)
                shaped = shape(item, {}) if item else None
                return self.of(shaped, item) if shaped else None
        return None

    def varied(self, rows: list, last: Optional[tuple] = None, mix: bool = False) -> list:
        """(shaped, item) rows in their order, except that each goes after one from another source and on
        other topics where one is left; failing that, from another source. With `mix`, text and video take
        turns where both are left."""
        veins = {row[1].id: self.of(*row) for row in rows}
        left, placed, video = list(rows), [], None
        while left:
            apart = [r for r in left if not self.same(veins[r[1].id], last)]
            row = (mix and next((r for r in apart if is_video(r[1], r[0]) != video), None)
                   or next(iter(apart), None)
                   or next((r for r in left if not self.same(veins[r[1].id], last, False)), left[0]))
            left.remove(row)
            placed.append(row)
            last, video = veins[row[1].id], is_video(row[1], row[0])
        return placed


def is_video(item: Item, shaped: dict) -> bool:
    return item.kind == "video" or shaped["kind"] == "video"


# ------------------------------------------------------------------- items --

def shape(item: Item, marks: dict) -> Optional[dict]:
    """One thing as the feed draws it, from the same card the days and the chat use."""
    card = life.clean_card(item.card())
    if card is None:
        return None
    player = card["embed"]
    kind = "video" if item.kind == "video" and player else "page" if card["readable"] else "text"
    return {"id": item.id, "kind": kind, "title": card["title"], "words": " ".join(item.why.split()),
            "text": card["excerpt"], "site": card["site"], "url": card["url"], "player": player,
            "picture": card["picture"], "source": item.source,
            "at": datetime.fromtimestamp(item.seen_at).astimezone().isoformat(timespec="seconds"),
            "seen": bool(marks.get(item.id, {}).get("seen")), "liked": item.state == KEPT,
            # Set by `ranked`: the feature the ranking's reason rests on, and the slot that served it.
            "reason_term": "", "slot": ""}


def errand(url: str) -> bool:
    """A page to go to or to do something on rather than a thing to read: a site's front page, a sign-in,
    account or payment page, or a shop's or a booking site's page."""
    parts = urlsplit(url)
    query = [key for key, _ in parse_qsl(parts.query) if not key.lower().startswith(TRACKING)]
    return (parts.path.strip("/").lower() in ("", "index.html", "index.htm", "index.php") and not query
            or browsed.secret(url) or browsed.site_of(parts.hostname or "").split(".")[0] in ERRAND_SITES)


def presentable(shaped: dict) -> bool:
    """A sample find for the first run: a real title, a summary that says something and a picture."""
    title = shaped["title"].strip()
    width = sum(2 if ord(c) > 0x2E80 else 1 for c in shaped["text"])
    return bool(title and title != shaped["site"] and not RELEASE.fullmatch(title) and width >= SUMMARY_MIN
                and shaped["picture"])


def ranked(library: Library, events: list, at: float, topics: list[str] = (), *, seed: Optional[int] = None,
           picks: bool = True) -> list[tuple[dict, Item]]:
    """Everything not dismissed, not skipped, found by `at`, reachable now and a thing to read (not an
    `errand`), unseen first, then by score:
    the model's draw seeded by `seed` (the cursor's ms unless given), with the character's picks placed
    when `picks`, or the old point sum when the FEED_MODEL switch is off; things on a topic the person said
    they do not want, then those of a source they hid, after all the rest. Reactions after `at` do not move
    anything, so a cursor's pages hold still."""
    runtime = library.root.parent
    then, _ = _marks(events, at)
    now, _ = _marks(events)
    items = library.items()
    learned = _by_source(then, {item.id: item.source for item in items})
    # A source the person asked to see less of: as it stood at `at`, like every other mark.
    hidden = {key for key, row in learned.items() if row["hides"]}
    ranker = None
    if model.enabled(runtime):
        ranker = model.Ranker(runtime, list(topics), at, int(at * 1000) if seed is None else seed, hidden, events)
        if model.due(runtime, events, life.clock()):
            model.fit(runtime)
    else:
        taste = _taste(then)
        matcher = topic_matcher(list(topics))
        acted = sum(1 for mark in then.values() if mark["open"] or mark["like"] or mark["skip"])
        topical = TOPIC * 0.5 ** (acted / TOPIC_HALF)
    reachable: dict[str, bool] = {}
    # A site and kind whose find opened to a sign-in or a check: held back while the person is signed out there.
    walls = {(mark["site"], mark["kind"]) for mark in now.values() if mark["walled"]}
    signed: dict[str, bool] = {}

    def up(host: str) -> bool:
        if host not in reachable:
            reachable[host] = situation.gate(runtime, host=host)
        return reachable[host]

    def held(shaped: dict) -> bool:
        if (shaped["site"], shaped["kind"]) not in walls:
            return False
        if shaped["site"] not in signed:
            signed[shaped["site"]] = signin.signed_in(runtime / "desktop", shaped["url"])
        return not signed[shaped["site"]]

    rows = []
    for item in items:
        if item.state == DISMISSED or item.seen_at > at or then.get(item.id, {}).get("skip") \
                or now.get(item.id, {}).get("walled"):
            continue
        shaped = shape(item, now)
        if shaped is None or errand(shaped["url"]) or held(shaped):
            continue
        # A thing whose page or player cannot be reached is held back, not shown and not counted against.
        if all(up(host) for host in model.hosts(item, shaped)):
            seen = bool(then.get(item.id, {}).get("seen"))
            if ranker:
                points = ranker.score(item, shaped)
            else:
                points = score(shaped, item.seen_at, item.fits, taste, at,
                               topical if on_topic(shaped, matcher) else 0, source_points(learned.get(item.source)))
            rows.append((seen, -points, -item.seen_at, item.id, shaped, item))
    rows.sort(key=lambda row: row[:4])
    # A topic the person said they do not want is a rule, not a weight: its things go after everything
    # else, whatever a draw gives them.
    avoid = None if ranker else topic_matcher(profile.avoided(runtime))
    away = {row[3] for row in rows if (ranker.info[row[3]]["away"] if ranker else on_topic(row[4], avoid))}
    # A walk's finds have no source: their site stands in. A source the person asked to see less of
    # goes after everything, so the cap never pulls its things up.
    shown = spread([row for row in rows if row[5].source not in hidden and row[3] not in away],
                   lambda row: row[5].source or "site:" + row[4]["site"])
    chosen: set[str] = set()
    if ranker and picks:
        shown, chosen = ranker.picks(shown, model.pick_share(events))
    rows = (shown + [row for row in rows if row[5].source not in hidden and row[3] in away]
            + [row for row in rows if row[5].source in hidden])
    if ranker:
        for rank, row in enumerate(rows):
            ranker.finish(row[4], rank, row[3] in chosen)
            row[4]["reason_term"] = ranker.info[row[3]]["term"]
    return [(row[4], row[5]) for row in rows]


def shown(library: Library, events: list, kept: bool = False, limit: int = LIMIT, before: str = "") -> dict:
    """One page of what the person was shown, newest first: each thing a card, the chat, the first run or the
    app's browser put in front of them, at its first showing, unless they turned it down since; with `kept`,
    the things they liked instead, at their last like. `before` is the `next` of the page above it: the
    milliseconds and id of its last row. Rows are (at, rank, item), rank the row's place in the whole list."""
    limit = max(1, min(LIMIT_MAX, int(limit or LIMIT)))
    cursor = re.fullmatch(r"(\d{1,16})\.([0-9a-f]{16})", before) if before else None
    if before and not cursor:
        raise ValueError("feed_cursor")
    first, liked = {}, {}
    for at, ident, action, *_ in events:
        first.setdefault(ident, at)
        if action == "like":
            liked[ident] = at
    marks = _marks(events)[0]
    rows = []
    for item in library.items():
        if kept and item.state == KEPT:
            rows.append((liked.get(item.id, item.seen_at), item))
        elif not kept and item.id in first and item.state != DISMISSED and not marks[item.id]["skip"]:
            rows.append((first[item.id], item))
    order = [((-int(at * 1000), item.id), at, item) for at, item in rows]
    order.sort(key=lambda row: row[0])
    start = 0
    if cursor:
        after = (-int(cursor[1]), cursor[2])
        start = next((n for n, row in enumerate(order) if row[0] > after), len(order))
    page = order[start:start + limit]
    return {"rows": [(at, start + n, item) for n, (_, at, item) in enumerate(page)],
            "next": f"{-page[-1][0][0]}.{page[-1][2].id}" if start + len(page) < len(order) else ""}


def first_round(library: Library, events: list, now: float, topics: list[str]) -> list[dict]:
    """The first run's sample finds: the ranking's best unseen that are `presentable`, FIRST_PER_SOURCE from
    one site and FIRST_MAX in all at most, those the character has read first, never two in a row from one
    source or on one topic where there is a choice, and text and video in turn where both are left. A site
    counts as one however many of its lists brought things (a video site has a list for each kind of video);
    only when too few sites have something presentable yet does one give more."""
    rows, more, per = [], [], Counter()
    for shaped, item in ranked(library, events, now, topics, seed=int(now * 1000)):
        if shaped["seen"] or len(rows) >= 3 * FIRST_MAX:
            break
        if not presentable(shaped):
            continue
        site = shaped["site"] or item.source
        if per[site] < FIRST_PER_SOURCE:
            per[site] += 1
            rows.append((shaped, item))
        else:
            more.append((shaped, item))
    rows.sort(key=lambda row: not row[1].judged)
    shown = Veins(topics).varied(rows, mix=True)[:FIRST_MAX]
    return [shaped for shaped, _ in shown + more[:FIRST_MAX - len(shown)]]


# What the pet's card was handed, by id, so a find it opens in the chat keeps the character's line and slot.
on_card: dict[str, dict] = {}


def remember(runtime: Path, item: Item, action: str, now: Optional[float] = None, *, surface: str = "",
             rank: Optional[int] = None, play_s: float = 0.0, slot: str = "", end: str = "",
             shown_s: float = 0.0, said: Optional[dict] = None, line: str = "") -> bool:
    """Keep one reaction with the model's snapshot of the thing at that moment, where it was shown
    (`surface`: card, list, chat or first), at which `rank` and in which `slot` (model, explore or character,
    as the ranking served it when the page does not say), and what it says of the item's source; one that
    changes nothing is not kept again, and a "play" or "read" (`play_s` its seconds) only when it ran longer
    than any before; an "end" (`end`, `shown_s`) once. `said`: what the person said of it in the chat
    ({"about", "words"}), which the model counts as more than a plain skip. A showing keeps what its reason
    rests on ("why", recommend.facts; `line` the character's sentence as it brought the thing) and also goes to
    shown.jsonl; any reaction but a showing asks the model to refit soon. Whether it was kept."""
    path = Path(runtime) / TASTE_FILE
    shaped = shape(item, {})
    at = round(life.clock() if now is None else now, 3)
    there = model.served.get(item.id, {})
    rank = there.get("r") if rank is None else rank
    slot = slot or there.get("p", "")
    if action == "seen":
        model.shown(runtime, item, surface, rank, at, model._host(item.url), slot)
    extra = {"f": model.Reader(runtime).snapshot(item, shaped, at) if shaped else None, "r": rank, "s": surface,
             "p": slot}
    if action in ("play", "read"):
        extra[action + "_s"] = round(play_s, 1)
    if action == "end":
        extra.update(end=end, shown_s=round(shown_s, 1))
    if said:
        extra["said"] = said
    with _lock:
        events = _load(path)
        mark = _marks(events)[0].get(item.id)
        mine = [e for e in events if e[1] == item.id]
        # An open again counts once it is a coming back (model.BACK_S after the last one); a talk counts once.
        opened = max((e[0] for e in mine if e[2] == "open"), default=None)
        done = {"seen": mark and mark["seen"], "open": opened is not None and at - opened < model.BACK_S,
                "talk": any(e[2] == "talk" for e in mine), "like": mark and mark["like"],
                "unlike": not (mark and mark["like"]), "skip": mark and mark["skip"],
                "hide_source": mark and mark["hide"], "play": play_s <= (mark["play"] if mark else 0),
                "read": play_s <= (mark["play"] if mark else 0), "end": mark and mark["end"]}[action]
        # A find that opened to a wall says nothing more of the person's taste.
        if done or mark and mark["walled"]:
            return False
        if action == "seen":
            from .. import recommend
            extra["why"] = recommend.facts(item, extra["f"], slot, surface, there.get("t", ""), line,
                                           model.history(events))
        events.append([at, item.id, action, shaped["site"] if shaped else item.site,
                       shaped["kind"] if shaped else "text", extra])
        _save(path, events, load_topics(path))
        # Time in the app's player or browser, how a card ended and a talk only tell the model; the source
        # learns nothing from them.
        if item.source and action not in ("play", "read", "end", "talk"):
            if action == "hide_source":
                weights.hidden(runtime, item.source)
            # Hiding a source skips the thing too, unless it was skipped already.
            if action != "hide_source" or not (mark and mark["skip"]):
                weights.reacted(runtime, item.source, "skip" if action == "hide_source" else action, mark is None)
    if action != "seen":
        model.fit(runtime)
    return True


def take_back(runtime: Path, item: Item) -> None:
    """Undo a no: the thing's skip, its "less from this source" and the ending the no gave its card go from
    the events, with what they did to its source's count and to the topic's lean, as if the card were
    still out. An ending from before the no (it was opened, or went by itself) stays."""
    path = Path(runtime) / TASTE_FILE
    with _lock:
        events = _load(path)
        gone = [e for e in events if e[1] == item.id and (
            e[2] in ("skip", "hide_source") or e[2] == "end" and e[5]["end"] in ("close", "quick", "known"))]
        _save(path, [e for e in events if e not in gone], load_topics(path))
    nos = [e for e in gone if e[2] != "end"]
    for event in nos:
        said = event[5].get("said") or {}
        if said.get("key"):
            profile.edit(runtime, {"leans": {said["key"]: said["was"]}})
    if nos and item.source:
        def edit(state: dict) -> None:
            # One skip was counted for the thing, whichever of the two said it first (remember).
            row = state[item.source]
            row["points"] = round(row["points"] - weights.POINTS["skip"], 2)
            row["hides"] -= sum(1 for e in nos if e[2] == "hide_source")

        weights.change(runtime, edit)
    model.fit(runtime)


def walled(runtime: Path, item: Item) -> bool:
    """A find opened in the app's browser came to a sign-in or a check: its open, its time there and the card's
    ending by the open go, with the open's points for its source, so it counts as neither interest nor dislike;
    it is not shown again, and its site's finds of its kind wait while the person is signed out there (`ranked`).
    Whether it was new."""
    path = Path(runtime) / TASTE_FILE
    with _lock:
        events = _load(path)
        mark = _marks(events)[0].get(item.id)
        if mark is None or mark["walled"]:
            return False
        gone = [e for e in events if e[1] == item.id and (
            e[2] in ("open", "read") or e[2] == "end" and e[5].get("end") == "open")]
        events = [e for e in events if e not in gone]
        events.append([round(life.clock(), 3), item.id, "walled", mark["site"], mark["kind"], {}])
        _save(path, events, load_topics(path))
    if item.source and any(e[2] == "open" for e in gone):
        def edit(state: dict) -> None:
            row = state[item.source]
            row["points"] = round(row["points"] - weights.POINTS["open"], 2)

        weights.change(runtime, edit)
    model.fit(runtime)
    return True


def forget_items(runtime: Path, ids: set[str]) -> None:
    """Things taken out of the library (a page deleted from the browser's history): their events go, and the model
    is fitted again without them."""
    path = Path(runtime) / TASTE_FILE
    with _lock:
        events = _load(path)
        kept = [e for e in events if e[1] not in ids]
        if len(kept) == len(events):
            return
        _save(path, kept, load_topics(path))
    model.fit(runtime)


def keep_topics(runtime: Path, topics: list[str]) -> None:
    path = Path(runtime) / TASTE_FILE
    with _lock:
        _save(path, _load(path), topics)


def forget_said(runtime: Path, match: re.Pattern) -> int:
    """Blank what `match` finds (casefolded) in the person's words about finds and in the snapshots' liked
    and avoided topics, and take the subjects it names out of the snapshots, so what the model learned of
    them goes with the next fit; for a forget (profile.forget). How many events changed."""
    path = Path(runtime) / TASTE_FILE
    changed = 0
    named = {key for key, row in subjects.SUBJECTS.items() if any(match.search(n.casefold()) for n in (key, *row[1]))}
    with _lock:
        events = _load(path)
        for event in events:
            extra = event[5] if len(event) > 5 and isinstance(event[5], dict) else {}
            said, snap = extra.get("said"), extra.get("f")
            hit = isinstance(said, dict) and bool(match.search(str(said.get("words") or "").casefold()))
            if hit:
                said["words"] = ""
            for key in ("likes", "avoid"):
                names = snap.get(key) if isinstance(snap, dict) else None
                if names and any(match.search(str(n).casefold()) for n in names):
                    snap[key] = [n for n in names if not match.search(str(n).casefold())]
                    hit = True
            if isinstance(snap, dict) and named & set(snap.get("subj") or ()):
                snap["subj"] = [k for k in snap["subj"] if k not in named]
                hit = True
            changed += hit
        if changed:
            _save(path, events, load_topics(path))
    return changed


# ------------------------------------------------------------------ routes --

class Feed:
    def __init__(self, workspace):
        self.ws = workspace

    def _path(self) -> Path:
        return self.ws._runtime() / TASTE_FILE

    def _events(self) -> list:
        return _load(self._path())

    def page(self, limit: int, before: str, kept: bool) -> dict:
        """One page of `shown` (`kept`: of the liked) for the app's 推荐记录, each thing as the main chat draws
        it, at the time it was shown or liked: with the chip and the topic it had in the chat or on the pet's
        card, else with those it would have now, its place for the list's reactions, and the sentence of why it
        was shown (`reason`): what its showing kept (recommend.facts), or for one shown before that was kept,
        the same read from its showing's event, the ranking's terms in shown.jsonl, the character's line in
        the chat and the person's history until then."""
        from .. import recommend
        runtime, events = self.ws._runtime(), self._events()
        library = self.ws._library()
        got = shown(library, events, kept, limit, before)
        chat = {m["card"]["id"]: m for m in self.ws._main.shared()}
        leans = profile.load(runtime, False)["leans"]
        name, _, lang = profile.character(self.ws.home, self.ws.config())
        why = recommend.Why(runtime, load_topics(self._path()), events, life.clock(), lang)
        # Each thing's showing: its first "seen", else its first event (a page opened in the app's browser).
        showing: dict[str, list] = {}
        for event in events:
            if event[1] not in showing or event[2] == "seen" and showing[event[1]][2] != "seen":
                showing[event[1]] = event
        tops = {}
        for row in reversed(paths.read_jsonl([runtime / model.SHOWN_FILE])):
            tops[row.get("id")] = row.get("top") or []

        def title_of(ident: str) -> str:
            found = library.get(ident)
            title = " ".join(found.title.split()) if found else ""
            return title if len(title) <= REASON_TITLE else title[:REASON_TITLE - 1] + "…"

        cards = []
        for at, rank, item in got["rows"]:
            card = life.said(item).get("card")
            if card is None:
                continue
            shaped = shape(item, {})
            message = chat.get(item.id, {})
            had = message.get("card") or on_card.get(item.id) or {}
            now = why.of(shaped, item)
            topic = had.get("topic") or now["topic"]
            chip = had.get("why") or now["why"]
            if chip and chip.get("key"):
                chip = {**chip, "lean": leans.get(chip["key"])}
            event = showing.get(item.id)
            extra = event[5] if event and len(event) > 5 else {}
            said = extra.get("why")
            if said is None:
                snap = extra.get("f") or {}
                # The chat's line, unless it is only the title the message falls back to.
                line = message.get("text") or ""
                line = "" if item.title.startswith(line.rstrip("…")) else line
                said = recommend.facts(item, snap, extra.get("p", ""), extra.get("s", ""),
                                       recommend.term_of(tops.get(item.id, []), bool(snap.get("guess"))), line,
                                       model.history(events, event[0] - 0.001) if event else {})
            card.update(why=chip, topic=topic, rank=rank, slot=had.get("slot", ""), surface="list",
                        reason=why.told(said, item, shaped, name, title_of),
                        at=datetime.fromtimestamp(at).astimezone().isoformat(timespec="seconds"))
            cards.append(card)
        return {"items": cards, "next": got["next"]}

    async def next(self, now: bool = False) -> dict:
        """The character's choice for the card (`recommend`), when the pacing says this is a moment for one
        (pacing.py), or `now` as the pet's menu and the card's "Another" ask: then the ranking's next comes
        at once when no choice waits, its sentence after it (`line`). Otherwise nothing while one is being
        chosen, and the pet is told to look again once it is (news)."""
        if not now and not await self.ws.pace.due("card"):
            return {"item": None}
        chosen = await self.ws.chooser.take("card", now)
        if chosen is None:
            return {"item": None}
        on_card[chosen[0]["id"]] = chosen[0]
        return {"item": chosen[0]}

    def numbers(self) -> dict:
        runtime = self.ws._runtime()
        return {**model.numbers(runtime), "calls": profile.calls(runtime)}

    def situation(self) -> dict:
        return situation.view(self.ws._runtime())

    def set_situation(self, payload: dict) -> dict:
        name = payload.get("name")
        if not isinstance(name, str):
            raise ValueError("situation_invalid")
        situation.set_value(self.ws._runtime(), name, payload.get("value"))
        return self.situation()

    def first(self) -> dict:
        return {"items": first_round(self.ws._library(), self._events(), life.clock(), load_topics(self._path()))}

    def _paced(self, ident: str, action: str, surface: str, end: str) -> None:
        """What the pacing hears: the card brought something, or how the last thing brought ended."""
        if surface == "card" and action == "seen":
            self.ws.pace.shown("card", ident)
        elif surface in ("card", "chat") and action == "end":
            self.ws.pace.ended(ident, end, surface)
        elif surface == "chat" and action in ("open", "like", "play", "read", "skip", "hide_source"):
            how = "close" if action in ("skip", "hide_source") else "like" if action == "like" else "open"
            self.ws.pace.ended(ident, how, "chat")

    def interests(self, lang: str) -> dict:
        lang = langs.normalize(lang) or langs.normalize(self.ws.config().get("AGENT_LANG")) or "en"
        return {"topics": load_topics(self._path()), "suggested": suggested(lang)}

    def choose(self, payload: dict) -> dict:
        keep_topics(self.ws._runtime(), clean_topics(payload.get("topics")))
        return self.interests(payload.get("lang") or "")

    async def react(self, payload: dict) -> dict:
        action = payload.get("action")
        if action not in ACTIONS:
            raise ValueError("feed_action")
        item = self.ws._item(payload)
        if action == "hide_source" and not item.source:
            raise ValueError("feed_no_source")
        surface, rank, slot = payload.get("surface") or "", payload.get("rank"), payload.get("slot") or ""
        # Seconds: played, read in the app's browser, or how long the card was out.
        seconds = payload.get({"read": "read_s", "end": "shown_s"}.get(action, "play_s")) or 0
        end = payload.get("end") if action == "end" else ""
        # "Not interested" with what it was about: a skip that says so, which counts as more than a plain one.
        # About its topic, `key` is the feature that then leans to less (topic:, like: or subj:).
        said = payload.get("said")
        if said is not None:
            if action != "skip" or not isinstance(said, dict) or said.get("about") not in SAID_ABOUT:
                raise ValueError("feed_action")
            key = said.get("key") if said["about"] == "topic" else None
            if said["about"] == "topic" and not (isinstance(key, str) and len(key) <= 120
                                                 and key.partition(":")[0] in ("topic", "like", "subj")):
                raise ValueError("feed_action")
            said = {"about": said["about"], "words": ""}
            if key:
                # What the lean was before, for an undo to put back.
                was = await asyncio.to_thread(lambda: profile.load(self.ws._runtime(), False)["leans"].get(key))
                said.update(key=key, was=was)
        if (surface not in model.SURFACES and surface != "" or slot not in ("", "model", "explore", "character")
                or rank is not None and (not isinstance(rank, int) or isinstance(rank, bool) or rank < 0)
                or not isinstance(seconds, (int, float)) or isinstance(seconds, bool)
                or not 0 <= seconds <= PLAY_MAX_S or action in ("play", "read") and not seconds
                or action == "end" and end not in ENDS):
            raise ValueError("feed_action")
        if end == "close" and seconds < QUICK_S:
            end = "quick"
        elif end == "timeout" and await asyncio.to_thread(activity.idle) >= seconds:
            # No key or mouse input the whole time it was out: the person was away, and it says nothing.
            end = "away"
        played = 0.0 if action == "end" else float(seconds)
        kept = await asyncio.to_thread(remember, self.ws._runtime(), item, action, surface=surface, rank=rank,
                                       play_s=played, slot=slot, end=end, shown_s=float(seconds) - played,
                                       said=said, line=on_card.get(item.id, {}).get("line", ""))
        await asyncio.to_thread(self._paced, item.id, action, surface, end)
        if action == "seen" and surface == "card":
            self.ws.chooser.shown(item.id)
        elif action in ("like", "unlike"):
            # A liked thing is a kept one: the days and the chat see the same choice.
            await self.ws.mark_item(item.id, "keep", action == "like")
        elif action in ("skip", "hide_source"):
            topical = bool(said) and said["about"] == "topic"
            # The chat's card stays folded to the line that says which no it was.
            self.ws.fold_card(item.id, "topic" if topical else action)
            if kept and topical:
                await asyncio.to_thread(profile.edit, self.ws._runtime(), {"leans": {said["key"]: -1}})
            if kept and (topical or action == "hide_source"):
                self.ws.chooser.again()
        elif end == "known":
            self.ws.fold_card(item.id, end)
        return {"ok": True}

    async def undo(self, payload: dict) -> dict:
        """Take back a no said on a card (the pet's, within a few seconds; the chat's folded line): the thing
        is as it was before it, and a card of it in the chat unfolds."""
        item = self.ws._item(payload)
        await asyncio.to_thread(take_back, self.ws._runtime(), item)
        self.ws.fold_card(item.id, "")
        self.ws.chooser.again()
        return {"ok": True}

    async def lean(self, payload: dict) -> dict:
        """More or less of what a card's reason names, from the chip under its site: the lean on that
        feature (profile "leans", as Settings sets it), `to` 1, -1, or None for neither."""
        await asyncio.to_thread(profile.edit, self.ws._runtime(), {"leans": {payload.get("key"): payload.get("to")}})
        self.ws.chooser.again()
        return {"ok": True}


def mount(app, workspace, body) -> None:
    panel = Feed(workspace)

    @app.get("/api/feed")
    async def feed_page(limit: int = LIMIT, before: str = "", kept: bool = False):
        return await asyncio.to_thread(panel.page, limit, before, kept)

    @app.get("/api/feed/next")
    async def feed_next(now: bool = False):
        return await panel.next(now)

    # Do not disturb, from the pet's menu: nothing is brought until it ends (pacing.py). In seconds left.
    @app.get("/api/feed/snooze")
    async def feed_snoozed():
        return {"left": max(0.0, workspace.pace.snoozed - life.clock())}

    @app.post("/api/feed/snooze")
    async def feed_snooze(request: Request):
        on = (await body(request)).get("on")
        if not isinstance(on, bool):
            raise ValueError("feed_snooze")
        return {"left": max(0.0, workspace.pace.snooze(on) - life.clock())}

    # The first run's round of sample finds, shown one at a time. On a fresh install the library is still
    # empty, or holds only the first round's language defaults, when the topics are chosen: while a sources
    # round is out (one was asked for as the topics were saved) its finds are waited for, FIRST_WAIT_S at most.
    @app.get("/api/feed/first")
    async def feed_first():
        started = life.clock()
        while app.state.sources.busy and life.clock() - started < FIRST_WAIT_S:
            await asyncio.sleep(1)
        return await asyncio.to_thread(panel.first)

    @app.get("/api/feed/numbers")
    async def feed_numbers():
        return await asyncio.to_thread(panel.numbers)

    # The situation table's view and the person's corrections (see `situation`).
    @app.get("/api/situation")
    async def situation_view():
        return await asyncio.to_thread(panel.situation)

    @app.post("/api/situation")
    async def situation_set(request: Request):
        return await asyncio.to_thread(panel.set_situation, await body(request))

    @app.post("/api/feed/react")
    async def feed_react(request: Request):
        return await panel.react(await body(request))

    @app.post("/api/feed/undo")
    async def feed_undo(request: Request):
        return await panel.undo(await body(request))

    @app.post("/api/feed/lean")
    async def feed_lean(request: Request):
        return await panel.lean(await body(request))

    # The sentence for a find the card showed before it was written ("more" on the item): "" with `wait`
    # while it is on its way.
    @app.get("/api/feed/line")
    async def feed_line(id: str = ""):
        return workspace.chooser.line(id)

    @app.get("/api/feed/interests")
    async def feed_interests(lang: str = ""):
        return await asyncio.to_thread(panel.interests, lang)

    @app.post("/api/feed/interests")
    async def feed_interests_choose(request: Request):
        chosen = await asyncio.to_thread(panel.choose, await body(request))
        # The sources go out for the topics at once, not at the next half hour: the first run's finds and
        # the first day's cards come from this round (catalog.pulls weighs the sources by the topics).
        app.state.sources.ask(even_if_running=True)
        return chosen

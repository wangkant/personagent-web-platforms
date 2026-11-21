"""The feed's model of its person.

One small Bayesian logistic regression per person (Chapelle and Li 2011,
Algorithm 3): a Gaussian prior N(m0, 1/q0) on each weight, a fit by cyclic
coordinate Newton on the person's reactions, a Laplace posterior N(m, 1/q) and
Thompson-sampled scores. The features are few and sparse: where a thing came
from (its source, or its site for a walk's find), what it is about (subjects.py),
its kind and length, its language (and whether the computer lists it as one
the person reads), how fresh it
is, how much on its subjects they were brought lately, what the character
thought of it, the topics the person picked and those they said they like or
do not want in the chat (profile.py), whose priors are set by what they said.

Every event in feed.json keeps a snapshot of these as they were at that moment
(see `Reader.snapshot`), so the model and the replay learn from what the feed
knew then, and survive the library dropping old things. The fit runs in a
worker thread at most once a minute and writes `<runtime>/taste_model.json`;
ranking only reads it.

    python -m mibo.taste replay [--runtime DIR]

replays the person's own reactions day by day against the old point sum.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from . import life, situation, subjects
from .library import Item, Library
from .sources import weights
from .storage import append_jsonl, atomic_write_text

logger = logging.getLogger("agent.taste")

MODEL_FILE = "taste_model.json"
SHOWN_FILE = "shown.jsonl"
SHOWN_MAX = 20_000
DAY = 86400
# Thompson draws at this share of the posterior's spread: below 1 does better over short runs.
ALPHA = 0.5
SWEEPS, STEP_MAX, TOLERANCE = 15, 1.0, 1e-4
# At most one fit this often, and a thing shown and left alone this long is a quiet no.
REFIT_S = 60
QUIET_S = DAY
# Each row counts half as much HALF_DAYS later.
HALF_DAYS = 45
# (label, confidence) of a thing's final mark. A play counts from PLAY_SHARE of the thing's own length in
# the app's player, or PLAY_MIN_S seconds where the length is not known or long, and so does reading for as
# long in the app's browser (READ_CHARS_S characters a second). A card the person closed at once is a firmer no.
HIDE, LIKE, SKIP, PLAY, QUICK = (0, 3.0), (1, 3.0), (0, 1.0), (1, 2.0), (0, 1.5)
PLAY_SHARE, READ_CHARS_S = 0.5, 15
# An open says the title drew them, not that the thing was for them; how long they stayed says that (dwell
# time, as feeds that learn from watch time rather than clicks count it). OPEN: opened where the app cannot
# see the stay (the system's browser), a faint yes. GLANCE: opened in the app and left within GLANCE_S, less
# than GLANCE_WAY of the way to a real play: a look and no more, a faint no. A longer stay short of a real
# play is a yes whose confidence grows from STAY to PLAY's with how far they got.
OPEN, GLANCE, STAY = (1, 0.5), (0, 0.5), 0.5
GLANCE_S, GLANCE_WAY = 10, 0.3
# Came back to it another time (BACK_S or more after first opening it), or answered it in the chat: more
# than a look, however long the stay.
BACK, TALK = (1, 2.0), (1, 1.5)
BACK_S = 3600
# A no the person put in words in the chat: firmer than any skip, short of hiding the source.
SAID_NO = (0, 2.5)
PLAY_MIN_S = 60
# A quiet no from the pet's card or the main chat, and from a list the person scrolled past.
SHOWN_CARD, SHOWN_LIST = 0.3, 0.1
# "first" is the first run's round of sample finds.
SURFACES = ("card", "list", "chat", "first", "browser")
# How a card ended that says nothing: it went while the person was away, or with the pet (a game in front),
# or they had seen the thing already ("known").
UNSEEN_ENDS = ("away", "hushed", "known")
PROMINENT = ("card", "chat")
# Videos under 4 minutes are short, over 20 long; text by its characters.
VIDEO_SHORT, VIDEO_LONG = 240, 1200
TEXT_SHORT, TEXT_LONG = 600, 6000
# Length also as one number, so short videos are told apart among themselves: log seconds of a video and
# log characters of a text the library holds, each 0 at its MID and 1 at SPAN times that, within +-LOG_MAX.
SECS_MID, CHARS_MID, SPAN, LOG_MAX = 180, 1500, 4, 2.5

# (m0, q0): q0 is roughly a quarter of the reactions' worth of evidence the prior counts for.
PRIORS = {"bias": (0.0, 1.0), "unread_lang": (-2.0, 2.0), "fresh": (1.5, 2.0), "judged": (0.0, 2.0),
          "secs": (0.0, 1.0), "chars": (0.0, 1.0),
          "char_fits": (1.0, 5.0), "char_love": (1.0, 5.0), "again_day": (-0.5, 1.0), "again_week": (-0.5, 1.0)}
# again_day and again_week: how many things on a subject of this one the card or the chat brought in the last
# day and the last week, counted up to AGAIN_DAY and AGAIN_WEEK. Whether more of the same tires this person
# is theirs to show: the prior only leans the way the "not two in a row" rule already does.
AGAIN_DAY, AGAIN_WEEK = 3, 6
# avoid:<t> is a topic the owner said no to in the profile, like:<t> one they said they like; their priors
# come from the profile, and fall back to these once the person takes the words back.
# subj:<key> is what a thing is about (subjects.py), learned from nothing.
# lang:<code> is the language a thing is in, learned from nothing too: the computer's list of languages is
# only a first guess (unread_lang), and without this a person who closes every English text teaches the
# model that they dislike text.
FAMILIES = {"origin": (0.0, 1.0), "kind": (0.0, 1.0), "len": (0.0, 1.0), "topic": (1.5, 3.0), "tag": (0.0, 1.0),
            "avoid": (0.0, 1.0), "like": (0.0, 1.0), "subj": (0.0, 1.0), "lang": (0.0, 1.0)}
# A source the person asked to see less of: the same form a person's own override takes.
HIDDEN = (-4.0, 100.0)
# The person's own lean on a feature in Settings (profile "leans"): more, less, or not counted at all (held at 0).
LEANS = {1: (3.0, 3.0), -1: (-1.5, 3.0), 0: (0.0, 100.0)}

# The character's pick: in each WINDOW placed things, k_c go to its best loved unseen ones, by PICK_BY.
WINDOW, PICK_BY, PICK_LOVE = 20, 10, 2
# A reason is named only when its term is at least this, and "something new" when the draw lifted it this much.
REASON_MIN, EXPLORE_MIN = 0.2, 1.0
SERVED_MAX = 5000

_SCRIPTS = (("ja", re.compile(r"[぀-ヿ]")), ("ko", re.compile(r"[가-힯ᄀ-ᇿ]")),
            ("zh", re.compile(r"[一-鿿]")), ("ru", re.compile(r"[Ѐ-ӿ]")))


def _feed():
    # The feed imports this module; this one reads the feed's events and cards.
    from .panels import feed
    return feed


def _base(lang: str) -> str:
    return str(lang).split("-")[0].lower()


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))


# ------------------------------------------------------------------ features --

def language(text: str, source_lang: str) -> str:
    """The language a thing is written in, from its script, its source's language telling Latin ones apart."""
    for code, script in _SCRIPTS:
        if script.search(text):
            # Han alone is Chinese or Japanese: the source says which, and which Chinese.
            return source_lang if code == "zh" and _base(source_lang) in ("zh", "ja") else code
    return source_lang if source_lang and _base(source_lang) not in ("zh", "ja", "ko", "ru") else "en"


def _length(item: Item, shaped: dict) -> str:
    if shaped["kind"] == "video":
        n = item.length
        return "" if not n else "short" if n < VIDEO_SHORT else "mid" if n <= VIDEO_LONG else "long"
    n = len(item.text) or len(shaped["text"])
    return "short" if n < TEXT_SHORT else "mid" if n < TEXT_LONG else "long"


def _size(item: Item, shaped: dict) -> dict:
    """A video's seconds or a held text's characters, where known: {"secs": n} or {"chars": n}."""
    if shaped["kind"] == "video":
        return {"secs": item.length} if item.length else {}
    return {"chars": len(item.text)} if item.text else {}


def _log(n: float, mid: float) -> float:
    return max(-LOG_MAX, min(LOG_MAX, math.log(n / mid) / math.log(SPAN)))


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def hosts(item: Item, shaped: dict) -> set[str]:
    """The hosts a thing needs: its page's and its player's (`situation.gate` takes each as its platform,
    so www.youtube-nocookie.com counts as youtube.com)."""
    return {_host(item.url), _host(shaped["player"]) if shaped["player"] else ""} - {""}


def readable(runtime: Path) -> set[str]:
    return {_base(lang) for lang in situation.readable(runtime)}


class Reader:
    """What a snapshot needs from the runtime, read once: the picked topics, the topics the owner said no
    to and those they said they like, and the sources' languages and what each says it brings."""

    def __init__(self, runtime: Path, topics: Optional[list[str]] = None, events: Optional[list] = None):
        from . import profile
        from .sources import catalog
        feed = _feed()
        self.runtime = Path(runtime)
        topics = feed.load_topics(self.runtime / feed.TASTE_FILE) if topics is None else topics
        self.topics, self.avoid, self.likes = ([t.casefold() for t in words] for words in (
            topics, profile.avoided(self.runtime), profile.liked(self.runtime)))
        # Every source this home knows, by key: the 推荐记录 list names a find's source by it (recommend.Why).
        self.sources = sources = catalog.all_sources(catalog.load_choice(self.runtime)["own"])
        self.langs = {k: s.lang for k, s in sources.items()}
        self.brings = {k: subjects.of("", " ".join((*s.name, *s.about))) for k, s in sources.items()}
        self.brought = self.lately(feed._load(self.runtime / feed.TASTE_FILE) if events is None else events)

    @staticmethod
    def lately(events: list) -> list[tuple[float, str, set]]:
        """(when, id, subjects) of each thing the card or the chat brought, from the events' own snapshots."""
        return [(e[0], e[1], set(e[5]["f"].get("subj", ()))) for e in events
                if e[2] == "seen" and len(e) > 5 and e[5].get("s") in PROMINENT and e[5].get("f")]

    def snapshot(self, item: Item, shaped: dict, now: float) -> dict:
        """The facts the model reads, as they are at `now`; kept in the event as they are."""
        text = " ".join((shaped["title"], shaped["text"], shaped["site"])).casefold()
        words = shaped["title"], " ".join((shaped["text"], shaped["site"], *map(str, item.tags)))
        # Its own words say what it is about; when they say nothing, what its source says it brings is a
        # guess (a recipe's title seldom says "food"), good enough to rank by and not to tell the person.
        own = subjects.of(*words)
        subj = list(own or self.brings.get(item.source, ()))
        on = set(subj)

        def named(rows: list, on: set = on) -> list:
            # Each word as one interest: a phrase naming two subjects needs both, or the phrase itself.
            return [name for name in rows if subjects.holds(name, on, text)]

        same = [at for at, ident, about in self.brought
                if now - 7 * DAY <= at < now and ident != item.id and about & on]
        return {"origin": "src:" + item.source if item.source else "site:" + shaped["site"],
                "kind": shaped["kind"], "len": _length(item, shaped), **_size(item, shaped),
                "lang": language(shaped["title"] + " " + shaped["text"][:200], self.langs.get(item.source, "")),
                "fresh": round(0.5 ** (max(0.0, now - item.seen_at) / DAY), 4),
                "judged": bool(item.judged or item.love >= 0), "fits": bool(item.fits), "love": item.love,
                "subj": subj, "guess": bool(subj) and not own,
                "again": [sum(1 for at in same if at >= now - DAY), len(same)],
                # A no is kept wide: a subject the thing so much as names counts.
                "topics": named(self.topics), "avoid": named(self.avoid, on | set(subjects.mentioned(*words))),
                "likes": named(self.likes),
                "tags": [str(t) for t in item.tags][:8], "host": _host(item.url), "site": shaped["site"]}


def vector(snap: dict, read: set[str], families: tuple = ()) -> dict[str, float]:
    """A snapshot as sparse features. `families` adds the ones still waiting on the replay ("tags")."""
    x = {"bias": 1.0, "origin:" + snap["origin"]: 1.0, "kind:" + snap["kind"]: 1.0, "fresh": snap["fresh"]}
    if snap["len"]:
        x["len:" + snap["len"]] = 1.0
    if snap.get("secs"):
        x["secs"] = _log(snap["secs"], SECS_MID)
    if snap.get("chars"):
        x["chars"] = _log(snap["chars"], CHARS_MID)
    x["lang:" + _base(snap["lang"])] = 1.0
    if _base(snap["lang"]) not in read:
        x["unread_lang"] = 1.0
    if snap["judged"]:
        x["judged"] = 1.0
    if snap["fits"]:
        x["char_fits"] = 1.0
    if snap["love"] > 0:
        x["char_love"] = snap["love"] / 3
    for key in snap.get("subj", ()):
        x["subj:" + key] = 1.0
    day, week = snap.get("again", (0, 0))
    if day:
        x["again_day"] = min(day, AGAIN_DAY) / AGAIN_DAY
    if week:
        x["again_week"] = min(week, AGAIN_WEEK) / AGAIN_WEEK
    for topic in snap["topics"]:
        x["topic:" + topic] = 1.0
    for topic in snap.get("avoid", ()):
        x["avoid:" + topic] = 1.0
    for topic in snap.get("likes", ()):
        x["like:" + topic] = 1.0
    if "tags" in families:
        for tag in snap["tags"]:
            x["tag:" + tag] = 1.0
    return x


def features(item: Item, shaped: dict, runtime: Path, now: float) -> dict[str, float]:
    return vector(Reader(runtime).snapshot(item, shaped, now), readable(runtime))


class Priors:
    """(m0, q0) per feature: the defaults, a hidden source pushed down, the owner's own "not this"
    rules from the profile, the topics they said they like and the length and kind they said they want,
    then the situation's. The replay passes the sources hidden by then and leaves the profile out, since
    it cannot tell when each was said."""

    def __init__(self, runtime: Path, hidden: Optional[set[str]] = None, rules: bool = True):
        from . import profile
        self.table = dict(PRIORS)
        if hidden is None:
            hidden = {key for key, row in weights.load(runtime).items() if row["hides"]}
        if rules:
            hidden = set(hidden) | profile.hidden_sources(runtime)
            self.table.update({key: LEANS[int(to)] for key, to in profile.load(runtime, False)["leans"].items()})
            for topic in profile.avoided(runtime):
                self.table["avoid:" + topic.casefold()] = self.table["topic:" + topic.casefold()] = profile.AVOID_TOPIC
            for topic in profile.liked(runtime):
                self.table["like:" + topic.casefold()] = profile.LIKE_TOPIC
            form = profile.load(runtime, False)["form"]
            against = {True: ["len:long"], False: ["len:short"]}.get(form.get("short"), []) + {
                "video": ["kind:text", "kind:page"], "text": ["kind:video"]}.get(form.get("kind"), [])
            self.table.update({name: profile.FORM_AGAINST for name in against})
            if form.get("short") is not None:
                # The same wish on the lengths as numbers: against longer, or against shorter.
                lean = profile.FORM_AGAINST[0] / 2 * (1 if form["short"] else -1)
                self.table.update({name: (lean, profile.FORM_AGAINST[1]) for name in ("secs", "chars")})
        self.table.update({"origin:src:" + key: HIDDEN for key in hidden})
        self.table.update(situation.priors(runtime))

    def __call__(self, name: str) -> tuple[float, float]:
        return self.table.get(name) or FAMILIES.get(name.split(":", 1)[0], (0.0, 1.0))


# -------------------------------------------------------------------- labels --

def history(events: list, until: float = math.inf) -> dict[str, dict]:
    """Per thing, what had happened to it by `until`: its first snapshot, slot and surface, and the reactions."""
    found: dict[str, dict] = {}
    for at, ident, action, site, kind, *extra in events:
        if at > until:
            continue
        extra = extra[0] if extra and isinstance(extra[0], dict) else {}
        h = found.get(ident)
        if h is None:
            h = found[ident] = {"f": extra.get("f"), "at": at, "s": extra.get("s", ""), "p": extra.get("p", ""),
                                "r": extra.get("r"), "site": site, "kind": kind, "open": False, "like": False,
                                "skip": False, "hide": False, "play": 0.0, "acted": None, "end": "",
                                "ended": None, "said": False, "opened": None, "back": False, "talk": False,
                                "walled": False}
        h["f"] = h["f"] or extra.get("f")
        if action == "seen":
            h["s"] = h["s"] or extra.get("s", "")
            continue
        if action == "end":
            # How a card went is not a reaction of its own: it says how to read the others, or their absence.
            h["end"], h["ended"] = extra.get("end") or "", at
            continue
        if action == "walled":
            # It opened to a sign-in or a check (feed.walled): no lesson either way.
            h["walled"] = True
            continue
        h["acted"] = at
        if action == "open":
            h["open"] = True
            h["back"] = h["back"] or h["opened"] is not None and at - h["opened"] >= BACK_S
            h["opened"] = at if h["opened"] is None else h["opened"]
        elif action == "talk":
            h["talk"] = True
        elif action == "like":
            h["like"], h["skip"] = True, False
        elif action == "unlike":
            h["like"] = False
        elif action == "skip":
            h["skip"], h["said"] = True, bool(extra.get("said"))
        elif action == "hide_source":
            h["like"], h["skip"], h["hide"] = False, True, True
        elif action in ("play", "read"):
            # Reading an article in the app's browser counts as playing a video does.
            h["play"] = max(h["play"], float(extra.get(action + "_s") or 0))
    return found


def _played(h: dict) -> bool:
    """Whether a thing was played or read for real: PLAY_SHARE of its own length, or PLAY_MIN_S seconds."""
    snap = h["f"] or {}
    full = snap.get("secs") or snap.get("chars", 0) / READ_CHARS_S
    return h["play"] >= PLAY_MIN_S or bool(full) and h["play"] >= PLAY_SHARE * full


def _stay(h: dict) -> tuple[int, float]:
    """What a stay short of a real play says: a glance is a faint no, a longer one a yes that grows with
    how far toward a real play they got."""
    snap = h["f"] or {}
    full = snap.get("secs") or snap.get("chars", 0) / READ_CHARS_S
    way = h["play"] / (min(PLAY_MIN_S, PLAY_SHARE * full) if full else PLAY_MIN_S)
    if h["play"] < GLANCE_S and way < GLANCE_WAY:
        return GLANCE
    return 1, STAY + (PLAY[1] - STAY) * way


def reacted(h: dict) -> Optional[int]:
    """1 for a thing liked, stayed with, come back to or answered, or opened where the stay is not seen;
    0 for one skipped, hidden or only glanced at; None when nothing was done."""
    if h["hide"] or h["like"] or h["skip"]:
        return int(h["like"])
    if _played(h) or h["back"] or h["talk"]:
        return 1
    if h["play"] > 0:
        return _stay(h)[0]
    return 1 if h["open"] else None


def label(h: dict, until: float) -> Optional[tuple[int, float, float]]:
    """(y, confidence, when) from a thing's final mark by `until`, or None while it says nothing yet."""
    if h["walled"]:
        return None
    if h["hide"]:
        y, c = HIDE
    elif h["like"]:
        y, c = LIKE
    elif h["skip"]:
        y, c = SAID_NO if h["said"] else QUICK if h["end"] == "quick" else SKIP
    elif _played(h):
        y, c = PLAY
    elif h["back"]:
        y, c = BACK
    elif h["talk"]:
        y, c = TALK
    elif h["play"] > 0:
        y, c = _stay(h)
    elif h["open"]:
        y, c = OPEN
    elif h["end"] in UNSEEN_ENDS:
        return None
    elif h["end"] in ("timeout", "next"):
        # Out its whole time with the person there and left alone, or passed over for another: the quiet no, at once.
        return 0, SHOWN_CARD, h["ended"]
    elif until - h["at"] >= QUIET_S:
        return 0, SHOWN_CARD if h["s"] in PROMINENT else SHOWN_LIST, h["at"] + QUIET_S
    else:
        return None
    return y, c, h["acted"]


def rows(events: list, until: float, read: set[str], rebuild: Callable[[str, float], Optional[dict]],
         families: tuple = ()) -> tuple[list, int]:
    """One training row (x, y, c, id) per thing with a label by `until`, and how many could not be rebuilt:
    an old event without a snapshot whose thing the library no longer has."""
    found, lost = [], 0
    for ident, h in history(events, until).items():
        got = label(h, until)
        if got is None:
            continue
        snap = h["f"] or rebuild(ident, h["at"])
        if snap is None:
            lost += 1
            continue
        if "subj" not in snap:
            # An event from before subjects were read: the thing's, as the library has it now.
            snap = dict(snap, subj=(rebuild(ident, h["at"]) or {}).get("subj", []))
        y, c, at = got
        found.append((vector(snap, read, families), y, c * 0.5 ** (max(0.0, until - at) / DAY / HALF_DAYS), ident))
    return found, lost


# ----------------------------------------------------------------------- fit --

def solve(data: list, prior: Callable[[str], tuple[float, float]], start: Optional[dict] = None
          ) -> tuple[dict[str, float], dict[str, float]]:
    """Cyclic coordinate Newton on ½Σq0(w-m0)² + Σc[log(1+e^z) - yz]: posterior means and precisions."""
    cols: dict[str, list] = {}
    for j, row in enumerate(data):
        for k, v in row[0].items():
            cols.setdefault(k, []).append((j, v))
    start = start or {}
    w = {k: start.get(k, prior(k)[0]) for k in cols}
    z = [sum(w[k] * v for k, v in row[0].items()) for row in data]
    ys, cs = [row[1] for row in data], [row[2] for row in data]

    def newton(k: str) -> tuple[float, float]:
        m0, q0 = prior(k)
        g, h = q0 * (w[k] - m0), q0
        for j, v in cols[k]:
            p = _sigmoid(z[j])
            g += cs[j] * (p - ys[j]) * v
            h += cs[j] * p * (1 - p) * v * v
        return g, h

    for _ in range(SWEEPS):
        moved = 0.0
        for k in sorted(cols):
            g, h = newton(k)
            step = max(-STEP_MAX, min(STEP_MAX, -g / h))
            w[k] += step
            for j, v in cols[k]:
                z[j] += step * v
            moved = max(moved, abs(step))
        if moved < TOLERANCE:
            break
    return w, {k: newton(k)[1] for k in cols}


def loss(data: list, w: dict, prior: Callable) -> float:
    """The objective `solve` lowers, for checking that it does."""
    total = sum(0.5 * prior(k)[1] * (v - prior(k)[0]) ** 2 for k, v in w.items())
    for x, y, c, *_ in data:
        z = sum(w.get(k, prior(k)[0]) * v for k, v in x.items())
        total += c * (max(z, 0) + math.log1p(math.exp(-abs(z))) - y * z)
    return total


class _Rebuild:
    """An old event's snapshot rebuilt from the thing as the library has it now."""

    def __init__(self, runtime: Path, reader: Reader):
        self.items = {item.id: item for item in Library(runtime).items()}
        self.reader = reader

    def __call__(self, ident: str, at: float) -> Optional[dict]:
        item = self.items.get(ident)
        shaped = _feed().shape(item, {}) if item else None
        return self.reader.snapshot(item, shaped, at) if shaped else None


def _model_path(runtime: Path) -> Path:
    return Path(runtime) / MODEL_FILE


_models: dict[Path, tuple[int, dict]] = {}
# The last few fits this process has read, so a cursor's later pages rank with the fit its first page had.
_kept: dict[Path, list[dict]] = {}
KEPT_FITS = 8


def load(runtime: Path) -> dict:
    """The last fit: {at, rows, lost, m, q}; an empty one before the first."""
    path = _model_path(runtime)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        empty = {"at": 0.0, "rows": 0, "lost": 0, "m": {}, "q": {}}
        _kept.setdefault(path, [empty])
        return empty
    hit = _models.get(path)
    if hit is None or hit[0] != stamp:
        hit = _models[path] = (stamp, json.loads(path.read_text(encoding="utf-8")))
        _kept[path] = (_kept.get(path, []) + [hit[1]])[-KEPT_FITS:]
    return hit[1]


def load_at(runtime: Path, at: float) -> dict:
    """The newest fit made by `at` that this process still has, else the last one."""
    last = load(runtime)
    made = [m for m in _kept.get(_model_path(runtime), []) if m["at"] <= at]
    return max(made, key=lambda m: m["at"]) if made else last


def train(runtime: Path, now: Optional[float] = None) -> dict:
    """Fit on every reaction so far and write the model; what was written."""
    runtime = Path(runtime)
    now = life.clock() if now is None else now
    from . import profile
    feed = _feed()
    events = feed._load(runtime / feed.TASTE_FILE)
    # Reactions from before the person started their taste over teach nothing.
    since = profile.load(runtime, False)["reset_at"]
    data, lost = rows([e for e in events if e[0] >= since], now, readable(runtime),
                      _Rebuild(runtime, Reader(runtime, events=events)))
    m, q = solve(data, Priors(runtime), load(runtime)["m"])
    model = {"at": now, "rows": len(data), "lost": lost, "m": {k: round(v, 5) for k, v in m.items()},
             "q": {k: round(v, 5) for k, v in q.items()}}
    atomic_write_text(_model_path(runtime), json.dumps(model, ensure_ascii=False), fsync=False)
    return model


_fitting = threading.Lock()
_pending: dict[str, threading.Timer] = {}
_last: dict[str, float] = {}


def fit(runtime: Path, soon: bool = False) -> None:
    """Refit soon in a worker thread, at most once every REFIT_S, or at once when `soon` (the person said
    something in the chat that changes the priors); never inside the feed's lock."""
    key = str(Path(runtime))
    with _fitting:
        if key in _pending:
            if not soon or _pending[key].interval == 0:
                return
            _pending.pop(key).cancel()
        wait = 0.0 if soon else max(0.0, _last.get(key, -REFIT_S) + REFIT_S - time.monotonic())
        timer = _pending[key] = threading.Timer(wait, _refit, (Path(runtime), key))
        timer.daemon = True
        timer.start()


def _refit(runtime: Path, key: str) -> None:
    with _fitting:
        # A timer `soon` replaced may already have been on its way here.
        _pending.pop(key, None)
        _last[key] = time.monotonic()
    try:
        train(runtime)
    except Exception:
        logger.exception("taste: the fit failed; the last model stays")


def reset(runtime: Path) -> None:
    """Forget the last fit (profile.reset set where the reactions now start) and fit again at once."""
    path = _model_path(runtime)
    path.unlink(missing_ok=True)
    _models.pop(path, None)
    _kept.pop(path, None)
    fit(runtime, soon=True)


def due(runtime: Path, events: list, now: float) -> bool:
    """A thing shown before has since become a quiet no the last fit did not count."""
    at = load(runtime)["at"]
    return any(e[2] == "seen" and at < e[0] + QUIET_S <= now for e in events)


def enabled(runtime: Path) -> bool:
    """The model ranks unless the person's FEED_MODEL switch (the situation row "feed_model") is off;
    then the old point sum does."""
    return situation.value(runtime, "feed_model") is not False


# --------------------------------------------------------------------- score --

class Sampler:
    """One Thompson draw of the weights, the same for every thing scored with one seed."""

    def __init__(self, model: dict, prior: Callable, seed: int):
        self.m, self.q, self.prior, self.seed = model["m"], model["q"], prior, seed
        self.drawn: dict[str, float] = {}

    def mean(self, k: str) -> float:
        return self.m[k] if k in self.m else self.prior(k)[0]

    def theta(self, k: str) -> float:
        if k not in self.drawn:
            q = self.q.get(k) or self.prior(k)[1]
            self.drawn[k] = self.mean(k) + ALPHA * random.Random(f"{self.seed}:{k}").gauss(0, 1) / math.sqrt(q)
        return self.drawn[k]

    def score(self, x: dict) -> tuple[float, float]:
        """The sampled score and the posterior mean's."""
        return sum(self.theta(k) * v for k, v in x.items()), sum(self.mean(k) * v for k, v in x.items())


def score(runtime: Path, feats: dict[str, float], seed: int) -> float:
    return Sampler(load(runtime), Priors(runtime), seed).score(feats)[0]


def pick_share(events: list) -> int:
    """k_c: how many of each WINDOW go to the character's picks, from how its picks did against the model's."""
    n = {"character": [0, 0], "model": [0, 0]}
    for h in history(events).values():
        slot = "character" if h["p"] == "character" else "model" if h["p"] in ("model", "explore") else ""
        if slot:
            n[slot][0] += 1
            n[slot][1] += bool(h["open"] or h["like"] or _played(h))
    r_c, r_m = ((1 + pos) / (2 + count) for count, pos in (n["character"], n["model"]))
    if r_c > 1.2 * r_m:
        return 2
    return 0 if n["character"][0] >= 10 and r_c < 0.5 * r_m else 1


def _topic_name(topic: str, lang: str) -> str:
    names = _feed().SUGGESTED.get(topic, ((topic, topic), ()))[0]
    return names[1] if _base(lang) == "zh" else names[0]


# What the ranking last served of each thing, for the events and shown.jsonl to say where it stood.
served: dict[str, dict] = {}
_serving = threading.Lock()


class Ranker:
    """Scores, reasons and the character's slot for one ranking at `at`."""

    def __init__(self, runtime: Path, topics: list[str], at: float, seed: int,
                 hidden: Optional[set[str]] = None, events: Optional[list] = None):
        self.reader = Reader(runtime, list(topics), events)
        self.read = readable(runtime)
        self.prior = Priors(runtime, hidden)
        self.sampler = Sampler(load_at(runtime, at), self.prior, seed)
        self.at = at
        self.info: dict[str, dict] = {}

    def score(self, item: Item, shaped: dict) -> float:
        snap = self.reader.snapshot(item, shaped, self.at)
        x = vector(snap, self.read)
        s, mean = self.sampler.score(x)
        # Each term as the posterior mean has it and as the draw that placed the thing had it.
        self.info[item.id] = {"s": s, "mean": mean, "terms": {k: self.sampler.mean(k) * v for k, v in x.items()},
                              "drawn": {k: self.sampler.theta(k) * v for k, v in x.items()},
                              "away": bool(snap["avoid"]), "guess": snap["guess"]}
        return s

    def reason(self, ident: str) -> str:
        """The largest term the score used besides the character's own: a site or a topic the person's
        reactions or picks lifted, in the posterior and in this draw alike, kept in info["term"] for the chip
        under a card (recommend.Why); else "explore" when the draw itself lifted the thing, else ""."""
        info = self.info[ident]
        best, key = REASON_MIN, ""
        # A subject only guessed from the source is not named to the person.
        named = ("origin:",) if info["guess"] else ("origin:", "topic:", "like:", "subj:")
        for k, term in info["terms"].items():
            if k.startswith(named) and term > best and info["drawn"][k] > REASON_MIN:
                best, key = term, k
        info["term"] = key
        return key or ("explore" if info["s"] - info["mean"] > EXPLORE_MIN else "")

    def picks(self, rows: list, k: int) -> tuple[list, set[str]]:
        """`rows` (seen first-flag, ..., item last) with k of each WINDOW given to the best loved unseen
        things not placed yet, moved up to PICK_BY in their window when they stood lower."""
        unseen = sum(1 for row in rows if not row[0])
        candidates = sorted((row for row in rows if not row[0] and row[-1].love >= PICK_LOVE),
                            key=lambda row: -row[-1].love)
        rows, chosen = list(rows), set()
        targets = [PICK_BY - 1] if k == 1 else [PICK_BY // 2 - 1, PICK_BY - 1][:k]
        for start in range(0, unseen, WINDOW):
            for target in targets:
                place = {id(row): n for n, row in enumerate(rows)}
                row = next((r for r in candidates if r[-1].id not in chosen and place[id(r)] >= start), None)
                if row is None:
                    return rows, chosen
                chosen.add(row[-1].id)
                target = min(start + target, unseen - 1)
                if place[id(row)] > target:
                    rows.remove(row)
                    rows.insert(target, row)
        return rows, chosen

    def finish(self, shaped: dict, rank: int, pick: bool) -> None:
        """Mark a served thing with its slot, and remember where it stood."""
        # Read for every thing: the card's chip takes its term (feed.ranked).
        reason = self.reason(shaped["id"])
        slot = "character" if pick else "explore" if reason == "explore" else "model"
        shaped["slot"] = slot
        info = self.info[shaped["id"]]
        top = sorted(info["terms"].items(), key=lambda kv: -kv[1])[:3]
        with _serving:
            served.pop(shaped["id"], None)
            served[shaped["id"]] = {"r": rank, "p": slot, "s": round(info["s"], 4),
                                    "lift": round(info["s"] - info["mean"], 4),
                                    "top": [[k, round(v, 3)] for k, v in top], "t": info["term"]}
            while len(served) > SERVED_MAX:
                served.pop(next(iter(served)))


def shown(runtime: Path, item: Item, surface: str, rank: Optional[int], now: float, host: str,
          slot: str = "") -> None:
    """One line in shown.jsonl for a thing the person was shown; the file keeps the last SHOWN_MAX."""
    path = Path(runtime) / SHOWN_FILE
    there = served.get(item.id, {})
    append_jsonl(path, {"at": now, "id": item.id, "surface": surface, "rank": there.get("r") if rank is None else rank,
                        # Where it stood among the candidates the model chose from (0: the ranking's first).
                        "six": there.get("six"),
                        "s": there.get("s"), "lift": there.get("lift"), "top": there.get("top", []),
                        "slot": slot or there.get("p", ""), "host": host,
                        "gated": bool(host) and not situation.gate(runtime, host=host)})
    # About 300 bytes a line: trimmed back once it is well past the cap.
    if path.stat().st_size > SHOWN_MAX * 450:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        atomic_write_text(path, "".join(lines[-SHOWN_MAX:]), fsync=False)


# ------------------------------------------------------------------- learned --

# A weight is told once it is this large and this many of its own deviations from zero.
LEARNED_MIN, LEARNED_SURE, LEARNED_MOST = 0.5, 1.0, 6


def taught(runtime: Path, lang: str = "en") -> dict:
    """What the person's own reactions have taught the model, strongest first: {"takes": [...], "passes": [...]},
    each {"key", "name", "weight"}. Only subjects, sources and forms, and only weights the fit is sure of the
    sign of; a form's name is its kind ("video", "text", "page") for the page to put in words. What they said
    is in the profile; what a subject taught is forgotten with feed.forget_said, or held at 0 by a lean.
    Weights that always come together are told together: a subject with the picked or said topics whose word
    stands for it (one they picked and open is not news, so it is left out), a form against the other forms."""
    from .sources import catalog
    model = load(runtime)
    sources = catalog.all_sources(catalog.load_choice(Path(runtime))["own"])
    zh = _base(lang) == "zh"
    told: dict[str, float] = {}
    for key, mean in model["m"].items():
        family, _, rest = key.partition(":")
        if family in ("topic", "like"):
            for subject in subjects.named(rest):
                told[subject] = told.get(subject, 0.0) + mean
    forms = [mean for key, mean in model["m"].items() if key.startswith("kind:")]
    found = []
    for key, mean in model["m"].items():
        family, _, rest = key.partition(":")
        if family == "subj" and rest in subjects.SUBJECTS:
            name = subjects.name(rest, lang)
            if rest in told:
                mean += told[rest]
                name = "" if mean > 0 else name
        elif family == "kind":
            name, mean = rest, mean - sum(forms) / len(forms)
        elif family == "origin":
            where, _, name = rest.partition(":")
            name = sources[name].name[zh] if where == "src" and name in sources else name
        else:
            name = ""
        if name and abs(mean) >= LEARNED_MIN and abs(mean) * math.sqrt(model["q"].get(key, 1.0)) >= LEARNED_SURE:
            found.append({"key": key, "name": name, "weight": round(mean, 2)})
    found.sort(key=lambda row: -abs(row["weight"]))
    return {"takes": [row for row in found if row["weight"] > 0][:LEARNED_MOST],
            "passes": [row for row in found if row["weight"] < 0][:LEARNED_MOST]}


# ------------------------------------------------------------------- numbers --

def numbers(runtime: Path, now: Optional[float] = None, days: int = 7) -> dict:
    """The week in numbers, for settings: what each surface's showings led to, play time, and the
    character's picks against the model's."""
    runtime = Path(runtime)
    now = life.clock() if now is None else now
    feed = _feed()
    since = now - days * DAY
    events = feed._load(runtime / feed.TASTE_FILE)
    recent = [e for e in events if e[0] >= since]
    surfaces: dict[str, dict] = {}
    for h in history(recent).values():
        row = surfaces.setdefault(h["s"] or "other", {"shown": 0, "open": 0, "like": 0, "skip_hide": 0})
        row["shown"] += 1
        row["open"] += h["open"] or _played(h)
        row["like"] += h["like"]
        row["skip_hide"] += h["skip"]
    for row in surfaces.values():
        for k in ("open", "like", "skip_hide"):
            row[k + "_rate"] = round(row[k] / row["shown"], 3)
    played = sum(float(e[5].get(e[2] + "_s") or 0) for e in recent if e[2] in ("play", "read") and len(e) > 5)
    picks = {"character": [0, 0], "model": [0, 0]}
    for h in history(recent).values():
        slot = "character" if h["p"] == "character" else "model" if h["p"] in ("model", "explore") else ""
        if slot:
            picks[slot][0] += 1
            picks[slot][1] += h["like"]
    model = load(runtime)
    try:
        shown_lines = (runtime / SHOWN_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        shown_lines = []
    gated_hosts = 0
    for line in shown_lines[-SHOWN_MAX:]:
        row = json.loads(line)
        # Whether its host was down when it was shown, not now.
        gated_hosts += row["at"] >= since and bool(row.get("gated"))
    return {"days": days, "surfaces": surfaces, "play_s": round(played),
            "picks": {slot: {"shown": n, "liked": pos, "like_rate": round(pos / n, 3) if n else None}
                      for slot, (n, pos) in picks.items()},
            "k_c": pick_share(events),
            "model": {"at": model["at"], "rows": model["rows"]},
            "shown_on_gated_hosts": gated_hosts, "on": enabled(runtime)}


# -------------------------------------------------------------------- replay --

def auc(pairs: list[tuple[float, int]]) -> Optional[float]:
    """The chance a random positive outscores a random negative, ties counted half."""
    pos = sum(y for _, y in pairs)
    neg = len(pairs) - pos
    if not pos or not neg:
        return None
    ordered = sorted(pairs, key=lambda p: p[0])
    rank_sum, n = 0.0, 0
    while n < len(ordered):
        end = n
        while end < len(ordered) and ordered[end][0] == ordered[n][0]:
            end += 1
        rank_sum += (n + end + 1) / 2 * sum(y for _, y in ordered[n:end])
        n = end
    return (rank_sum - pos * (pos + 1) / 2) / (pos * neg)


def _logloss(p: float, y: int) -> float:
    p = min(1 - 1e-6, max(1e-6, p))
    return -math.log(p if y else 1 - p)


def _metrics(days: list[list[dict]], name: str) -> dict:
    flat = [row for day in days for row in day]
    top = [row["y"] for day in days for row in sorted(day, key=lambda r: -r[name + "_s"])[:5]]
    return {"auc": auc([(row[name + "_s"], row["y"]) for row in flat]),
            "logloss": sum(_logloss(row[name + "_p"], row["y"]) for row in flat) / len(flat) if flat else None,
            "top5": sum(top) / len(top) if top else None}


def _ci(values: list[float]) -> list[Optional[float]]:
    values = sorted(v for v in values if v is not None)
    if not values:
        return [None, None]
    return [round(values[int(0.025 * (len(values) - 1))], 4), round(values[int(0.975 * (len(values) - 1))], 4)]


def _day_start(at: float) -> float:
    return datetime.combine(datetime.fromtimestamp(at).date(), datetime.min.time()).timestamp()


def replay(runtime: Path, resamples: int = 1000, seed: int = 0, families: tuple = ("tags",)) -> dict:
    """Prequential replay over the person's own reactions in time order: each day is predicted by models fit
    only on what was known when it began: the old point sum against this model's posterior mean, and this
    model with each family still waiting on the replay. AUC (opened, played or liked against skipped or
    hidden), log loss (each score calibrated on the days before) and the top-5 positive rate, each with a
    day-block bootstrap 95% CI."""
    runtime = Path(runtime)
    feed = _feed()
    events = sorted(feed._load(runtime / feed.TASTE_FILE), key=lambda e: e[0])
    reader = Reader(runtime)
    rebuild = _Rebuild(runtime, reader)
    read = readable(runtime)
    by_source = {}
    for ident, h in history(events).items():
        snap = h["f"] or rebuild(ident, h["at"])
        if snap and snap["origin"].startswith("src:"):
            by_source[ident] = snap["origin"][4:]
    starts = sorted({_day_start(e[0]) for e in events})
    days: list[list[dict]] = []
    calibration: dict[str, list] = {}
    warm: dict[str, dict] = {"model": {}, "tags": {}}
    lost_ids: set[str] = set()
    for start in starts:
        end = start + DAY
        test = []
        for ident, h in history(events, end).items():
            y = reacted(h)
            if y is None or not start <= h["acted"] < end:
                continue
            snap = h["f"] or rebuild(ident, h["at"])
            if snap is None:
                lost_ids.add(ident)
                continue
            test.append((ident, h, snap, y))
        if not test:
            continue
        # What the person had hidden when the day began; the profile's rules are left out (see Priors).
        prior = Priors(runtime, {by_source[e[1]] for e in events
                                 if e[2] == "hide_source" and e[0] < start and e[1] in by_source}, rules=False)
        fits = {}
        for name, fam in (("model", ()), ("tags", ("tags",))):
            if name != "model" and name not in families:
                continue
            data, _ = rows(events, start, read, rebuild, fam)
            fits[name] = solve(data, prior, warm[name])[0]
            warm[name] = fits[name]
        # The old point sum, as feed.score had it when the day began.
        then = feed._marks(events, start)[0]
        old_taste = feed._taste(then)
        acted = sum(1 for mark in then.values() if mark["open"] or mark["like"] or mark["skip"])
        topical = feed.TOPIC * 0.5 ** (acted / feed.TOPIC_HALF)
        learned = feed._by_source(then, by_source)
        # Each score becomes a probability the same way, by a logistic fit on the days before's reacted
        # things: the old sum has no scale, and the model is fit on quiet noes the test leaves out.
        platt = {name: solve(rows_, lambda k: (0.0, 1.0))[0] if rows_ else {} for name, rows_ in calibration.items()}
        day = []
        for ident, h, snap, y in test:
            days_old = math.log(max(snap["fresh"], 1e-9)) / math.log(0.5)
            row = {"y": y, "old_s": feed.score({"site": snap["site"], "kind": snap["kind"]},
                                               int(h["at"] - days_old * DAY), snap["fits"], old_taste, h["at"],
                                               topical if snap["topics"] else 0,
                                               feed.source_points(learned.get(by_source.get(ident))))}
            for name, w in fits.items():
                x = vector(snap, read, ("tags",) if name == "tags" else ())
                row[name + "_s"] = sum(w.get(k, prior(k)[0]) * v for k, v in x.items())
            for name in ("old", *fits):
                a = platt.get(name, {})
                row[name + "_p"] = _sigmoid(a.get("bias", 0.0) + a.get("s", 0.0) * row[name + "_s"])
                calibration.setdefault(name, []).append(({"bias": 1.0, "s": row[name + "_s"]}, y, 1.0))
            day.append(row)
        days.append(day)
    rng = random.Random(seed)
    names = ["old", "model"] + [f for f in families if f in warm]
    point = {name: _metrics(days, name) for name in names}
    boot = {name: {"auc": [], "logloss": [], "top5": []} for name in names}
    delta = {name: {"auc": [], "logloss": []} for name in names if name != "old"}
    for _ in range(resamples if days else 0):
        sample = [days[rng.randrange(len(days))] for _ in days]
        got = {name: _metrics(sample, name) for name in names}
        for name in names:
            for k in ("auc", "logloss", "top5"):
                boot[name][k].append(got[name][k])
        for name, base in (("model", "old"), *((f, "model") for f in names[2:])):
            if got[name]["auc"] is not None and got[base]["auc"] is not None:
                delta[name]["auc"].append(got[name]["auc"] - got[base]["auc"])
            delta[name]["logloss"].append(got[name]["logloss"] - got[base]["logloss"])
    n = sum(len(day) for day in days)
    report = {"reacted": n, "days": len(days), "lost": len(lost_ids),
              "scores": {name: {k: {"value": None if v is None else round(v, 4), "ci": _ci(boot[name][k])}
                                for k, v in point[name].items()} for name in names},
              "delta": {name: {k: _ci(v) for k, v in d.items()} for name, d in delta.items()}}
    low = report["delta"]["model"]["auc"][0] if n else None
    worse = (point["model"]["logloss"] or 0) > (point["old"]["logloss"] or 0)
    report["ship"] = {"enough": n >= 100, "auc_noninferior": low is not None and low > -0.02,
                      "logloss_no_worse": bool(n) and not worse}
    report["ship"]["passes"] = all(report["ship"].values())
    for name in names[2:]:
        d = report["delta"][name]
        lowers = (point[name]["logloss"] or 0) < (point["model"]["logloss"] or 0)
        report.setdefault("families", {})[name] = (
            "helps" if lowers and d["auc"][1] is not None and d["auc"][1] >= 0 else "no split")
    report.setdefault("families", {}).update({"sim": "not built", "ctx": "not built"})
    return report


def _print(report: dict) -> None:
    print(f"reacted things: {report['reacted']} over {report['days']} days; "
          f"{report['lost']} could not be rebuilt (old events whose things are gone)")
    print(f"{'':8}{'AUC':>24}{'log loss':>26}{'top-5 positive':>26}")
    for name, got in report["scores"].items():
        cells = [f"{got[k]['value']} [{got[k]['ci'][0]}, {got[k]['ci'][1]}]" for k in ("auc", "logloss", "top5")]
        print(f"{name:8}" + "".join(f"{c:>26}" for c in cells))
    for name, d in report["delta"].items():
        print(f"delta {name}: AUC {d['auc']}, log loss {d['logloss']}")
    ship = report["ship"]
    print(f"ship rule: >=100 reacted {ship['enough']}, AUC CI low > -0.02 {ship['auc_noninferior']}, "
          f"log loss no worse {ship['logloss_no_worse']} -> {'passes' if ship['passes'] else 'does not pass'}")
    print("families: " + ", ".join(f"{k} {v}" for k, v in report["families"].items()))


def main(argv: Optional[list[str]] = None) -> None:
    from .paths import runtime_dir
    parser = argparse.ArgumentParser(prog="python -m mibo.taste")
    parser.add_argument("command", choices=["replay"])
    parser.add_argument("--runtime", type=Path, default=None, help="the runtime folder (default: this home's)")
    parser.add_argument("--resamples", type=int, default=1000)
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    report = replay(args.runtime or runtime_dir(), args.resamples)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print(report)


if __name__ == "__main__":
    main()

"""Which public sources a round reads, learned from what the person does with what they bring.

Nobody ticks sources. Each has a weight: a prior from the languages the
person reads, the sources on by default for them and the topics they named,
times exp(the situation's supply term + what was learned of the source),
where what was learned is the taste model's sampled coefficient for it, or
the reactions to what it brought (the runtime database's `source_state`
table, db.py) when the model is off. A source whose reads failed at the transport two rounds in a
row is left out until a retry is due. Most of a round is drawn by weight; a
fifth goes to sources read least, so the feed does not narrow to what it
already knows.

This module imports nothing from the rest of the package but db: the
feed ranks by the same learning and is itself imported by the walks.
"""
from __future__ import annotations

import math
import random
import re
import time
from pathlib import Path
from typing import Callable, Optional

from .. import db

# Sources read in one round besides the person's own feeds, and the share of it kept for exploring.
ROUND = 16
EXPLORE = ROUND // 5
# The prior: 1 for a source in the reader's own language (the one the app speaks with them), READ_TOO in another
# language they read, OTHER_LANG in one they did not say they read. A person who reads Chinese and English in a
# Chinese app then gets mostly Chinese, with some English, as news apps do for a primary and an added language.
READ_TOO = 0.2
OTHER_LANG = 0.05
# Added for a source on by default for the reader, halved every DEFAULT_HALF things reacted to,
# since by then what the person does says more. At first a round is then about 10 of the 12 or 13
# defaults, 3 others drawn by weight and 3 explored.
DEFAULT = 30.0
DEFAULT_HALF = 40
# Added when a source's group, name or line about it names a topic the person chose; with `topical`, this
# many times its pull instead (the share of what it brought that is on an interest the library holds little of).
TOPIC = 3.0
PULL = 40.0
# Points a source earns for each thing of it seen, opened, liked or skipped (a like taken back undoes one).
POINTS = {"seen": -0.25, "open": 1.25, "like": 2.0, "unlike": -2.0, "skip": -2.0}
# The learned factor is 2 ** (GAIN * points / (things reacted to + SMOOTH)), within 2 ** LOW and 2 ** HIGH:
# five things skipped bring a source to about a fifth, five liked to four times.
GAIN, SMOOTH, LOW, HIGH = 2.0, 5, -3.0, 2.0
# Each "less like this source" multiplies its weight by this; each round in a row it failed, by FAILED.
HIDE = 0.05
FAILED, FAILS_MAX = 0.5, 3
FLOOR = 0.01
# Rounds in a row a source could not be reached before it is left out, and how long it is left out.
BLOCKED_AFTER = 2
RETRY_S = 6 * 3600
# What one source's read in a round came to.
OK, BLOCKED, FAILED_READ = "ok", "blocked", "failed"

# ------------------------------------------------------------------ what was learned --

# The columns of a source's row, in the table's order after its key.
FIELDS = ("reads", "fails", "items", "points", "hides", "blocked", "tried")


def _blank() -> dict:
    return {"reads": 0, "fails": 0, "items": 0, "points": 0.0, "hides": 0, "blocked": 0, "tried": 0}


def _state(found: list) -> dict[str, dict]:
    return {key: dict(zip(FIELDS, values)) for key, *values in found}


def load(runtime: Path) -> dict[str, dict]:
    """Per source key: rounds it was read in, rounds in a row it failed, things reacted to, points, hides."""
    return _state(db.at(runtime).read("SELECT * FROM source_state"))


def row(runtime: Path, key: str) -> Optional[dict]:
    """One source's row, or None for a source never read nor reacted to."""
    return _state(db.at(runtime).read("SELECT * FROM source_state WHERE key = ?", (key,))).get(key)


def change(runtime: Path, edit: Callable[[dict[str, dict]], None]) -> None:
    """Read, edit and write the rows as one transaction; only the rows `edit` changed or added are written."""
    with db.at(runtime).write() as conn:
        before = _state(conn.execute("SELECT * FROM source_state").fetchall())
        state = {key: dict(values) for key, values in before.items()}
        edit(state)
        conn.executemany("INSERT OR REPLACE INTO source_state VALUES (?,?,?,?,?,?,?,?)",
                         [(key, *(values[f] for f in FIELDS)) for key, values in state.items()
                          if values != before.get(key)])


def reacted(runtime: Path, key: str, action: str, first: bool) -> None:
    """One reaction to a thing `key` brought; `first` when it is the first for that thing."""
    def edit(state: dict) -> None:
        row = state.setdefault(key, _blank())
        row["items"] += int(first)
        row["points"] = round(row["points"] + POINTS[action], 2)

    change(runtime, edit)


def hidden(runtime: Path, key: str) -> None:
    def edit(state: dict) -> None:
        state.setdefault(key, _blank())["hides"] += 1

    change(runtime, edit)


def read(runtime: Path, answered: dict[str, str | bool], now: Optional[float] = None) -> None:
    """A round read these sources: OK, BLOCKED (the transport could not reach it) or FAILED_READ
    (anything else); True and False stand for OK and FAILED_READ."""
    now = int(time.time() if now is None else now)

    def edit(state: dict) -> None:
        for key, outcome in answered.items():
            outcome = OK if outcome is True else FAILED_READ if outcome is False else outcome
            row = state.setdefault(key, _blank())
            row["reads"] += 1
            row["tried"] = now
            if outcome == OK:
                row["fails"] = row["blocked"] = 0
            elif outcome == BLOCKED:
                row["blocked"] += 1
            else:
                row["fails"] += 1

    change(runtime, edit)


def open_to(row: Optional[dict], now: float, retry_s: float = RETRY_S) -> bool:
    """Whether a source may be read: not blocked, or its retry is due (`retry_s` after its last try)."""
    if not row or row["blocked"] < BLOCKED_AFTER:
        return True
    return now - row["tried"] >= retry_s


def learned(row: Optional[dict]) -> float:
    """The exponent learned from reactions, LOW to HIGH; 0 for a source nobody reacted to."""
    if not row:
        return 0.0
    return max(LOW, min(HIGH, GAIN * row["points"] / (row["items"] + SMOOTH)))


# --------------------------------------------------------------------------- weights --

def _text(source) -> str:
    return " ".join((source.group, *source.name, *source.about)).casefold()


def weigh(sources: list, state: dict[str, dict], reader: str | set[str], matcher: Optional[re.Pattern], *,
          unreadable: set[str] = frozenset(), defaults: Optional[set[str]] = None, supply: Optional[Callable] = None,
          origin: Optional[Callable[[str], float]] = None, retry_s: float = RETRY_S,
          now: Optional[float] = None, topical: Optional[dict[str, float]] = None,
          own: Optional[set[str]] = None) -> list[dict]:
    """Each source's gate, prior, learned exponent and weight, highest weight first.

    w = G * prior * exp(supply(s) + theta) * HIDE^hides * FAILED^min(fails, FAILS_MAX), with
    prior = L * (1 + default * is_default + TOPIC * on_topic), or with `topical` (source key -> pull, 0-1)
    L * (1 + default * is_default + PULL * pull). G is 0 for a blocked source not yet due a
    retry; L is 1 for the person's own language (`own`, all they read when None), READ_TOO for another
    language they read, 0 for one they said they cannot, OTHER_LANG otherwise;
    theta is `origin(key)` (the taste model's coefficient), or ln 2 times the reactions' exponent without it.
    A source with G or L at 0 gets weight 0 and is never drawn. is_default tests `defaults` (the reader's
    languages unexpanded, so a Simplified reader's defaults are not a Traditional reader's) or else `reader`."""
    readable = {reader} if isinstance(reader, str) else set(reader)
    own = (readable if own is None else own) - set(unreadable)
    defaults = readable if defaults is None else defaults
    now = time.time() if now is None else now
    acted = sum(row["items"] for row in state.values())
    default = DEFAULT * 0.5 ** (acted / DEFAULT_HALF)
    rows = []
    for source in sources:
        row = state.get(source.key) or _blank()
        gate = 1.0 if open_to(row, now, retry_s) else 0.0
        lang = (1.0 if source.lang in own or not source.lang else READ_TOO if source.lang in readable
                else 0.0 if source.lang in unreadable else OTHER_LANG)
        topic = (PULL * topical.get(source.key, 0.0) if topical is not None
                 else TOPIC * bool(matcher and matcher.search(_text(source))))
        prior = lang * (1 + default * (source.default in defaults or source.default == "all") + topic)
        exponent = origin(source.key) if origin else math.log(2) * learned(row)
        situated = supply(source) if supply else 0.0
        weight = (gate * prior * math.exp(situated + exponent) * HIDE ** row["hides"]
                  * FAILED ** min(row["fails"], FAILS_MAX))
        rows.append({"source": source, "gate": gate, "lang_prior": lang, "prior": round(prior, 3),
                     "supply": round(situated, 3), "learned": round(exponent, 3),
                     "weight": round(max(FLOOR, weight), 4) if gate and lang else 0.0, **row})
    rows.sort(key=lambda r: (-r["weight"], r["source"].key))
    return rows


def pick(weighed: list[dict], rng: random.Random, size: int = ROUND, explore: int = EXPLORE) -> list[dict]:
    """One round: `size - explore` drawn by weight without replacement, then `explore` of the
    sources read least, a source in another language counted as read 1 / its language prior times more,
    and none the person asked to see less of. A source its gate or language leaves out is never drawn."""
    # Efraimidis and Spirakis: the largest u ** (1 / w) are a draw without replacement in proportion to w.
    keyed = sorted((r for r in weighed if r["weight"] > 0), key=lambda r: -math.log(1 - rng.random()) / r["weight"])
    drawn = keyed[:size - explore]
    rest = [r for r in keyed[size - explore:] if not r["hides"]]
    rest.sort(key=lambda r: ((r["reads"] + 1) / r["lang_prior"], rng.random()))
    return drawn + rest[:explore]

"""The chat log: per chat, a record of the lines the character took in and sent, with the recent window's row
shape and sequence numbers, kept as rows of the runtime database (db.py). The ground truth notes cite and the
review checks them against. Kept MEMORY_LOG_DAYS days; never copied, never shared across chats, never in a
backup. No model call here."""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import db, ranking
from .textproc import _focus_tokens

__all__ = ["ChatLog", "clean_row", "merge_refs", "quote", "refs_of", "row_args"]

logger = logging.getLogger("agent")

DAY_S = 86400.0
#: A second bound beside the days: a chat's newest lines kept.
MAX_LINES = 5000
#: The most lines one search returns.
SEARCH_MAX = 20
TEXT_MAX = 2000
#: The log lines a note cites, at most.
REFS_MAX = 6
COLUMNS = "seq, t, t1, user_id, name, text, self, gone, mid"


def _num(value, kind=float):
    try:
        return kind(value)
    except (TypeError, ValueError, OverflowError):
        return kind(0)


def refs_of(item) -> list[int]:
    """The log lines a note or event cites (its `refs`); [] when none."""
    refs = item.get("refs") if isinstance(item, dict) else None
    if not isinstance(refs, list):
        return []
    out = [r for r in refs if isinstance(r, int) and not isinstance(r, bool) and r > 0]
    return list(dict.fromkeys(out))[:REFS_MAX]


def merge_refs(*groups: Iterable[int]) -> list[int]:
    """Several notes' or lines' refs as one: the newest REFS_MAX, in order."""
    seqs = {s for group in groups for s in group
            if isinstance(s, int) and not isinstance(s, bool) and s > 0}
    return sorted(seqs)[-REFS_MAX:]


def clean_row(row) -> Optional[dict]:
    """A log row from a recent-window row (room line or DM turn), or None."""
    if not isinstance(row, dict):
        return None
    seq = _num(row.get("seq"), int)
    if seq <= 0:
        return None
    out = {"seq": seq, "t": _num(row.get("t")),
           "user_id": str(row.get("user_id") or ""),
           "name": str(row.get("name") or ""),
           "text": str(row.get("text") or "")[:TEXT_MAX]}
    if _num(row.get("t1")) > out["t"]:
        out["t1"] = _num(row.get("t1"))
    for flag in ("self", "gone"):
        if row.get(flag):
            out[flag] = True
    if row.get("mid"):
        out["mid"] = str(row["mid"])[:80]
    return out


def row_args(conv: str, row: dict) -> tuple:
    """A clean row as the chatlog table's columns."""
    return (conv, row["seq"], row["t"], row.get("t1"), row["user_id"], row["name"], row["text"],
            int(bool(row.get("self"))), int(bool(row.get("gone"))), row.get("mid"))


def _row(values: tuple) -> dict:
    """A table row as the row shape clean_row gives."""
    seq, t, t1, user_id, name, text, mine, gone, mid = values
    out = {"seq": seq, "t": t, "user_id": user_id, "name": name, "text": text}
    if t1:
        out["t1"] = t1
    if mine:
        out["self"] = True
    if gone:
        out["gone"] = True
    if mid:
        out["mid"] = mid
    return out


def _end(row: dict) -> float:
    return row.get("t1") or row["t"]


class ChatLog:
    """The chatlog table of the runtime's database: one row per (chat, seq). A row taken again with its seq is a
    line that grew (a merge) and replaces the one before. A `gone` row only holds the place of a forgotten newest
    line, so it is not logged again."""

    def __init__(self, runtime: Path, days: float = 30, max_lines: int = MAX_LINES):
        self.runtime = Path(runtime)
        self.db = db.at(self.runtime)
        self.days = float(days or 0)
        self.max_lines = int(max_lines)

    @property
    def on(self) -> bool:
        return self.days > 0

    # ---- reading --------------------------------------------------------------

    def read(self, conv: str) -> list[dict]:
        """The chat's lines in seq order."""
        return [_row(r) for r in self.db.read(
            f"SELECT {COLUMNS} FROM chatlog WHERE conv = ? AND gone = 0 ORDER BY seq", (conv,))]

    def lines(self, conv: str, seqs: Iterable[int]) -> list[dict]:
        want = {_num(s, int) for s in seqs}
        return [row for row in self.read(conv) if row["seq"] in want]

    def last_seq(self, conv: str) -> int:
        return self.db.read("SELECT max(seq) FROM chatlog WHERE conv = ?", (conv,))[0][0] or 0

    def search(self, conv: str, words: str = "", *,
               since: float = 0.0, until: float = 0.0, limit: int = SEARCH_MAX,
               lang: str = "zh", skip: Iterable[int] = (),
               rows: Optional[list[dict]] = None) -> list[dict]:
        """Lines by words (BM25 over focus tokens) and by time range: best
        first when `words` is given, else newest first. Words that make no
        token find nothing."""
        skipped = {_num(s, int) for s in skip}
        rows = [row for row in (self.read(conv) if rows is None else rows)
                if row["seq"] not in skipped and row["text"].strip()
                and (not since or _end(row) >= since)
                and (not until or row["t"] <= until)]
        limit = max(0, min(int(limit), SEARCH_MAX))
        if not words:
            return rows[::-1][:limit]
        query = _focus_tokens(words, lang)
        if not query or not rows:
            return []
        docs = [(row["text"].lower(),) for row in rows]
        lex = ranking.Lexicon(query, docs)
        hits = [(lex.score(doc, (1.0,)), row["seq"], row) for doc, row in zip(docs, rows)]
        hits = sorted((h for h in hits if h[0] > 0), key=lambda h: (-h[0], -h[1]))
        return [row for _s, _q, row in hits[:limit]]

    def status(self, conv: Optional[str] = None) -> dict:
        """What the page shows: on or off, the days kept, and for one chat
        how many lines and from when to when."""
        out: dict = {"on": self.on, "days": int(self.days)}
        if conv is not None:
            rows = self.read(conv)
            out.update(lines=len(rows),
                       first_at=rows[0]["t"] if rows else 0.0,
                       last_at=_end(rows[-1]) if rows else 0.0)
        else:
            out["chats"] = self.db.read("SELECT count(DISTINCT conv) FROM chatlog")[0][0]
        return out

    # ---- writing --------------------------------------------------------------

    def take(self, conv: str, rows: Iterable[dict]) -> int:
        """Add the rows not logged yet, and a grown copy of the newest one
        logged; returns how many were taken. Never raises."""
        if not self.on:
            return 0
        try:
            with self.db.write() as conn:
                last = conn.execute("SELECT seq, length(text), gone FROM chatlog WHERE conv = ? "
                                    "ORDER BY seq DESC LIMIT 1", (conv,)).fetchone()
                # A forgotten line's place takes no grown copy of it either.
                seq, size = (0, 0) if last is None else (last[0], TEXT_MAX + 1 if last[2] else last[1])
                fresh = []
                for row in sorted(filter(None, map(clean_row, rows)), key=lambda r: r["seq"]):
                    if row["seq"] > seq or (row["seq"] == seq and len(row["text"]) > size):
                        fresh.append(row)
                        seq, size = row["seq"], len(row["text"])
                # A line without a time (a page chat read back) cannot be dated,
                # and one older than the kept days would only be pruned again.
                cutoff = time.time() - self.days * DAY_S
                conn.executemany("INSERT OR REPLACE INTO chatlog VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 [row_args(conv, r) for r in fresh if r["t"] > 0 and _end(r) >= cutoff])
            return len(fresh)
        except sqlite3.Error as e:
            logger.warning("[Agent] chat log append failed: %s", e)
            return 0

    def forget(self, conv: str, gone: Callable[[dict], object]) -> int:
        """Remove the lines `gone` is true for; a line it returns words for
        keeps its place with those words. How many changed."""
        with self.db.write() as conn:
            rows = [_row(r) for r in conn.execute(
                f"SELECT {COLUMNS} FROM chatlog WHERE conv = ? ORDER BY seq", (conv,)).fetchall()]
            changed = 0
            for row in rows:
                verdict = not row.get("gone") and gone(row)
                if not verdict:
                    continue
                changed += 1
                if isinstance(verdict, str):
                    conn.execute("UPDATE chatlog SET text = ? WHERE conv = ? AND seq = ?",
                                 (verdict, conv, row["seq"]))
                elif row is rows[-1]:
                    # A gone newest line leaves its place.
                    conn.execute("UPDATE chatlog SET t1 = NULL, user_id = '', name = '', text = '', self = 0, "
                                 "gone = 1, mid = NULL WHERE conv = ? AND seq = ?", (conv, row["seq"]))
                else:
                    conn.execute("DELETE FROM chatlog WHERE conv = ? AND seq = ?", (conv, row["seq"]))
        if changed:
            self.db.scrubbed()
        return changed

    def drop(self, conv: str) -> bool:
        """Forget this chat: its lines go."""
        with self.db.write() as conn:
            gone = conn.execute("DELETE FROM chatlog WHERE conv = ?", (conv,)).rowcount
        self.db.scrubbed()
        return gone > 0

    def private_chats(self, platform: str) -> set[str]:
        """The one-to-one chats of `platform` that have a log."""
        prefix = f"private:{platform}:"
        return {conv for (conv,) in self.db.read(
            "SELECT DISTINCT conv FROM chatlog WHERE substr(conv, 1, ?) = ?", (len(prefix), prefix))}

    def clear(self) -> int:
        """Every chat's log goes (the log turned off, a reset); how many chats had one."""
        with self.db.write() as conn:
            chats = conn.execute("SELECT count(DISTINCT conv) FROM chatlog").fetchone()[0]
            conn.execute("DELETE FROM chatlog")
        self.db.scrubbed()
        return chats

    def prune(self, now: Optional[float] = None) -> int:
        """Drop lines older than the kept days and past the per-chat cap;
        returns how many went."""
        now = time.time() if now is None else now
        count = "SELECT count(*) FROM chatlog WHERE gone = 0"
        try:
            with self.db.write() as conn:
                before = conn.execute(count).fetchone()[0]
                conn.execute("DELETE FROM chatlog WHERE coalesce(t1, t) < ?", (now - self.days * DAY_S,))
                conn.execute("DELETE FROM chatlog WHERE (conv, seq) IN (SELECT conv, seq FROM (SELECT conv, seq, "
                             "row_number() OVER (PARTITION BY conv ORDER BY seq DESC) AS n FROM chatlog) "
                             "WHERE n > ?)", (self.max_lines,))
                return before - conn.execute(count).fetchone()[0]
        except sqlite3.Error as e:
            logger.warning("[Agent] chat log prune failed: %s", e)
            return 0


def quote(row: dict, tz=None) -> str:
    """One logged line as "YYYY-MM-DD HH:MM name: text"."""
    t = row.get("t") or 0.0
    when = datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d %H:%M") if t else ""
    return f"{when} {row.get('name') or '?'}: {row.get('text', '')}".strip()

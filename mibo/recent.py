"""The recent turns of each chat on disk (runtime/recent.json), so a restart
does not lose the conversation. Rebuildable: written without fsync, and a
damaged file is moved aside and every chat starts empty."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from .storage import atomic_write_text, load_rebuildable_json
from .storage import num as _num

FORMAT = 1
KEEP_HOURS = 72.0
MAX_CONVS = 256
UNANSWERED_MAX = 3


def clean_entry(entry: dict) -> dict:
    """One chat's entry: its turns (held within the history's share by the chat's compaction, dm.py) and its
    newest unanswered messages."""
    rows = entry.get("dm")
    unanswered = entry.get("unanswered")
    return {"seq": _num(entry.get("seq"), int), "last_at": _num(entry.get("last_at")),
            "dm": [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else [],
            "unanswered": [str(r) for r in unanswered if isinstance(r, str)][-UNANSWERED_MAX:]
            if isinstance(unanswered, list) else []}


def _parse(data) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("convs"), dict):
        raise ValueError("not a recent.json")
    return {str(conv): clean_entry(entry) for conv, entry in data["convs"].items() if isinstance(entry, dict)}


class RecentStore:
    """{conv: entry}; see `clean_entry` for an entry's keys."""

    def __init__(self, path: Path, keep_hours: float = KEEP_HOURS,
                 max_convs: int = MAX_CONVS):
        self.path = Path(path)
        self.keep_hours = keep_hours
        self.max_convs = max_convs
        self.convs: dict[str, dict] = {}

    def load(self) -> dict:
        self.convs = load_rebuildable_json(self.path, _parse) or {}
        return self.convs

    def snapshot(self, conv: str) -> Optional[dict]:
        return self.convs.get(conv)

    def put(self, conv: str, state: dict) -> None:
        self.convs[conv] = clean_entry(state)

    def drop(self, conv: str) -> bool:
        return self.convs.pop(conv, None) is not None

    def trim(self, now: Optional[float] = None) -> list[str]:
        """Drop chats quiet for longer than keep_hours, then all but the
        newest max_convs; returns the dropped keys."""
        now = time.time() if now is None else now
        cutoff = now - self.keep_hours * 3600.0
        order = sorted(self.convs, key=lambda c: self.convs[c]["last_at"],
                       reverse=True)
        keep = [c for c in order if self.convs[c]["last_at"] >= cutoff][:self.max_convs]
        kept = set(keep)
        dropped = [c for c in order if c not in kept]
        for conv in dropped:
            del self.convs[conv]
        return dropped

    def write(self) -> None:
        atomic_write_text(self.path, json.dumps(
            {"format": FORMAT, "convs": self.convs}, ensure_ascii=False,
            separators=(",", ":")), fsync=False)

    def delete(self) -> None:
        """MEMORY_KEEP_RECENT=false: nothing of the chats stays on disk."""
        self.convs = {}
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

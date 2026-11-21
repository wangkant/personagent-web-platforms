"""Memory bookkeeping (runtime/memory_state.json): the fold's call budget and
its backoff, and the lines a chat lost. Rebuildable: losing it can only repeat
or skip a call or reset a day's count. No model call here."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .storage import atomic_write_text, load_rebuildable_json

SAVE_EVERY_S = 60.0
PER_CONV_PER_DAY = 16
MIN_GAP_S = 1200.0
BACKOFF_START_S = 600.0
BACKOFF_MAX_S = 21600.0
FAILS_BEFORE_PAUSE = 3
PAUSE_S = 3600.0
DAY_S = 86400.0


def _parse(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError("not a memory_state.json")
    return data


def _day(now: float) -> str:
    return datetime.fromtimestamp(now).date().isoformat()


class MemoryState:
    """The file as a dict in `data`; `save()` writes at most once a minute
    unless forced."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = load_rebuildable_json(self.path, _parse) or {}
        self._dirty = False
        self._saved_at: Optional[float] = None

    def touch(self) -> None:
        self._dirty = True

    def save(self, force: bool = False) -> bool:
        if not self._dirty and not force:
            return False
        mono = time.monotonic()
        if (not force and self._saved_at is not None
                and mono - self._saved_at < SAVE_EVERY_S):
            return False
        atomic_write_text(self.path, json.dumps(
            self.data, ensure_ascii=False, separators=(",", ":")), fsync=False)
        self._dirty = False
        self._saved_at = mono
        return True

    def _map(self, key: str) -> dict:
        value = self.data.get(key)
        if not isinstance(value, dict):
            value = self.data[key] = {}
        return value

    # ---- small per-chat records --------------------------------------------

    def drop_conv(self, conv: str) -> None:
        self._map("per_conv").pop(conv, None)
        self.touch()


class CallBudget:
    """How many consolidation calls may run, when, and the backoff after a
    failure. Pure bookkeeping over a MemoryState."""

    def __init__(self, state: MemoryState):
        self.state = state

    def _conv(self, conv: str) -> dict:
        per = self.state._map("per_conv")
        row = per.get(conv)
        if not isinstance(row, dict):
            row = per[conv] = {"day": "", "count": 0, "last": 0.0, "fails": 0,
                               "next_at": 0.0, "last_error": ""}
        return row

    def _calls(self, now: float) -> list[float]:
        calls = self.state.data.get("calls")
        calls = [float(c) for c in calls if isinstance(c, (int, float))] \
            if isinstance(calls, list) else []
        calls = [c for c in calls if now - c < DAY_S]
        self.state.data["calls"] = calls
        return calls

    def allowed(self, conv: str, now: float, per_day_total: int,
                per_conv: int = PER_CONV_PER_DAY, min_gap_s: float = MIN_GAP_S,
                reason: str = "") -> Optional[str]:
        """None when a call may go now, else why not: off, paused, backoff,
        gap, chat_cap or day_cap. Reason "evict" skips the per-chat gap."""
        if per_day_total <= 0:
            return "off"
        if now < float(self.state.data.get("paused_until") or 0.0):
            return "paused"
        row = self.state._map("per_conv").get(conv)
        if isinstance(row, dict):
            if now < float(row.get("next_at") or 0.0):
                return "backoff"
            if reason != "evict" and now - float(row.get("last") or 0.0) < min_gap_s:
                return "gap"
            if row.get("day") == _day(now) and int(row.get("count") or 0) >= per_conv:
                return "chat_cap"
        if len(self._calls(now)) >= per_day_total:
            return "day_cap"
        return None

    def record_call(self, conv: str, now: float) -> None:
        """Counted when made, success or not."""
        calls = self._calls(now)
        calls.append(now)
        row = self._conv(conv)
        today = _day(now)
        if row.get("day") != today:
            row["day"], row["count"] = today, 0
        row["count"] = int(row.get("count") or 0) + 1
        row["last"] = now
        self.state.touch()

    def failed(self, conv: str, now: float, error="") -> float:
        """Back off this chat, doubling to six hours; three failures in a row
        anywhere pause every chat for an hour. Returns the chat's next try."""
        row = self._conv(conv)
        row["fails"] = int(row.get("fails") or 0) + 1
        row["next_at"] = now + min(BACKOFF_START_S * 2 ** (row["fails"] - 1),
                                   BACKOFF_MAX_S)
        row["last_error"] = (type(error).__name__ if isinstance(error, BaseException)
                             else str(error or ""))[:80]
        in_row = int(self.state.data.get("fails_in_row") or 0) + 1
        self.state.data["fails_in_row"] = in_row
        if in_row >= FAILS_BEFORE_PAUSE:
            self.state.data["paused_until"] = now + PAUSE_S
        self.state.touch()
        return row["next_at"]

    def succeeded(self, conv: str) -> None:
        row = self._conv(conv)
        row["fails"], row["next_at"], row["last_error"] = 0, 0.0, ""
        self.state.data["fails_in_row"] = 0
        self.state.touch()

    def status(self, now: float, cap: int, conv: Optional[str] = None) -> dict:
        """What the page shows: calls in the last 24 hours against the cap,
        a pause, and for one chat its next try."""
        row = self.state._map("per_conv").get(conv) if conv else None
        paused = float(self.state.data.get("paused_until") or 0.0)
        return {
            "calls_today": len(self._calls(now)),
            "cap": cap,
            "paused_until": paused if paused > now else 0.0,
            "next_at": (float(row.get("next_at") or 0.0)
                        if isinstance(row, dict)
                        and float(row.get("next_at") or 0.0) > now else 0.0),
        }

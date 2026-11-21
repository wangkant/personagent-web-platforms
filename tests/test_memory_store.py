"""The memory helpers: the call budget.

    python -m pytest tests/test_memory_store.py
"""
from __future__ import annotations

from pathlib import Path

from helpers import check


def test_the_call_budget(tmp: Path) -> None:
    from mibo import memstate

    state = memstate.MemoryState(tmp / "memory_state.json")
    budget = memstate.CallBudget(state)
    now = 1_800_000_000.0
    check("off at zero", budget.allowed("g", now, 0) == "off")
    check("a first call may go", budget.allowed("g", now, 40) is None)
    budget.record_call("g", now)
    check("twenty minutes apart", budget.allowed("g", now + 600, 40) == "gap"
          and budget.allowed("g", now + 1200, 40) is None)
    check("eviction skips the gap", budget.allowed("g", now + 60, 40, reason="evict") is None)
    check("the total for all chats", budget.allowed("h", now + 60, 1) == "day_cap")
    for i in range(15):
        budget.record_call("g", now + (i + 1) * 1200)
    check("sixteen a day per chat", budget.allowed("g", now + 17 * 1200, 40) == "chat_cap")
    nexts = [budget.failed("k", now, TimeoutError()) - now for _ in range(7)]
    check("backoff doubles from ten minutes to six hours",
          nexts == [600.0, 1200.0, 2400.0, 4800.0, 9600.0, 19200.0, 21600.0], repr(nexts))
    check("three failures in a row pause every chat",
          budget.allowed("other", now + 60, 40) == "paused")
    check("the error class is kept", state.data["per_conv"]["k"]["last_error"]
          == "TimeoutError")
    budget.succeeded("k")
    check("success resets", state.data["fails_in_row"] == 0
          and state.data["per_conv"]["k"]["fails"] == 0)
    status = budget.status(now + 60, 40, "k")
    check("the status the page shows", status["calls_today"] == 16 and status["cap"] == 40
          and status["paused_until"] > 0, repr(status))
    state.save(force=True)
    check("saved and loaded", memstate.MemoryState(tmp / "memory_state.json").data
          ["per_conv"]["g"]["count"] == 16)

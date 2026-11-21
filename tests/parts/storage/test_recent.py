"""The recent window on disk (recent.json): a damaged file is set aside, and only chats from the last 72 hours,
at most 256 of them, are kept."""
from __future__ import annotations

import json
import time
from pathlib import Path

from mibo import recent


def _chat(at: float) -> dict:
    return {"seq": 1, "last_at": at, "dm": [{"role": "user", "content": "hi", "t": at, "seq": 1}]}


def test_a_damaged_window_file_is_set_aside(tmp_path: Path) -> None:
    path = tmp_path / "recent.json"
    path.write_text("{not json", encoding="utf-8")
    assert recent.RecentStore(path).load() == {}
    assert (tmp_path / "recent.json.corrupt").is_file()


def test_only_recent_and_at_most_256_chats_are_kept(tmp_path: Path) -> None:
    now = time.time()
    convs = {f"private:page:c{n}": _chat(now - n) for n in range(260)}
    convs["private:page:quiet"] = _chat(now - 73 * 3600)
    path = tmp_path / "recent.json"
    path.write_text(json.dumps({"format": 1, "convs": convs}), encoding="utf-8")
    store = recent.RecentStore(path)
    store.load()
    dropped = store.trim(now)
    assert "private:page:quiet" in dropped and len(store.convs) == recent.MAX_CONVS
    assert "private:page:c0" in store.convs and "private:page:c259" not in store.convs

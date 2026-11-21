"""The runtime database (mibo/db.py), where the chat record, the page's chats and the embedding cache are kept:
every write whole or not at all, the page's threads and a second process writing at once without losing a row,
and a home's files from before it moved in once, read back, then gone."""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import threading
import time
from pathlib import Path

import pytest

from mibo import chatlog, db, embeddings, welcome

CHAT = "private:browser:c1"
ROOM = "telegram:c9"


def _row(seq: int, text: str) -> dict:
    return {"seq": seq, "t": time.time(), "user_id": "browser:c1", "name": "", "text": text}


def _take_worker(runtime: str, start: int, count: int) -> None:
    log = chatlog.ChatLog(Path(runtime))
    for seq in range(start, start + count):
        assert log.take(f"private:browser:p{start}", [_row(seq, f"line {seq}")]) == 1


def test_a_write_is_whole_or_not_at_all(tmp_path: Path) -> None:
    store = db.at(tmp_path)
    with pytest.raises(RuntimeError):
        with store.write() as conn:
            conn.execute("INSERT INTO page_chats VALUES ('a', 'c', '', '', 0)")
            raise RuntimeError("cut short")
    assert store.read("SELECT count(*) FROM page_chats") == [(0,)]


def test_the_page_threads_write_at_once_and_lose_no_row(tmp_path: Path) -> None:
    log = chatlog.ChatLog(tmp_path)

    def write(worker: int) -> None:
        for seq in range(1, 101):
            log.take(f"private:browser:t{worker}", [_row(seq, f"line {seq}")])
            log.read(f"private:browser:t{worker}")

    threads = [threading.Thread(target=write, args=(worker,)) for worker in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert [len(log.read(f"private:browser:t{worker}")) for worker in range(4)] == [100] * 4


def test_a_second_process_writes_the_same_database_and_loses_no_row(tmp_path: Path) -> None:
    log = chatlog.ChatLog(tmp_path)
    other = multiprocessing.get_context("spawn").Process(target=_take_worker, args=(str(tmp_path), 1, 60))
    other.start()
    for seq in range(1, 61):
        log.take(CHAT, [_row(seq, f"mine {seq}")])
    other.join(60)
    assert other.exitcode == 0
    assert len(log.read(CHAT)) == 60 and len(log.read("private:browser:p1")) == 60


def _home_kept_in_files(runtime: Path, messages: int) -> None:
    """A home's runtime folder as the app kept it before the database."""
    desktop, log = runtime / "desktop", runtime / "log"
    (desktop / "chats").mkdir(parents=True)
    log.mkdir()
    page = [{"role": "user" if n % 2 else "assistant", "text": f"message {n}", "seq": n, "at": "2026-11-20T10:00:00"}
            for n in range(1, messages + 1)]
    (desktop / "browser-chat.json").write_text(json.dumps(
        {"conversation": "c1", "seq": messages, "messages": page, "trimmed": False}), encoding="utf-8")
    (desktop / "chats" / "0123456789abcdef.json").write_text(json.dumps(
        {"conversation": "c2", "seq": 1, "title": "plans", "at": "2026-11-20T11:00:00",
         "messages": [{"role": "user", "text": "a plain chat", "seq": 1}]}), encoding="utf-8")
    name = hashlib.sha256(CHAT.encode()).hexdigest()[:32]
    lines = [json.dumps(_row(n, f"logged {n}")) for n in range(1, 4)]
    # A write cut short in the old file is left behind.
    (log / f"{name}.jsonl").write_text("\n".join(lines) + '\n{"seq": 4, "t', encoding="utf-8")
    (log / f".{name}.jsonl.append.lock").write_bytes(b"\0")
    # A room's record: its key is in no line of it, only in the recent turns.
    room = hashlib.sha256(ROOM.encode()).hexdigest()[:32]
    (log / f"{room}.jsonl").write_text(json.dumps(_row(1, "in the room")) + "\n", encoding="utf-8")
    (runtime / "recent.json").write_text(json.dumps({"format": 1, "convs": {ROOM: {}}}), encoding="utf-8")
    vector = embeddings.unit([3.0, 4.0])
    (runtime / "embeddings.jsonl").write_text(json.dumps(
        {"model": "m", "key": "k1", "vec": embeddings.base64.b64encode(vector.tobytes()).decode()}) + "\n",
        encoding="utf-8")


def test_a_home_kept_in_files_moves_into_the_database_once_and_its_files_go(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = home / "runtime"
    _home_kept_in_files(runtime, 120)
    workspace = welcome.Workspace(home)
    try:
        shown = workspace.history()["messages"]
        assert [m["text"] for m in shown] == [f"message {n}" for n in range(1, 121)], "every message, not the last 50"
        assert [c["title"] for c in workspace.chats()["chats"]] == ["plans"]
        assert workspace.history(chat="0123456789abcdef")["messages"][0]["text"] == "a plain chat"
    finally:
        workspace.close()
    assert [r["text"] for r in chatlog.ChatLog(runtime).read(CHAT)] == ["logged 1", "logged 2", "logged 3"]
    assert [r["text"] for r in chatlog.ChatLog(runtime).read(ROOM)] == ["in the room"]
    assert list(embeddings.EmbeddingCache(runtime).get("m", "k1")) == list(embeddings.unit([3.0, 4.0]))
    left = sorted(p.relative_to(runtime).as_posix() for p in runtime.rglob("*"))
    assert [f for f in left if not f.startswith("mibo.db")] == ["desktop", "recent.json"], "the old files are gone"
    # A file of the old kind that turns up later is not moved again over what the home has since.
    (runtime / "desktop" / "browser-chat.json").write_text(json.dumps(
        {"conversation": "c1", "seq": 1, "messages": [{"role": "user", "text": "stale", "seq": 1}]}), encoding="utf-8")
    db.close(runtime)
    again = welcome.Workspace(home)
    try:
        assert len(again.history()["messages"]) == 120
        assert not (runtime / "desktop" / "browser-chat.json").exists()
    finally:
        again.close()


def test_a_file_the_move_cannot_place_is_kept_and_the_rest_still_moves(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = home / "runtime"
    _home_kept_in_files(runtime, 4)
    # A chat file cut short, and a record whose chat nothing in the home names.
    (runtime / "desktop" / "chats" / "feedfeedfeedfeed.json").write_text('{"conversation": "c9", "mess', encoding="utf-8")
    stray = hashlib.sha256(b"private:someone-nothing-names").hexdigest()[:32]
    (runtime / "log" / f"{stray}.jsonl").write_text(json.dumps({"seq": 1, "text": "kept words"}) + "\n", encoding="utf-8")
    workspace = welcome.Workspace(home)
    try:
        assert len(workspace.history()["messages"]) == 4
    finally:
        workspace.close()
    kept = sorted(p.relative_to(runtime).as_posix() for p in runtime.rglob("*.unmoved"))
    assert kept == ["desktop/chats/feedfeedfeedfeed.json.unmoved", f"log/{stray}.jsonl.unmoved"]
    assert "kept words" in (runtime / "log" / f"{stray}.jsonl.unmoved").read_text(encoding="utf-8")

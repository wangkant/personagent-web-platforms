"""The chat record (mibo/chatlog.py): each chat's lines in the runtime database for MEMORY_LOG_DAYS, searched by
recall, cited by what memory keeps, and reached by every forget."""
from __future__ import annotations

import time
from pathlib import Path

from mibo import chatlog

CHAT = "private:page:c1"
OTHER = "private:page:c2"
DAY = 86400.0


def row(seq: int, text: str, t: float = 0.0, mine: bool = False) -> dict:
    """A line as the page's chat gives it: the person's, or the character's own."""
    return {"seq": seq, "t": t or time.time(), "user_id": "" if mine else "page:me",
            "name": "Mibo" if mine else "", "text": text, "self": mine}


def test_lines_are_appended_once_and_a_grown_line_is_read_at_its_newest(tmp_path: Path) -> None:
    log = chatlog.ChatLog(tmp_path)
    assert log.take(CHAT, [row(1, "hi"), row(2, "tea?")]) == 2
    assert log.take(CHAT, [row(1, "hi"), row(2, "tea?")]) == 0
    assert log.take(CHAT, [row(2, "tea? green")]) == 1
    assert [r["text"] for r in log.read(CHAT)] == ["hi", "tea? green"]
    # Opened again, it knows where it stopped.
    again = chatlog.ChatLog(tmp_path)
    assert again.take(CHAT, [row(2, "tea? green")]) == 0 and again.last_seq(CHAT) == 2


def test_a_chat_key_is_only_a_key(tmp_path: Path) -> None:
    log = chatlog.ChatLog(tmp_path)
    keys = ("../../evil", "a/b\\c", "C:\\x", "\x00", "private:page:../../..", "x'; DROP TABLE chatlog; --")
    for n, key in enumerate(keys, 1):
        log.take(key, [row(1, f"line {n}")])
    assert [log.read(key)[0]["text"] for key in keys] == [f"line {n}" for n in range(1, len(keys) + 1)]
    assert {p.name for p in tmp_path.iterdir()} <= {"mibo.db", "mibo.db-wal", "mibo.db-shm"}
    assert not (tmp_path.parent / "evil").exists()


def test_lines_past_their_age_or_the_cap_are_pruned(tmp_path: Path) -> None:
    now = time.time()
    log = chatlog.ChatLog(tmp_path, days=30, max_lines=3)
    log.take(CHAT, [row(i, f"line {i}", t=now - 60 + i) for i in range(2, 7)])
    log.take(OTHER, [row(1, "kept a while")])
    assert log.prune(now) == 2
    assert [r["seq"] for r in log.read(CHAT)] == [4, 5, 6]
    # Thirty-one days on, every line has gone, and no chat has a record.
    assert log.prune(now + 31 * DAY) == 4
    assert log.status()["chats"] == 0 and log.private_chats("page") == set()
    assert log.take(OTHER, [row(2, "stale", t=now - 31 * DAY)]) == 1 and not log.read(OTHER)


def test_recall_finds_lines_across_chats_by_words_and_by_time(tmp_path: Path) -> None:
    now = time.time()
    log = chatlog.ChatLog(tmp_path)
    log.take(CHAT, [row(1, "I adopted a cat named Miso", now - 5 * DAY),
                    row(2, "nice weather today", now - 4 * DAY, mine=True),
                    row(3, "Miso knocked over my plant", now - DAY)])
    log.take(OTHER, [row(1, "我下周去杭州出差", now - 3600)])
    # As pastchats.py asks: the lines of every chat in one list, each with its chat.
    rows = [dict(r, conv=conv) for conv in (CHAT, OTHER) for r in log.read(conv)]
    assert [(r["conv"], r["seq"]) for r in log.search("", "Miso the cat", lang="en", rows=rows)][:1] == [(CHAT, 1)]
    assert [(r["conv"], r["seq"]) for r in log.search("", "杭州", lang="zh", rows=rows)] == [(OTHER, 1)]
    assert [r["text"] for r in log.search("", "", since=now - 2 * DAY, rows=rows)] \
        == ["我下周去杭州出差", "Miso knocked over my plant"]
    assert [r["seq"] for r in log.search(CHAT, "Miso", skip=[1])] == [3]
    assert log.search(CHAT, "a") == []
    assert len(log.search("", "", limit=999, rows=rows * 40)) <= chatlog.SEARCH_MAX


def test_a_forgotten_newest_line_is_not_logged_again(tmp_path: Path) -> None:
    log = chatlog.ChatLog(tmp_path)
    log.take(CHAT, [row(1, "hi"), row(2, "my secret")])
    assert log.forget(CHAT, lambda r: "secret" in r["text"]) == 1
    assert [r["text"] for r in log.read(CHAT)] == ["hi"]
    assert not any(b"secret" in p.read_bytes() for p in tmp_path.glob("mibo.db*"))
    # Opened again, it does not log it again from the chat's window.
    again = chatlog.ChatLog(tmp_path)
    assert again.take(CHAT, [row(1, "hi"), row(2, "my secret")]) == 0
    assert [r["text"] for r in again.read(CHAT)] == ["hi"]


def test_a_forget_that_keeps_a_lines_place_gives_it_the_words_it_is_handed(tmp_path: Path) -> None:
    log = chatlog.ChatLog(tmp_path)
    log.take(CHAT, [row(1, "my cat Miso is sick"), row(2, "get well soon", mine=True), row(3, "thanks")])
    assert log.forget(CHAT, lambda r: "[forgotten]" if "Miso" in r["text"] else False) == 1
    assert [r["text"] for r in log.read(CHAT)] == ["[forgotten]", "get well soon", "thanks"]
    assert log.drop(CHAT) and log.read(CHAT) == [] and not log.drop(CHAT)


def test_what_memory_cites_is_read_as_a_few_line_numbers() -> None:
    assert chatlog.refs_of({"refs": [3, "4", -1, True, 3, 5, 6, 7, 8, 9, 10]}) == [3, 5, 6, 7, 8, 9]
    assert chatlog.refs_of({}) == [] == chatlog.refs_of({"refs": "1,2"}) == chatlog.refs_of(None)

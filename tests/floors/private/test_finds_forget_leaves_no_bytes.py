"""What the person deletes or forgets of their finds is in no byte of the runtime database once it is done: a page
deleted from the history of the app's browser (its visits, the library item its reading made, what the recommender
heard of it), and the words of a 不感兴趣 said in the chat once what they named is forgotten. Not in mibo.db, not in
mibo.db-wal, not in mibo.db-shm (secure_delete, and the write-ahead log cut after a forget: db.py)."""
from __future__ import annotations

import sqlite3
import time

from harness.home import holding
from harness.model import say
from harness.turn import run_turn

from mibo import browsed, taste
from mibo.library import Item, Library
from mibo.panels import feed

PAGE_TITLE = "Harbourlight pickling notes"
PAGE_PATH = "pickled-lanterns-7731"
PAGE_TEXT = "Brine the lanterns for nine nights under the harbour wall."
DISLIKE = "榴莲千层蛋糕"  # copy: voice


def _database_files_holding(home, words: str) -> list[str]:
    return [f for f in holding(home / "runtime", [words])[words] if f.startswith("mibo.db")]


def _crowd(runtime, now: float) -> None:
    """Enough finds, visits and reactions around the one that goes that the database's pages split and move."""
    library = Library(runtime)
    for n in range(80):
        item = library.add(Item(url=f"https://crowd.example/a/{n}", title=f"Crowd find {n}",
                                description="filler " * 60, seen_at=int(now) - n * 60))
        feed.remember(runtime, item, "seen", now - n)
        browsed.record(runtime / "desktop", {"url": f"https://crowd.example/p/{n}", "title": f"Visit {n} " * 8,
                                             "s": 3, "at": now - 3600 - n})


def test_a_page_deleted_from_the_history_leaves_no_byte_of_it(app, monkeypatch):
    monkeypatch.setattr(taste, "fit", lambda runtime, soon=False: None)
    runtime, now = app.home / "runtime", time.time()
    _crowd(runtime, now)
    url = f"https://shore.example/{PAGE_PATH}"
    row = {"url": url, "title": PAGE_TITLE, "s": 120, "at": now - 600}
    assert browsed.record(runtime / "desktop", row)
    ident = browsed.keep_item(runtime, row, {"title": PAGE_TITLE, "text": PAGE_TEXT, "kind": "article"})
    feed.remember(runtime, Library(runtime).get(ident), "open", now - 300)
    _crowd(runtime, now - 86400)
    assert _database_files_holding(app.home, PAGE_TITLE), "the visit is kept before it is deleted"
    day = app.get("/api/browser/history").json()["pages"]
    entry = next(page for page in day if page["url"] == url)
    assert app.post("/api/browser/history", {"url": url, "day": entry["day"]}).json() == {"ok": True}
    assert Library(runtime).get(ident) is None and not feed.of(runtime, ident)
    for words in (PAGE_TITLE, PAGE_PATH, PAGE_TEXT):
        left = _database_files_holding(app.home, words)
        assert left == [], f"SQLite {sqlite3.sqlite_version} keeps the deleted page in {left}"


def test_the_words_of_a_no_leave_no_byte_once_what_they_named_is_forgotten(app, monkeypatch):
    monkeypatch.setattr(taste, "fit", lambda runtime, soon=False: None)
    runtime, now = app.home / "runtime", time.time()
    _crowd(runtime, now)
    item = Library(runtime).add(Item(url="https://bakery.example/cake/1", title="A layered cake", seen_at=int(now)))
    # 不感兴趣 in the chat, with the person's own words (welcome._chat_finds).
    assert feed.remember(runtime, item, "skip", now, surface="chat",
                         said={"about": "topic", "words": f"我不爱吃{DISLIKE}"})  # copy: voice
    _crowd(runtime, now - 86400)
    run_turn(app, f"我真的不喜欢{DISLIKE}", say("记下了。"))  # copy: voice
    assert app.post("/api/chat", {"text": f"记住我不喜欢{DISLIKE}"}).status_code == 200  # copy: voice
    assert _database_files_holding(app.home, DISLIKE), "the words are kept before the forget"
    # Forgotten from Settings › 记忆, with the lines of the chats that name it.
    kept = next(fact for fact in app.get("/api/person").json()["facts"] if DISLIKE in fact["text"])
    assert app.post("/api/person/forget_chats", {"id": kept["id"]}).status_code == 200
    assert feed.of(runtime, item.id), "the no itself stays; only the words go"
    left = _database_files_holding(app.home, DISLIKE)
    assert left == [], f"SQLite {sqlite3.sqlite_version} keeps the forgotten words of a no in {left}"

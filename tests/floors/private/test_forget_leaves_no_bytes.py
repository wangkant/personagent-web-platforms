"""What a person forgets, and what they said in a 无痕对话, is in no byte of the runtime database once the forget
is done or the 无痕对话 is closed: not in mibo.db, not in mibo.db-wal, not in mibo.db-shm. The database deletes
with secure_delete on and cuts its write-ahead log after a forget (db.py), on whatever SQLite this Python brings;
the same test runs on Windows, Linux and macOS."""
from __future__ import annotations

import sqlite3

from harness.home import holding
from harness.model import say
from harness.turn import run_turn

PET = "芝麻糊"  # copy: voice
SPOT = "朝南的窗台上晒太阳"  # copy: voice
SECRET_PLACE = "月亮湾小码头"  # copy: voice


def _database_files_holding(home, words: str) -> list[str]:
    return [f for f in holding(home / "runtime", [words])[words] if f.startswith("mibo.db")]


def test_a_forget_leaves_the_words_in_no_byte_of_the_database(app):
    # Enough lines around it that the database's pages split and move.
    for n in range(12):
        run_turn(app, f"今天第{n}件小事：" + "散步、做饭、看书。" * 8, say("听起来很充实。" * 6))  # copy: voice
    run_turn(app, f"我家猫叫{PET}，最爱趴在{SPOT}", say("真可爱。"))  # copy: voice
    assert app.post("/api/chat", {"text": f"记住我家猫叫{PET}"}).status_code == 200  # copy: voice
    for n in range(6):
        run_turn(app, f"后来又聊了第{n}件事", say("嗯嗯，我在听。"))  # copy: voice
    assert _database_files_holding(app.home, PET), "the chat is kept before the forget"
    # Forgotten from Settings › 记忆, with the lines of the chats that name it.
    kept = next(item for item in app.get("/api/person").json()["facts"] if PET in item["text"])
    assert app.post("/api/person/forget_chats", {"id": kept["id"]}).status_code == 200
    assert not any(words in str(app.get("/api/chat/history").json()) for words in (PET, SPOT))
    for words in (PET, SPOT):
        left = _database_files_holding(app.home, words)
        assert left == [], f"SQLite {sqlite3.sqlite_version} keeps the forgotten words in {left}"


def test_a_private_chat_leaves_no_byte_once_it_is_closed(app):
    run_turn(app, "先在主对话里说一句", say("好的。"))  # copy: voice
    chat = app.post("/api/chats", {"private": True}).json()["id"]
    run_turn(app, f"别告诉别人，我把钥匙藏在{SECRET_PLACE}", say(f"{SECRET_PLACE}，我不会记下来。"),  # copy: voice
             conversation=chat)
    # Shown while it is open, and kept in this process only.
    assert SECRET_PLACE in str(app.get(f"/api/chat/history?chat={chat}").json())
    assert _database_files_holding(app.home, SECRET_PLACE) == []
    assert app.post("/api/chats/delete", {"id": chat}).json() == {"ok": True}
    left = _database_files_holding(app.home, SECRET_PLACE)
    assert left == [], f"SQLite {sqlite3.sqlite_version} keeps a closed 无痕对话 in {left}"

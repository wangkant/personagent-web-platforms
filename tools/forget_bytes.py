"""The audit's check for the runtime database (mibo/db.py), on this Python and the SQLite it brings: after a forget,
and after a chat is deleted, are the words absent from every raw byte of mibo.db, mibo.db-wal and mibo.db-shm?

    python tools/forget_bytes.py [folder]

Writes a few thousand chat-record rows and a long page chat into a fresh runtime folder (a temporary one when none
is given), forgets the lines holding one word as the app does (the chat record's forget, the page copy's scrub),
deletes a chat, and prints one JSON object: Python, SQLite, the system, and the files that still hold each word.
Needs only the standard library and the package's chatlog and db modules. The floor test
tests/floors/private/test_forget_leaves_no_bytes.py holds the same through the page in CI on all three systems."""
from __future__ import annotations

import json
import platform
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FORGOTTEN = "zebrafinch-forgotten"
PRIVATE = "harbour-private-line"


def holding(folder: Path, words: str) -> list[str]:
    forms = {words.encode("utf-8"), json.dumps(words)[1:-1].encode("ascii")}
    return sorted(p.name for p in folder.glob("mibo.db*") if any(f in p.read_bytes() for f in forms))


def main(argv: list[str]) -> int:
    # The chat record and the database only: the check runs on a Python without the app's other packages.
    from mibo import chatlog, db

    runtime = Path(argv[0]) if argv else Path(tempfile.mkdtemp(prefix="forget-bytes-"))
    runtime.mkdir(parents=True, exist_ok=True)
    now, conv = time.time(), "private:browser:check"
    log = chatlog.ChatLog(runtime)
    store = log.db
    for start in range(1, 3001, 300):
        log.take(conv, [{"seq": s, "t": now - 3000 + s, "user_id": "browser:check", "name": "",
                         "text": f"line {s} " + (FORGOTTEN if s % 97 == 0 else "words " * 12)}
                        for s in range(start, start + 300)])
    # A long page chat, its rows as welcome.PageChat writes them.
    page = [(n, json.dumps({"role": "user", "text": f"message {n} " + (FORGOTTEN if n % 41 == 0 else "talk " * 20),
                            "seq": n}, ensure_ascii=False)) for n in range(1, 401)]
    with store.write() as conn:
        conn.execute("INSERT INTO page_chats VALUES ('main', 'check', '', '', 400)")
        conn.executemany("INSERT INTO page_messages(chat, seq, body) VALUES ('main', ?, ?)", page)
    before = holding(runtime, FORGOTTEN)
    # A forget: the record's lines go, the page copy's lines become the forgotten mark, in one step.
    log.forget(conv, lambda row: FORGOTTEN in row["text"])
    with store.write() as conn:
        conn.executemany("UPDATE page_messages SET body = ? WHERE chat = 'main' AND seq = ?",
                         [(json.dumps({"role": "user", "text": "(forgotten)", "seq": n}), n)
                          for n, body in page if FORGOTTEN in body])
    store.scrubbed()
    # A 无痕对话 is never written (PageChat.save); a plain chat deleted goes as a forget does.
    with store.write() as conn:
        conn.execute("INSERT INTO page_messages(chat, seq, body) VALUES ('0123456789abcdef', 1, ?)",
                     (json.dumps({"role": "user", "text": PRIVATE, "seq": 1}),))
    with store.write() as conn:
        conn.execute("DELETE FROM page_messages WHERE chat = '0123456789abcdef'")
    store.scrubbed()
    print(json.dumps({"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                      "system": f"{platform.system()} {platform.release()} {platform.machine()}",
                      "files": sorted(p.name for p in runtime.glob("mibo.db*")),
                      "forgotten_held_before": before, "forgotten_held_after": holding(runtime, FORGOTTEN),
                      "deleted_chat_held_after": holding(runtime, PRIVATE)}, indent=1))
    db.close(runtime)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

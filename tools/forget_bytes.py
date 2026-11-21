"""The audit's check for the runtime database (mibo/db.py), on this Python and the SQLite it brings: after a forget,
after a chat is deleted, and after a find goes (a page deleted from the history of the app's browser, with the library
item its reading made, its reactions and showings; the words of a 不感兴趣 forgotten), are the words absent from every
raw byte of mibo.db, mibo.db-wal and mibo.db-shm?

    python tools/forget_bytes.py [folder]

Writes a few thousand chat-record rows and a long page chat into a fresh runtime folder (a temporary one when none
is given), forgets the lines holding one word as the app does (the chat record's forget, the page copy's scrub),
deletes a chat, writes a few thousand finds, visits, showings and reactions and takes one page and one no's words out
of them with the statements browsed.py, library.py and panels/feed.py use, and prints one JSON object: Python,
SQLite, the system, and the files that still hold each word. Needs only the standard library and the package's
chatlog and db modules. The floor tests tests/floors/private/test_forget_leaves_no_bytes.py and
test_finds_forget_leaves_no_bytes.py hold the same through the page in CI on all three systems."""
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
PAGE = "lantern-pickling-page"
SAID = "durian-layer-cake-words"


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
    finds_before = finds(store, now, runtime)
    print(json.dumps({"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                      "system": f"{platform.system()} {platform.release()} {platform.machine()}",
                      "files": sorted(p.name for p in runtime.glob("mibo.db*")),
                      "forgotten_held_before": before, "forgotten_held_after": holding(runtime, FORGOTTEN),
                      "deleted_chat_held_after": holding(runtime, PRIVATE),
                      "finds_held_before": finds_before, "deleted_page_held_after": holding(runtime, PAGE),
                      "forgotten_no_held_after": holding(runtime, SAID)}, indent=1))
    db.close(runtime)
    return 0


def finds(store, now: float, runtime: Path) -> dict[str, list[str]]:
    """Finds, visits, showings and reactions, then one page deleted from the history and one no's words forgotten,
    each the way the app does it (browsed._drop, Library.drop, feed.forget_items, feed.forget_said); the files that
    held each word before."""
    gone = "pickled"
    with store.write() as conn:
        for n in range(3000):
            ident, url = f"{n:016x}", f"https://shore.example/{PAGE if n == 1500 else n}"
            if n == 1500:
                ident = gone
            item = {"url": url, "title": f"find {n} " + ("about " * 30), "text": (PAGE + " ") * 3 if n == 1500 else ""}
            conn.execute("INSERT INTO library VALUES (?,?,?,?,?,?)",
                         (ident, int(now) - n, "", "browsed" if n == 1500 else "", "", json.dumps(item)))
            conn.execute("INSERT INTO browsed (at, url, row) VALUES (?,?,?)",
                         (now - n, url, json.dumps({"url": url, "title": item["title"], "at": now - n})))
            conn.execute("INSERT INTO shown (at, id, gated, row) VALUES (?,?,0,?)", (now - n, ident, '{"surface": "card"}'))
            said = {"said": {"about": "topic", "words": SAID}} if n % 211 == 0 else {}
            conn.execute("INSERT INTO feed_events (at, id, action, site, kind, extra) VALUES (?,?,?,?,?,?)",
                         (now - n, ident, "skip" if said else "seen", "shore.example", "article", json.dumps(said)))
    before = {PAGE: holding(runtime, PAGE), SAID: holding(runtime, SAID)}
    with store.write() as conn:
        conn.execute("DELETE FROM browsed WHERE url LIKE ?", (f"%{PAGE}%",))
    store.scrubbed()
    with store.write() as conn:
        conn.execute("DELETE FROM library WHERE id = ?", (gone,))
    store.scrubbed()
    with store.write() as conn:
        conn.execute("DELETE FROM feed_events WHERE id = ?", (gone,))
        conn.execute("DELETE FROM shown WHERE id = ?", (gone,))
    store.scrubbed()
    with store.write() as conn:
        conn.execute("UPDATE feed_events SET extra = ? WHERE extra LIKE ?",
                     (json.dumps({"said": {"about": "topic", "words": ""}}), f"%{SAID}%"))
    store.scrubbed()
    return before


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

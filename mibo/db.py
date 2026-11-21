"""The runtime database (runtime/mibo.db): one SQLite file, through Python's own sqlite3, for each chat's record
(chatlog.py), the page's copy of each chat (welcome.PageChat), the embedding cache (embeddings.py), the finds
(library.py), the feed's events, topics and showings (panels/feed.py, taste.py), the pages read in the app's browser
(browsed.py), the sources' state (sources/weights.py, sources/catalog.py) and the situation (situation.py).

One connection per file in this process, shared by the page's threads under one lock; WAL, so a write never
blocks for long and a second process (a restart overlapping the last) waits on the busy timeout; every write one
transaction. secure_delete overwrites what a delete removes, and `scrubbed` after a forget checkpoints the WAL
into the file and truncates it, so no older copy of the words stays in mibo.db, mibo.db-wal or mibo.db-shm.
Search stays the app's own BM25 over the rows read: FTS5's trigram tokenizer matches no word under three
characters, which is most Chinese words."""
from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

logger = logging.getLogger("agent.db")

FILE = "mibo.db"
BUSY_S = 10.0
JOURNAL_MAX = 4 * 1024 * 1024

# A page message row's kind: "message" is what the page shows; another kind (a summary marker, say) is kept in
# the same table at its own seq and left alone by PageChat, which reads and writes only its own kind.
# A row that holds a whole thing as JSON (a find, an event's extra, a showing, a page read) keeps beside it, as
# columns, only what a query picks rows by.
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS chatlog (
    conv TEXT NOT NULL, seq INTEGER NOT NULL, t REAL NOT NULL, t1 REAL,
    user_id TEXT NOT NULL, name TEXT NOT NULL, text TEXT NOT NULL,
    self INTEGER NOT NULL DEFAULT 0, gone INTEGER NOT NULL DEFAULT 0, mid TEXT,
    PRIMARY KEY (conv, seq)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS page_chats (
    id TEXT PRIMARY KEY, conversation TEXT NOT NULL, title TEXT NOT NULL, at TEXT NOT NULL,
    seq INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS page_messages (
    chat TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'message', body TEXT NOT NULL,
    PRIMARY KEY (chat, seq)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS embeddings (
    model TEXT NOT NULL, key TEXT NOT NULL, vec BLOB NOT NULL, PRIMARY KEY (model, key)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS library (
    id TEXT PRIMARY KEY, seen_at INTEGER NOT NULL, state TEXT NOT NULL, via TEXT NOT NULL, source TEXT NOT NULL,
    item TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS library_seen_at ON library (seen_at);
CREATE TABLE IF NOT EXISTS feed_events (
    n INTEGER PRIMARY KEY, at REAL NOT NULL, id TEXT NOT NULL, action TEXT NOT NULL, site TEXT NOT NULL,
    kind TEXT NOT NULL, extra TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS feed_events_id ON feed_events (id);
CREATE TABLE IF NOT EXISTS feed_topics (n INTEGER PRIMARY KEY, topic TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shown (
    n INTEGER PRIMARY KEY, at REAL NOT NULL, id TEXT NOT NULL, gated INTEGER NOT NULL, row TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS shown_id ON shown (id);
CREATE TABLE IF NOT EXISTS browsed (n INTEGER PRIMARY KEY, at REAL NOT NULL, url TEXT NOT NULL, row TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_state (
    key TEXT PRIMARY KEY, reads INTEGER NOT NULL, fails INTEGER NOT NULL, items INTEGER NOT NULL,
    points REAL NOT NULL, hides INTEGER NOT NULL, blocked INTEGER NOT NULL, tried INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS own_feeds (key TEXT PRIMARY KEY, n INTEGER NOT NULL, entry TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS source_reads (
    key TEXT PRIMARY KEY, at INTEGER NOT NULL, count INTEGER NOT NULL, error TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS situation (name TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS hosts (
    site TEXT PRIMARY KEY, fails INTEGER NOT NULL, fail_at REAL NOT NULL, ok_at REAL NOT NULL, wall_at REAL NOT NULL,
    mode TEXT NOT NULL) WITHOUT ROWID;
"""
# What a backup carries: everything but the chat record, which a forget must reach in full.
BACKED_UP = ("page_chats", "page_messages", "embeddings", "library", "feed_events", "feed_topics", "shown", "browsed",
             "source_state", "own_feeds", "source_reads", "situation", "hosts")


class Db:
    """One database file: `read` for queries, `write` for a transaction (nested ones join the outer)."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, timeout=BUSY_S, isolation_level=None, check_same_thread=False)
        self._lock = threading.RLock()
        self._depth = 0
        self._commits = 0
        # trusted_schema off: a database a restore attaches runs no function of its own schema. The write-ahead log
        # is cut back to JOURNAL_MAX after each checkpoint, so one large write does not leave a large file behind.
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "secure_delete=ON", "trusted_schema=OFF",
                       f"journal_size_limit={JOURNAL_MAX}"):
            self._conn.execute(f"PRAGMA {pragma}")
        self._conn.executescript(SCHEMA)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # Windows ACLs do not map to POSIX mode bits

    def read(self, sql: str, args=()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if self._depth:
                self._depth += 1
                try:
                    yield self._conn
                finally:
                    self._depth -= 1
                return
            self._conn.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")
                self._commits += 1
            finally:
                self._depth = 0

    def version(self) -> tuple[int, int]:
        """Changes when anything is committed, by this process or another: for a reader that keeps what it read."""
        with self._lock:
            return self._commits, self._conn.execute("PRAGMA data_version").fetchone()[0]

    def scrubbed(self) -> None:
        """After a forget or a delete: the WAL's pages go into the file and the WAL is cut to nothing."""
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_open: dict[str, Db] = {}
_guard = threading.Lock()


def at(runtime: Path) -> Db:
    """The database of the runtime folder `runtime`, opened once per process; a home's old files move in on the
    first open."""
    key = os.path.abspath(Path(runtime) / FILE)
    with _guard:
        db = _open.get(key)
        if db is None:
            db = _open[key] = Db(Path(key))
            move_in(db, Path(runtime))
        return db


def close(runtime: Path) -> None:
    """Close the runtime's database, if this process opened it: before a restore or a reset replaces the file."""
    with _guard:
        db = _open.pop(os.path.abspath(Path(runtime) / FILE), None)
    if db is not None:
        db.close()


def snapshot(runtime: Path, target: Path) -> bool:
    """A consistent copy of the runtime's database at `target` through SQLite's backup API, without the chat
    record; False when the runtime has no database."""
    if not (Path(runtime) / FILE).is_file():
        return False
    source = at(runtime)
    copy = sqlite3.connect(target, isolation_level=None)
    try:
        with source._lock:
            source._conn.backup(copy)
        # One file in the archive, no -wal beside it.
        copy.execute("PRAGMA journal_mode=DELETE")
        copy.execute("DELETE FROM chatlog")
        copy.execute("VACUUM")
    finally:
        copy.close()
    return True


def take_back(runtime: Path, archived: Optional[Path]) -> None:
    """A restore: what a backup carries, from the database `archived` (None: an archive with none), in place of
    the runtime's own; the chat record stays as it is."""
    db = at(runtime)
    with db._lock:
        if archived is not None:
            db._conn.execute("ATTACH DATABASE ? AS archived", (str(archived),))
        try:
            with db.write() as conn:
                for table in BACKED_UP:
                    conn.execute(f"DELETE FROM {table}")
                    if archived is not None:
                        conn.execute(f"INSERT INTO {table} SELECT * FROM archived.{table}")
        finally:
            if archived is not None:
                db._conn.execute("DETACH DATABASE archived")
    db.scrubbed()


# ---- the one-time move of a home's files from before the database ----------------------------------------

def _log_rows(path: Path) -> list[dict]:
    from .chatlog import clean_row

    by_seq: dict[int, dict] = {}
    for raw in path.read_bytes().split(b"\n"):
        try:
            row = clean_row(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, ValueError):
            continue
        if row is not None:
            by_seq[row["seq"]] = row
    return [by_seq[s] for s in sorted(by_seq)]


def _old_files(runtime: Path) -> list[Path]:
    desktop = runtime / "desktop"
    return [*sorted((runtime / "log").glob("*.jsonl")), desktop / "browser-chat.json",
            *sorted((desktop / "chats").glob("*.json")), runtime / "embeddings.jsonl"]


def _read_old(runtime: Path, moving: dict[Path, Path]) -> tuple[tuple[dict, list, dict], set]:
    """What the old files hold: ({chat key: record rows}, the page's chats, {(model, key): vector}), and the files
    that could not be placed (a chat file that does not parse, a record of no known chat): those are kept."""
    import hashlib

    from .embeddings import blob_of

    records, chats, stored, unplaced = {}, [], {}, set()
    for path, now in moving.items():
        if path.suffix == ".json":
            try:
                saved = json.loads(now.read_text(encoding="utf-8"))
            except ValueError:
                saved = None
            if not isinstance(saved, dict):
                logger.warning("[Agent] a chat file that does not read was not moved: %s", path.name)
                unplaced.add(path)
                continue
            messages = [m for m in saved.get("messages") or [] if isinstance(m, dict) and isinstance(m.get("seq"), int)]
            chats.append(("main" if path.name == "browser-chat.json" else path.stem, str(saved.get("conversation") or ""),
                          str(saved.get("title") or ""), str(saved.get("at") or ""), int(saved.get("seq") or 0), messages))
        elif path.name == "embeddings.jsonl":
            for line in now.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                    stored[(rec["model"], rec["key"])] = blob_of(rec["vec"])
                except (ValueError, KeyError, TypeError):
                    continue
    # A record's file is named by a hash of its chat key: the key is one of the page's chats, a chat the recent
    # turns or the memory bookkeeping name (rooms among them), or the speaker of one of its own lines.
    keys = {f"private:browser:{chat[1]}" for chat in chats}
    for name, fields in (("recent.json", ("convs",)), ("memory_state.json", ("per_conv", "lost_lines"))):
        try:
            saved = json.loads((runtime / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        keys |= {str(k) for f in fields if isinstance(saved, dict) and isinstance(saved.get(f), dict) for k in saved[f]}
    for path, now in moving.items():
        if path.suffix == ".jsonl" and path.name != "embeddings.jsonl":
            rows = _log_rows(now)
            names = keys | {"private:" + r["user_id"] for r in rows if r["user_id"]}
            conv = next((k for k in names if hashlib.sha256(k.encode("utf-8")).hexdigest()[:32] == path.stem), None)
            if conv is None:
                logger.warning("[Agent] a chat record of no known chat was not moved: %s", path.name)
                unplaced.add(path)
            else:
                records[conv] = rows
    return (records, chats, stored), unplaced


def _move(db: Db, key: str, old: list[Path], read: Callable, write: Callable) -> None:
    """Files `old` of a home from before the database, moved once (the meta row `key` says it was): renamed to
    *.moving, `read(moving)` gives (what they hold, the files that could not be placed), `write(conn, held)` puts it
    in and reads it back (sqlite3.DatabaseError when it does not), in one transaction; then the files are deleted.
    A move that does not read back is undone and its files keep their names. A file that could not be placed is
    never deleted: it stays beside the database as *.unmoved. A file of the kind that turns up after the move is
    deleted, not moved over what the home has since."""
    old = [path for path in old if path.is_file()]
    if old and not db.read("SELECT 1 FROM meta WHERE key = ?", (key,)):
        moving = {path: path.rename(path.with_name(path.name + ".moving")) for path in old}
        held, unplaced = read(moving)
        try:
            with db.write() as conn:
                write(conn, held)
                conn.execute("INSERT INTO meta VALUES (?, '1')", (key,))
        except sqlite3.Error as error:
            logger.error("[Agent] the old files did not move into %s (%s); they stay as they were", db.path, error)
            for path, now in moving.items():
                now.replace(path)
            return
        # The whole move went through the write-ahead log: cut it back now.
        db.scrubbed()
        for path in unplaced:
            moving.pop(path).replace(path.with_name(path.name + ".unmoved"))
        old = list(moving.values())
    for path in old:
        path.unlink()


def _write_chats(conn: sqlite3.Connection, held: tuple) -> None:
    from .chatlog import row_args

    records, chats, stored = held
    for conv, rows in records.items():
        conn.executemany("INSERT OR REPLACE INTO chatlog VALUES (?,?,?,?,?,?,?,?,?,?)", [row_args(conv, r) for r in rows])
    for ident, conversation, title, when, seq, messages in chats:
        conn.execute("INSERT OR REPLACE INTO page_chats VALUES (?,?,?,?,?)", (ident, conversation, title, when, seq))
        conn.executemany("INSERT OR REPLACE INTO page_messages(chat, seq, body) VALUES (?,?,?)",
                         [(ident, m["seq"], json.dumps(m, ensure_ascii=False)) for m in messages])
    conn.executemany("INSERT OR REPLACE INTO embeddings VALUES (?,?,?)",
                     [(model, key, vec) for (model, key), vec in stored.items()])
    # Read back: every row moved is in the database.
    count = lambda sql, arg: conn.execute(sql, arg).fetchone()[0]  # noqa: E731
    if not (all(count("SELECT count(*) FROM chatlog WHERE conv = ?", (conv,)) == len(rows)
                for conv, rows in records.items())
            and all(count("SELECT count(*) FROM page_messages WHERE chat = ?", (chat[0],)) == len(chat[5])
                    for chat in chats)
            and count("SELECT count(*) FROM embeddings", ()) >= len(stored)):
        raise sqlite3.DatabaseError("the moved rows do not read back")
    logger.info("[Agent] moved %d chat records, %d page chats and %d vectors into %s",
                len(records), len(chats), len(stored), FILE)


# The finds' files: the library, the feed's events and topics, the showings, the pages read in the app's
# browser, the sources' state and the person's own feeds, and the situation.
FINDS = ("library/items.jsonl", "feed.json", "shown.jsonl", "desktop/browsed.jsonl", "sources_taste.json",
         "sources.json", "situation.json")


def _lines(path: Path) -> list[dict]:
    """The lines of a JSON-lines file that read as objects; a torn or damaged line is passed over, as the
    file's readers passed it over."""
    found = []
    for raw in path.read_bytes().split(b"\n"):
        try:
            row = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(row, dict):
            found.append(row)
    return found


def _read_finds(runtime: Path, moving: dict[Path, Path]) -> tuple[dict[str, list[tuple]], set[Path]]:
    """{table: rows} from the finds' files, and the files that could not be placed: a whole-object file that does
    not read as one."""
    from dataclasses import fields

    from . import library, situation
    from .sources import weights

    rows: dict[str, list[tuple]] = {}
    unplaced = set()
    by_name = {path.relative_to(runtime).as_posix(): (path, now) for path, now in moving.items()}
    known = {f.name for f in fields(library.Item)}

    def whole(name: str) -> Optional[dict]:
        path, now = by_name[name]
        try:
            saved = json.loads(now.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, ValueError):
            saved = None
        if not isinstance(saved, dict):
            logger.warning("[Agent] a file that does not read was not moved: %s", path.name)
            unplaced.add(path)
            return None
        return saved

    if "library/items.jsonl" in by_name:
        items = {}
        for row in _lines(by_name["library/items.jsonl"][1]):
            if row.get("id") and row.get("url"):
                item = library.Item(**{k: v for k, v in row.items() if k in known})
                items[item.id] = library.row(item)
        rows["library"] = list(items.values())
    if "feed.json" in by_name and (saved := whole("feed.json")) is not None:
        events = saved.get("events") if isinstance(saved.get("events"), list) else []
        # An event of five, from before events kept the model's snapshot, gets an empty one.
        rows["feed_events"] = [(None, *e[:5], json.dumps(e[5] if len(e) == 6 else {}, ensure_ascii=False))
                               for e in events if isinstance(e, list)
                               and (len(e) == 5 or len(e) == 6 and isinstance(e[5], dict))]
        topics = saved.get("topics") if isinstance(saved.get("topics"), list) else []
        rows["feed_topics"] = [(None, t) for t in topics if isinstance(t, str)]
    if "shown.jsonl" in by_name:
        rows["shown"] = [(None, r.pop("at"), r.pop("id"), bool(r.pop("gated", False)), json.dumps(r, ensure_ascii=False))
                         for r in _lines(by_name["shown.jsonl"][1]) if "at" in r and "id" in r]
    if "desktop/browsed.jsonl" in by_name:
        rows["browsed"] = [(None, float(r.get("at") or 0), r["url"], json.dumps(r, ensure_ascii=False))
                           for r in _lines(by_name["desktop/browsed.jsonl"][1])
                           if str(r.get("url", "")).startswith(("http://", "https://"))]
    if "sources_taste.json" in by_name and (saved := whole("sources_taste.json")) is not None:
        state = saved.get("sources") if isinstance(saved.get("sources"), dict) else {}
        rows["source_state"] = [(str(key), *({**weights._blank(), **{f: v for f, v in row.items() if f in weights.FIELDS}}[f]
                                             for f in weights.FIELDS))
                                for key, row in state.items() if isinstance(row, dict)]
    if "sources.json" in by_name and (saved := whole("sources.json")) is not None:
        own = {e["key"]: e for e in saved.get("own") or [] if isinstance(e, dict) and e.get("key") and e.get("url")}
        rows["own_feeds"] = [(key, n, json.dumps(e, ensure_ascii=False)) for n, (key, e) in enumerate(own.items())]
        last = saved.get("last") if isinstance(saved.get("last"), dict) else {}
        rows["source_reads"] = [(key, int(r.get("at") or 0), int(r.get("count") or 0), str(r.get("error") or ""))
                                for key, r in last.items() if isinstance(r, dict)]
    if "situation.json" in by_name and (saved := whole("situation.json")) is not None:
        factors = saved.get("factors") if isinstance(saved.get("factors"), dict) else {}
        rows["situation"] = [(name, json.dumps(row, ensure_ascii=False, sort_keys=True)) for name, row in factors.items()
                             if name in situation.NAMES and isinstance(row, dict) and isinstance(row.get("by"), dict)]
        if saved.get("probed"):
            rows["situation"].append(("probed", json.dumps(float(saved["probed"]))))
        hosts = saved.get("hosts") if isinstance(saved.get("hosts"), dict) else {}
        rows["hosts"] = [(site, *({**situation._blank_host(), **row}[f] for f in situation.HOST_FIELDS))
                         for site, row in hosts.items() if isinstance(row, dict)]
    return rows, unplaced


def _write_finds(conn: sqlite3.Connection, rows: dict[str, list[tuple]]) -> None:
    for table, found in rows.items():
        before = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        if found:
            conn.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(found[0]))})", found)
        # Read back: every row moved is in the table.
        if conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] != before + len(found):
            raise sqlite3.DatabaseError(f"the moved rows of {table} do not read back")
    logger.info("[Agent] moved the finds into %s: %s", FILE, ", ".join(f"{len(v)} {k}" for k, v in rows.items()))


def move_in(db: Db, runtime: Path) -> None:
    """A home's files from before the database, each kind once (`_move`): the chat record (log/*.jsonl), the page's
    chats (desktop/browser-chat.json, desktop/chats/*.json) and the embedding cache (embeddings.jsonl); then the
    finds (FINDS)."""
    desktop = runtime / "desktop"
    # A move cut short: its files are taken as they were.
    for folder in (runtime, runtime / "log", desktop, desktop / "chats", runtime / "library"):
        for moving in folder.glob("*.moving"):
            moving.replace(moving.with_name(moving.name[:-len(".moving")]))
    _move(db, "moved", _old_files(runtime), lambda moving: _read_old(runtime, moving), _write_chats)
    for path in (runtime / "log").glob(".*.jsonl.append.lock"):
        path.unlink()
    for folder in (runtime / "log", desktop / "chats"):
        with contextlib.suppress(OSError):
            folder.rmdir()
    _move(db, "moved_finds", [runtime / name for name in FINDS], lambda moving: _read_finds(runtime, moving),
          _write_finds)

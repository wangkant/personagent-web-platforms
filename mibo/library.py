"""What the character has seen on its walks, kept on this computer.

One row per thing in the runtime database's `library` table (db.py), its cover
(made small) in `<runtime>/library/pictures/`, and `outing.json` saying whether
it is out now. The walk writes here; the home page and the days read from here.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import db
from .storage import atomic_write_text

KINDS = ("video", "article", "post", "page")
# What became of an item: the owner's doing, never the character's.
NEW, KEPT, DISMISSED = "", "kept", "dismissed"
CHAT = "chat"
# Read by the person themselves in the app's own browser (browsed.py), not by the character.
BROWSED = "browsed"
TEXT_MAX = 20_000
EXCERPT_MAX = 400
PICTURE_MAX_BYTES = 200_000
ITEMS_MAX = 2000
# A thing the person opened, liked, played or read this recently is kept past the limit while older ones can go.
REACTED_DAYS = 45
# A walk's or a source's find nobody kept or opened retires this long after it came: the feed would not bring it now.
STALE_DAYS = 30
# What the feed counts as the person taking a thing up; a showing or a skip is not.
ENGAGED = ("open", "like", "play", "read")


@dataclass
class Item:
    """One thing read or watched. `embed` is the site's own player address,
    set only when the page offered one and its host is allowed; `why` is the
    character's sentence; `fits` says it kept this for its owner."""
    url: str
    site: str = ""
    kind: str = "page"
    title: str = ""
    author: str = ""
    published: str = ""
    length: Optional[int] = None        # seconds, for a video
    description: str = ""
    text: str = ""
    embed: str = ""
    picture: str = ""                   # file name under pictures/, or ""
    seen_at: int = 0
    outing: str = ""
    why: str = ""
    fits: bool = False
    told: bool = False
    state: str = NEW
    id: str = ""
    source: str = ""                    # the public source that brought it, "" for a walk's find
    # The character's own judgement: love 0-3 (-1 before it was asked), a few short English tags,
    # and whether it was asked at all, so "not asked" and "asked, said no" differ.
    love: int = -1
    tags: list = field(default_factory=list)
    judged: bool = False
    # CHAT when the character read it during a chat, BROWSED when the person read it in the app's browser;
    # "" for a walk's find or a source's.
    via: str = ""
    # The words from a chat that sent a walk looking for it, "" when it went on its own.
    wonder: str = ""

    def card(self) -> dict:
        """What the page draws: public facts, no file paths."""
        excerpt = " ".join((self.description or self.text).split())[:EXCERPT_MAX]
        return {"kind": self.kind, "title": self.title, "site": self.site, "author": self.author,
                "length": self.length, "picture": f"/api/library/picture/{self.id}" if self.picture else "",
                "embed": self.embed, "url": self.url, "excerpt": excerpt, "readable": bool(self.text.strip())}


def item_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


@dataclass
class Outing:
    """Whether the character is out, and how its last walk ended."""
    state: str = "home"                 # home | out
    site: str = ""
    since: int = 0
    last_end: int = 0
    last_count: int = 0
    day: str = ""
    today: int = 0                      # walks started today
    sites: list = field(default_factory=list)   # every site ever visited, by name


def row(item: Item) -> tuple:
    """The library row of `item`: the columns a query picks rows by, and the whole thing."""
    return item.id, item.seen_at, item.state, item.via, item.source, json.dumps(asdict(item), ensure_ascii=False)


def _item(saved: str) -> Item:
    return Item(**json.loads(saved))


# Library writes in this process, per database: what `version` counts.
_writes: dict[Path, int] = {}


class Library:
    def __init__(self, runtime: Path):
        self.root = Path(runtime) / "library"
        self.pictures = self.root / "pictures"
        self.outing_path = self.root / "outing.json"
        self.db = db.at(runtime)

    # -- items

    def add(self, item: Item) -> Item:
        """Keep `item`; one already there for the same address is replaced, with the owner's choice kept."""
        item.id = item.id or item_id(item.url)
        item.kind = item.kind if item.kind in KINDS else "page"
        item.text = item.text[:TEXT_MAX]
        item.seen_at = item.seen_at or int(time.time())
        with self.db.write() as conn:
            before = self.get(item.id)
            if before is not None:
                item.state, item.told = before.state, before.told or item.told
                if not item.judged:
                    item.judged, item.love, item.tags = before.judged, before.love, before.tags
            conn.execute("INSERT OR REPLACE INTO library VALUES (?,?,?,?,?,?)", row(item))
            self._wrote()
        return item

    def get(self, ident: str) -> Optional[Item]:
        found = self.db.read("SELECT item FROM library WHERE id = ?", (str(ident),))
        return _item(found[0][0]) if found else None

    def items(self, *, newest_first: bool = True, since: float = 0) -> list[Item]:
        """Every thing, or those found at `since` or later."""
        order = "DESC" if newest_first else "ASC"
        found = self.db.read(f"SELECT item FROM library WHERE seen_at >= ? ORDER BY seen_at {order}, id {order}", (since,))
        # Every thing in one parse: far quicker than one parse a row.
        return [Item(**saved) for saved in json.loads("[" + ",".join(saved for saved, in found) + "]")]

    def states(self) -> dict[str, tuple[str, int]]:
        """{id: (state, seen_at)} of every thing, without reading the things themselves."""
        found = self.db.read("SELECT id, state, seen_at FROM library")
        return {ident: (state, seen_at) for ident, state, seen_at in found}

    def sources(self) -> dict[str, str]:
        """{id: the source that brought it} of every thing ("" for a walk's find)."""
        return dict(self.db.read("SELECT id, source FROM library"))

    def version(self) -> tuple[int, int]:
        """Changes when the library does, in this process or another: for a reader that keeps the things it read."""
        return _writes.get(self.db.path, 0), self.db.version()[1]

    def _wrote(self) -> None:
        _writes[self.db.path] = _writes.get(self.db.path, 0) + 1

    def change(self, ident: str, **values) -> Optional[Item]:
        # Read and written in one transaction, so two changes to one thing at once both stay.
        with self.db.write() as conn:
            item = self.get(ident)
            if item is None:
                return None
            for name, value in values.items():
                if name in ("state", "told", "why", "fits", "love", "tags", "judged", "picture"):
                    setattr(item, name, value)
            conn.execute("INSERT OR REPLACE INTO library VALUES (?,?,?,?,?,?)", row(item))
            self._wrote()
        return item

    def seen(self, url: str) -> bool:
        return bool(self.db.read("SELECT 1 FROM library WHERE id = ?", (item_id(url),)))

    def _gone(self, ids: list[str]) -> None:
        """The things `ids` out of the library, with their pictures."""
        with self.db.write() as conn:
            for n in range(0, len(ids), 500):
                part = ids[n:n + 500]
                marks = ",".join("?" * len(part))
                for saved, in conn.execute(f"SELECT item FROM library WHERE id IN ({marks})", part).fetchall():
                    picture = _item(saved).picture
                    if picture:
                        (self.pictures / picture).unlink(missing_ok=True)
                conn.execute(f"DELETE FROM library WHERE id IN ({marks})", part)
            self._wrote()

    def compact(self) -> None:
        """Retire the stale (a walk's or a source's find older than STALE_DAYS that nobody kept or took up). Past
        the limit the dismissed go first, then the oldest shown and never taken up, then the oldest not taken up
        in REACTED_DAYS, then the oldest of the rest; a kept one never goes."""
        with self.db.write() as conn:
            since = time.time() - REACTED_DAYS * 86400
            marks = ",".join("?" * len(ENGAGED))
            taken = {i for i, in conn.execute(f"SELECT DISTINCT id FROM feed_events WHERE at >= ? AND action IN ({marks})",
                                              (since, *ENGAGED))}
            shown = {i for i, in conn.execute("SELECT DISTINCT id FROM feed_events")}
            rows = conn.execute("SELECT id, seen_at, state, via FROM library ORDER BY seen_at, id").fetchall()
            old = time.time() - STALE_DAYS * 86400
            # The person's own reading has its own keeping (browsed.py).
            stale = [i for i, at, state, via in rows if at < old and state != KEPT and i not in taken and not via]
            retired = set(stale)
            left = [r for r in rows if r[0] not in retired]
            # In the order the limit takes them: each thing by the first rule it falls under, oldest first.
            order = sorted((0 if state == DISMISSED else 1 if i in shown and i not in taken else 2 if i not in taken
                            else 3, n, i) for n, (i, at, state, via) in enumerate(left) if state != KEPT)
            over = [i for _, _, i in order[:max(0, len(left) - ITEMS_MAX)]]
            self._gone(stale + over)

    def drop(self, gone) -> list[str]:
        """Every item `gone(item)` is true of out of the library at once, with its picture, and no byte of them left
        in the database: their ids."""
        with self.db.write():
            dropped = [r.id for r in self.items(newest_first=False) if gone(r)]
            self._gone(dropped)
        if dropped:
            self.db.scrubbed()
        return dropped

    # -- pictures

    def save_picture(self, ident: str, data: bytes, suffix: str = ".jpg") -> str:
        """Keep a cover that is already small; "" when it is too large or empty."""
        if not data or len(data) > PICTURE_MAX_BYTES or suffix not in (".jpg", ".png", ".webp"):
            return ""
        self.pictures.mkdir(parents=True, exist_ok=True)
        name = f"{ident}{suffix}"
        (self.pictures / name).write_bytes(data)
        return name

    def picture_path(self, ident: str) -> Optional[Path]:
        item = self.get(ident)
        if item is None or not item.picture:
            return None
        path = (self.pictures / item.picture).resolve()
        return path if path.is_file() and path.parent == self.pictures.resolve() else None

    # -- the walk

    def outing(self) -> Outing:
        try:
            saved = json.loads(self.outing_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Outing()
        known = set(Outing.__dataclass_fields__)
        return Outing(**{k: v for k, v in saved.items() if k in known}) if isinstance(saved, dict) else Outing()

    def _save_outing(self, outing: Outing) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.outing_path, json.dumps(asdict(outing), ensure_ascii=False))

    def left(self, site: str = "", now: Optional[float] = None) -> Outing:
        now = int(time.time() if now is None else now)
        outing = self.outing()
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if outing.day != day:
            outing.day, outing.today = day, 0
        outing.state, outing.site, outing.since, outing.today = "out", site, now, outing.today + 1
        self._save_outing(outing)
        return outing

    def at(self, site: str) -> None:
        outing = self.outing()
        if outing.state == "out":
            outing.site = site
            if site and site not in outing.sites:
                outing.sites.append(site)
            self._save_outing(outing)

    def returned(self, count: int, sites: Iterable[str] = (), now: Optional[float] = None) -> Outing:
        outing = self.outing()
        outing.state, outing.site = "home", ""
        outing.last_end, outing.last_count = int(time.time() if now is None else now), int(count)
        for site in sites:
            if site and site not in outing.sites:
                outing.sites.append(site)
        self._save_outing(outing)
        return outing

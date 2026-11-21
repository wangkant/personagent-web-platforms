"""The places a chat works on: pages in the app's browser, other programs' windows, files and folders, each by an
id (p1, p2, …) that open gives and look takes (surface.py). A place stays while it is open, across turns of the
same chat, and the turn's context lists the open ones in a line each (turnctx.py), so "这个网页" and "刚才那个窗口"
find their place without a word list.

    Place     {id, kind page|window|file|folder, title, handle (tab id, window handle or path), where (site or
              program), shown (in the middle for the person, or out of sight), opened_by (the turn that opened it)}
    add(conversation, ...)        the place, a known one given again by its handle
    get(conversation, id)         the place, or None
    drop(conversation, id)        a place that closed
    listing(conversation)         its lines for the turn's context
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from . import turnctx

KINDS = ("page", "window", "file", "folder")
# Places one chat keeps at most: the oldest goes first.
KEPT = 12


@dataclass
class Place:
    id: str
    kind: str
    title: str
    handle: object
    where: str = ""
    shown: bool = False
    opened_by: str = ""

    def line(self) -> str:
        """One line for the turn's context: id, kind, title, site or program, in the middle or out of sight."""
        where = f" · {self.where}" if self.where and self.where != self.title else ""
        seen = "in the middle, where they see it" if self.shown else "out of sight"
        return f"{self.id} · {self.kind} · {self.title[:80] or '(untitled)'}{where} · {seen}"


_places: dict[str, list[Place]] = {}
_lock = threading.Lock()


def add(conversation: str, kind: str, handle, title: str = "", where: str = "", shown: bool = False,
        turn: str = "") -> Place:
    """The chat's place for `handle`, made when it is new; a known one takes the newer title and whether it shows."""
    with _lock:
        mine = _places.setdefault(conversation, [])
        place = next((p for p in mine if p.kind == kind and p.handle == handle), None)
        if place is None:
            number = max((int(p.id[1:]) for p in mine), default=0) + 1
            place = Place(f"p{number}", kind, title, handle, where, shown, turn)
            mine.append(place)
            del mine[:-KEPT]
        else:
            place.title, place.where = title or place.title, where or place.where
        if shown:
            # One place is in the middle at a time.
            for other in mine:
                other.shown = other is place
        return place


def get(conversation: str, ident: str) -> Place | None:
    with _lock:
        return next((p for p in _places.get(conversation, []) if p.id == str(ident).strip()), None)


def all_of(conversation: str) -> list[Place]:
    with _lock:
        return list(_places.get(conversation, []))


def drop(conversation: str, ident: str) -> None:
    with _lock:
        _places[conversation] = [p for p in _places.get(conversation, []) if p.id != ident]


def listing(conversation: str) -> list[str]:
    places = all_of(conversation)
    return ["<open_places>\n" +"\n".join(p.line() for p in places) + "\n</open_places>"] if places else []


def _provider(said: str, chat: str) -> list[str]:
    """The open places of the chat whose key `chat` is (prompt.py passes the routing key, dm.chat_key)."""
    from .dm import chat_key
    conversation = next((c for c in list(_places) if chat_key(c)[1] == chat), "")
    return listing(conversation) if conversation else []


turnctx.register(_provider)

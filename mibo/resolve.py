"""What a name the person said opens: a tab already open, a path, a program or a shortcut Start and the Desktop
list, a site the app knows, or a web address, in that order (tool-layer-design.json, open). The fuzzy score only
ranks: when the top two are close, or no match is strong, the candidates go back to the model, which picks.

    score(asked, text)                    how well a word names a text, 0 to 1 (desk/names.py ranks with it too)
    pick(name, near, as_, tabs=..., program=..., folders=...)
                                          the Resolved place, over facts it is given (offline, on any system)
    find(name, near, as_, tabs)           the same on this computer
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

# A score from here on is a match; two this near, neither named exactly, go back to the model.
FOUND = 0.6
CLOSE = 0.03
CANDIDATES = 8
AS = ("program", "file", "page")
NEAR = ("desktop", "downloads", "documents", "pictures", "music", "videos")
# What reads as a site: localhost, an IP, or dotted names ending in letters, a port and a path after it.
_SITE = re.compile(r"(localhost|(\d{1,3}\.){3}\d{1,3}|([^\W_]([\w-]*[^\W_])?\.)+([^\W\d_]{2,63}|xn--[a-z0-9-]+))"
                   r"(:\d{1,5})?(/\S*)?", re.IGNORECASE)
PLACES = Path(__file__).with_name("web") / "places.json"
# Words after a site's name that still mean its front page.
_HOME = re.compile(r"\s*(首页|主页|官网|网站|网页|home ?page|website|site)$", re.IGNORECASE)


def plain(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).casefold() if ch.isalnum() or ch in "+#")


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+|[^\W\d_a-z]+", unicodedata.normalize("NFKC", text).casefold()))


def score(asked: str, text: str) -> float:
    """How well the person's word names `text`, 0 to 1: the same name; each word of it (Word for Microsoft
    Word); its beginning (网易云 for 网易云音乐); the name with its maker's in front (腾讯QQ for QQ); a part of
    it (资源管理器 for 文件资源管理器); or a slip of the hand."""
    a, t = plain(asked), plain(text)
    if not a or not t:
        return 0.0
    if a == t:
        return 1.0
    part = min(1.0, len(a) / len(t))
    if _words(asked) <= _words(text):
        return 0.8 + 0.15 * part
    if len(a) >= 2 and t.startswith(a):
        return 0.75 + 0.15 * part
    if len(t) >= 2 and a.endswith(t):
        return 0.65 + 0.2 * len(t) / len(a)
    if len(a) >= (3 if a.isascii() else 2) and a in t:
        return 0.6 + 0.15 * part
    return difflib.SequenceMatcher(None, a, t).ratio() - 0.25 if len(a) >= 4 else 0.0


@dataclass
class Resolved:
    """kind: tab (target a tab id), window (a window handle), program (a name Start or the Desktop lists, or the
    path of a shortcut in the person's own folders), page (a web address), file or folder (a path), several
    (candidates to pick from) or none."""
    kind: str
    target: object = ""
    title: str = ""
    candidates: list[str] = field(default_factory=list)


@dataclass
class Program:
    """What desk/names.find made of a name: an open window, an entry to start, or the names it could have meant."""
    hwnd: int = 0
    app: str = ""
    names: list[str] = field(default_factory=list)
    several: bool = False


def address(text: str) -> str:
    """The web address `text` is or names as a site (https:// added when it names no scheme); "" for none."""
    text = text.strip()
    parts = urlsplit(text if "://" in text else f"https://{text}")
    if parts.scheme in ("http", "https") and _SITE.fullmatch(parts.netloc + parts.path):
        return parts.geturl()
    return ""


def known_site(name: str) -> tuple[str, str]:
    """(home page, name) of a site the app knows by the name the person used (抖音, b站, Gmail, YouTube), "首页"
    or "官网" after it or not (B站首页)."""
    from .web import signin
    name = _HOME.sub("", name).strip() or name
    row = signin.find(name)
    if row:
        return row["home"], row["name"].get("zh") or row["name"].get("en") or name
    rows = json.loads(PLACES.read_text(encoding="utf-8"))
    best = max(rows, key=lambda r: score(name, r["name"]), default=None)
    return (best["home"], best["name"]) if best and score(name, best["name"]) >= 0.95 else ("", "")


def inside(path: str, folder: str) -> bool:
    """Whether `path` lies in `folder` or under it."""
    path, folder = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return path.startswith(folder.rstrip("\\/") + os.sep)


def on_desktop(path: str, folders: dict) -> bool:
    """Whether `path` lies on the person's Desktop (`folders` maps the profile folders' keys to paths)."""
    return "desktop" in folders and inside(path, folders["desktop"])


def _in_folder(name: str, folder: str) -> list[str]:
    """The entries of `folder` the name means: its exact name, else its name without the extension, else the ones
    it names well."""
    try:
        entries = [e.path for e in os.scandir(folder)]
    except OSError:
        return []
    asked = name.strip().strip('"')
    exact = [p for p in entries if os.path.basename(p).casefold() == asked.casefold()]
    if exact:
        return exact
    stems = [p for p in entries if os.path.splitext(os.path.basename(p))[0].casefold() == asked.casefold()]
    if stems:
        return stems
    ranked = sorted(((score(asked, os.path.basename(p)), p) for p in entries), reverse=True)
    return [p for s, p in ranked if s >= FOUND and s > ranked[0][0] - CLOSE]


def pick(name: str, near: str = "", as_: str = "", *, tabs: list[dict] = (),
         program: Callable[[str], Program] = lambda name: Program(), folders: dict | None = None) -> Resolved:
    """The place `name` means. `tabs` are the app browser's open tabs ({"id", "title", "url"}), `program` finds a
    program or shortcut by name (desk/names.find on Windows), `folders` maps the profile folders' keys to paths."""
    name = " ".join(str(name or "").split())
    folders = folders or {}
    if not name:
        return Resolved("none")
    if as_ in ("", "page") and "://" in name and address(name):
        # A web address in full: the tab showing it, else the page; never a program's name.
        tab = next((t for t in tabs if t.get("url") == address(name)), None)
        return (Resolved("tab", tab["id"], tab.get("title") or "") if tab
                else Resolved("page", address(name), urlsplit(name).hostname or ""))
    if as_ in ("", "page"):
        ranked = sorted(((max(score(name, t.get("title") or ""), score(name, urlsplit(t.get("url") or "").hostname
                                                                        or "")), t) for t in tabs),
                        key=lambda row: -row[0])
        if ranked and ranked[0][0] >= 0.75:
            return Resolved("tab", ranked[0][1]["id"], ranked[0][1].get("title") or "")
    # A path: in full, or in the folder they named.
    base = folders.get(near, near if os.path.isabs(near) else "")
    found: list[str] = []
    if os.path.isabs(os.path.expandvars(os.path.expanduser(name))):
        whole = os.path.expandvars(os.path.expanduser(name))
        found = [whole] if os.path.exists(whole) else []
    elif base and as_ != "page":
        found = _in_folder(name, base)
    if len(found) > 1:
        return Resolved("several", candidates=[os.path.basename(p) for p in found][:CANDIDATES])
    if found:
        path = found[0]
        stem, ext = os.path.splitext(os.path.basename(path))
        if ext.lower() in (".lnk", ".url") and as_ != "file" and on_desktop(path, folders):
            # A shortcut on the person's Desktop opens itself (desk/names.find takes it by its path), as the ones
            # the Desktop lists by name do. One kept anywhere else (Downloads, Documents) never does.
            return Resolved("program", path, stem)
        if ext.lower() in (".lnk", ".url", ".exe") and as_ != "file":
            # A shortcut or a program elsewhere: only what Start and the Desktop list opens, by its name.
            name = stem
        else:
            return Resolved("folder" if os.path.isdir(path) else "file", path, os.path.basename(path))
    if as_ in ("", "program"):
        got = program(name)
        if got.hwnd:
            return Resolved("window", got.hwnd, name)
        if got.app:
            return Resolved("program", got.app, got.app)
        if got.several:
            return Resolved("several", candidates=got.names[:CANDIDATES])
        if as_ == "program":
            return Resolved("none", candidates=got.names[:CANDIDATES])
    if as_ in ("", "page"):
        url = address(name)
        home, title = known_site(name) if not url else ("", "")
        if home or url:
            return Resolved("page", home or url, title or urlsplit(url).hostname or "")
    return Resolved("none")


def profile_folders() -> dict:
    """The profile folders by key (desktop, downloads, …), where this computer has them."""
    from .shell.grants import known_folders
    return {row["key"]: row["path"] for row in known_folders()}


def program(name: str) -> Program:
    """desk/names.find on this computer: the open window or the Start or Desktop entry a name means. Nothing on a
    system without other programs' windows to work in."""
    if sys.platform != "win32":
        return Program()
    from .desk import names
    found = names.find(name)
    return Program(found.hwnd, found.app.name if found.app else "", found.names, found.several)


def find(name: str, near: str = "", as_: str = "", tabs: list[dict] = ()) -> Resolved:
    """`pick` on this computer."""
    return pick(name, near, as_, tabs=tabs, program=program, folders=profile_folders())

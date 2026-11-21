"""What the chat's "+" offers besides pictures and files (static/welcome/plus.js).

GET /api/compose says what can work now: a screenshot (snip.py, on this computer's app window), the page open
in the app's browser and the pages read there lately, what the clipboard holds, operating the app's browser
and other programs, a walk to look something up. The rest are the entries' own: the screenshot taken, the
clipboard read, the recent pages listed, one find brought now, a walk sent out now to look something up.

A lookup joins the walks' one queue (today.wonder) and wakes the walk loop (roam.Roam.request); when that
walk is back, the main chat gets a line on what it read, what it brought and where it could not read
(today.wondered), with the things it brought as cards.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import ctypes
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fastapi import Request

from .. import browsed, today
from ..roam import LIMIT, WALKS_OFF, WEB

ERRORS: frozenset[str] = frozenset({"compose_unavailable", "compose_invalid"})
# The pages offered under "recent", and how long a screenshot may wait for the person's drag or click.
RECENT_SHOWN = 6
SNIP_S = 300
# Clipboard text up to this many characters is offered whole.
CLIP_MAX = 20_000
DWMWA_CLOAK = 13
# The line a lookup's walk gets in the main chat: what it brought, nothing worth keeping, or nothing read.
REPORT = {
    "zh": {"kept": "已查看「{w}」：阅读 {n} 个网页，带回 {k} 条内容。",
           "none": "已查看「{w}」：阅读 {n} 个网页，未找到值得带回的内容{why}。可更换关键词后重试。",
           "unread": "已查看「{w}」：未能读到相关网页{why}。可更换关键词后重试。"},
    "en": {"kept": "Looked into “{w}”: read {n} pages and brought back {k}.",
           "none": "Looked into “{w}”: read {n} pages, nothing worth bringing back{why}. Try other words.",
           "unread": "Looked into “{w}”: no page on it could be read{why}. Try other words."},
}
# Why a place could not be read, after its name (roam.Roam.walk's `missed`); any other reason is "could not open".
WHY = {
    "zh": {"sign_in": "需要登录", "verification": "需要人机验证", "paywall": "需要付费订阅", "robots": "不允许程序访问",
           "none": "没有相关结果", "": "无法打开"},
    "en": {"sign_in": "asks to sign in", "verification": "asks for a human check", "paywall": "is behind a paywall",
           "robots": "does not allow programs", "none": "had nothing on it", "": "could not be opened"},
}
WEB_NAME = {"zh": "网页搜索", "en": "the web search"}
# Reasons named in one report, each with at most this many places.
WHY_SHOWN, PLACES_SHOWN = 3, 3


def _own_windows() -> list[int]:
    """This process's visible top-level windows: the app window, the pet, the browser pane."""
    user32 = ctypes.windll.user32
    found: list[int] = []
    pid = ctypes.c_ulong()
    enum = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def each(hwnd, _):
        user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        if pid.value == os.getpid() and user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
            found.append(hwnd)
        return True
    user32.EnumWindows(enum(each), 0)
    return found


def _cloak(windows: list[int], on: bool) -> None:
    flag = ctypes.c_int(1 if on else 0)
    for hwnd in windows:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_CLOAK, ctypes.byref(flag), 4)


_ready: dict = {"snip": None}


def _snipper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "mibo.snip", "--wait"], stdin=subprocess.PIPE,
                            text=True)


def warm() -> None:
    """Starts the screenshot's process when the menu opens, so it comes up at once when picked."""
    if _ready["snip"] is None or _ready["snip"].poll() is not None:
        _ready["snip"] = _snipper()


def snip(lang: str) -> bytes | None:
    """The person's screenshot as PNG, with the app's own windows out of the way meanwhile; None when cancelled."""
    user32 = ctypes.windll.user32
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    old = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    box = [user32.GetSystemMetrics(n) for n in (76, 77, 78, 79)]
    user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))
    own = _own_windows()
    folder = tempfile.mkdtemp(prefix="mibo-snip-")
    out = Path(folder) / "snip.png"
    process, _ready["snip"] = _ready["snip"], None
    if process is None or process.poll() is not None:
        process = _snipper()
    _cloak(own, True)
    try:
        # The screen is redrawn without them before it is pictured.
        time.sleep(0.25)
        process.stdin.write("\t".join([str(out), *map(str, box), lang]) + "\n")
        process.stdin.close()
        return out.read_bytes() if process.wait(SNIP_S) == 0 and out.exists() else None
    except subprocess.TimeoutExpired:
        process.kill()
        return None
    finally:
        _cloak(own, False)
        if own:
            user32.SetForegroundWindow(ctypes.c_void_p(own[0]))
        with contextlib.suppress(OSError):
            out.unlink()
            os.rmdir(folder)


def clipboard_kind() -> str:
    """"text", "picture" or "" for what the clipboard holds, without reading it."""
    user32 = ctypes.windll.user32
    if user32.IsClipboardFormatAvailable(13):
        return "text"
    return "picture" if user32.IsClipboardFormatAvailable(8) or user32.IsClipboardFormatAvailable(2) else ""


def clipboard_text() -> str:
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(13)
        pointer = kernel32.GlobalLock(handle) if handle else None
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clipboard_picture() -> bytes | None:
    from io import BytesIO

    from PIL import Image, ImageGrab
    picture = ImageGrab.grabclipboard()
    if not isinstance(picture, Image.Image):
        return None
    out = BytesIO()
    picture.convert("RGBA" if picture.mode in ("RGBA", "LA", "P") else "RGB").save(out, "PNG")
    return out.getvalue()


def open_page(window, desktop: Path) -> dict | None:
    """The page open in the app's browser, as its bar shows it: {"url", "title"}; None for none, while
    private, a sign-in page, or with sharing off."""
    pane = getattr(window, "pane", None)
    if pane is None or pane.private or not browsed.shared(desktop):
        return None
    url = pane.address()
    if not browsed.web(url) or browsed.secret(url):
        return None
    reading = getattr(pane, "_reading", None) or {}
    title = reading.get("title") if reading.get("url") == url else ""
    return {"url": url, "title": str(title or "")[:200]}


def recent_pages(desktop: Path) -> list[dict]:
    seen, rows = set(), []
    for row in browsed.recent(desktop, 40):
        if row["url"] in seen or browsed.secret(row["url"]):
            continue
        seen.add(row["url"])
        rows.append({"url": row["url"], "title": str(row.get("title") or "")[:200], "host": row.get("host") or ""})
        if len(rows) >= RECENT_SHOWN:
            break
    return rows


def report(row: dict, lang: str) -> str:
    """The main chat's line on a lookup's walk (a today.wondered row)."""
    places: dict[str, list[str]] = {}
    for site, why in row["missed"]:
        names = places.setdefault(WHY[lang].get(why, WHY[lang][""]), [])
        if site not in names:
            names.append(site)
    join, space = ("、", "") if lang == "zh" else (", ", " ")
    parts = [join.join(WEB_NAME[lang] if site == WEB else site for site in names[:PLACES_SHOWN]) + space + said
             for said, names in list(places.items())[:WHY_SHOWN]]
    why = (f"（{'；'.join(parts)}）" if lang == "zh" else f" ({'; '.join(parts)})") if parts else ""
    shape = "kept" if row["kept"] else "none" if row["read"] else "unread"
    return REPORT[lang][shape].format(w=row["w"], n=row["read"], k=row["kept"], why=why)


def mount(app, workspace, body) -> None:
    desktop = Path(workspace.home) / "runtime" / "desktop"
    reports: set = set()

    def lang() -> str:
        return "zh" if str(workspace.config().get("AGENT_LANG") or "").startswith("zh") else "en"

    def roam():
        return getattr(workspace.character.agent, "roam", None)

    async def tell(row: dict) -> None:
        """A lookup's walk is back: its line in the main chat, then what it brought as cards (no errands)."""
        from . import feed
        library = workspace._library()
        items = [item for item in (library.get(ident) for ident in row["ids"])
                 if item is not None and not feed.errand(item.url)]
        async with workspace._chat_lock:
            workspace._append([{"role": "assistant", "text": report(row, lang()), "remark": True}])
            workspace._save_chat()
            if items:
                await workspace._say(items, {})

    def back(row: dict) -> None:
        task = asyncio.get_running_loop().create_task(tell(row))
        reports.add(task)
        task.add_done_callback(reports.discard)
    today.report_to(back)

    def own_window(request: Request):
        return getattr(app.state, "window", None)

    def computer() -> bool:
        from ..acting import Modes
        return Modes(desktop).mode() != "off"

    def facts(request: Request, walker) -> dict:
        window = own_window(request)
        windows = os.name == "nt" and window is not None
        return {
            "capture": windows and importlib.util.find_spec("tkinter") is not None,
            "page": open_page(window, desktop) if windows else None,
            "recent": windows and bool(browsed.recent(desktop, 1)),
            "clipboard": clipboard_kind() if os.name == "nt" else "",
            "browser": windows and getattr(window, "pane", None) is not None,
            "computer": computer(),
            # A lookup is offered only where a walk can leave for it; else the entry says why not (walks
            # switched off, no browser or no driver).
            "walks": walker is not None and walker.settings.roam_enabled and not walker.available(),
            "walkWhy": (walker.available() or ("" if walker.settings.roam_enabled else WALKS_OFF)) if walker else "",
        }

    @app.get("/api/compose")
    async def compose_facts(request: Request):
        found = await asyncio.to_thread(facts, request, roam())
        # Asked as the menu opens: the screenshot gets ready meanwhile.
        if found["capture"] and request.query_params.get("warm"):
            await asyncio.to_thread(warm)
        return found

    @app.post("/api/compose/capture")
    async def compose_capture(request: Request):
        payload = await body(request)
        if own_window(request) is None or os.name != "nt":
            raise ValueError("compose_unavailable")
        lang = "zh" if str(payload.get("lang") or "").startswith("zh") else "en"
        data = await asyncio.to_thread(snip, lang)
        return {"picture": base64.b64encode(data).decode() if data else ""}

    @app.get("/api/compose/clipboard")
    async def compose_clipboard(request: Request):
        if os.name != "nt":
            raise ValueError("compose_unavailable")
        kind = await asyncio.to_thread(clipboard_kind)
        if kind == "text":
            return {"kind": "text", "text": (await asyncio.to_thread(clipboard_text))[:CLIP_MAX]}
        data = await asyncio.to_thread(clipboard_picture) if kind == "picture" else None
        return {"kind": "picture", "picture": base64.b64encode(data).decode()} if data else {"kind": ""}

    @app.get("/api/compose/recent")
    async def compose_recent(request: Request):
        if own_window(request) is None:
            raise ValueError("compose_unavailable")
        return {"pages": await asyncio.to_thread(recent_pages, desktop)}

    @app.post("/api/compose/wonder")
    async def compose_wonder(request: Request):
        """Words to look up now: queued for the walk the loop sends out at once, or, with today's walks
        all taken, the first walk of tomorrow (`later`)."""
        words = (await body(request)).get("words")
        walker = roam()
        if not isinstance(words, str) or not words.strip():
            raise ValueError("compose_invalid")
        if walker is None:
            raise ValueError("compose_unavailable")
        words = today.wonder(walker.library.root.parent, words, time.time(), asked=True)
        return {"words": words, "later": walker.request() == LIMIT}

    @app.post("/api/compose/recommend")
    async def compose_recommend(request: Request):
        """One find into the main chat now, as the pet's menu brings one to its card: the chooser's next."""
        await body(request)
        async with workspace._chat_lock:
            chosen = await workspace.chooser.take("chat")
            if chosen is None:
                return {"count": 0}
            shaped, item = chosen
            await asyncio.to_thread(workspace.pace.shown, "chat", item.id)
            return await workspace._say([item], {item.id: shaped})

"""The app's own browser on Windows: a link from the app, a chat card or the
pet's card opens inside the app window, in a pane with the conversation beside it.

The app page draws the pane's bar (browse.js) and keeps the space under it;
here a WebView2 of its own lies over that space, a child of the app window's
form that goes straight to the site. It is a place apart from the app page: no
bridge to Python, nothing of the page's token, only http and https, and
cookies and storage in a WebView2 folder of its own that stays between runs,
so the person stays signed in. Minimizing it or going to Settings only hides the pane: the page goes on, with its sound and its history, one press away.
Closing it ends its WebView2 processes: nothing of it runs while no page is open.
While the pane is in front the window is as wide as a site and the chat beside it
want, where the screen allows (`_room`). The page open last, until the pane is
closed, each site's own zoom (outside private mode) and the size the person gave
the window while a site was in front are kept in kept.json beside that folder.

The pane has tabs (the strip is browsetabs.js): one WebView2 each, on the same
folder and so the same browser processes. A site's new tab or popup opens as a
tab of its own that the site keeps hold of (window.opener), so a sign-in popup
hands its answer back and closes; the bar's private button opens an InPrivate
tab beside the others. Only the tab in front is shown, timed and operated.

The bar's ··· menu (browsemenu.js) finds in the page and prints it with the browser's own find bar and print
preview, zooms, and lists the downloads (open, show in folder), which go to the download folder. The page's
right-click menu adds 问 Mibo over a selection (the text goes into the chat's box as a quote, for the person to
send or not) and, on a link, a new tab or the system browser.

What a site asks of the person is said on a strip under the bar (browsestrip.js), for the tab in front: the camera,
the microphone, the location and the rest (本次允许, 始终允许, 不允许, as browsers ask), a link to a program of this
computer (mailto:, weixin:), which is never started by the site, and a popup the page opened by itself, which was
stopped. A tab behind keeps what it asks until it comes to the front. While the character operates nothing is asked
and everything is refused. Only a refusal is saved in the profile (the browser then refuses by itself); an
allowance is kept here, so neither a run nor the Reader is ever given one.

Every page is timed while the window is in front, shown and in use. When it is
left it goes to `visit` (browsed.py keeps the row; a find's time also goes to
the recommender), unless its tab is private or the page is a sign-in or
payment page.

`Reader` reads one address with the person's session, out of sight: a second
WebView2 on the same folder, made for the read and gone after.
"""
from __future__ import annotations

import contextlib
import ctypes
import json
import math
import os
import re
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from . import browsed
from .storage import atomic_write_text

# Reading: no input for this long and the time stops counting.
IDLE_S = 60
# How often a page in front checks for that, and for a password field come late; nothing ticks otherwise.
TICK_MS = 20000
# Shorter than this is a glance, not a reading.
READ_MIN_S = 2
# The share of its screen's width the window grows to by itself when a page comes to the front, and the shape
# (width over height) it then takes: a window widened for a page is made taller with it.
ROOM_SHARE = 0.75
ROOM_SHAPE = 1.6
# The page's own canvas (app.css), shown before the site draws: light, dark.
CANVAS = ("#fcfcfd", "#202022")
# The browser's own language, fixed when it first starts (see language()).
_LANGUAGE: list[str] = []


def language() -> str:
    """The language of the browser's own prompts (Save password?): the app's as the browser first starts, not the
    system's. Every WebView2 on the pane's folder asks for this same one, or the later ones fail to start."""
    if not _LANGUAGE:
        from . import i18n
        code = i18n.lang()
        _LANGUAGE.append({"zh": "zh-CN"}.get(code, code))
    return _LANGUAGE[0]
ACTIONS = ("back", "forward", "reload", "stop", "system", "close", "private", "hide", "return", "mute", "loaded",
           "op_stop", "op_take", "fit", "tab_new", "tab_close", "tab_select", "tab_reopen", "tab_next", "tab_prev",
           "find", "print", "zoom_in", "zoom_out", "zoom_reset", "menu", "dl_open", "dl_folder",
           "allow_once", "allow_always", "refuse")
# The permissions the strip asks for, by WebView2's name. The rest (notifications, sensors, autoplay, kinds not
# known) are refused without a word.
ASKED = {"Camera": "camera", "Microphone": "microphone", "Geolocation": "geolocation", "ClipboardRead": "clipboard",
         "MultipleAutomaticDownloads": "downloads", "FileReadWrite": "files", "LocalFonts": "fonts",
         "MidiSystemExclusiveMessages": "midi", "WindowManagement": "windows"}
# Schemes that are the browser's own, or that Chromium never hands to a program: refused without a word. Any other
# scheme that is not the web is a program's (mailto:, weixin:), named on the strip.
INSIDE = ("about", "blob", "chrome", "data", "devtools", "edge", "file", "filesystem", "javascript", "view-source",
          "afp", "disk", "disks", "hcp", "ie.http", "ms-help", "nntp", "res", "shell", "vbscript", "vnd.ms.radio")
# Tabs closed that Ctrl+Shift+T can bring back, newest last (never a private one).
CLOSED_KEPT = 10
# The browser's own zoom steps (Chromium's), for the menu's − and +.
ZOOM_STEPS = (0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 5.0)
# The downloads the bar's menu lists, newest first (those of private tabs only while the app runs); how often a
# download under way tells its progress.
DOWNLOADS_KEPT = 5
DOWNLOAD_TELL_S = 0.5
# A selection 问 Mibo puts in the chat's box, at most this long.
ASK_MAX = 2000
# Beside the pane's WebView2 folder: the page open last ({"url", "title"}), each site's own zoom, the window's
# size while a site is in front, once the person set one ([wide, high] in px at 100 %), the downloads, the site
# permissions allowed always ({origin: kinds}), and whether sites are fitted to the pane's width.
KEPT = "kept.json"
# Fit to width (on until the ··· menu's switch turns it off, as mobile browsers lay a desktop site out at a desktop
# width and show it whole): a pane narrower than this many CSS px, what a site laid out for a desktop wants
# (browse.js PAGE_WIDE), zooms the site out until its layout gets this width, so it does not scroll sideways, down
# to FIT_MIN, below which text stops being easy to read and the site lays out narrower instead.
FIT_WIDTH = 1024
FIT_MIN = 0.67
# What a page's facts keep of its text when it is read off as the person reads it (a read for the character
# hands over all it read), and a list's rows.
TEXT_MAX = 6000
ROWS_MAX = 40
# Reading out of sight: how long a page may take.
READ_WITHIN_S = 25.0
# A feed read in the background (Feeds): how often it is looked at, how often one site's is read, how many reads
# a day, how many of its rows are kept, and the walls that turn it off.
FEED_LOOK_S = 60.0
FEED_EVERY_S = 30 * 60.0
FEED_DAY_MAX = 24
FEED_ROWS = 20
FEED_STOPS = ("verification", "sign_in", "sign_in_overlay", "robots")
SECRET_SCRIPT = "!!document.querySelector('input[type=password]')"
# A DevTools call to the page: how long it may take. While the character operates, how often the person's own
# input in the pane is looked for (it pauses the run), and how long a page may take to come up for it.
CDP_S = 20.0
WATCH_MS = 250
OPEN_WITHIN_S = 20.0
# A page the character opens out of sight: laid out at this size behind the app's own page, where nothing of it
# shows. Every view on the pane's folder runs with Playwright's own switches for pages not in front, so a page
# out of sight keeps its timers and drawing (the views of one folder share one browser and must ask alike).
ASIDE = (1280, 800)
BROWSER_ARGS = ("--disable-background-timer-throttling --disable-renderer-backgrounding "
                "--disable-backgrounding-occluded-windows")
VK_ESCAPE = 0x1B
# The pointer's buttons (left, right, middle, the two side buttons) among the virtual keys.
POINTER_BUTTONS = {0x01, 0x02, 0x04, 0x05, 0x06}
# The steps of a run the column beside the page lists (browse.js), the ends after which the page is the person's
# until they say to go on, and for how long a run going on from one of those keeps its steps.
STEPS_KEPT = 40
THEIRS = ("handover", "asked", "paused", "cap", "waiting")
GO_ON_S = 30 * 60.0


def _idle_s() -> float:
    """How long since the last key or pointer input anywhere on this computer."""
    from ctypes import wintypes

    class LastInput(ctypes.Structure):
        _fields_ = [("size", wintypes.UINT), ("time", wintypes.DWORD)]

    info = LastInput()
    info.size = ctypes.sizeof(LastInput)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return ((ctypes.windll.kernel32.GetTickCount() - info.time) & 0xFFFFFFFF) / 1000


def _last_input_tick() -> int:
    """When Windows last saw a key or pointer input (GetLastInputInfo), in its own ticks."""
    from ctypes import wintypes

    class LastInput(ctypes.Structure):
        _fields_ = [("size", wintypes.UINT), ("time", wintypes.DWORD)]

    info = LastInput()
    info.size = ctypes.sizeof(LastInput)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return int(info.time)


def _dark() -> bool:
    import winreg
    with contextlib.suppress(OSError):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    return False


def web_address(address: str) -> bool:
    return urlsplit(str(address)).scheme in ("http", "https")


def origin_of(address: str) -> str:
    """The site a permission is for, as the browser's profile writes it: "https://host[:port]/"."""
    parts = urlsplit(str(address))
    return f"{parts.scheme}://{parts.netloc}/" if parts.netloc else ""


def handed(status: str, note: str) -> str:
    """Why a run handed the page to the person, for the column to say what they should do: "secret:<field>",
    a wall's kind (verification, sign_in, ...), "drawn" or "stuck"; "" for any other end."""
    if status != "handover":
        return ""
    secret = re.search(r"needs their own (.+?) first", note)
    if secret:
        return f"secret:{secret.group(1)[:40]}"
    wall = re.search(r"shows a (\w+) wall", note)
    if wall:
        return wall.group(1)
    return "drawn" if "almost nothing to work with" in note or "drawn" in note else "stuck"


def on_page(x: float, y: float, wide: float, high: float, cuts: list, scale: float) -> bool:
    """Whether a point in the pane's own px (from its top left) is on the page: inside the pane and not under a
    part of the app page laid over it (`cuts`, CSS px: the chat beside the page with its cards, the bar's
    suggestions), which is cut out of the pane and pressed there, not on the page."""
    if not (0 <= x < wide and 0 <= y < high):
        return False
    return not any(cx * scale <= x < (cx + cw) * scale and cy * scale <= y < (cy + ch) * scale
                   for cx, cy, cw, ch in cuts)


def went_to_page(pressed: bool, at_page: bool, typed: bool, focus_in_page: bool) -> bool:
    """Whether the person's input went to the page in the pane: a pointer press goes where the pointer is (a press
    on the chat, a card or the bar laid over the page is on the app's own page, whatever holds the focus), keys go
    where the keyboard focus is."""
    if pressed:
        return at_page
    return typed and focus_in_page


def tell_script(state: dict) -> str:
    """What the app page runs to show `state` in the pane's bar (browse.js)."""
    return f"window.paBrowse && paBrowse({json.dumps(state)})"


def facts_script() -> str:
    """The generic reader (web/reader.js) over the page as it is: its rows, its content, its own facts."""
    from .web.read import READER
    return f"({READER})({{}})"


def page_of(url: str, data, rows: list | None = None, start: int = 0, most: int = TEXT_MAX) -> dict:
    """What a read page gives: title, declared facts and readable text, as roam.py reads them, and for a list
    its rows (title, link, author, time, counts). The text is the `most` characters from `start`."""
    from .roam import facts_from
    data = data if isinstance(data, dict) else {}
    facts = facts_from(data, url)
    listed = [{k: str(row.get(k) or "")[:300] for k in ("title", "link", "author", "time") if row.get(k)}
              | ({"counts": row["counts"]} if isinstance(row.get("counts"), dict) and row["counts"] else {})
              for row in (data.get("rows") if rows is None else rows) or [] if isinstance(row, dict)]
    text = facts.get("text") or ""
    return {"url": url, "title": facts.get("title") or str(data.get("title") or ""),
            "site": facts.get("site", ""), "kind": facts.get("kind", ""), "author": facts.get("author", ""),
            "published": facts.get("published", ""), "description": facts.get("description", ""),
            "text": text[start:start + most],
            **({"rows": [row for row in listed if row.get("title")][:ROWS_MAX]}
               if data.get("kind") == "list" else {})}


def _run(core, script: str):
    """Run `script` in a page, on the form's thread; its answer is heard there too (as pywebview does)."""
    from System import Action, String
    from System.Threading.Tasks import Task, TaskScheduler

    done = threading.Event()
    heard: list = []

    def finished(task) -> None:
        with contextlib.suppress(Exception):
            heard.append(task.Result)
        done.set()
    core.ExecuteScriptAsync(script).ContinueWith(Action[Task[String]](finished),
                                                  TaskScheduler.FromCurrentSynchronizationContext())
    return done, heard


def _wait(asked, seconds: float):
    """What a script run by `_run` answered, from a thread other than the form's; None when it took too long."""
    if not asked:
        return None
    done, heard = asked
    return heard[0] if done.wait(seconds) and heard else None


def _devtools(core, method: str, params: dict, box: dict, done: threading.Event, session: str = "") -> None:
    """One DevTools protocol call in the page, or with `session` in one of its frames from another site, in
    process (no debugging port); started on the form's thread, its answer heard on a pool thread, so a thread
    waiting for it never holds the form."""
    from System import Action, String
    from System.Threading.Tasks import Task

    def finished(task) -> None:
        try:
            box["result"] = json.loads(str(task.Result))
        except Exception:
            inner = task.Exception.InnerException if task.Exception is not None else None
            box["error"] = str(inner.Message if inner is not None else "the call failed")
        done.set()
    box["handler"] = finished
    call = (core.CallDevToolsProtocolMethodForSessionAsync(session, method, json.dumps(params)) if session
            else core.CallDevToolsProtocolMethodAsync(method, json.dumps(params)))
    call.ContinueWith(Action[Task[String]](finished))


def _call(ui: Callable, core_of: Callable, method: str, params: dict, timeout: float, session: str = "") -> dict:
    """`method` sent to the page `core_of()` gives (on the form's thread), from a thread other than the
    form's; a suspended page is resumed first. Raises RuntimeError with the page's own reason."""
    box: dict = {}
    done = threading.Event()

    def start() -> None:
        core = core_of()
        if core is None:
            box["error"] = "the page is closed"
            done.set()
            return
        if core.IsSuspended:
            core.Resume()
        _devtools(core, method, params, box, done, session)
    ui(start)
    if not done.wait(timeout):
        raise RuntimeError(f"Timed out: {method}")
    if "error" in box:
        raise RuntimeError(box["error"])
    return box["result"]


class _Tab:
    """One tab of the pane: its WebView (made once it has a page to show), its core once that is ready, whether it
    is private (an InPrivate profile: no sign-in of the person's, nothing kept), the address on its way, the
    site's request a popup waits on (with its deferral), the tab that opened it, what the strip shows, what its page
    asks of the person (the strip under the bar shows the first, while the tab is in front) and the permissions
    allowed this time, for its page as it is now ({origin: kinds})."""

    def __init__(self, ident: int, private: bool, opener: "_Tab | None" = None):
        self.id, self.private, self.opener = ident, private, opener
        self.view = self.core = None
        self.pending = ""
        self.popup: tuple | None = None
        self.url = self.title = ""
        self.loading = self.sound = False
        # Opened by the character out of sight (Pane.place_page): drawn behind the app's page until it comes to the front.
        self.aside = False
        self.notices: list[dict] = []
        self.once: dict[str, set] = {}


class Pane:
    """The site's WebViews in the app window's `form`, one per tab, made when a link opens and
    gone again when the pane closes. Its work runs on the form's thread, through
    `ui`. `tell(state)` hands the page what its bar shows, `find` names the find
    at an address ("" for none; called on a thread of its own), and `visit(row)`
    takes a page left ({"url", "title", "s", "at", "id", "facts"}; on a thread of its
    own). `full(core, on)` makes the window full screen for a video, or puts it
    back; it answers with `cover`."""

    def __init__(self, folder: Path, form, *, ui: Callable[[Callable[[], None]], None],
                 tell: Callable[[dict], None], find: Callable[[str], str], visit: Callable[[dict], None],
                 full: Callable[[object, bool], None]):
        self.folder = Path(folder)
        self.form = form
        self.ui = ui
        self.tell = tell
        self.find = find
        self.visit = visit
        self.full = full
        # The tabs in the strip's order, the one in front, the addresses of tabs closed (Ctrl+Shift+T), and the
        # last tab number given.
        self._tabs: list[_Tab] = []
        self._tab: _Tab | None = None
        self._closed: list[str] = []
        self._made = 0
        # The space the page keeps for the WebView (form px), and what of the app page lies over it (the chat in a
        # narrow window, the address bar's suggestions): rects in its CSS px.
        self._box = None
        self._cuts: list = []
        # While the pane is in front: the window as it stood for the chat, where it was put, and the size given it.
        self._roomed: dict | None = None
        # The page being read: its address, title, find id once known, seconds counted and since when
        # (monotonic), whether it showed a password field, and its facts once read long enough.
        self._reading: dict | None = None
        self._quieted = False
        self._full = False
        # Hidden: the person minimized the pane or went to Settings, and the page goes on behind the chip that
        # brings it back.
        self._hidden = False
        self._timer = None
        # .NET holds no reference to these.
        self._handlers = (lambda s, a: self._count(), lambda s, a: self._pause(), self._resized, self._tick,
                          self._resize_end)
        # While the character operates the page: the task, the step it is on, why it must stop ("stopped",
        # "paused" when the person moved in the pane), and the input watch that finds the person's own moves
        # (the run's own clicks go through DevTools, not Windows).
        self.op: dict | None = None
        self._op_last: dict | None = None
        self._op_timer = None
        self._op_seen = 0
        self._op_tick = lambda sender, args: self._input_tick()
        self._listeners: list = []
        # Zoom: the app page's px per CSS px, the person's own zoom per site (kept in KEPT; in private mode for this
        # run only), the menu's 适应宽度 (fit to width, kept in KEPT, on until turned off), the factor last set here,
        # and what the bar was told. KEPT also holds the page open last and the window's size with a site in front.
        self._scale = 1.0
        self._kept = self._read_kept()
        self.fit = self._kept["fit"]
        self._zoom_private: dict[str, float] = {}
        self._zoom_set = 0.0
        self._zoom_told: tuple = ()
        # Between a navigation's start and its end, WebView2 may bring back a zoom it remembers for the site.
        self._navigating = False
        # The downloads the menu lists, newest first ({"id", "path", "url", "bytes", "got", "state", "private"}); the
        # kept ones come back from KEPT.
        self._downloads: list[dict] = [{**row, "id": n + 1, "private": False}
                                       for n, row in enumerate(self._kept["downloads"])]
        self._download_n = len(self._downloads)
        # The site permissions the person allowed always ({origin: kinds}): kept in KEPT, in private mode for this
        # run only; and the last number given to something the strip shows.
        self._permits_private: dict[str, list] = {}
        self._noticed = 0
        self.ui(self._watch_form)

    # ---- the tab in front: its WebView, its core, whether it is private

    @property
    def _view(self):
        return self._tab.view if self._tab is not None else None

    @property
    def _core(self):
        return self._tab.core if self._tab is not None else None

    @property
    def private(self) -> bool:
        return self._tab is not None and self._tab.private

    def _tab_of(self, sender) -> _Tab | None:
        """The tab a WebView or a core belongs to."""
        return next((tab for tab in self._tabs if sender is not None and (
            (tab.view is not None and tab.view.Equals(sender)) or (tab.core is not None and tab.core.Equals(sender)))),
            None)

    # ---- what the app asks, from any thread

    def open(self, address: str) -> None:
        """A link from the app: in a new tab when the one in front has a page."""
        self.ui(lambda: self._open(address))

    def go(self, address: str) -> None:
        """What the person typed in the bar: in the tab in front."""
        self.ui(lambda: self._open(address, here=True))

    def place_page(self, address: str, show: bool) -> dict:
        """A page the character opens (surface.py): with `show` as a link does, in front; else in a tab of its own
        behind the others, the tab in front and what the person sees left as they are. Its tab's number and address."""
        made: list = []

        def go() -> None:
            if show:
                self._open(address)
            tab = self._tab if show else self._aside(address)
            made.append({"id": tab.id, "url": address, "title": ""})
        self.ui(go)
        return made[0]

    def tabs(self) -> list[dict]:
        """Every tab: its number, title, address, whether it is private, in front, or opened out of sight."""
        found: list = []
        self.ui(lambda: found.extend({"id": t.id, "title": t.title, "url": t.url, "private": t.private,
                                      "front": t is self._tab, "aside": t.aside} for t in self._tabs))
        return found

    def front(self, ident: int) -> bool:
        """Tab `ident` comes to the front, the pane shown; False when there is no such tab."""
        done: list = []

        def bring() -> None:
            tab = next((t for t in self._tabs if t.id == ident), None)
            if tab is not None:
                self._switch(tab)
                if self._hidden:
                    self._do("return")
                self.tell({"open": True, "url": tab.url, "host": urlsplit(tab.url).hostname or "",
                           "title": tab.title, "private": tab.private, **self._strip()})
            done.append(tab is not None)
        self.ui(bring)
        return bool(done and done[0])

    def core_of(self, ident: int = 0):
        """The page of tab `ident`, or of the tab in front with 0; None when it is not there."""
        if not ident:
            return self._core
        return next((t.core for t in self._tabs if t.id == ident), None)

    def place(self, box: list, scale: float, cuts: list) -> None:
        self.ui(lambda: self._place(box, scale, cuts))

    def room(self, wide: float, scale: float) -> None:
        self.ui(lambda: self._room(wide, scale))

    def rescale(self, factor: float) -> None:
        """On the form's thread: the window came onto a monitor of another scale (`factor` times the
        last). The page's CSS px stay as they were, so it may not say where the pane is again."""
        if self._box is not None:
            self._place(self._css, self._scale * factor, self._cuts)

    def do(self, action: str, tab: int = 0) -> None:
        """A press in the bar, the strip or the menu; `tab` names the tab a strip press is for, or the download a
        press in the menu's list is for."""
        self.ui(lambda: self._do(action, tab))

    def quiet(self, on: bool) -> None:
        if self._tabs:
            self.ui(lambda: self._quiet(on))

    def close(self) -> None:
        """The app is quitting: the page left is handed over now, while the page's server still answers. Its
        address stays kept, offered when the app starts again."""
        sent: list = []
        self.ui(lambda: sent.append(self._shut(forget=False)))
        if sent and sent[0]:
            with contextlib.suppress(Exception):
                self.visit(sent[0])

    def forget(self, zoom: bool) -> None:
        """History cleared: the page open last, the tabs closed lately and the list of downloads are forgotten (the
        files stay), with `zoom` every site's zoom too; the pane stays open, its page read as from now."""
        def drop() -> None:
            reading = self._reading
            if reading is not None:
                reading.update(s=0.0, at=time.time(), since=time.monotonic() if reading["since"] is not None else None)
            self._closed.clear()
            self._kept["last"] = {}
            if zoom:
                self._kept["zoom"].clear()
            self._downloads.clear()
            self._tell_downloads(keep=True)
            self.tell({"kept": None})
        self.ui(drop)

    # ---- on the form's thread

    def _watch_form(self) -> None:
        from System.Windows.Forms import Timer
        activated, deactivated, resized, tick, resize_end = self._handlers
        self.form.Activated += activated
        self.form.Deactivate += deactivated
        self.form.Resize += resized
        self.form.ResizeEnd += resize_end
        self._timer = Timer()
        self._timer.Interval = TICK_MS
        self._timer.Tick += tick

    def _begin(self, address: str) -> None:
        """A page begins to be read: the last one is handed over first."""
        self._send(self._finish())
        self._reading = {"url": address, "title": "", "id": "", "s": 0.0, "since": None, "begun": False,
                         "secret": self.private, "facts": None, "at": time.time()}
        reading = self._reading
        threading.Thread(target=self._look_up, args=(reading,), name="mibo-browser", daemon=True).start()

    def _open(self, address: str, here: bool = False) -> None:
        """`address` in the tab in front when `here` or when it is still empty (and not private), else in a new
        tab: a link from the app or a tab brought back is never private."""
        if self._tab is None or not (here or self._tab.view is None and not self._tab.private):
            self._switch(self._new_tab(False))
        tab = self._tab
        self._begin(address)
        tab.url, tab.title = address, ""
        if tab.core is None:
            tab.pending = address
            # With the pane already placed the WebView is made now; else once the page says where it goes.
            if tab.view is None and self._box is not None:
                self._make(tab)
        else:
            tab.core.Navigate(address)
        if self._quieted:
            self._quiet(False)
        if self._hidden:
            self._do("return")
        self.tell({"open": True, "url": address, "host": urlsplit(address).hostname or "", "title": "",
                   "back": False, "forward": False, "loading": True, "private": self.private, **self._strip()})

    def _new_tab(self, private: bool, opener: _Tab | None = None) -> _Tab:
        """A tab with no page yet, after the one in front (a site's new tab beside the tab that opened it)."""
        self._made += 1
        tab = _Tab(self._made, private, opener)
        at = self._tabs.index(self._tab) + 1 if self._tab in self._tabs else len(self._tabs)
        self._tabs.insert(at, tab)
        return tab

    def _aside(self, address: str) -> _Tab:
        """A tab out of sight at the end of the strip, its WebView made now behind the app's page and muted."""
        from System.Drawing import Rectangle
        self._made += 1
        tab = _Tab(self._made, False)
        tab.aside, tab.url, tab.pending = True, address, address
        self._tabs.append(tab)
        self._make(tab, Rectangle(0, 0, *ASIDE))
        self.tell(self._strip())
        return tab

    def _switch(self, tab: _Tab) -> None:
        """`tab` comes to the front: the one before is hidden and its reading ends, this one is shown, sized, cut
        and zoomed as the pane is, and its page is read from now. A run under way pauses (it acts on the page in
        front only), unless it waits for the person's own sign-in."""
        if tab is self._tab:
            return
        tab.aside = False
        before = self._tab
        if before is not None:
            self._send(self._finish())
            if before.view is not None:
                before.view.Visible = False
        self._tab = tab
        op = self.op
        if op is not None and not op.get("held") and not op.get("wait"):
            op["held"] = "paused"
            self._tell_op()
        if tab.view is not None:
            tab.view.Visible = not self._hidden and not self._quieted
            if self._box is not None:
                tab.view.Bounds = self._box
                self._cut()
            tab.view.BringToFront()
            self._zoom_told = ()
            self._zoom()
        if tab.core is not None and web_address(str(tab.core.Source)):
            self._begin(str(tab.core.Source))
            self._reading["begun"] = True
            self._count()
        self._state()
        self._tell_prompt()
        if tab.core is not None:
            self._sound(tab.core, None)

    def _close_tab(self, tab: _Tab) -> None:
        """A tab closes (its ×, Ctrl+W, or the page's own window.close()): its address can come back with
        Ctrl+Shift+T, unless it was private or a sign-in or payment page. The tab that opened it, else the one
        beside it, comes to the front; the last tab closes the pane."""
        if len(self._tabs) == 1:
            self._send(self._shut())
            return
        url = str(tab.core.Source) if tab.core is not None else ""
        if not tab.private and web_address(url) and not browsed.secret(url):
            self._closed.append(url)
            del self._closed[:-CLOSED_KEPT]
        at = self._tabs.index(tab)
        if tab is self._tab:
            self._send(self._finish())
            self._tab = None
        self._tabs.remove(tab)
        self._drop(tab)
        if self._tab is None:
            self._switch(tab.opener if tab.opener in self._tabs else self._tabs[min(at, len(self._tabs) - 1)])
        else:
            self.tell(self._strip())

    def _drop(self, tab: _Tab) -> None:
        """A tab's WebView and its page go; a popup still waiting is let go as the site's own, and what its page
        asked is refused."""
        self._clear(tab)
        if tab.popup is not None:
            args, deferral = tab.popup
            tab.popup = None
            args.Handled = True
            deferral.Complete()
        if tab.view is not None:
            self.form.Controls.Remove(tab.view)
            tab.view.Dispose()
        tab.view = tab.core = None

    def _strip(self) -> dict:
        """What the strip shows: each tab's number, title, address, private, loading and sound, and the one in front."""
        return {"tabs": [{"id": t.id, "title": t.title, "url": t.url, "private": t.private, "loading": t.loading,
                          "sound": t.sound} for t in self._tabs],
                "tab": self._tab.id if self._tab is not None else 0}

    def _look_up(self, reading: dict) -> None:
        ident = self.find(reading["url"])
        if ident:
            self.ui(lambda: self._found(reading, ident))

    def _found(self, reading: dict, ident: str) -> None:
        if reading is self._reading:
            reading["id"] = ident

    def _place(self, box: list, scale: float, cuts: list) -> None:
        """The page's pane moved or changed size, or part of the app page came over it (`cuts`: [x, y, wide, high]
        in CSS px inside it): the WebView follows, made the first time."""
        from System.Drawing import Rectangle
        if self._tab is None:
            return
        # The app page fills the form; its CSS px times its device pixel ratio make form px.
        origin = self.form.browser.webview.Location
        x, y, wide, high = (int(round(v * scale)) for v in box)
        self._box = Rectangle(origin.X + x, origin.Y + y, max(wide, 1), max(high, 1))
        self._css, self._scale, self._cuts = box, scale, cuts
        if self._view is None:
            # An empty tab (Ctrl+T, the private button) waits for an address.
            if not self._tab.pending:
                return
            self._make(self._tab)
        elif not self._full:
            self._view.Bounds = self._box
        self._cut()
        self._zoom()

    def _cut(self) -> None:
        """Under the chat laid over the page, or the bar's suggestions, the WebView is cut away, not made smaller:
        the site keeps its width and its layout, and the app page shows where it is cut. Whole again in full
        screen and with nothing over it."""
        from System.Drawing import Rectangle, Region
        if self._cuts and not self._full:
            region = Region(Rectangle(0, 0, self._box.Width, self._box.Height))
            for cut in self._cuts:
                region.Exclude(Rectangle(*(round(v * self._scale) for v in cut)))
            self._view.Region = region
        else:
            self._view.Region = None

    def _room(self, wide: float, scale: float) -> None:
        """The pane came to the front and its page asks for a window `wide` of its CSS px wide (the sidebar, a site
        at the width a desktop layout wants, the chat beside it); with 0 the pane left the front. The window grows
        to that once, taller too so it keeps a page's shape, within its monitor's work area, and never gets smaller; the size the person gave it while a
        site was in front (KEPT) is used instead of the page's. When the pane leaves the front the window takes
        back the size it had for the chat. Not while maximized or in full screen."""
        from System.Drawing import Point, Rectangle, Size
        from System.Windows.Forms import FormWindowState, Screen
        form, unit = self.form, self.form._scale
        if form.WindowState != FormWindowState.Normal or self._full:
            return
        if not wide:
            roomed, self._roomed = self._roomed, None
            if roomed is not None:
                left, top, was_wide, was_high = roomed["before"]
                # Where it stood, unless the person moved it since.
                at = Point(left, top) if form.Location.Equals(roomed["at"]) else form.Location
                form.Bounds = Rectangle(at, Size(round(was_wide * unit), round(was_high * unit)))
            return
        if self._roomed is not None:
            return
        area = Screen.FromControl(form).WorkingArea
        kept = self._kept["size"] or [(form.Width - form.ClientSize.Width + wide * scale) / unit, 0]
        # By itself it takes at most ROOM_SHARE of the screen's width; a size the person gave it may be the whole of it.
        most = area.Width if self._kept["size"] else max(form.Width, round(area.Width * ROOM_SHARE))
        width = min(max(form.Width, round(kept[0] * unit)), most)
        # Widened by itself it is made as tall as a page's shape asks, within the screen.
        high = kept[1] * unit if self._kept["size"] else width / ROOM_SHAPE
        size = Size(width, min(max(form.Height, round(high)), area.Height))
        # It grows to the right and down; a window that stood on its screen is moved back only as far as keeps it there.
        left = min(form.Left, max(area.Left, area.Right - size.Width)) if form.Right <= area.Right else form.Left
        top = min(form.Top, max(area.Top, area.Bottom - size.Height)) if form.Bottom <= area.Bottom else form.Top
        self._roomed = {"before": (form.Left, form.Top, form.Width / unit, form.Height / unit), "at": Point(left, top),
                        "size": [round(size.Width / unit), round(size.Height / unit)]}
        form.Bounds = Rectangle(Point(left, top), size)

    def _resize_end(self, sender, args) -> None:
        """The person let go of the window's edge with a site in front: that size is the one sites get from now on."""
        from System.Windows.Forms import FormWindowState
        form, roomed = self.form, self._roomed
        if roomed is None or form.WindowState != FormWindowState.Normal:
            return
        size = [round(form.Width / form._scale), round(form.Height / form._scale)]
        if max(abs(size[0] - roomed["size"][0]), abs(size[1] - roomed["size"][1])) > 2:
            roomed["size"] = self._kept["size"] = size
            self._keep()

    def _make(self, tab: _Tab, box=None) -> None:
        """The tab's WebView, on the pane's folder (so in the same browser processes as the other tabs); with `box`
        out of sight there, behind the app's page."""
        from Microsoft.Web.WebView2.WinForms import CoreWebView2CreationProperties, WebView2
        from System.Drawing import ColorTranslator

        view = WebView2()
        properties = CoreWebView2CreationProperties()
        properties.UserDataFolder = str(self.folder / "webview")
        # Private: an InPrivate profile, so no sign-in of the person's reaches the page and nothing of it stays.
        properties.IsInPrivateModeEnabled = tab.private
        properties.Language = language()
        properties.AdditionalBrowserArguments = BROWSER_ARGS
        view.CreationProperties = properties
        view.DefaultBackgroundColor = ColorTranslator.FromHtml(CANVAS[_dark()])
        view.Bounds = box or self._box
        view.Visible = box is not None or tab is self._tab and not self._hidden and not self._quieted
        view.CoreWebView2InitializationCompleted += self._ready
        view.ZoomFactorChanged += self._zoomed
        view.KeyDown += self._key
        self.form.Controls.Add(view)
        if box is None:
            view.BringToFront()
        else:
            view.SendToBack()
        tab.view = view
        view.EnsureCoreWebView2Async(None)

    def _ready(self, sender, args) -> None:
        tab = self._tab_of(sender)
        if tab is None:
            return
        if not args.IsSuccess:
            # No view for it: the tab goes, and a popup waiting on it is let go.
            self._close_tab(tab)
            return
        core = sender.CoreWebView2
        settings = core.Settings
        # Nothing reaches Python from here, and nobody inspects the app through it.
        settings.IsWebMessageEnabled = False
        settings.AreHostObjectsAllowed = False
        settings.AreDevToolsEnabled = False
        settings.IsSwipeNavigationEnabled = True
        # The browser's own password saving and autofill, and each page looked at for a sign-in (signwatch.py).
        from . import signwatch
        signwatch.ready(self, core, tab.private)
        core.NavigationStarting += self._starting
        core.NavigationCompleted += self._completed
        core.NewWindowRequested += self._new_window
        core.WindowCloseRequested += self._close_requested
        core.PermissionRequested += self._permission
        core.LaunchingExternalUriScheme += self._external
        core.DocumentTitleChanged += self._changed
        core.SourceChanged += self._changed
        core.HistoryChanged += self._changed
        core.ContainsFullScreenElementChanged += self._full_screen
        core.DownloadStarting += self._download
        core.ContextMenuRequested += self._menu
        core.IsDocumentPlayingAudioChanged += self._sound
        core.IsMutedChanged += self._sound
        tab.core = core
        if tab.aside:
            # Out of sight: no sound, and the page acts as a page in front would (focus, as Playwright's do).
            core.IsMuted = True
            core.CallDevToolsProtocolMethodAsync("Emulation.setFocusEmulationEnabled", '{"enabled": true}')
        if tab.popup is not None:
            # The site's popup is this tab's page: it keeps window.opener, so a sign-in hands its answer back.
            request, deferral = tab.popup
            tab.popup = None
            request.NewWindow = core
            request.Handled = True
            deferral.Complete()
        elif tab.pending:
            core.Navigate(tab.pending)
            tab.pending = ""
        if tab is self._tab:
            self._count()

    def _starting(self, sender, args) -> None:
        """Only web addresses load: file:, about:, data: and the browser's other schemes are refused; a program's
        scheme goes on to `_external`, which never lets it start. A new page drops what the last one asked."""
        from Microsoft.Web.WebView2.Core import CoreWebView2NavigationKind
        if not web_address(args.Uri):
            scheme = urlsplit(str(args.Uri)).scheme
            args.Cancel = not scheme or scheme in INSIDE
            return
        tab = self._tab_of(sender)
        tab.loading = True
        if tab.notices or tab.once:
            self._clear(tab)
            if tab is self._tab:
                self._tell_prompt()
        if tab is not self._tab:
            self.tell(self._strip())
            return
        self._navigating = True
        reading = self._reading
        # Leaving the page: a click on it, or back and forward. A redirect or a reload stays on it.
        if (reading is not None and reading["begun"] and not args.IsRedirected
                and args.NavigationKind != CoreWebView2NavigationKind.Reload
                and (args.IsUserInitiated or args.NavigationKind == CoreWebView2NavigationKind.BackOrForward)):
            self._send(self._finish())
        self._state(loading=True)

    def _completed(self, sender, args) -> None:
        tab = self._tab_of(sender)
        tab.loading = False
        if tab is not self._tab:
            self._changed(sender, args)
            return
        if args.IsSuccess:
            source = str(sender.Source)
            if self._reading is None:
                # A page come to from the last one.
                self._begin(source)
            reading = self._reading
            reading["url"], reading["begun"] = source, True
            self._check_secret(reading)
            self._count()
        # WebView2 brings back a zoom it remembers for the site as it navigates: the fit goes over it.
        self._navigating = False
        self._zoom()
        self._state(loading=False)

    def _check_secret(self, reading: dict) -> None:
        """A password field on the page makes it a sign-in page: never kept. Asked again on each tick."""
        task = _run(self._core, SECRET_SCRIPT)

        def heard() -> None:
            if _wait(task, 10) == "true":
                reading["secret"] = True
        threading.Thread(target=heard, name="mibo-browser", daemon=True).start()

    def _changed(self, sender, args) -> None:
        tab = self._tab_of(sender)
        if tab is self._tab:
            self._state()
        elif tab is not None:
            tab.url, tab.title = str(sender.Source), str(sender.DocumentTitle or "").strip()
            self.tell(self._strip())

    def _state(self, loading: bool | None = None) -> None:
        """What the bar shows of the tab in front (an empty one has no address yet), with the strip."""
        tab, core = self._tab, self._core
        if core is not None:
            tab.url, tab.title = str(core.Source), str(core.DocumentTitle or "").strip()
        source, title = tab.url, tab.title
        if self._reading is not None and self._reading["url"] == source:
            self._reading["title"] = title
        state = {"url": source, "host": urlsplit(source).hostname or "", "title": title,
                 "back": core is not None and bool(core.CanGoBack), "forward": core is not None and bool(core.CanGoForward),
                 "private": tab.private, "loading": tab.loading if loading is None else loading, **self._strip()}
        self.tell(state)
        self._keep_last(source, title)

    def _sound(self, sender, args) -> None:
        """A tab began or stopped playing sound, or was muted: the bar and the chip show a speaker for the one in
        front, the strip one on each tab that plays."""
        tab = self._tab_of(sender)
        tab.sound = bool(sender.IsDocumentPlayingAudio)
        if tab is self._tab:
            self.tell({"sound": tab.sound, "muted": bool(sender.IsMuted), **self._strip()})
        else:
            self.tell(self._strip())

    def _new_window(self, sender, args) -> None:
        """A site's new tab or popup, only from the person's click: a tab of its own in front, whose page the site
        keeps hold of (the WebView is made, then handed to the site in `_ready`). While the character acts, it
        opens in the page it acts on instead, so the run stays on the one page its guards watch. A popup the page
        opens by itself is stopped and named on the strip, which can open it in a tab."""
        args.Handled = True
        uri = str(args.Uri)
        if not args.IsUserInitiated:
            opener = self._tab_of(sender)
            if web_address(uri) and self.op is None and opener is not None:
                self._notice(opener, "popup", url=uri)
            return
        if not (web_address(uri) or uri == "about:blank"):
            return
        op = self.op
        if op is not None and not op.get("held") and not op.get("wait"):
            if web_address(uri):
                self._send(self._finish())
                sender.Navigate(uri)
            return
        opener = self._tab_of(sender)
        tab = self._new_tab(opener.private, opener)
        tab.popup, tab.url = (args, args.GetDeferral()), uri if web_address(uri) else ""
        self._switch(tab)
        self._make(tab)

    def _permission(self, sender, args) -> None:
        """A site asks for the camera, the microphone, the location or the rest. Refused without a word while the
        character operates, and for the kinds never asked about; allowed at once where the person allowed it always,
        or this time on this page; else the strip asks, for the tab that asked (a deferral holds the site's request
        until the press). Nothing is saved in the profile here: the press decides that."""
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState as State
        tab, kind, origin = self._tab_of(sender), ASKED.get(str(args.PermissionKind), ""), origin_of(str(args.Uri))
        args.SavesInProfile = False
        if self.op is not None or not kind or tab is None:
            args.State = State.Deny
            return
        if kind in self._permits(tab).get(origin, ()) or kind in tab.once.get(origin, ()):
            args.State = State.Allow
            return
        # A page asking for the camera and the microphone at once is asked about both on one strip.
        notice = next((n for n in tab.notices if n["kind"] == "permission" and n["origin"] == origin), None)
        if notice is None:
            notice = self._notice(tab, "permission", origin=origin, what=[], asks=[])
        if kind not in notice["what"]:
            notice["what"] = sorted({*notice["what"], kind}, key=list(ASKED.values()).index)
        notice["asks"].append((args, args.GetDeferral()))
        if tab is self._tab:
            self._tell_prompt()

    def _permits(self, tab: _Tab) -> dict:
        return self._permits_private if tab.private else self._kept["permits"]

    def _notice(self, tab: _Tab, kind: str, origin: str = "", **more) -> dict:
        """Something for the strip, on `tab`: a permission asked, a program's link, a popup stopped. A program's link
        or a popup takes the place of the one of its kind before it."""
        tab.notices[:] = [n for n in tab.notices if n["kind"] == "permission" or n["kind"] != kind]
        self._noticed += 1
        host = urlsplit(origin or (str(tab.core.Source) if tab.core is not None else tab.url)).hostname or ""
        notice = {"id": self._noticed, "kind": kind, "origin": origin, "host": host, **more}
        tab.notices.append(notice)
        if tab is self._tab and kind != "permission":
            self._tell_prompt()
        return notice

    def _tell_prompt(self) -> None:
        """The strip shows the first thing the tab in front asks, or nothing."""
        tab = self._tab
        notice = tab.notices[0] if tab is not None and tab.notices else None
        self.tell({"prompt": None if notice is None else {k: v for k, v in notice.items() if k != "asks"}})

    def _clear(self, tab: _Tab) -> None:
        """What `tab` asked goes unanswered, refused and not saved (its page left, it closed, a run began), and what
        was allowed this time ends with its page."""
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState as State
        for notice in tab.notices:
            for args, deferral in notice.get("asks", ()):
                args.State = State.Deny
                deferral.Complete()
        tab.notices.clear()
        tab.once.clear()

    def _answer(self, ident: int, how: str) -> None:
        """The person's press on the strip: "once" (本次允许, 打开), "always" (始终允许) or "no" (不允许, 取消, ×). A
        refusal is saved in the profile, so the browser refuses by itself from then on; an allowance is kept here
        (always) or for this page (once). Nothing is allowed while the character operates."""
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState as State
        tab = next((t for t in self._tabs if any(n["id"] == ident for n in t.notices)), None)
        if tab is None or self.op is not None and how != "no":
            return
        notice = next(n for n in tab.notices if n["id"] == ident)
        tab.notices.remove(notice)
        yes = how != "no"
        if notice["kind"] == "permission":
            origin, what = notice["origin"], notice["what"]
            if how == "always":
                permits = self._permits(tab)
                permits[origin] = sorted({*permits.get(origin, ()), *what})
                if not tab.private:
                    self._keep()
            elif yes:
                tab.once.setdefault(origin, set()).update(what)
            for args, deferral in notice["asks"]:
                args.State = State.Allow if yes else State.Deny
                args.SavesInProfile = not yes and not tab.private
                deferral.Complete()
        elif notice["kind"] == "app" and yes:
            threading.Thread(target=os.startfile, args=(notice["url"],), name="mibo-browser", daemon=True).start()
        elif notice["kind"] == "popup" and yes:
            # In a tab of its own in front, as a link would open (the page that tried keeps no hold of it).
            self._switch(self._new_tab(tab.private))
            self._open(notice["url"], here=True)
        self._tell_prompt()

    def _hush(self) -> None:
        """A run begins: whatever the tabs were asking is refused, and nothing is asked until it ends."""
        for tab in self._tabs:
            self._clear(tab)
        self._tell_prompt()

    def permits(self) -> list[dict]:
        """The site permissions the person decided, for Settings, by site: [{"origin", "host", "kinds": [{"kind",
        "state": "allow" | "deny"}]}]. Allowed always is kept here; refused is in the profile (WebView2's
        GetNonDefaultPermissionSettingsAsync). From a thread other than the form's."""
        from .signwatch import Profile
        rows = {(origin, kind): "allow" for origin, kinds in self._kept["permits"].items() for kind in kinds}
        with Profile(self)._page() as (_, core_of):
            asked: list = []
            self.ui(lambda: asked.append(core_of().Profile.GetNonDefaultPermissionSettingsAsync()))
            asked[0].Wait(int(CDP_S * 1000))
            self.ui(lambda: rows.update({(str(s.PermissionOrigin), ASKED[str(s.PermissionKind)]): "deny"
                                         for s in asked[0].Result if str(s.PermissionKind) in ASKED
                                         and str(s.PermissionState) == "Deny"}))
        sites: dict[str, dict] = {}
        for (origin, kind), state in rows.items():
            site = sites.setdefault(origin, {"origin": origin, "host": urlsplit(origin).hostname or origin, "kinds": []})
            site["kinds"].append({"kind": kind, "state": state})
        return sorted(sites.values(), key=lambda site: site["host"])

    def unpermit(self, origin: str, kind: str) -> None:
        """A site permission taken back in Settings: the site is asked about it again next time. From a thread other
        than the form's."""
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionKind as Kind
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState as State

        from .signwatch import Profile

        def drop() -> None:
            kinds = self._kept["permits"].get(origin, [])
            if kind in kinds:
                kinds.remove(kind)
                if not kinds:
                    del self._kept["permits"][origin]
                self._keep()
        self.ui(drop)
        name = next(name for name, word in ASKED.items() if word == kind)
        with Profile(self)._page() as (_, core_of):
            asked: list = []
            self.ui(lambda: asked.append(core_of().Profile.SetPermissionStateAsync(getattr(Kind, name), origin,
                                                                                    State.Default)))
            asked[0].Wait(int(CDP_S * 1000))

    def _download(self, sender, args) -> None:
        """Every download goes to the download folder and into the menu's list (the browser's own flyout is not
        shown), where its progress and end follow it. One while the character operates waits (a deferral) in
        op["downloads"], the same record, for the person's answer on a card (operate.Hands): saved on yes,
        cancelled on no."""
        from Microsoft.Web.WebView2.Core import CoreWebView2DownloadInterruptReason, CoreWebView2DownloadState
        args.Handled = True
        operation, tab = args.DownloadOperation, self._tab_of(sender)
        self._download_n += 1
        record = {"id": self._download_n, "path": str(args.ResultFilePath), "url": str(operation.Uri),
                  "bytes": int(operation.TotalBytesToReceive or 0), "got": 0, "state": "progress",
                  "private": tab is not None and tab.private, "told": 0.0}
        self._downloads.insert(0, record)
        del self._downloads[DOWNLOADS_KEPT:]

        def changed(sender, _) -> None:
            state = sender.State
            record["path"], record["got"] = str(sender.ResultFilePath), int(sender.BytesReceived)
            record["bytes"] = int(sender.TotalBytesToReceive or 0) or record["bytes"]
            if state == CoreWebView2DownloadState.Completed:
                record["state"] = "done"
            elif state == CoreWebView2DownloadState.Interrupted:
                record["state"] = ("cancelled" if sender.InterruptReason == CoreWebView2DownloadInterruptReason.UserCanceled
                                   else "failed")
            self._tell_downloads(keep=record["state"] != "progress")

        def received(sender, _) -> None:
            record["got"] = int(sender.BytesReceived)
            if time.monotonic() - record["told"] >= DOWNLOAD_TELL_S:
                record["told"] = time.monotonic()
                self._tell_downloads()
        record["handlers"] = (changed, received)
        operation.StateChanged += changed
        operation.BytesReceivedChanged += received
        if self.op is not None:
            record["state"], deferral = "waiting", args.GetDeferral()

            def answer(yes: bool) -> None:
                def finish() -> None:
                    args.Cancel = not yes
                    record["state"] = "progress" if yes else "cancelled"
                    deferral.Complete()
                    self._tell_downloads()
                self.ui(finish)
            record["answer"] = answer
            self.op.setdefault("downloads", []).append(record)
        self._tell_downloads()

    def _tell_downloads(self, keep: bool = False) -> None:
        """The menu's list as it stands (a file deleted since is marked); with `keep` it is kept too, without
        those of private tabs and those still under way."""
        self.tell({"downloads": [{"id": r["id"], "name": Path(r["path"]).name, "state": r["state"], "bytes": r["bytes"],
                                  "got": r["got"], "gone": r["state"] == "done" and not Path(r["path"]).exists()}
                                 for r in self._downloads]})
        if keep:
            self._kept["downloads"] = [{k: r[k] for k in ("path", "url", "bytes", "got", "state")}
                                       for r in self._downloads if not r["private"] and r["state"] in ("done", "failed")]
            self._keep()

    def _opened(self, ident: int, folder: bool) -> None:
        """A download in the menu's list opened (once it is done), or shown in its folder (the folder alone when
        the file is gone): the person's own press."""
        record = next((r for r in self._downloads if r["id"] == ident), None)
        if record is None:
            return
        path = Path(record["path"])
        if folder:
            subprocess.Popen(f'explorer /select,"{path}"' if path.exists() else f'explorer "{path.parent}"')
        elif record["state"] == "done" and path.exists():
            os.startfile(str(path))

    def _menu(self, sender, args) -> None:
        """The page's right-click menu: 问 Mibo first over a selection; on a link, 在新标签页中打开链接 in place of the
        browser's new window, and 在系统浏览器中打开链接. The rest is the browser's own."""
        from Microsoft.Web.WebView2.Core import CoreWebView2ContextMenuItemKind as Kind

        from . import i18n
        target, items, lang = args.ContextMenuTarget, args.MenuItems, i18n.lang()

        def item(key: str, act: str = "", value: str = ""):
            made = sender.Environment.CreateContextMenuItem(i18n.t(f"browse.{key}", lang) if key else "", None,
                                                            Kind.Command if key else Kind.Separator)
            if key:
                made.CustomItemSelected += lambda s, a: self._picked(act, value)
            return made
        link = str(target.LinkUri or "") if target.HasLinkUri else ""
        if web_address(link):
            at = next((n for n in range(items.Count) if str(items[n].Name) == "openLinkInNewWindow"), 0)
            if at < items.Count and str(items[at].Name) == "openLinkInNewWindow":
                items.RemoveAt(at)
            items.Insert(at, item("linkSystem", "system", link))
            items.Insert(at, item("linkTab", "tab", link))
        selection = str(target.SelectionText or "").strip() if target.HasSelection else ""
        if selection:
            items.Insert(0, item(""))
            items.Insert(0, item("askSelection", "ask", selection))

    def _picked(self, act: str, value: str) -> None:
        """One of the page's own right-click items: the selection to the chat's box as a quote (the page's text,
        for the person to send or not), a link in a tab behind the one in front, or in the system browser."""
        if act == "ask":
            self.tell({"ask": value[:ASK_MAX]})
        elif act == "tab":
            tab = self._new_tab(self.private)
            tab.url = tab.pending = value
            self._make(tab)
            self.tell(self._strip())
        elif act == "system":
            threading.Thread(target=webbrowser.open, args=(value,), name="mibo-browser", daemon=True).start()

    def _find(self) -> None:
        """The menu's 查找: the browser's own find bar, as Ctrl F opens it, with the page's selection in it."""
        from System import Action, String
        from System.Threading.Tasks import Task, TaskScheduler
        core, view = self._core, self._view

        def start(task) -> None:
            term = ""
            with contextlib.suppress(Exception):
                term = str(json.loads(str(task.Result)) or "").strip()
            options = core.Environment.CreateFindOptions()
            options.FindTerm = term[:200]
            options.ShouldHighlightAllMatches = True
            options.SuppressDefaultFindDialog = False
            view.Focus()
            core.Find.StartAsync(options)
        core.ExecuteScriptAsync("getSelection().toString()").ContinueWith(
            Action[Task[String]](start), TaskScheduler.FromCurrentSynchronizationContext())

    def _zoom_step(self, step: int) -> None:
        """The menu's −, + and its percentage: the site's own zoom one browser step down or up, or none (0)."""
        host, zooms = self._host(), self._zooms()
        own = zooms.get(host, 1.0)
        steps = ZOOM_STEPS if step > 0 else ZOOM_STEPS[::-1]
        own = next((z for z in steps if (z - own) * step > 0.001), own) if step else 1.0
        if abs(own - 1.0) < 0.005:
            zooms.pop(host, None)
        else:
            zooms[host] = own
        self._zoom(show=True)
        if not self.private:
            self._keep()

    def _external(self, sender, args) -> None:
        """mailto:, a program's own scheme: never started by the site (nor the browser's own dialog shown); the strip
        names it, and its 打开 starts it, the person's own press. Nothing is named while the character operates."""
        args.Cancel = True
        tab = self._tab_of(sender)
        if self.op is None and tab is not None:
            self._notice(tab, "app", origin_of(str(args.InitiatingOrigin)), url=str(args.Uri))

    def _close_requested(self, sender, args) -> None:
        """The page closed its own window (a sign-in popup that is done): its tab goes, once the event is over."""
        from System import Action
        tab = self._tab_of(sender)
        self.form.BeginInvoke(Action(lambda: self._close_tab(tab) if tab in self._tabs else None))

    def _full_screen(self, sender, args) -> None:
        """A video's full screen is the whole monitor's: the window covers it (`full`, then `cover`)."""
        self.full(sender, bool(sender.ContainsFullScreenElement))

    def shows(self, core) -> bool:
        return self._core is not None and core is not None and self._core.Equals(core)

    def cover(self, mine: bool | None) -> None:
        """The window went full screen: for the pane's own video (True) its WebView covers the whole
        window; for the page's player (False) it steps out of the way. None: back where the page keeps it."""
        if self._view is None:
            return
        self._full = mine is True
        if mine:
            self._view.Bounds = self.form.ClientRectangle
            self._view.BringToFront()
        else:
            self._view.Visible = mine is None and not self._quieted and not self._hidden
            if self._box is not None:
                self._view.Bounds = self._box
        self._cut()
        # A video full screen is at 1; back in the pane, its own zoom again.
        self._zoom()

    def _do(self, action: str, ident: int = 0) -> None:
        """The bar's, the strip's and the menu's buttons, from the page; `ident` names the tab a strip press is for,
        or the download of a press in the menu's list."""
        core = self._core
        if action == "close":
            self._send(self._shut())
        elif action == "system":
            # Read elsewhere from here on: the tab has done its part.
            target = str(core.Source) if core is not None else self._tab.pending
            if web_address(target):
                threading.Thread(target=webbrowser.open, args=(target,), name="mibo-browser",
                                 daemon=True).start()
            self._close_tab(self._tab)
        elif action in ("private", "tab_new"):
            # An empty tab in front, private or not, and the address to type in; the other tabs stay as they are.
            self._switch(self._new_tab(action == "private"))
            self.tell({"focus": "address"})
        elif action in ("tab_select", "tab_close"):
            tab = next((t for t in self._tabs if t.id == ident), None)
            if tab is not None:
                (self._switch if action == "tab_select" else self._close_tab)(tab)
        elif action in ("tab_next", "tab_prev") and len(self._tabs) > 1:
            at = self._tabs.index(self._tab) + (1 if action == "tab_next" else -1)
            self._switch(self._tabs[at % len(self._tabs)])
        elif action == "tab_reopen" and self._closed:
            self._open(self._closed.pop())
        elif action == "fit":
            # The menu's 适应宽度: the site fitted to the pane's width, or at its own size, from now on.
            self.fit = self._kept["fit"] = not self.fit
            self._keep()
            self._zoom(show=True)
        elif action in ("op_stop", "op_take"):
            # The bar's 停止 (or Esc) and 我来接手: the run stops at its next step.
            if self.op is not None:
                self.op["held"] = "stopped" if action == "op_stop" else "paused"
                self._tell_op()
        elif action in ("hide", "return"):
            # 最小化 or Settings: the page goes on behind the chip, with its sound and its history; "return"
            # brings it back as it was. A run under way pauses: the page is out of sight.
            self._hidden = action == "hide"
            if self._view is not None:
                self._view.Visible = not self._hidden and not self._quieted
            if self._hidden:
                self._pause()
                if self.op is not None and not self.op.get("held"):
                    self.op["held"] = "paused"
                    self._tell_op()
            else:
                self._count()
            self.tell({"hidden": self._hidden})
        elif action == "loaded":
            # The app page loaded again: a page still open waits behind its chip; with none, the page open last
            # is offered.
            self._tell_downloads()
            if self._tab is None:
                if self._kept["last"]:
                    self.tell({"kept": self._kept["last"]})
                return
            self._state()
            self._tell_prompt()
            if core is not None:
                self._sound(core, None)
            self._zoom_told = ()
            self._zoom()
            self._do("hide")
        elif action == "menu":
            # The bar's menu opened: its list again, with any file deleted since.
            self._tell_downloads()
        elif action in ("dl_open", "dl_folder"):
            self._opened(ident, action == "dl_folder")
        elif action in ("allow_once", "allow_always", "refuse"):
            # The strip's buttons; `ident` names what they answer.
            self._answer(ident, {"allow_once": "once", "allow_always": "always"}.get(action, "no"))
        elif core is None:
            return
        elif action == "find":
            self._find()
        elif action == "print":
            # The browser's own print preview, as Ctrl P opens it.
            from Microsoft.Web.WebView2.Core import CoreWebView2PrintDialogKind
            self._view.Focus()
            core.ShowPrintUI(CoreWebView2PrintDialogKind.Browser)
        elif action in ("zoom_in", "zoom_out", "zoom_reset"):
            self._zoom_step({"zoom_in": 1, "zoom_out": -1}.get(action, 0))
        elif action == "mute":
            core.IsMuted = not core.IsMuted
        elif action == "back" and core.CanGoBack:
            self._send(self._finish())
            core.GoBack()
        elif action == "forward" and core.CanGoForward:
            self._send(self._finish())
            core.GoForward()
        elif action == "reload":
            core.Reload()
        elif action == "stop":
            core.Stop()

    # ---- zoom: the person's own for the site, as the browser steps it; over the fit to the pane's width when on

    def _fit_factor(self) -> float:
        if not self.fit or self._box is None:
            return 1.0
        # The site's CSS px at zoom 1 are the monitor's, whatever the app page's own zoom. The form's scale is the
        # one it was made on, and the WebView may draw at the app page's (a window far off the screens): the larger
        # of the two never leaves the site wider than the pane.
        wide = self._box.Width / max(self.form._scale, self._scale)
        return 1.0 if wide >= FIT_WIDTH else max(FIT_MIN, wide / FIT_WIDTH)

    def _host(self) -> str:
        return (urlsplit(str(self._core.Source)).hostname or "") if self._core is not None else ""

    def _zoom(self, show: bool = False) -> None:
        """Set the site's zoom: 1 in full screen, else the fit times the person's own for this site."""
        view = self._view
        if view is None:
            return
        target = 1.0 if self._full else self._fit_factor() * self._zooms().get(self._host(), 1.0)
        # Down to the next thousandth: rounding up would leave the site a pixel short of the width it needs.
        target = math.floor(min(5.0, max(0.25, target)) * 1000) / 1000
        self._zoom_set = target
        if abs(float(view.ZoomFactor) - target) > 0.0005:
            view.ZoomFactor = target
        told = (round(target * 100), self.fit)
        if show or told != self._zoom_told:
            self._zoom_told = told
            self.tell({"zoom": told[0], "fit": self.fit, "zoom_show": show})

    def _zoomed(self, sender, args) -> None:
        """The zoom changed. Set here: nothing to do. While a page loads (WebView2 gives a new page the zoom set
        last) or in full screen: ours goes over it. Otherwise the person's Ctrl +/- or Ctrl wheel, the browser's
        own steps: kept for the site (over the fit, when that is on; Ctrl 0 comes through _key)."""
        view = self._view
        # A tab behind is zoomed again as it comes to the front.
        if view is None or sender is not None and not view.Equals(sender):
            return
        now = float(view.ZoomFactor)
        if abs(now - self._zoom_set) <= 0.0005:
            return
        if self._full or self._navigating:
            self._zoom()
            return
        host, zooms = self._host(), self._zooms()
        own = round(now / self._fit_factor(), 3)
        if abs(own - 1.0) < 0.005:
            zooms.pop(host, None)
        else:
            zooms[host] = own
        self._zoom(show=True)
        if not self.private:
            self._keep()

    def _key(self, sender, args) -> None:
        """Ctrl L in the page: to the bar's address, as in any browser. Ctrl T, Ctrl W, Ctrl Shift T, Ctrl Tab and
        Ctrl Shift Tab: a new tab, this one closed, the last one closed back, the next and the one before. Ctrl 0
        (WebView2 may leave the zoom as it is): back to the fit for the site."""
        from System.Windows.Forms import Keys
        tabs = ((Keys.T, "tab_reopen" if args.Shift else "tab_new"), (Keys.W, "tab_close"),
                (Keys.Tab, "tab_prev" if args.Shift else "tab_next"))
        action = next((name for key, name in tabs if args.KeyCode == key), "") if args.Control and not args.Alt else ""
        if action:
            args.Handled = True
            self._do(action, self._tab.id)
        elif args.Control and args.KeyCode == Keys.L:
            self.form.browser.webview.Focus()
            self.tell({"focus": "address"})
        elif args.Control and args.KeyCode in (Keys.D0, Keys.NumPad0) and self._zooms().pop(self._host(), None):
            self._zoom(show=True)
            if not self.private:
                self._keep()

    def _zooms(self) -> dict:
        """The person's own zoom per site: kept, or in private mode for this run only."""
        return self._zoom_private if self.private else self._kept["zoom"]

    # ---- kept between runs (KEPT)

    def _read_kept(self) -> dict:
        try:
            kept = json.loads((self.folder / KEPT).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            kept = {}
        return {"zoom": kept.get("zoom") or {}, "last": kept.get("last") or {}, "size": kept.get("size") or [],
                "downloads": kept.get("downloads") or [], "permits": kept.get("permits") or {}, "fit": kept.get("fit", True)}

    def _keep(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.folder / KEPT, json.dumps(self._kept, ensure_ascii=False))

    def _keep_last(self, url: str, title: str) -> None:
        """The page open now, offered again after a restart; never in private mode or a sign-in or payment page."""
        last = {"url": url, "title": title[:200]}
        if self.private or not web_address(url) or browsed.secret(url) or last == self._kept["last"]:
            return
        self._kept["last"] = last
        self._keep()

    # ---- the character operating the page (web/operate.py), from a thread other than the form's

    def cdp(self, method: str, params: dict | None = None, timeout: float = CDP_S, session: str = "",
            tab: int = 0) -> dict:
        """One DevTools protocol call in the page open now (with `tab`, in that tab's), or with `session` in one of
        its frames."""
        return _call(self.ui, lambda: self.core_of(tab), method, params or {}, timeout, session)

    def listen(self, name: str, handler: Callable[..., None], session: bool = False, tab: int = 0) -> None:
        """`handler(params)` on each DevTools event `name` of the page open now (with `tab`, of that tab's); with
        `session`, `handler(params, session)`, the session it came from ("" for the page, else a frame's)."""
        def on(sender, args) -> None:
            with contextlib.suppress(ValueError):
                params = json.loads(str(args.ParameterObjectAsJson))
                if session:
                    handler(params, str(args.SessionId or ""))
                else:
                    handler(params)

        def add() -> None:
            core = self.core_of(tab)
            if core is not None:
                core.GetDevToolsProtocolEventReceiver(name).DevToolsProtocolEventReceived += on
                self._listeners.append(on)
        self.ui(add)

    def address(self, tab: int = 0) -> str:
        found: list = []

        def read() -> None:
            shown = next((t for t in self._tabs if t.id == tab), None) if tab else self._tab
            core = shown.core if shown is not None else None
            found.append(str(core.Source) if core is not None else shown.pending if shown is not None else "")
        self.ui(read)
        return found[0] if found else ""

    def bar_address(self) -> str:
        """The address its bar shows: the page open, or the one on its way while a slow page has not answered."""
        current = self.address()
        return current if web_address(current) else (self._reading or {}).get("url", "")

    def ready(self, address: str = "") -> bool:
        """The pane open and in front with a page loaded: on `address` when it is given and the page open is
        on another site, or none is open. False when no page came up in time."""
        current = self.address()
        if address and (not web_address(current) or browsed.site_of(urlsplit(address).hostname or "")
                        != browsed.site_of(urlsplit(current).hostname or "")):
            self.open(address)
        elif not web_address(current):
            return False
        else:
            self.ui(lambda: (self._quiet(False), self._do("return")))
        began = time.monotonic()
        while time.monotonic() - began < OPEN_WITHIN_S:
            if self._core is not None and web_address(self.address()):
                with contextlib.suppress(RuntimeError):
                    state = self.cdp("Runtime.evaluate", {"expression": "document.readyState",
                                                          "returnByValue": True}, timeout=5)
                    if (state.get("result") or {}).get("value") in ("interactive", "complete"):
                        return True
            time.sleep(0.3)
        return False

    def operate(self, task: str, go_on: bool = False) -> None:
        """A run begins: the column beside the page shows it, and the person's moves in the pane pause it. Going
        on from a run that left the page to the person, the column keeps what was asked and the steps so far."""
        last = self._op_last
        if not (go_on and last and last["end"] in THEIRS and time.monotonic() - last["at"] < GO_ON_S):
            last = None
        self.op = {"task": last["task"] if last else task[:200], "step": 0, "note": "", "held": "", "end": "",
                   "why": "", "steps": last["steps"] if last else [], "card": None}
        with contextlib.suppress(RuntimeError):
            self.cdp("Page.enable")
        self.ui(self._watch_input)
        self.ui(self._hush)
        self._tell_op()

    def op_step(self, n: int, note: str) -> None:
        if self.op is not None:
            self.op.update(step=n, note=note[:120])
            self._tell_op()

    def op_said(self, text: str) -> None:
        """The step about to happen, in the person's words: the one before it is done. The same step again
        (another scroll) counts up instead of making a new line."""
        op = self.op
        if op is None or not text:
            return
        steps = op["steps"]
        last = steps[-1] if steps else None
        if last and last["state"] == "doing":
            last["state"] = "done"
        if (last and last["state"] == "done" and last["text"] == text[:120] and not last.get("why")
                and last.get("note", "") == op["note"]):
            last.update(state="doing", n=last.get("n", 1) + 1)
        else:
            # With what the step is for (the loop's note, op_step), which the column shows over it.
            steps.append({"text": text[:120], "state": "doing", **({"note": op["note"]} if op["note"] else {})})
            del steps[:-STEPS_KEPT]
        self._tell_op()

    def op_result(self, state: str, why: str = "") -> None:
        """How the step under way came out: "done", or "failed" with why in the person's words."""
        op = self.op
        if op is None or not op["steps"] or op["steps"][-1]["state"] != "doing":
            return
        op["steps"][-1].update(state=state, **({"why": why} if why else {}))
        self._tell_op()

    def op_card(self, card: dict, text: str) -> None:
        """A card waits on the person's press, beside the page: the column shows it, and `text` as the step."""
        op = self.op
        if op is None:
            return
        if op["steps"] and op["steps"][-1]["state"] == "doing":
            op["steps"][-1]["state"] = "done"
        op["steps"].append({"text": text[:160], "state": "waiting", "card": True,
                            **({"note": op["note"]} if op["note"] else {})})
        op["card"] = card
        self._tell_op()

    def op_answered(self, state: str, text: str) -> None:
        """The card is answered (or gone unanswered): its step says how, and the card goes."""
        op = self.op
        if op is None:
            return
        for step in reversed(op["steps"]):
            if step["state"] == "waiting":
                step.update(state=state, text=text[:160])
                break
        op["card"] = None
        self._tell_op()

    def op_end(self, status: str, note: str, unfinished: str = "") -> None:
        """The run ended: the column says how, and stays until the person closes it. A step it never finished
        says `unfinished`; ended waiting on a card, the card and its step stay, to be answered."""
        op = self.op
        if op is None:
            return
        self.ui(self._unwatch_input)
        waiting = status == "waiting" and op["card"] is not None
        for step in op["steps"]:
            if step["state"] == "doing" or step["state"] == "waiting" and not waiting:
                step.update(state="done" if status == "done" else "stopped", why="" if status == "done" else unfinished)
        op.update(end=status, note=note[:300], why=handed(status, note), card=op["card"] if waiting else None)
        self._tell_op()
        self._op_last = {**op, "at": time.monotonic()}
        self.op = None

    def op_kept(self, yes: bool) -> None:
        """The card a run ended waiting on was answered: it goes, and a no ends the run as declined."""
        last = self._op_last
        if self.op is None and last and last["card"] is not None:
            for step in last["steps"]:
                if step["state"] == "waiting":
                    step["state"] = "done" if yes else "cancelled"
            last.update(card=None, **({} if yes else {"end": "declined"}))
            self.tell({"operating": {k: last.get(k) for k in ("task", "step", "note", "held", "end", "why", "steps",
                                                              "card", "wait")}})

    def op_steps(self) -> list:
        """The steps of the run under way, else of the last one."""
        return list((self.op or self._op_last or {}).get("steps") or [])

    def _tell_op(self) -> None:
        op = self.op
        self.tell({"operating": None if op is None else {
            k: op.get(k) for k in ("task", "step", "note", "held", "end", "why", "steps", "card", "wait")}})

    def _watch_input(self) -> None:
        from System.Windows.Forms import Timer
        self._unwatch_input()
        timer = Timer()
        timer.Interval = WATCH_MS
        self._op_seen = _last_input_tick()
        timer.Tick += self._op_tick
        timer.Start()
        self._op_timer = timer

    def _unwatch_input(self) -> None:
        if self._op_timer is not None:
            self._op_timer.Stop()
            self._op_timer.Dispose()
            self._op_timer = None

    def _input_tick(self) -> None:
        """The person's own click or key on the page in the pane (a pointer passing over it or its wheel does not
        count, nor a press on the chat or a card laid over it): Esc stops the run, anything else pauses it."""
        from System.Windows.Forms import Cursor
        seen = _last_input_tick()
        moved, self._op_seen = seen != self._op_seen, seen
        if not moved or self.op is None or self._view is None or self.op.get("held") or not self._view.Visible:
            return
        # The keys and buttons down or pressed since the last tick, read once (reading clears "pressed since").
        keys = ctypes.windll.user32.GetAsyncKeyState
        down = {k for k in range(0x01, 0xFF) if keys(k) & 0x8001}
        point = self._view.PointToClient(Cursor.Position)
        at_page = on_page(point.X, point.Y, self._view.Width, self._view.Height, [] if self._full else self._cuts,
                          self._scale)
        if not went_to_page(bool(down & POINTER_BUTTONS), at_page, bool(down - POINTER_BUTTONS),
                            self._view.ContainsFocus):
            return
        # Waiting for their sign-in, the page is theirs to type in: only Esc stops the run.
        if self.op.get("wait") and VK_ESCAPE not in down:
            return
        self.op["held"] = "stopped" if VK_ESCAPE in down else "paused"
        self._tell_op()

    def _shut(self, forget: bool = True) -> dict | None:
        """The pane closes: every tab's WebView and their processes go, and the reading ends. Closed by the person,
        the page open last is forgotten too; the app quitting keeps it, to offer when the app starts again."""
        sent = self._finish()
        if self._full:
            self.full(self._core, False)
        for tab in self._tabs:
            self._drop(tab)
        self._tabs, self._tab, self._box = [], None, None
        self._quieted = self._full = self._hidden = False
        if forget and self._kept["last"]:
            self._kept["last"] = {}
            self._keep()
        self.tell({"open": False})
        return sent

    def _quiet(self, on: bool) -> None:
        """Quiet hides the pane with the pet and suspends every tab's page; they come back as they were."""
        from Microsoft.Web.WebView2.Core import CoreWebView2MemoryUsageTargetLevel
        if on == self._quieted:
            return
        self._quieted = on
        if on:
            self._pause()
        for tab in self._tabs:
            if tab.view is not None:
                tab.view.Visible = not on and (tab.aside or tab is self._tab and not self._hidden)
            if tab.core is not None:
                tab.core.MemoryUsageTargetLevel = (CoreWebView2MemoryUsageTargetLevel.Low if on
                                                   else CoreWebView2MemoryUsageTargetLevel.Normal)
                if on:
                    tab.core.TrySuspendAsync()
        if not on:
            self._count()

    def _resized(self, sender, args) -> None:
        from System.Windows.Forms import FormWindowState
        if self.form.WindowState == FormWindowState.Minimized:
            self._pause()
        else:
            self._count()

    # ---- the reading

    def _count(self) -> None:
        """Count from now, while the page shows in front."""
        from System.Windows.Forms import Form, FormWindowState
        reading, form = self._reading, self.form
        if (reading is None or not reading["begun"] or reading["since"] is not None or self._view is None
                or self._quieted or self._hidden or not form.Visible
                or form.WindowState == FormWindowState.Minimized
                or Form.ActiveForm is None or not Form.ActiveForm.Equals(form)):
            return
        reading["since"] = time.monotonic()
        self._timer.Start()

    def _settle(self) -> None:
        """Add the time since the last look, not counting what went past a minute without input."""
        reading = self._reading
        if reading is None or reading["since"] is None:
            return
        now = time.monotonic()
        last_input = now - _idle_s()
        reading["s"] += max(0.0, min(now, last_input + IDLE_S) - reading["since"])
        reading["since"] = now

    def _tick(self, sender, args) -> None:
        self._settle()
        reading = self._reading
        if reading is None or self._core is None or not reading["begun"]:
            return
        self._check_secret(reading)
        if reading["facts"] is None and reading["s"] >= browsed.ITEM_S and not reading["secret"] and not self.private:
            # Read long enough to become an Item: what it is, read off the page once.
            reading["facts"] = {}
            task = _run(self._core, facts_script())

            def heard() -> None:
                raw = _wait(task, 10)
                with contextlib.suppress(ValueError, TypeError):
                    reading["facts"] = page_of(reading["url"], json.loads(raw))
            threading.Thread(target=heard, name="mibo-browser", daemon=True).start()

    def _pause(self) -> None:
        self._settle()
        if self._reading is not None:
            self._reading["since"] = None
        self._timer.Stop()

    def _finish(self) -> dict | None:
        """The reading ends: the row to hand over, or None for a private or a sign-in page, or for a glance at
        anything but a find (a find's glance is handed over: the recommender learns from it)."""
        self._pause()
        reading, self._reading = self._reading, None
        if (reading is None or reading["s"] < READ_MIN_S and not reading["id"] or reading["secret"] or self.private
                or not web_address(reading["url"]) or browsed.secret(reading["url"])):
            return None
        return {"url": reading["url"], "title": reading["title"], "s": round(reading["s"], 1),
                "at": reading["at"], "id": reading["id"], "facts": reading["facts"] or {}}

    def _send(self, sent: dict | None) -> None:
        if sent:
            threading.Thread(target=self.visit, args=(sent,), name="mibo-browser", daemon=True).start()


class Reader:
    """Reads with the person's session (the pane's WebView2 folder), out of sight: a hidden WebView2 in
    `form`, made for one site and disposed after (web/tabs.HiddenTab reads through it). Read only: it goes
    only to that site's addresses, and requests to other sites, and any but GET, are refused, so the
    person's cookies go to that site alone; a move to another site (a sign-in elsewhere) stops the read.
    Pace (browsed.Pace): one read at a time, a pause between, a day's cap per site, and a site whose check
    or sign-in wall showed is not read again for a while. Cookies and storage are never read out: only the
    page's text leaves."""

    def __init__(self, folder: Path, form, *, ui: Callable[[Callable[[], None]], None], pace: browsed.Pace):
        self.folder = Path(folder)
        self.form = form
        self.ui = ui
        self.pace = pace

    def read(self, url: str, screens: int = 0, runtime: Path | None = None, background: bool = False,
             start: int = 0) -> dict:
        """{"url", "title", "site", "kind", "author", "published", "description", "text"} (and "rows" for a
        list, `screens` more screens scrolled; the text from character `start`, with "from" and "of" where the
        page has more than one read gives), or {"error": why} with why one of not_web, sign_in_page,
        wall, host_day_cap, failed, or the wall met (verification, sign_in, sign_in_overlay, error, dead, empty,
        robots for a `background` read robots.txt refuses)."""
        import asyncio

        from .web import read as reading
        from .web.tabs import HiddenTab
        with self.pace.lock:
            refused = self.pace.refuse(url)
            if refused:
                return {"error": refused}
            self.pace.wait()
            hidden = _Hidden(self, url)
            try:
                if not hidden.made.wait(READ_WITHIN_S) or hidden.core is None:
                    page = reading.Page(url, wall="failed")
                else:
                    page = asyncio.run(reading.read(HiddenTab(hidden), url, screens=screens, runtime=runtime,
                                                    background=background, mode="pane"))
            finally:
                hidden.close()
            wall = "sign_in" if hidden.left else page.wall
            self.pace.done(url, wall if wall in ("verification", "sign_in", "sign_in_overlay") else "")
            if wall:
                return {"error": wall}
            # The whole text it read: the character's reader (web/pagetext.py) fits it to the model.
            return page_of(page.url, page.data, page.rows, start, most=len(page.data.get("text") or ""))


class Feeds:
    """The person's own feed on a site, read in the background, only where they turned it on in Settings
    (the card's "feed": {on, url, day, n, at, stopped}): at most every FEED_EVERY_S and FEED_DAY_MAX a day a
    site, through the Reader (its pace, robots.txt honoured), read only. A check or a sign-in wall turns the
    site's reading off until the person turns it on again. The rows go to the library as finds."""

    def __init__(self, reader: Reader, runtime: Path, *, shared: Callable[[], bool]):
        self.reader = reader
        self.runtime = Path(runtime)
        self.shared = shared
        self.stop = threading.Event()
        threading.Thread(target=self._run, name="mibo-feeds", daemon=True).start()

    def _run(self) -> None:
        while not self.stop.wait(FEED_LOOK_S):
            for key, feed in feeds_on(self.runtime).items():
                if self.stop.is_set() or not self.shared():
                    break
                today = time.strftime("%Y-%m-%d")
                n = feed.get("n", 0) if feed.get("day") == today else 0
                if n >= FEED_DAY_MAX or time.time() - float(feed.get("at") or 0) < FEED_EVERY_S:
                    continue
                page = self.reader.read(feed["url"], screens=1, runtime=self.runtime, background=True)
                error = page.get("error", "")

                def note(card: dict, error=error, n=n, today=today) -> None:
                    kept = card.setdefault("feed", {})
                    kept.update(at=time.time(), day=today, n=n + 1, last=error or "ok")
                    if error in FEED_STOPS:
                        kept.update(on=False, stopped=error)
                from .web import sites
                sites._change(self.runtime, key, note)
                if not error:
                    keep_finds(self.runtime, key, page.get("rows") or [])


def feeds_on(runtime: Path) -> dict:
    """{site key: its feed} for the sites whose background reading the person turned on."""
    from .web import sites
    return {key: card["feed"] for key, card in sites.load(runtime).items()
            if isinstance(card, dict) and isinstance(card.get("feed"), dict) and card["feed"].get("on")
            and web_address(card["feed"].get("url", ""))}


def keep_finds(runtime: Path, key: str, rows: list) -> int:
    """A feed's rows as library finds of that site (source "feed <site>"); how many were new."""
    from .library import Item, Library, item_id
    from .roam import site_name
    from .web import sites
    library, new = Library(runtime), 0
    for row in rows[:FEED_ROWS]:
        link = str(row.get("link") or "")
        if not row.get("title") or not web_address(link) or sites.key_of(link) != key or browsed.secret(link):
            continue
        if library.get(item_id(link)) is None:
            library.add(Item(url=link, site=site_name(link), kind="post", title=str(row["title"])[:300],
                             author=str(row.get("author") or "")[:200], source=f"feed {key}"))
            new += 1
    return new


class _Hidden:
    """One hidden WebView2 for one site, as HiddenTab drives it: `goto`, `cdp`, `address`, `close`, each
    from a thread other than the form's."""

    def __init__(self, reader: Reader, url: str):
        self.reader = reader
        self.site = browsed.site_of(urlsplit(url).hostname or "")
        self.view = self.core = None
        self.made = threading.Event()
        self.loaded = threading.Event()
        # Sent to another site (a sign-in elsewhere): the read stops there.
        self.left = ""
        reader.ui(self._make)

    def _make(self) -> None:
        from Microsoft.Web.WebView2.WinForms import CoreWebView2CreationProperties, WebView2
        from System.Drawing import Rectangle

        view = WebView2()
        properties = CoreWebView2CreationProperties()
        properties.UserDataFolder = str(self.reader.folder / "webview")
        properties.Language = language()
        properties.AdditionalBrowserArguments = BROWSER_ARGS
        view.CreationProperties = properties
        view.Visible = False
        view.Bounds = Rectangle(0, 0, 1280, 900)
        self.view = view
        # .NET holds no reference to these.
        self._handlers = (self._ready, self._requested, self._starting, self._completed, self._refuse,
                          self._deny, self._cancel)
        view.CoreWebView2InitializationCompleted += self._ready
        self.reader.form.Controls.Add(view)
        view.SendToBack()
        view.EnsureCoreWebView2Async(None)

    def _ready(self, sender, args) -> None:
        if not args.IsSuccess or self.view is None:
            self.made.set()
            return
        from Microsoft.Web.WebView2.Core import CoreWebView2WebResourceContext
        core = self.view.CoreWebView2
        settings = core.Settings
        settings.IsWebMessageEnabled = False
        settings.AreHostObjectsAllowed = False
        settings.AreDevToolsEnabled = False
        settings.AreDefaultScriptDialogsEnabled = False
        settings.AreDefaultContextMenusEnabled = False
        settings.IsStatusBarEnabled = False
        core.IsMuted = True
        core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All)
        core.WebResourceRequested += self._requested
        core.NavigationStarting += self._starting
        core.NavigationCompleted += self._completed
        core.NewWindowRequested += self._refuse
        core.PermissionRequested += self._deny
        core.LaunchingExternalUriScheme += self._cancel
        core.DownloadStarting += self._cancel
        self.core = core
        self.made.set()

    def _requested(self, sender, args) -> None:
        # Only the site asked for, and only reading: nothing else goes anywhere with the cookies.
        request = args.Request
        address = str(request.Uri)
        if (str(request.Method).upper() != "GET" or not web_address(address)
                or browsed.site_of(urlsplit(address).hostname or "") != self.site):
            args.Response = sender.Environment.CreateWebResourceResponse(None, 403, "Blocked", "")

    def _starting(self, sender, args) -> None:
        address = str(args.Uri)
        if not web_address(address):
            args.Cancel = True
            return
        if browsed.site_of(urlsplit(address).hostname or "") != self.site or browsed.secret(address):
            self.left = address
            args.Cancel = True
            self.loaded.set()

    def _completed(self, sender, args) -> None:
        self.loaded.set()

    def _refuse(self, sender, args) -> None:
        args.Handled = True

    def _deny(self, sender, args) -> None:
        # Refused for this read only: the person is asked when they visit the site themselves.
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState
        args.State = CoreWebView2PermissionState.Deny
        args.SavesInProfile = False

    def _cancel(self, sender, args) -> None:
        args.Cancel = True

    def goto(self, url: str, timeout: float = READ_WITHIN_S) -> str:
        """Go to `url` of this site and wait for it; the address where it ended (the other site's when the
        read was sent away)."""
        from .web.tabs import TabError
        if browsed.site_of(urlsplit(url).hostname or "") != self.site:
            raise TabError("another site than the one this read is for")
        self.loaded.clear()
        self.reader.ui(lambda: self.core.Navigate(url))
        if not self.loaded.wait(timeout):
            raise TabError("timeout")
        return self.left or self.address()

    def address(self) -> str:
        found: list = []
        self.reader.ui(lambda: found.append(str(self.core.Source) if self.core is not None else ""))
        return found[0] if found else ""

    def cdp(self, method: str, params: dict) -> dict:
        return _call(self.reader.ui, lambda: self.core, method, params, CDP_S)

    def close(self) -> None:
        def drop() -> None:
            view, self.view, self.core = self.view, None, None
            if view is not None:
                self.reader.form.Controls.Remove(view)
                view.Dispose()
        self.reader.ui(drop)

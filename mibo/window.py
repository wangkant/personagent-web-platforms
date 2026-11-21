"""The local page in a window of its own instead of a browser tab.

pywebview draws it: WebView2 on Windows, where it is installed with mibo,
and whatever `mibo[app]` finds on macOS and Linux. Without it, or when no
window opens, `mibo start` falls back to the browser.
"""
from __future__ import annotations

import contextlib
import hmac
import importlib.util
import json
import os
import secrets
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlsplit, urlunsplit

from . import browsed, petchat, petoff, petshot, quiet

TITLE = "Mibo"
ICON = Path(__file__).with_name("static") / "app"
# Windows files taskbar buttons under this, not under Python.
APP_ID = "wangkant.mibo"
SIZE = (1200, 820)
MIN_SIZE = (400, 560)
# How long the window has to appear before it counts as not opening.
SHOWN_WITHIN = 30
# How long the page has to ask for its title bar before the window takes its own back.
PAGE_WITHIN = 25
# What the page's own title bar may ask of a frameless window.
ACTIONS = ("minimize", "maximize", "restore", "close")
# The pet: a small window with nothing in it but the character, drawn in the
# 200 by 214 at its bottom (PET_FIGURE); the 64 above hold its dial and the line
# of what it is doing (pet.js HEAD_ROOM), taking the pointer only while they show.
PET_SIZE = (200, 278)
PET_FIGURE = (200, 214)
# A pet let go this near a side of the screen, its middle in CSS px, tucks in there.
TUCK_PX = 70
# How far from the right edge of the screen it first stands.
PET_MARGIN = 80
# How tall the strip of the figure that peeks over the app window is, from
# above its head to just under its eyes, until the page has measured the figure (CSS px).
PEEK_HEAD = 110
# How round the corners of the card beside it are: card.css's border-radius (CSS px).
CARD_RADIUS = 14
# How far left of the app window's right edge it peeks: clear of the corner,
# and of the window's buttons when it has to peek from inside (CSS px).
PEEK_INSET = 190
# How long the page takes to duck the figure out of sight, or to sink its head behind the edge.
DUCK_S = 0.24
# The app window rises into place from this far below, fading in: a quarter of a second.
RISE_PX, RISE_STEPS, RISE_STEP_S = 28, 15, 0.016
# Closed, it fades out over these steps.
FADE_STEPS, FADE_STEP_S = 8, 0.016
# How often a dragged pet catches up with the cursor, and a walking one takes a step.
DRAG_STEP_S = 0.008
# The pet's eyes follow the cursor this near its window (CSS px), looked for this often;
# and the person is back when the keyboard or mouse is used after this long without.
NEAR_PX = 240
NEAR_STEP_S = 0.12
AWAY_S = 300
WALK_STEP_S = 0.03
# winuser.h: the extended styles of a window that takes no focus and has no
# taskbar button, and of one that has a taskbar button.
_GWL_EXSTYLE, _WS_EX_NOACTIVATE, _WS_EX_TOOLWINDOW, _WS_EX_APPWINDOW = -20, 0x08000000, 0x00000080, 0x00040000
# The owner of a window that is not a child.
_GWLP_HWNDPARENT = -8
_WM_WINDOWPOSCHANGED = 0x0047
# SetWindowPos: keep focus; move only, keeping size, order and focus; keep
# order and focus; keep place, size and focus; and where in the order.
_NO_FOCUS = 0x0010
_MOVE_ONLY = 0x0001 | 0x0004 | _NO_FOCUS
_KEEP_ORDER = 0x0004 | _NO_FOCUS
_ORDER_ONLY = 0x0001 | 0x0002 | _NO_FOCUS
_HWND_TOPMOST, _HWND_NOTOPMOST = -1, -2
_RGN_OR = 2
_SW_HIDE, _SW_SHOWNOACTIVATE, _SW_RESTORE = 0, 4, 9
# Which mouse button is the primary one.
_SM_SWAPBUTTON, _VK_LBUTTON, _VK_RBUTTON = 23, 0x01, 0x02
# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, and the message a window gets on a monitor of another scale.
_PER_MONITOR_V2, _WM_DPICHANGED = -4, 0x02E0


def available() -> bool:
    try:
        return importlib.util.find_spec("webview") is not None
    except (ImportError, ValueError):
        return False


def _toolkit() -> str | None:
    """Which toolkit draws the window; None lets pywebview choose."""
    if sys.platform == "win32":
        return "edgechromium"
    # pywebview tries GTK first and prints a traceback when it is absent,
    # which it is where the app extra installed Qt.
    with contextlib.suppress(ImportError, ValueError):
        if (sys.platform.startswith("linux") and importlib.util.find_spec("gi") is None
                and importlib.util.find_spec("qtpy") is not None):
            return "qt"
    return None


def icon() -> Path:
    return ICON.with_suffix(".ico" if sys.platform == "win32" else ".png")


def alert(text: str) -> None:
    """A sentence for someone who started mibo with no terminal."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x30)


def _text_menus(window) -> bool:
    """pywebview turns right-click off on Windows. Put it back where there is
    text to paste into or to copy: an API key is often pasted that way.
    Also lets the page mark part of itself as the bar the window is moved by;
    True when this WebView2 can do that."""
    from System import Func, Type  # pythonnet, which pywebview has loaded

    form = window.native
    found = {"regions": False}

    def on_menu(sender, args) -> None:
        target = args.ContextMenuTarget
        args.Handled = not (target.IsEditable or target.HasSelection)

    def enable() -> None:
        core = form.browser.webview.CoreWebView2
        core.Settings.AreDefaultContextMenusEnabled = True
        core.ContextMenuRequested += on_menu
        # `app-region: drag` in the page; an older runtime has no such setting.
        with contextlib.suppress(Exception):
            core.Settings.IsNonClientRegionSupportEnabled = True
            found["regions"] = bool(core.Settings.IsNonClientRegionSupportEnabled)

    form.Invoke(Func[Type](enable))
    return found["regions"]


def _own_pages(window, origin: str, open_link: Callable[[str], None]) -> None:
    """Keep a Windows window on mibo's own pages. Any page it loads gets
    pywebview's bridge to Python, so nothing dropped on it loads, it goes to no
    other site, and a page's new windows go to `open_link` when they are web
    addresses, never to whatever else Windows would start for them.
    The players' iframes are frames, not the window's page, and still load."""
    from System import Func, Type

    form = window.native

    def on_navigation(sender, args) -> None:
        address = urlsplit(str(args.Uri))
        args.Cancel = f"{address.scheme}://{address.netloc}" != origin

    def on_new_window(sender, args) -> None:
        args.Handled = True
        address = str(args.Uri)
        if urlsplit(address).scheme in ("http", "https"):
            open_link(address)

    def hold() -> None:
        form.browser.webview.AllowExternalDrop = False
        core = form.browser.webview.CoreWebView2
        core.NavigationStarting += on_navigation
        # pywebview's own handler gives any address at all to the system.
        core.NewWindowRequested -= form.browser.on_new_window_request
        core.NewWindowRequested += on_new_window

    form.Invoke(Func[Type](hold))


# Window messages and metrics the frameless window handles (winuser.h).
_WM_NCDESTROY, _WM_NCCALCSIZE, _WM_WINDOWPOSCHANGING = 0x0082, 0x0083, 0x0046
_SWP_NOSIZE = 0x0001
_WM_SIZE, _SIZE_RESTORED, _SIZE_MINIMIZED, _SIZE_MAXIMIZED = 0x0005, 0, 1, 2
_SM_CYSIZEFRAME, _SM_CXPADDEDBORDER = 33, 92
# shellapi.h: is there a taskbar that hides itself at this edge of this screen,
# and how far a maximized page stays from it.
_ABM_GETAUTOHIDEBAREX = 0x0000000B
_HIDDEN_TASKBAR_GAP = 2
# A window's style, and the frame full screen takes off it; SetWindowPos for it: the frame worked out again.
_GWL_STYLE, _WS_CAPTION, _WS_THICKFRAME = -16, 0x00C00000, 0x00040000
_FULL_POS, _SW_RESTORE = 0x0020 | 0x0200, 9
_SW_HIDE, _SW_SHOWMAXIMIZED, _SW_SHOWMINNOACTIVE, _WPF_RESTORETOMAXIMIZED = 0, 3, 7, 2
# SetWindowPos: keep place, size and order, and work the frame out again.
_FRAME_CHANGED = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020


def _strip_title_bar(window):
    """Take the title bar off a Windows window and leave everything else the
    system gives one: the resize borders at the sides and the bottom, the
    shadow, snapping and the taskbar entry. The page draws its own bar.

    Returns what must stay referenced while the window lives, or None when
    the title bar is still there."""
    import ctypes
    from ctypes import wintypes

    from System import Func, Type

    form = window.native
    # Libraries of our own: pywebview calls the shared ones with other argument types.
    user32, comctl32, shell32 = ctypes.WinDLL("user32"), ctypes.WinDLL("comctl32"), ctypes.WinDLL("shell32")
    result_t = ctypes.c_ssize_t
    proc_t = ctypes.WINFUNCTYPE(result_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT), ("work", wintypes.RECT),
                    ("flags", wintypes.DWORD)]

    class AppBar(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                    ("edge", wintypes.UINT), ("rect", wintypes.RECT), ("lparam", wintypes.LPARAM)]

    class WindowPos(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("after", wintypes.HWND), ("x", ctypes.c_int), ("y", ctypes.c_int),
                    ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]

    comctl32.DefSubclassProc.restype = result_t
    comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    comctl32.SetWindowSubclass.restype = wintypes.BOOL
    comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, proc_t, ctypes.c_size_t, ctypes.c_size_t]
    comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, proc_t, ctypes.c_size_t]
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    shell32.SHAppBarMessage.restype = ctypes.c_size_t
    shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(AppBar)]

    def frame_height(hwnd) -> int:
        dpi = user32.GetDpiForWindow(hwnd) or 96
        return (user32.GetSystemMetricsForDpi(_SM_CYSIZEFRAME, dpi)
                + user32.GetSystemMetricsForDpi(_SM_CXPADDEDBORDER, dpi))

    def leave_hidden_taskbar(hwnd, rect) -> None:
        """A page that covers the whole screen counts as full screen, and a
        taskbar set to hide itself would never come back: stop short of its edge."""
        info = MonitorInfo()
        info.size = ctypes.sizeof(MonitorInfo)
        if not user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info)):
            return
        for edge, side, step in ((0, "left", 1), (1, "top", 1), (2, "right", -1), (3, "bottom", -1)):
            bar = AppBar()
            bar.size, bar.edge, bar.rect = ctypes.sizeof(AppBar), edge, info.monitor
            if shell32.SHAppBarMessage(_ABM_GETAUTOHIDEBAREX, ctypes.byref(bar)):
                setattr(rect, side, getattr(rect, side) + step * _HIDDEN_TASKBAR_GAP)

    # Back from the taskbar or from maximized, WinForms sizes the window once
    # more from its page as if it had a title bar: a bar's height taller each
    # time (and, on a monitor scaled unlike the main one, with that one's frame,
    # narrower too). That one change keeps the size the system brought it back at.
    back = {"away": False, "size": None}

    def proc(hwnd, message, wparam, lparam, _subclass, _data):
        if message == _WM_SIZE:
            if wparam in (_SIZE_MINIMIZED, _SIZE_MAXIMIZED):
                back["away"] = True
            elif back["away"]:
                back["away"] = False
                if wparam == _SIZE_RESTORED:
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    back["size"] = (rect.right - rect.left, rect.bottom - rect.top)
        elif message == _WM_WINDOWPOSCHANGING and back["size"]:
            pos = ctypes.cast(ctypes.c_void_p(lparam), ctypes.POINTER(WindowPos))[0]
            (wide, high), back["size"] = back["size"], None
            if not pos.flags & _SWP_NOSIZE and (pos.cx, pos.cy) != (wide, high):
                pos.cx, pos.cy = wide, high
        if message == _WM_NCCALCSIZE and wparam:
            # The system works out the sides and the bottom; the top stays where
            # the window begins, so there is no title bar. A maximized window
            # reaches past the screen by its frame, and the top comes back in.
            rect = ctypes.cast(ctypes.c_void_p(lparam), ctypes.POINTER(wintypes.RECT))
            top = rect[0].top
            result = comctl32.DefSubclassProc(hwnd, message, wparam, lparam)
            if result == 0:
                with contextlib.suppress(Exception):
                    zoomed = bool(user32.IsZoomed(hwnd))
                    rect[0].top = top + (frame_height(hwnd) if zoomed else 0)
                    if zoomed:
                        leave_hidden_taskbar(hwnd, rect[0])
            return result
        if message == _WM_NCDESTROY:
            comctl32.RemoveWindowSubclass(hwnd, callback, 1)
        return comctl32.DefSubclassProc(hwnd, message, wparam, lparam)

    callback = proc_t(proc)
    done = {"ok": False}

    def install() -> None:
        hwnd = form.Handle.ToInt64()
        if comctl32.SetWindowSubclass(hwnd, callback, 1, 0):
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, _FRAME_CHANGED)
            done["ok"] = True

    def undo() -> None:
        """Give the window its title bar back."""
        def remove() -> None:
            hwnd = form.Handle.ToInt64()
            comctl32.RemoveWindowSubclass(hwnd, callback, 1)
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, _FRAME_CHANGED)
        form.Invoke(Func[Type](remove))

    # Called from the window's own thread before it shows, so no title bar is ever drawn.
    install()
    return (callback, undo) if done["ok"] else None


def _watch_moves(hwnd: int, moved: Callable[[], None]):
    """Call `moved` on the window's own thread each time it has moved, changed
    size, shown or hidden: in the same instant, not on a timer that trails it.
    Called on that thread; returns what must stay referenced while the window lives."""
    import ctypes
    from ctypes import wintypes

    comctl32 = ctypes.WinDLL("comctl32")
    result_t = ctypes.c_ssize_t
    proc_t = ctypes.WINFUNCTYPE(result_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
    comctl32.DefSubclassProc.restype = result_t
    comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, proc_t, ctypes.c_size_t, ctypes.c_size_t]
    comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, proc_t, ctypes.c_size_t]

    def proc(hwnd, message, wparam, lparam, _subclass, _data):
        # The window is where it is going first, then what goes with it follows.
        result = comctl32.DefSubclassProc(hwnd, message, wparam, lparam)
        if message == _WM_WINDOWPOSCHANGED:
            moved()
        elif message == _WM_NCDESTROY:
            comctl32.RemoveWindowSubclass(hwnd, callback, 2)
        return result

    callback = proc_t(proc)
    # 2: the title bar's own handler is 1.
    comctl32.SetWindowSubclass(hwnd, callback, 2, 0)
    return callback


def _watch_scale(hwnd: int, changed: Callable[[float, tuple[int, int, int, int]], None]):
    """Call `changed(scale, suggested)` on the window's own thread when it comes onto a monitor
    of another scale, or its monitor's scale changes: its new device px per CSS px, and the
    (left, top, right, bottom) Windows suggests for the same size in CSS px. WinForms on the
    .NET Framework leaves the window as it was in device px; WebView2 draws at the new scale
    by itself. Called on that thread; returns what must stay referenced while the window lives."""
    import ctypes
    from ctypes import wintypes

    comctl32 = ctypes.WinDLL("comctl32")
    result_t = ctypes.c_ssize_t
    proc_t = ctypes.WINFUNCTYPE(result_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
    comctl32.DefSubclassProc.restype = result_t
    comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, proc_t, ctypes.c_size_t, ctypes.c_size_t]
    comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, proc_t, ctypes.c_size_t]

    def proc(hwnd, message, wparam, lparam, _subclass, _data):
        result = comctl32.DefSubclassProc(hwnd, message, wparam, lparam)
        if message == _WM_DPICHANGED:
            box = ctypes.cast(ctypes.c_void_p(lparam), ctypes.POINTER(wintypes.RECT))[0]
            changed((wparam & 0xFFFF) / 96, (box.left, box.top, box.right, box.bottom))
        elif message == _WM_NCDESTROY:
            comctl32.RemoveWindowSubclass(hwnd, callback, 3)
        return result

    callback = proc_t(proc)
    comctl32.SetWindowSubclass(hwnd, callback, 3, 0)
    return callback


def _user32():
    """user32 with the argument types the pet's calls need: a library of our
    own, as pywebview calls the shared one with other types."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    hwnd = wintypes.HWND
    user32.GetWindowRect.argtypes = user32.GetClientRect.argtypes = [hwnd, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [hwnd, ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.IsWindowVisible.argtypes = user32.IsIconic.argtypes = [hwnd]
    user32.ShowWindow.argtypes = [hwnd, ctypes.c_int]
    user32.SetWindowPos.argtypes = [hwnd, hwnd, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.UINT]
    user32.GetWindowLongW.argtypes = [hwnd, ctypes.c_int]
    user32.SetWindowLongW.argtypes = [hwnd, ctypes.c_int, wintypes.LONG]
    user32.SetWindowLongPtrW.argtypes = [hwnd, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetForegroundWindow.argtypes = [hwnd]
    user32.SetWindowRgn.argtypes = [hwnd, wintypes.HANDLE, wintypes.BOOL]
    user32.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
    user32.MonitorFromRect.restype = user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = [hwnd, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    return user32


def _monitor(user32, box: tuple[int, int, int, int] | int) -> tuple[tuple[int, int, int, int], float]:
    """The monitor a window is on, by its handle, or would be on at `box` (left, top, right,
    bottom): the one most of it covers, as Windows chooses its scale. Its whole rectangle,
    and its device px per CSS px."""
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT), ("work", wintypes.RECT),
                    ("flags", wintypes.DWORD)]

    # MONITOR_DEFAULTTONEAREST.
    handle = (user32.MonitorFromWindow(box, 2) if isinstance(box, int)
              else user32.MonitorFromRect(ctypes.byref(wintypes.RECT(*box)), 2))
    info, dpi = MonitorInfo(), wintypes.UINT()
    info.size = ctypes.sizeof(MonitorInfo)
    user32.GetMonitorInfoW(handle, ctypes.byref(info))
    # MDT_EFFECTIVE_DPI.
    ctypes.windll.shcore.GetDpiForMonitor(wintypes.HANDLE(handle), 0, ctypes.byref(dpi), ctypes.byref(dpi))
    m = info.monitor
    return (m.left, m.top, m.right, m.bottom), dpi.value / 96


def _onto(user32, box: tuple[int, int, int, int], monitor: tuple[int, int, int, int]) -> tuple[int, int]:
    """Where a window at `box` goes, moved as little as it takes, for most of it to be on
    `monitor`: (left, top). Otherwise its new size would put it back on the last monitor,
    and the two scales would take turns."""
    left, top, right, bottom = box
    wide, high = right - left, bottom - top
    # Its middle on the monitor first; then, for a monitor it only partly lies beside, all of it.
    x = min(max(left + wide // 2, monitor[0] + 2), monitor[2] - 2) - wide // 2
    y = min(max(top + high // 2, monitor[1] + 2), monitor[3] - 2) - high // 2
    if _monitor(user32, (x, y, x + wide, y + high))[0] != monitor:
        x = min(max(x, monitor[0]), monitor[2] - wide)
        y = min(max(y, monitor[1]), monitor[3] - high)
    return x, y


@contextlib.contextmanager
def _interrupt(stop: Callable[[], None]) -> Iterator[None]:
    """Ctrl+C calls `stop` at once. The main thread sits in the window's own
    loop, where a Python signal handler would wait for it to come back."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    reader, writer = socket.socketpair()
    writer.setblocking(False)

    def wait() -> None:
        with contextlib.suppress(OSError):
            if reader.recv(1):
                stop()

    handler = signal.signal(signal.SIGINT, lambda *_: None)
    wakeup = signal.set_wakeup_fd(writer.fileno(), warn_on_full_buffer=False)
    threading.Thread(target=wait, name="mibo-interrupt", daemon=True).start()
    try:
        yield
    finally:
        signal.set_wakeup_fd(wakeup)
        signal.signal(signal.SIGINT, handler)
        writer.close()
        reader.close()


# The pet's menu: its colours in a light and a dark system theme (back, ink,
# hover, rule, edge), and how round its hover and its corners are (px).
MENU_COLORS = {"light": ("#ffffff", "#1f2328", "#eef1f5", "#e6e9ee", "#d9dde3"),
               "dark": ("#2b2d31", "#eceef1", "#3a3d43", "#3d4046", "#46494f")}
MENU_ROUND = 5
_menu_class = None


def _round_corners(hwnd: int) -> None:
    """Windows 11's small round corners and its own thin edge for a menu; nothing on Windows 10."""
    import ctypes

    dwmapi = ctypes.WinDLL("dwmapi")
    # DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUNDSMALL.
    dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 33, ctypes.byref(ctypes.c_int(3)), 4)


def _menu_look(scale: float):
    """A renderer that draws the pet's menu in the app's own neutral look: flat,
    a rounded hover, thin rules between groups, and the system's light or dark theme;
    `scale` device px per px, the monitor's where the menu shows."""
    global _menu_class
    import winreg

    import clr
    from System.Drawing import ColorTranslator, Pen, Rectangle, SolidBrush
    from System.Drawing.Drawing2D import GraphicsPath, SmoothingMode
    from System.Windows.Forms import (ToolStripArrowRenderEventArgs, ToolStripItemImageRenderEventArgs,
                                      ToolStripItemRenderEventArgs, ToolStripProfessionalRenderer,
                                      ToolStripRenderEventArgs, ToolStripSeparatorRenderEventArgs)

    # pythonnet overrides a protected method only when told its .NET signature.
    def override(kind):
        return clr.clrmethod(None, [kind])

    if _menu_class is None:
        class MenuLook(ToolStripProfessionalRenderer):
            # pythonnet makes a .NET class of it, once per process.
            __namespace__ = "Mibo.Pet"

            def __init__(self):
                pass

            @override(ToolStripRenderEventArgs)
            def OnRenderToolStripBackground(self, e):
                e.Graphics.Clear(self.back)

            @override(ToolStripRenderEventArgs)
            def OnRenderToolStripBorder(self, e):
                # Windows 11 draws its own edge round the rounded window.
                if not self.rounded:
                    pen = Pen(self.edge)
                    e.Graphics.DrawRectangle(pen, 0, 0, e.ToolStrip.Width - 1, e.ToolStrip.Height - 1)
                    pen.Dispose()

            @override(ToolStripRenderEventArgs)
            def OnRenderImageMargin(self, e):
                pass

            @override(ToolStripItemRenderEventArgs)
            def OnRenderMenuItemBackground(self, e):
                if not (e.Item.Selected and e.Item.Enabled):
                    return
                inset = round(2 * self.scale)
                box = Rectangle(inset, 0, e.Item.Width - 2 * inset, e.Item.Height)
                path, d = GraphicsPath(), round(MENU_ROUND * 2 * self.scale)
                path.AddArc(box.X, box.Y, d, d, 180, 90)
                path.AddArc(box.Right - d, box.Y, d, d, 270, 90)
                path.AddArc(box.Right - d, box.Bottom - d, d, d, 0, 90)
                path.AddArc(box.X, box.Bottom - d, d, d, 90, 90)
                path.CloseFigure()
                e.Graphics.SmoothingMode = SmoothingMode.AntiAlias
                brush = SolidBrush(self.hover)
                e.Graphics.FillPath(brush, path)
                brush.Dispose()
                path.Dispose()

            @override(ToolStripSeparatorRenderEventArgs)
            def OnRenderSeparator(self, e):
                pen = Pen(self.rule)
                middle, side = e.Item.Height // 2, round(10 * self.scale)
                e.Graphics.DrawLine(pen, side, middle, e.Item.Width - side, middle)
                pen.Dispose()

            @override(ToolStripArrowRenderEventArgs)
            def OnRenderArrow(self, e):
                # A small chevron for the submenu, in the text's colour.
                k = self.scale
                box, pen = e.ArrowRectangle, Pen(self.ink, 1.6 * k)
                x, y = box.X + box.Width / 2 - 2 * k, box.Y + box.Height / 2
                e.Graphics.SmoothingMode = SmoothingMode.AntiAlias
                e.Graphics.DrawLine(pen, float(x), y - 3 * k, x + 3 * k, float(y))
                e.Graphics.DrawLine(pen, x + 3 * k, float(y), float(x), y + 3 * k)
                pen.Dispose()

            @override(ToolStripItemImageRenderEventArgs)
            def OnRenderItemCheck(self, e):
                k = self.scale
                box, pen = e.ImageRectangle, Pen(self.ink, 1.6 * k)
                x, y = box.X + box.Width / 2, box.Y + box.Height / 2
                e.Graphics.SmoothingMode = SmoothingMode.AntiAlias
                e.Graphics.DrawLine(pen, x - 4 * k, float(y), x - k, y + 3 * k)
                e.Graphics.DrawLine(pen, x - k, y + 3 * k, x + 5 * k, y - 3 * k)
                pen.Dispose()

        _menu_class = MenuLook
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            light = bool(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except OSError:
        light = True
    look = _menu_class()
    look.back, look.ink, look.hover, look.rule, look.edge = (
        ColorTranslator.FromHtml(value) for value in MENU_COLORS["light" if light else "dark"])
    # Windows 11 (build 22000 on) rounds the menu itself (_round_corners).
    look.rounded = sys.getwindowsversion().build >= 22000
    look.scale = scale
    return look


class _PetApi:
    """What the pet's page may ask of its window."""

    def __init__(self, owner: "AppWindow"):
        self._owner = owner

    def open(self) -> None:
        self._owner.show()

    def menu(self) -> None:
        self._owner._pet_menu()

    def walk(self, dx: float, ms: float) -> None:
        """A walk of `ms` milliseconds, `dx` CSS px every 30 ms; back when it ends."""
        self._owner._pet_walk(dx, ms)

    def place(self, x: float, start: float) -> None:
        """The page walks it: its window's left edge to `x` CSS px on the screen,
        moved when the page draws the feet that stand still as it goes; `start`
        is where it was as this walk began."""
        self._owner._pet_place(x, start)

    def rest(self) -> None:
        """Stop walking now: pressed, or a card is coming."""
        self._owner._walking = False

    def drag(self, x: float, y: float) -> None:
        """The pet's window follows the cursor until the button is let go,
        held where it was pressed: (x, y) CSS px inside it."""
        self._owner._pet_drag(x, y)

    def drop(self) -> None:
        self._owner._dragging = False

    def words(self) -> dict:
        """The labels of the small actions beside the pet: the same as its menu's."""
        return self._owner.words()

    def room(self, on: bool) -> None:
        """While it moves, its window takes the pointer round all of it, so a
        move is never cut; at rest only round the figure."""
        self._owner._pet_room(on)

    def pose(self) -> str:
        """standing; leaving the ground, peeking over the app window, or returning."""
        return self._owner._pose

    def figure(self) -> str:
        return self._owner.figure()

    def measured(self, head: float, outline: list) -> None:
        """How tall the strip of this figure that peeks over the window is, and
        where the figure is drawn in its 160 by 190 box, room for its moves
        included: [left, top, right, bottom]. CSS px."""
        self._owner._pet_measured(head, outline)

    def card(self, width: float, height: float, box: list | None = None) -> str:
        return self._owner._pet_card(width, height, box)

    def open_at(self, target: str) -> None:
        self._owner.open_at(target)

    def spots(self, boxes: list, bare: bool = False) -> None:
        """What else takes the pointer round the figure: [x, y, width, height, corner] CSS px
        of the pet's 200 by 278 cell; `bare` while the figure itself takes none (tucked)."""
        self._owner._pet_spots(boxes, bare)

    def chat(self, how: str, payload: dict | None = None) -> None:
        """Its compact chat beside it (petchat.py): "show", "away", or "take" with what to attach."""
        petchat.of(self._owner).do(how, payload)

    def tuck(self) -> str:
        """Let go near a side of the screen with no other screen beyond: flush against it,
        and which side, "left" or "right"; else ""."""
        return self._owner._pet_tuck()

    def browse(self, address: str) -> None:
        """Its dial's "Open the browser": the app window, `address` in its browser."""
        if urlsplit(address).scheme in ("http", "https"):
            self._owner._pet_browse(address)


def _own_process() -> None:
    """This process as Windows should take it: sharp on every monitor, and the app's own entry on the taskbar."""
    import ctypes
    # Per-monitor aware (v2) before any window is made. The first call wins, so pywebview's
    # SetProcessDPIAware later changes nothing; the packaged app's manifest says the same.
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(_PER_MONITOR_V2))
    with contextlib.suppress(Exception):
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


class AppWindow:
    """One window on `url`. While `background` says so, closing hides the
    window behind a notification-area icon instead, where the system has one
    for us (Windows); `words` names that icon's menu.

    With `pet` (Windows), mibo starts as the character alone on the
    desktop: the window stays hidden until the pet is double-clicked, and
    closing it hides it again. The pet's menu quits, and chooses the figure
    among `figures` ([(key, label)]) through `choose`; `figure` says which it
    is. While the window shows, the pet peeks over its top edge and goes
    wherever the window goes."""

    def __init__(self, url: str, *, storage: Path, background: Callable[[], bool] = lambda: False,
                 words: Callable[[], dict] = lambda: {}, start_hidden: bool = False,
                 pet: bool = False, figure: Callable[[], str] = lambda: "",
                 figures: Callable[[], list[tuple[str, str]]] = lambda: [],
                 choose: Callable[[str], object] = lambda key: None,
                 links: Callable[[], str] = lambda: "app", find: Callable[[str], str] = lambda address: "",
                 address: Callable[[str], str] = lambda ident: ""):
        self.url = url
        self.storage = storage
        self.background = background
        self.words = words
        self.start_hidden = start_hidden
        self.pet = pet and sys.platform == "win32"
        self.figure = figure
        self.figures = figures
        self.choose = choose
        # Where links open, "app" or "system"; the find at an address, and a find's address (see browser.py).
        self.links = links
        self.find = find
        self.address = address
        self._browser = self._reader = self._feeds = None
        # A video full screen in the window: the WebView showing it and how the window stood before.
        self._full = None
        self._pet = None
        self._pet_strip = None
        self._pet_hwnd = self._main_hwnd = 0
        # Device px per CSS px of the monitor each window is on (_watch_scale), and its handlers,
        # which .NET holds no reference to; and the pet's walk: where it began, CSS px and device px.
        self._pet_scale = self._main_scale = 1.0
        self._scale_watch = self._pet_scale_watch = None
        self._rescaling = False
        self._walk_from = (None, 0)
        self._user32 = None
        # standing on the ground; leaving it, peeking over the app window's top
        # edge, or returning to the ground. The pet's page is told each change.
        self._pose = "standing"
        # The pet's window is held on the app window's edge: owned by it and moved with it.
        self._attached = False
        # Where the pet's window stood on the ground: its left and its bottom.
        self._ground = (0, 0)
        # How tall the peeking pet's window is, CSS px; the page measures each figure.
        self._head = PEEK_HEAD
        # Where the figure is drawn in its box, room for its moves included, and
        # where the card is: the window takes the pointer over these alone.
        self._outline = (0, 0, *PET_SIZE)
        self._card_box = (0, 0, 0, 0)
        # What else the page shows round the figure (its dial, the line above it, a tucked head):
        # [x, y, width, height, corner] CSS px of the window; with `_bare` the figure itself takes nothing.
        self._spots: list = []
        self._bare = False
        # The compact chat beside the pet (petchat.of), made the first time it is asked for.
        self._compact = None
        # One way at a time: to the window or back to the ground.
        self._turn = threading.Lock()
        self._dragging = False
        self._walking = False
        # The app window's move handler; .NET holds no reference.
        self._watch = None
        # Which side of the figure a card is open on: "left", "right", or "" for none.
        self._card = ""
        # Where the pet asked the window's page to go, until the page takes it.
        self._goto = ""
        # Out of sight while a game or something full screen is in front (quiet.py).
        # The tray brings it back, and then it stays until quiet starts again.
        self._hushed = False
        self._by_hand = False
        # Hidden from its own menu: it stays so until the tray brings it back.
        self._tucked = False
        # Moving: its window takes the pointer round all of it (_pet_room).
        self._room = False
        self._hush = threading.Lock()
        # Menu handlers stay referenced here, or .NET would lose them.
        self._open_click = lambda sender, event: self.show()
        # 显示桌宠 off (petoff.py): no figure, finds as a card alone at the screen's corner; the tray turns it on.
        self._pet_off = False
        self._pet_click = lambda sender, event: petoff.turn(self, True) if self._pet_off else self._show_pet()
        self._quit_click = lambda sender, event: self.close()
        self._figure_click = lambda sender, event: (self.choose(str(sender.Tag)), self.refigure())
        self._settings_click = lambda sender, event: self.open_at("settings")
        self._bring_click = lambda sender, event: self._tell(self._pet, "petBring()")
        self._snooze_click = lambda sender, event: self._tell(
            self._pet, "petSnooze(true)" if str(sender.Tag) == "on" else "petSnooze(false)")
        self._tuck_click = lambda sender, event: petoff.turn(self, False)
        self._share_click = lambda sender, event: petshot.save(self)
        self.opened = False
        # Why no window opened, for the log.
        self.error = ""
        self.hidden = False
        self._window = None
        self._leaving = False
        self._tray = None
        self._told = False
        # A window started for the background is created minimized.
        self._minimized = start_hidden
        #: The window has no title bar of its own: the page draws the bar and its buttons.
        self.frameless = False
        self.maximized = False
        # The frameless window's message handler and how to undo it; .NET holds no reference.
        self._frame = None
        self._frame_seen = threading.Event()
        # Only the page inside this window knows it: a browser tab on the same
        # address neither draws the window's bar nor presses its buttons.
        self.frame_key = secrets.token_urlsafe(16)

    @property
    def can_hide(self) -> bool:
        """Closing can leave mibo running behind an icon."""
        return self._tray is not None

    def show(self) -> bool:
        """Bring the window back in front; False when there is none. With the
        pet, the window comes up where the pet was and the pet peeks over it."""
        if not self.opened or self._leaving:
            return False
        if self._pet is not None:
            if self._attached:
                self._on_ui(self._window.native.Activate)
            else:
                threading.Thread(target=self._go_in, name="mibo-pet", daemon=True).start()
            return True
        try:
            # Only out of the taskbar: restoring a maximized window would shrink it.
            if self._minimized:
                self._window.restore()
            if self.hidden:
                self._page_seen(True)
            self._window.show()
            if self.hidden:
                self.hidden = False
                if self._tray is not None:
                    self._on_ui(lambda: setattr(self._tray, "Visible", False))
        except Exception:
            return False
        return True

    def hide(self) -> bool:
        """Leave the window for the notification area; False when it cannot."""
        if self._tray is None or not self.opened or self._leaving:
            return False
        words = {"still": "", **self.words()}

        def away() -> None:
            self._tray.Visible = True
            if not self._told and words["still"]:
                self._told = True
                self._tray.ShowBalloonTip(5000, TITLE, words["still"], self._tray_info)

        try:
            self._page_seen(False)
            self._window.hide()
            self.hidden = True
            self._on_ui(away)
        except Exception:
            return False
        return True

    def open_at(self, target: str) -> bool:
        """Show the window with its page asked to go to `target` ("feed/<id>", "feed" or "chat")."""
        self._goto = str(target or "")
        shown = self.show()
        # A find from the pet's card opens in the window's browser too, over the chat that carries it.
        if self._goto.startswith("feed/") and self._browser is not None and self.links() != "system":
            found = self.address(self._goto[5:])
            if found:
                self._browser.open(found)
        # A window already in front learns it from this, one coming up from showing.
        self._frame_changed()
        return shown

    # What the pages are told as it happens, so they need not keep asking (pet.js, app.js).

    def _tell(self, view, script: str) -> None:
        """Run `script` in a window's page, on a thread of its own: whoever
        tells it, the page's server among them, never waits for the page."""
        threading.Thread(target=lambda: view.evaluate_js(script), name="mibo-tell", daemon=True).start()

    def _page_seen(self, seen: bool) -> None:
        """The app window's WebView told it shows or is hidden in the
        notification area, before the window itself goes (as in _put_away):
        its page then knows it is hidden (document.hidden) and stops asking."""
        view = self._window.native.browser.webview
        self._on_ui(lambda: setattr(view, "Visible", seen))

    def _frame_changed(self) -> None:
        if self.opened:
            self._tell(self._window, "window.paFrame && paFrame()")

    def pet_look(self) -> None:
        """A line or something found is waiting for the pet: it looks now."""
        if self.opened and self._pet is not None and not self._hushed:
            self._tell(self._pet, "petLook()")

    def pet_busy(self, doing: str) -> None:
        """What the character is doing in a chat, for the pet to show:
        "thinking", "searching", "reading", or "" when the turn is over."""
        if self.opened and self._pet is not None and not self._hushed:
            self._tell(self._pet, f"window.petActivity && petActivity({json.dumps(doing)})")

    def refigure(self) -> None:
        """The figure may have changed: the pet and the app window read it again.
        A hushed pet reads it when it wakes."""
        if self.opened and self._pet is not None and not self._hushed:
            self._tell(self._pet, "petFigure()")
        self._frame_changed()

    def chats_changed(self) -> None:
        """A chat changed (a reply, a remark, something shared or delivered):
        the app window's page looks now, unless it is hidden."""
        if self.opened and not self.hidden:
            self._tell(self._window, "window.paChats && paChats()")
        # The pet's compact chat too, while it is out (petchat.py).
        if self._compact is not None and self._compact.out:
            self._tell(self._compact.window, "window.paChats && paChats()")

    def notify(self, text: str) -> None:
        """A scheduled task's message (panels/schedule.py) or a card waiting on the person (cardwait.py), as a
        system notification when the app window is not in front. The icon it comes from shows for as long as the
        notification."""
        if not self.opened or self._tray is None or self._leaving:
            return
        from System.Windows.Forms import Form

        def tell() -> None:
            if not self.hidden and Form.ActiveForm is not None and Form.ActiveForm == self._window.native:
                return
            shown = self._tray.Visible
            self._tray.Visible = True
            self._tray.ShowBalloonTip(8000, TITLE, " ".join(text.split())[:200], self._tray_info)
            if not shown:
                threading.Timer(10, lambda: self._on_ui(back)).start()

        def back() -> None:
            if not self.hidden:
                self._tray.Visible = False

        self._on_ui(tell)

    def pick_folder(self) -> str:
        """The system's folder dialog over the app window (Settings › Files and commands): the folder picked, or ""."""
        import webview
        picked = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return picked[0] if picked else ""

    def pick_files(self, many: bool) -> list[str]:
        """The system's Open dialog over the app window (a page's upload card): the files picked, or none."""
        import webview
        return list(self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=many) or [])

    def save_file(self, name: str) -> str:
        """The system's Save dialog over the app window (Settings › 记忆's export), offering `name`: the path chosen, or ""."""
        import webview
        picked = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name)
        return (picked if isinstance(picked, str) else picked[0] if picked else "") or ""

    def take_goto(self) -> str:
        """Where the page was asked to go, once."""
        target, self._goto = self._goto, ""
        return target

    def open_link(self, address: str) -> None:
        """A link that leaves the app: the browser inside the app window, unless the person chose the system's."""
        if self._browser is not None and self.links() != "system":
            if self.hidden or self._minimized:
                self.show()
            self._browser.open(address)
        else:
            import webbrowser
            threading.Thread(target=webbrowser.open, args=(address,), name="mibo-link", daemon=True).start()

    def browse_place(self, box: list, scale: float, cuts: list) -> bool:
        """Where the page keeps the browser's pane, CSS px, its device pixel ratio, and what of the page lies over
        the pane (rects inside it: the chat in a narrow window, the bar's suggestions); False with no pane."""
        if self._browser is None:
            return False
        self._browser.place(box, scale, cuts)
        return True

    def browse_room(self, wide: float, scale: float) -> bool:
        """The pane came to the front and its page asks for a window `wide` of its CSS px wide, or with 0 it left
        the front and the window takes its own size back (browser.Pane._room); False with no pane."""
        if self._browser is None:
            return False
        self._browser.room(wide, scale)
        return True

    def browse_do(self, action: str, tab: int = 0) -> bool:
        """A button of the pane's bar or its tab strip (browser.ACTIONS; `tab` names the tab); False with no pane."""
        if self._browser is None:
            return False
        self._browser.do(action, tab)
        return True

    def _watch_main_scale(self) -> None:
        """On the window's thread: from monitor to monitor it keeps its size in CSS px."""
        form = self._window.native
        self._main_scale = form._scale
        self._scale_watch = _watch_scale(form.Handle.ToInt64(), self._main_rescaled)

    def _main_rescaled(self, scale: float, suggested: tuple[int, int, int, int]) -> None:
        """Where Windows suggests, no smaller than MIN_SIZE CSS px there, and the pane where the page keeps it."""
        import ctypes
        from ctypes import wintypes

        from System.Drawing import Size

        form, old = self._window.native, self._main_scale
        self._main_scale = scale
        user32 = ctypes.WinDLL("user32")
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
        least = Size(int(MIN_SIZE[0] * scale), int(MIN_SIZE[1] * scale))
        # The smaller minimum first, so the old one never holds the new size back.
        if scale < old:
            form.MinimumSize = least
        left, top, right, bottom = suggested
        user32.SetWindowPos(form.Handle.ToInt64(), None, left, top, right - left, bottom - top, _KEEP_ORDER)
        if scale >= old:
            form.MinimumSize = least
        if self._browser is not None:
            self._browser.rescale(scale / old)

    def _watch_full_screen(self) -> None:
        core = self._window.native.browser.webview.CoreWebView2
        self._page_full = lambda sender, args: self._full_screen(sender, bool(sender.ContainsFullScreenElement))
        core.ContainsFullScreenElementChanged += self._page_full
        # Hidden, put in the notification area or minimised, the video leaves full screen with it.
        form = self._window.native
        self._away_full = lambda sender, args: self._leave_full()
        form.VisibleChanged += self._away_full
        form.Resize += self._away_full
        form.browser.webview.VisibleChanged += self._away_full

    def _leave_full(self, anyway: bool = False) -> None:
        from System.Windows.Forms import FormWindowState
        form = self._window.native
        if self._full is None or (form.Visible and form.browser.webview.Visible
                                  and form.WindowState != FormWindowState.Minimized and not anyway):
            return
        core = self._full["core"]
        self._full_screen(core, False)
        core.ExecuteScriptAsync("document.fullscreenElement && document.exitFullscreen()")

    def _full_screen(self, core, on: bool) -> None:
        """A video in the page or in the pane (`core`, its CoreWebView2) went full screen, or left it: the
        window covers its whole monitor, taskbar too, with no frame and only that WebView in it; then it
        stands again as it was, maximised or not. On the form's thread."""
        import ctypes
        from ctypes import wintypes

        from System.Windows.Forms import Screen

        class Placement(ctypes.Structure):
            _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("show", wintypes.UINT),
                        ("min", wintypes.POINT), ("max", wintypes.POINT), ("normal", wintypes.RECT)]

        if on == (self._full is not None):
            return
        form = self._window.native
        user32 = ctypes.WinDLL("user32")
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
        # The placement kept from going full screen is of an earlier call's Placement: passed by address.
        user32.GetWindowPlacement.argtypes = user32.SetWindowPlacement.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.IsZoomed.argtypes = user32.IsIconic.argtypes = user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        dwmapi = ctypes.WinDLL("dwmapi")
        hwnd = form.Handle.ToInt64()
        pane = self._browser
        mine = pane is not None and pane.shows(core)
        if on and (not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd)):
            # Nothing comes to the front by itself: a window out of sight gives full screen back at once.
            core.ExecuteScriptAsync("document.fullscreenElement && document.exitFullscreen()")
            return
        if on:
            # Where it stood, maximised or not, as the system keeps it.
            placement = Placement()
            placement.length = ctypes.sizeof(Placement)
            user32.GetWindowPlacement(hwnd, ctypes.addressof(placement))
            if user32.IsZoomed(hwnd):
                user32.ShowWindow(hwnd, _SW_RESTORE)
            # Read once restored: a maximised window's style says so, and would maximise it again.
            self._full = {"placement": placement, "style": user32.GetWindowLongPtrW(hwnd, _GWL_STYLE), "core": core}
            screen = Screen.FromControl(form).Bounds
            user32.SetWindowLongPtrW(hwnd, _GWL_STYLE, self._full["style"] & ~(_WS_CAPTION | _WS_THICKFRAME))
            # DWMWA_WINDOW_CORNER_PREFERENCE: square corners, as full screen is.
            dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 33, ctypes.byref(ctypes.c_int(1)), 4)
            user32.SetWindowPos(hwnd, None, screen.X, screen.Y, screen.Width, screen.Height, _FULL_POS)
            if pane is not None:
                pane.cover(mine)
            # In front, it is a video full screen to quiet as well: the pet keeps still meanwhile.
            quiet.own_full_screen(True)
            return
        full, self._full = self._full, None
        user32.SetWindowLongPtrW(hwnd, _GWL_STYLE, full["style"])
        dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 33, ctypes.byref(ctypes.c_int(0)), 4)
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, _FRAME_CHANGED)
        placement = full["placement"]
        # Left by going away: it stays away, to come back where it stood.
        if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
            if placement.show == _SW_SHOWMAXIMIZED:
                placement.flags |= _WPF_RESTORETOMAXIMIZED
            placement.show = _SW_SHOWMINNOACTIVE if user32.IsIconic(hwnd) else _SW_HIDE
        user32.SetWindowPlacement(hwnd, ctypes.addressof(placement))
        if user32.IsIconic(hwnd):
            # WinForms brings a minimised window back at the size it kept itself: told it here.
            from System.Drawing import Rectangle
            normal = placement.normal
            form.Bounds = Rectangle(normal.left, normal.top, normal.right - normal.left, normal.bottom - normal.top)
        if pane is not None:
            pane.cover(None)
        quiet.own_full_screen(False)

    def _read(self, ident: str, seconds: float) -> None:
        """A find read in the app's browser, told to the recommender as the page tells it a reaction."""
        import httpx

        from .welcome import TOKEN_HEADER

        address = urlsplit(self.url)
        httpx.post(f"{address.scheme}://{address.netloc}/api/feed/react", timeout=5, trust_env=False,
                   json={"id": ident, "action": "read", "read_s": seconds, "surface": "browser"},
                   headers={TOKEN_HEADER: address.fragment.partition("token=")[2]})

    def _visit(self, row: dict) -> None:
        """A page read in the app's browser: its row kept; read long enough and shared, an Item of the
        person's reading; a find's or such an Item's time told to the recommender."""
        desktop = self.storage.parent
        from .browser import READ_MIN_S
        from .web import signin
        # A signed-in site's pages are the person's own: kept only where they let it be browsed in the background.
        if signin.kept_out(desktop, row["url"]):
            return
        if row["s"] < READ_MIN_S:
            # A glance at a find: not a page they read, only told to the recommender as the look it was.
            if browsed.shared(desktop):
                self._read(row["id"], max(row["s"], 0.1))
            return
        if not browsed.record(desktop, row) or not browsed.shared(desktop):
            return
        ident = row["id"] or browsed.keep_item(desktop.parent, row, row["facts"])
        if ident:
            self._read(ident, row["s"])

    @property
    def pane(self):
        """The app's own browser pane (browser.Pane), None without one."""
        return self._browser

    def attended(self) -> bool:
        """The person is at the app: its window shows, neither hidden nor minimized."""
        return self.opened and not self.hidden and not self._minimized

    def browse_read(self, url: str, start: int = 0) -> dict:
        """One address read with the person's session, out of sight (browser.Reader), from character `start`."""
        if self._reader is None or not browsed.shared(self.storage.parent):
            return {"error": "off"}
        return self._reader.read(url, runtime=self.storage.parent.parent, start=start)

    def browse_clear(self, since: float = 0.0) -> None:
        """The history from `since` on (all of it with 0): its rows and the library items they made (browsed.clear),
        what the pane keeps of it (Pane.forget), and the profile's own history, download history and cache of that
        time. Sign-ins and saved passwords stay: Settings › 连接 has them."""
        from .signwatch import Profile
        if self._browser is not None:
            # On the form's thread in this order, so the page open now is read as from now before the rows go.
            self._browser.forget(zoom=not since)
            Profile(self._browser).clear("history", since)
        browsed.clear(self.storage.parent, since)

    def browse_permits(self) -> list[dict]:
        """The site permissions the person decided in the app's browser, by site (browser.Pane.permits)."""
        return self._browser.permits() if self._browser is not None else []

    def browse_unpermit(self, origin: str, kind: str) -> None:
        """One site permission taken back: the site is asked again next time."""
        if self._browser is not None:
            self._browser.unpermit(origin, kind)

    def close(self) -> None:
        self._leaving = True
        if self._feeds is not None:
            self._feeds.stop.set()
        if self._browser is not None:
            with contextlib.suppress(Exception):
                self._browser.close()
        # Every window goes, the hidden compact chat too: pywebview ends its loop only when none is left.
        compact = self._compact.window if self._compact is not None else None
        for window in (compact, self._pet, self._window):
            if window is not None:
                with contextlib.suppress(Exception):
                    window.destroy()

    @property
    def pet_url(self) -> str:
        address = urlsplit(self.url)
        return urlunsplit((address.scheme, address.netloc, "/pet", "", address.fragment))

    def _shown(self) -> bool:
        return bool(self._user32.IsWindowVisible(self._main_hwnd) and not self._user32.IsIconic(self._main_hwnd))

    def _set_pose(self, pose: str) -> None:
        """Tell the pet's page; it ducks, peeks, sinks or pops up for it (pet.js)."""
        self._pose = pose
        self._pet.evaluate_js(f"petPose({json.dumps(pose)})")

    def _go_in(self) -> None:
        """The pet ducks out of sight, the window rises into its place, and the
        pet's head comes up over the window's top edge."""
        if not self._turn.acquire(blocking=False):
            return
        try:
            if self._attached or self._leaving:
                return
            self._set_pose("leaving")
            self._on_pet(self._leave_ground)
            time.sleep(DUCK_S)
            if self._user32.IsIconic(self._main_hwnd):
                # Back from the taskbar as it was, maximized or not.
                self._user32.ShowWindow(self._main_hwnd, _SW_RESTORE)
            if self.hidden:
                self._page_seen(True)
            if self._shown():
                self._window.show()
            else:
                self._rise()
            self.hidden = False
            self._on_ui(self._attach)
            self._set_pose("peek")
        finally:
            self._turn.release()

    def _go_out(self, how: str) -> None:
        """The pet's head sinks behind the edge, the window goes ("hide" or
        "minimize"; "gone" when it went by itself), and the pet pops back up on
        the ground where it stood."""
        with self._turn:
            if how == "gone" and not self._attached:
                return
            out = self._attached
            if out and how != "gone":
                self._set_pose("returning")
                time.sleep(DUCK_S)
            # Let go first: the window going is no longer the pet's to follow.
            self._attached = False
            if how == "hide":
                self._fade_out()
                self.hidden = True
            elif how == "minimize":
                self._window.minimize()
            if out:
                self._set_pose("returning")
                self._on_pet(self._land)
                self._set_pose("standing")

    def _moved(self) -> None:
        """On the app window's thread, each time it moved, changed size, showed or hid."""
        if self._leaving:
            return
        shown = self._shown()
        if self._attached and shown:
            self._hold()
        elif (self._attached or (shown and self._pose == "standing")) and not self._turn.locked():
            # Minimized or shown again from the taskbar, not by the pet or the page.
            work = (lambda: self._go_out("gone")) if self._attached else self._go_in
            threading.Thread(target=work, name="mibo-pet", daemon=True).start()

    def _rise(self) -> None:
        """The window comes up into its place from a little below, fading in."""
        from System.Drawing import Point

        form = self._window.native
        place = {}

        def begin() -> None:
            # A maximized window has nowhere to rise from: it only fades in.
            lift = 0 if self.maximized else round(RISE_PX * form._scale)
            place["at"], place["lift"] = (form.Left, form.Top), lift
            form.Opacity = 0
            form.Location = Point(form.Left, form.Top + lift)

        def step(share: float) -> None:
            (left, top), lift = place["at"], place["lift"]
            form.Location = Point(left, top + round(lift * (1 - share)))
            form.Opacity = share

        self._on_ui(begin)
        self._window.show()
        for i in range(1, RISE_STEPS + 1):
            self._on_ui(lambda share=1 - (1 - i / RISE_STEPS) ** 3: step(share))
            time.sleep(RISE_STEP_S)

    def _fade_out(self) -> None:
        form = self._window.native
        for i in range(FADE_STEPS - 1, -1, -1):
            self._on_ui(lambda share=i / FADE_STEPS: setattr(form, "Opacity", share))
            time.sleep(FADE_STEP_S)
        self._page_seen(False)
        self._window.hide()
        self._on_ui(lambda: setattr(form, "Opacity", 1.0))

    def _restyle(self) -> None:
        """No focus and no taskbar button for the pet's window: WinForms marks
        it as one that belongs on the taskbar, and an owner or a change of
        order must not bring the button back."""
        user32, pet = self._user32, self._pet_hwnd
        style = user32.GetWindowLongW(pet, _GWL_EXSTYLE)
        user32.SetWindowLongW(pet, _GWL_EXSTYLE, (style | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW) & ~_WS_EX_APPWINDOW)

    def _leave_ground(self) -> None:
        """On the pet's thread: put any card away and keep where it stood."""
        self._fit_card(0, 0)
        form = self._pet.native
        self._ground = (form.Left, form.Bottom)

    def _peek_spot(self) -> tuple[int, int, int, int]:
        """Where the peeking pet's window goes: its bottom on the app window's
        top edge, towards the right. Where there is no room above (maximized,
        or at the top of the screen) it stays there and runs off the screen."""
        from ctypes import byref, wintypes

        user32, main, form = self._user32, self._main_hwnd, self._window.native
        origin, page, outer = wintypes.POINT(0, 0), wintypes.RECT(), wintypes.RECT()
        user32.ClientToScreen(main, byref(origin))
        user32.GetClientRect(main, byref(page))
        # A window with its own title bar begins above its page.
        user32.GetWindowRect(main, byref(outer))
        edge = origin.y if self.frameless else outer.top
        wide, high = round(PET_SIZE[0] * self._pet_scale), round(self._head * self._pet_scale)
        left = max(origin.x, origin.x + page.right - wide - round(PEEK_INSET * form._scale))
        return left, edge - high, wide, high

    def _hold(self, front: bool = False) -> None:
        """On the app window's thread: the pet's window to its spot on the edge,
        and with `front` just above the app window in the order."""
        left, top, wide, high = self._peek_spot()
        self._user32.SetWindowPos(self._pet_hwnd, None, left, top, wide, high,
                                  _NO_FOCUS if front else _KEEP_ORDER)

    def _attach(self) -> None:
        """On the app window's thread: the pet's window is handed to the app
        window. Owned by it, it stays just above it and goes behind other apps
        with it, no longer on top of everything."""
        user32, pet = self._user32, self._pet_hwnd
        # Changed while hidden: changed while it shows, the window's see-through
        # parts come out white, and stay so until it is shown again. The pet is
        # out of sight below its window's bottom meanwhile (pet.js).
        user32.ShowWindow(pet, _SW_HIDE)
        user32.SetWindowPos(pet, _HWND_NOTOPMOST, 0, 0, 0, 0, _ORDER_ONLY)
        user32.SetWindowLongPtrW(pet, _GWLP_HWNDPARENT, self._main_hwnd)
        self._restyle()
        self._attached = True
        self._hold(front=True)
        self._shape()
        if not self._hushed:
            user32.ShowWindow(pet, _SW_SHOWNOACTIVATE)

    def _land(self) -> None:
        """On the pet's thread: back on the ground where it stood, on top of everything."""
        user32, pet, scale = self._user32, self._pet_hwnd, self._pet_scale
        # As in _attach.
        user32.ShowWindow(pet, _SW_HIDE)
        user32.SetWindowLongPtrW(pet, _GWLP_HWNDPARENT, 0)
        user32.SetWindowPos(pet, _HWND_TOPMOST, 0, 0, 0, 0, _ORDER_ONLY)
        self._restyle()
        wide, high = int(PET_SIZE[0] * scale), int(PET_SIZE[1] * scale)
        left, bottom = self._ground
        user32.SetWindowPos(pet, None, left, bottom - high, wide, high, _KEEP_ORDER)
        self._shape()
        if not self._hushed:
            user32.ShowWindow(pet, _SW_SHOWNOACTIVATE)

    def _pet_room(self, on: bool) -> None:
        self._room = on
        if self._pet_hwnd and not self._attached:
            self._on_pet(self._shape)

    def _pet_measured(self, head: float, outline: list) -> None:
        self._head, self._outline = max(1, round(head)), tuple(outline)
        self._on_pet(self._reshape)

    def _reshape(self) -> None:
        """Another figure: its window cut to it again. Shown anew after, as a
        new figure leaves the see-through parts white too (see _attach)."""
        if not self._pet_hwnd:
            # The page measured it before the window was placed, which cuts it then.
            return
        self._user32.ShowWindow(self._pet_hwnd, _SW_HIDE)
        if self._attached:
            self._hold()
        self._shape()
        if not self._hushed:
            self._user32.ShowWindow(self._pet_hwnd, _SW_SHOWNOACTIVATE)

    def _quiet_changed(self, state: dict) -> None:
        """quiet.py, on its own loop: a game or something full screen came to the front, or went."""
        if state["quiet"]:
            self._by_hand = False
        threading.Thread(target=self._hush_pet if state["quiet"] else self._wake_pet,
                         name="mibo-quiet", daemon=True).start()
        # The app window's page, open behind the game, asks nothing meanwhile either (app.js).
        if self.opened:
            self._tell(self._window, f"window.paQuiet && paQuiet({json.dumps(state['quiet'])})")
        # Not the pane while a video of the window's own is full screen and in front: that is what
        # made it quiet. Quiet for something else (a game come to the front) ends that full screen.
        own = state["quiet"] and self._full is not None and quiet.front().get("pid") == os.getpid()
        if state["quiet"] and self._full is not None and not own:
            self._on_ui(lambda: self._leave_full(True))
        if self._browser is not None and not own:
            self._browser.quiet(state["quiet"])

    def _show_pet(self) -> None:
        """The tray's "Show the pet": the person's choice holds until quiet starts again."""
        self._tucked = False
        self._by_hand = True
        threading.Thread(target=self._wake_pet, name="mibo-quiet", daemon=True).start()

    def _hush_pet(self) -> None:
        """The pet ducks out of sight and its window hides, out of alt-tab and
        the taskbar, its page stilled; the tray icon can bring it back.
        Both this and _wake_pet go by quiet as it is when they run, not by
        the event that started them, so a quick on-off ends shown."""
        with self._hush:
            if self._hushed or self._leaving or self._by_hand or not quiet.active():
                return
            self._pet.evaluate_js("petQuiet(true)")
            time.sleep(DUCK_S)
            self._on_pet(self._put_away)
            if self._tray is not None:
                self._on_ui(lambda: setattr(self._tray, "Visible", True))

    def _wake_pet(self) -> None:
        """Back where it was, on the ground or over the app window: its window
        shows with the figure still out of sight, and the figure comes up."""
        with self._hush:
            if not self._hushed or self._leaving or self._tucked or (quiet.active() and not self._by_hand):
                return
            self._on_pet(self._bring_back)
            self._pet.evaluate_js("petQuiet(false)")
            if self._tray is not None and not self._pet_off:
                self._on_ui(lambda: setattr(self._tray, "Visible", False))

    def _put_away(self) -> None:
        """On the pet's thread, which is the app window's too: _attach, _land,
        _reshape and _fit_card show the pet's window unless it is hushed.
        The WebView is hidden first: hidden after its window, it would never
        learn it is out of sight, and only a WebView that knows suspends.
        Then it keeps little memory and stops its page."""
        self._hushed = True
        view = self._pet.native.browser.webview
        view.Visible = False
        self._user32.ShowWindow(self._pet_hwnd, _SW_HIDE)
        # An older WebView2 runtime has neither.
        with contextlib.suppress(Exception):
            from Microsoft.Web.WebView2.Core import CoreWebView2MemoryUsageTargetLevel
            view.CoreWebView2.MemoryUsageTargetLevel = CoreWebView2MemoryUsageTargetLevel.Low
            view.CoreWebView2.TrySuspendAsync()

    def _bring_back(self) -> None:
        """On the pet's thread: the window first, then the WebView, which wakes
        its page by showing (see _put_away). Nothing white shows between: the
        window alone is see-through, and the page last drew the figure ducked."""
        self._hushed = False
        view = self._pet.native.browser.webview
        with contextlib.suppress(Exception):
            from Microsoft.Web.WebView2.Core import CoreWebView2MemoryUsageTargetLevel
            view.CoreWebView2.MemoryUsageTargetLevel = CoreWebView2MemoryUsageTargetLevel.Normal
        self._user32.ShowWindow(self._pet_hwnd, _SW_SHOWNOACTIVATE)
        view.Visible = True

    def _shape(self) -> None:
        """On the pet's thread: the pet's window shows anything, and takes the
        pointer, only over the figure and the card; everywhere else a click
        goes through to whatever is behind. A window region does it: the
        system goes by it alone, whatever the page draws."""
        import ctypes
        from ctypes import byref, wintypes

        user32, pet, scale = self._user32, self._pet_hwnd, self._pet_scale
        box = wintypes.RECT()
        user32.GetWindowRect(pet, byref(box))
        wide, high = box.right - box.left, box.bottom - box.top
        x0, y0, x1, y1 = (round(v * scale) for v in self._outline)
        # Each part: left, top, right, bottom, and how round its corners are.
        if self._attached:
            # The strip over the window's edge: as wide as the figure, all of its height.
            parts = [] if self._bare else [(x0, 0, x1, high, 0)]
        else:
            # The figure keeps the window's bottom corner (pet.css).
            fx = wide - round(PET_SIZE[0] * scale) if self._card == "left" else 0
            fy = high - round(PET_FIGURE[1] * scale)
            parts = [] if self._bare and not self._room else [
                (fx, fy, fx + round(PET_SIZE[0] * scale), high, 0) if self._room
                else (fx + x0, fy + y0, fx + x1, fy + y1, 0)]
            parts += [(fx + round(x * scale), high - round((PET_SIZE[1] - y) * scale),
                       fx + round((x + w) * scale), high - round((PET_SIZE[1] - y - h) * scale), round(2 * r * scale))
                      for x, y, w, h, r in self._spots]
            if self._card:
                cx, cy, cw, ch = (round(v * scale) for v in self._card_box)
                if self._card == "right":
                    cx = wide - cx - cw
                # Round as the card is: the see-through corners outside it come
                # out white where they touch the window's side.
                # A pixel more all round: the card's drawn edge is soft, and a region's is not.
                parts.append((cx - 1, cy - 1, cx + cw + 1, cy + ch + 1, round(2 * CARD_RADIUS * scale) + 2))
        gdi32 = ctypes.WinDLL("gdi32")
        gdi32.CreateRectRgn.restype = gdi32.CreateRoundRectRgn.restype = wintypes.HANDLE
        gdi32.CombineRgn.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.c_int]
        gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        region = gdi32.CreateRectRgn(0, 0, 0, 0)
        for *part, corner in parts:
            more = gdi32.CreateRoundRectRgn(*part, corner, corner) if corner else gdi32.CreateRectRgn(*part)
            gdi32.CombineRgn(region, region, more, _RGN_OR)
            gdi32.DeleteObject(more)
        # The system owns the region from here on.
        user32.SetWindowRgn(pet, region, True)

    def _pet_drag(self, x: float, y: float) -> None:
        """Move the pet's window with the cursor, the point (x, y) CSS px of it
        under the cursor, until the primary button is up or the page says it
        was let go. Read from the cursor itself, so the window keeps up however
        fast the hand moves."""
        from ctypes import byref, wintypes

        user32, pet = self._user32, self._pet_hwnd
        if self._pose != "standing":
            return
        button = _VK_RBUTTON if user32.GetSystemMetrics(_SM_SWAPBUTTON) else _VK_LBUTTON
        at, box = wintypes.POINT(), wintypes.RECT()

        def spot(scale: float, wide: float, high: float) -> tuple[int, int, int, int]:
            """The window at `scale`, the pressed point under the cursor."""
            left, top = at.x - round(x * scale), at.y - round(y * scale)
            return left, top, left + round(wide * scale), top + round(high * scale)

        self._dragging = True
        while self._dragging and user32.GetAsyncKeyState(button) & 0x8000 and self._pose == "standing":
            user32.GetCursorPos(byref(at))
            user32.GetWindowRect(pet, byref(box))
            # Its size in CSS px; the scale is the monitor's it is on now (_pet_rescaled).
            scale = self._pet_scale
            wide, high = (box.right - box.left) / scale, (box.bottom - box.top) / scale
            goes = spot(scale, wide, high)
            other = _monitor(user32, goes)[1]
            if other != scale:
                # Onto a monitor of another scale, where it is drawn at that one; unless at that
                # size most of it would be back on this one: then it waits at this monitor's edge.
                there = spot(other, wide, high)
                goes = there if _monitor(user32, there)[1] == other else _onto(user32, goes, _monitor(user32, pet)[0])
            user32.SetWindowPos(pet, None, goes[0], goes[1], 0, 0, _MOVE_ONLY)
            time.sleep(DRAG_STEP_S)
        self._dragging = False

    def _watch_cursor(self) -> None:
        """Tell the pet where the cursor is while it is near (its eyes follow
        it), and when the person is back after a while away. Two cheap reads
        a few times a second; the page is told only when something changed."""
        import ctypes
        from ctypes import byref, wintypes

        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        user32, kernel32 = self._user32, ctypes.windll.kernel32
        kernel32.GetTickCount.restype = wintypes.DWORD
        at, box, last = wintypes.POINT(), wintypes.RECT(), LastInput(ctypes.sizeof(LastInput), 0)
        told, away = None, False
        while self.opened:
            time.sleep(NEAR_STEP_S)
            if self._hushed or not self._pet_hwnd:
                continue
            if user32.GetLastInputInfo(byref(last)):
                idle = (kernel32.GetTickCount() - last.dwTime) / 1000
                if idle > AWAY_S:
                    away = True
                elif away and idle < 1:
                    away = False
                    self._tell(self._pet, "window.petBack && petBack()")
            near = None
            if self._pose == "standing" and not self._dragging and not self._tucked:
                user32.GetCursorPos(byref(at))
                user32.GetWindowRect(self._pet_hwnd, byref(box))
                middle = ((box.left + box.right) / 2, (box.top + box.bottom) / 2)
                scale = self._pet_scale
                if abs(at.x - middle[0]) < NEAR_PX * scale and abs(at.y - middle[1]) < NEAR_PX * scale:
                    # With the button held: something may be carried to it (pet.js petdrop).
                    held = user32.GetAsyncKeyState(_VK_RBUTTON if user32.GetSystemMetrics(_SM_SWAPBUTTON)
                                                   else _VK_LBUTTON) & 0x8000
                    near = (round((at.x - box.left) / scale), round((at.y - box.top) / scale), bool(held))
            if near != told and (near is None or told is None or near[2] != told[2]
                                 or max(abs(near[0] - told[0]), abs(near[1] - told[1])) > 3):
                told = near
                self._tell(self._pet, f"window.petCursor && petCursor({json.dumps(near)})")

    def _on_pet(self, action: Callable[[], None]) -> None:
        from System import Func, Type

        def run() -> None:
            action()

        self._pet.native.Invoke(Func[Type](run))

    def _place_pet(self) -> None:
        """Cut the pet's window down to the figure and stand it on the taskbar,
        near the right edge."""
        import ctypes
        from ctypes import byref, wintypes

        from System.Drawing import Color, Size
        from System.Windows.Forms import Screen

        form = self._pet.native

        def place() -> None:
            scale = form._scale
            form.Hide()
            form.MinimumSize = Size(1, 1)
            form.Size = Size(int(PET_SIZE[0] * scale), int(PET_SIZE[1] * scale))
            # pywebview leaves the form's own grey behind a transparent page. With
            # the window's glass over all of it, black there is see-through and
            # the page is drawn over the desktop as it is. A colour key would do
            # that too, but the system takes clicks by the window's own pixels
            # then, which WebView2 stops drawing into once the window is resized.
            form.BackColor = Color.Black
            self._pet_hwnd, self._pet_scale = form.Handle.ToInt64(), scale
            dwmapi = ctypes.WinDLL("dwmapi")
            dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.c_void_p]
            dwmapi.DwmExtendFrameIntoClientArea(self._pet_hwnd, byref((ctypes.c_int * 4)(-1, -1, -1, -1)))
            self._restyle()
            self._pet_scale_watch = _watch_scale(self._pet_hwnd, self._pet_rescaled)
            area = Screen.FromControl(form).WorkingArea
            form.Left = area.Right - form.Width - int(PET_MARGIN * scale)
            form.Top = area.Bottom - form.Height
            self._shape()
            form.Show()

        self._on_pet(place)

    def _watch_window(self) -> None:
        """On the app window's thread: follow its every move from now on."""
        self._main_hwnd = self._window.native.Handle.ToInt64()
        self._watch = _watch_moves(self._main_hwnd, self._moved)

    def _pet_walk(self, dx: float, ms: float) -> None:
        """The pet walks sideways at `dx` CSS px every WALK_STEP_S for `ms`
        milliseconds: where it is goes by the clock, a device pixel at a time,
        so its speed holds however long a move takes. It turns round where the
        screen ends (the page is told, to face the other way). It stops when
        pressed or a card comes (rest), or when it leaves the ground or is
        hushed. One call from the page per walk."""
        from System.Windows.Forms import Screen

        form = self._pet.native
        walk = {"turned": False}
        begun = time.monotonic()

        def begin() -> None:
            area = Screen.FromControl(form).WorkingArea
            walk.update(speed=dx * form._scale / WALK_STEP_S, x=float(form.Left), at=time.monotonic(),
                        low=area.Left, high=area.Right - form.Width)

        def step() -> None:
            # On the pet's thread, so a card's resize never lands between a step's read and its move.
            if not self._walking or self._pose != "standing" or self._hushed:
                self._walking = False
                return
            now = time.monotonic()
            x = walk["x"] + walk["speed"] * (now - walk["at"])
            walk["at"] = now
            if not walk["low"] <= x <= walk["high"]:
                x = min(max(x, walk["low"]), walk["high"])
                walk["speed"], walk["turned"] = -walk["speed"], True
            walk["x"] = x
            if round(x) != form.Left:
                form.Left = round(x)

        self._on_pet(begin)
        self._walking = True
        end = begun + ms / 1000
        while self._walking and time.monotonic() < end:
            self._on_pet(step)
            if walk["turned"]:
                walk["turned"] = False
                self._pet.evaluate_js(f"petTurn({json.dumps(walk['speed'] < 0)})")
            time.sleep(0.008)
        self._walking = False

    def _pet_place(self, x: float, start: float) -> None:
        """Its window's left edge to `x` CSS px, within the screen, while it stands; `start`
        is where the page saw it as this walk began. The walk goes on from where the window
        was then: the page's screen px are whole ones, and on a scaled monitor its origin in
        them is not its device px over the scale."""
        from System.Windows.Forms import Screen

        form = self._pet.native

        def move() -> None:
            if self._pose != "standing" or self._hushed:
                return
            if self._walk_from[0] != start:
                self._walk_from = (start, form.Left)
            area = Screen.FromControl(form).WorkingArea
            # `x` is a whole device px in the page's px, `start` need not be: rounded apart, each
            # step moves the window by just the device px the page drew the feet for.
            scale = form._scale
            left = self._walk_from[1] + round(x * scale) - round(start * scale)
            form.Left = min(max(left, area.Left), area.Right - form.Width)

        self._on_pet(move)

    def _fit_card(self, width: float, height: float) -> None:
        """On the pet's thread: grow its window to `width` by `height` CSS px
        for a card beside the figure, or with 0 shrink it back to the figure.
        The figure stays where it is on screen: the window grows away from it,
        up and to the left, or to the right where the screen ends on the left."""
        from System.Windows.Forms import Screen

        form = self._pet.native
        scale = form._scale
        wide, high = int(PET_SIZE[0] * scale), int(PET_SIZE[1] * scale)
        left = form.Right - wide if self._card == "left" else form.Left
        bottom = form.Bottom
        grow = bool(width and height and self._pose == "standing")
        if not grow and not self._card:
            return
        if grow and self._card and (form.Width, form.Height) == (max(wide, int(width * scale)), max(high, int(height * scale))):
            # Something else in a card as big: only where the window takes the pointer changes.
            return self._shape()
        # Resized while it shows, its see-through parts can come out white (see _attach).
        self._user32.ShowWindow(self._pet_hwnd, _SW_HIDE)
        if grow:
            width, height = max(wide, int(width * scale)), max(high, int(height * scale))
            self._card = "left" if left + wide - width >= Screen.FromControl(form).WorkingArea.Left else "right"
            form.SetBounds(left + wide - width if self._card == "left" else left, bottom - height, width, height)
        else:
            self._card = ""
            form.SetBounds(left, bottom - high, wide, high)
        self._shape()
        if self._hushed:
            return
        if grow:
            # Shown at once, it would show the page as it was drawn in the smaller
            # window, the figure out of place and cut by the new shape, for a few frames.
            threading.Thread(target=self._show_grown, args=(form.Width / scale,), name="mibo-card", daemon=True).start()
        else:
            self._user32.ShowWindow(self._pet_hwnd, _SW_SHOWNOACTIVATE)

    def _show_grown(self, wide: float) -> None:
        """Off the pet's thread: the grown window shows once its page is laid
        out that wide and has drawn a frame or two, or after half a second."""
        began = time.monotonic()
        while time.monotonic() - began < 0.5:
            if abs(float(self._pet.evaluate_js("innerWidth") or 0) - wide) <= 1:
                time.sleep(0.05)
                break
            time.sleep(0.016)
        self._on_pet(lambda: self._hushed or self._user32.ShowWindow(self._pet_hwnd, _SW_SHOWNOACTIVATE))

    def _pet_rescaled(self, scale: float, suggested: tuple[int, int, int, int]) -> None:
        """On the pet's thread: on a monitor of another scale its window keeps its size in CSS px,
        on the app window's edge or standing where it stood (its bottom, and the side the figure
        keeps), and most of it on that monitor; cut to the figure again."""
        user32, pet, form = self._user32, self._pet_hwnd, self._pet.native
        old, self._pet_scale = self._pet_scale, scale
        if self._rescaling:
            # Peeking over an app window that lies across two monitors, the new size can put the
            # pet back on the last one: it stays so, until the app window moves again.
            return
        self._rescaling = True
        shown = user32.IsWindowVisible(pet)
        # Resized while it shows, its see-through parts can come out white (see _attach).
        user32.ShowWindow(pet, _SW_HIDE)
        if self._attached:
            self._hold()
        else:
            if self._card:
                wide, high = round(form.Width / old * scale), round(form.Height / old * scale)
            else:
                wide, high = int(PET_SIZE[0] * scale), int(PET_SIZE[1] * scale)
            left = form.Right - wide if self._card == "left" else form.Left
            box = (left, form.Bottom - high, left + wide, form.Bottom)
            left, top = _onto(user32, box, _monitor(user32, pet)[0])
            form.SetBounds(left, top, wide, high)
        self._shape()
        self._rescaling = False
        if shown and not self._hushed:
            threading.Thread(target=self._show_grown, args=(form.Width / scale,), name="mibo-card", daemon=True).start()

    def _pet_card(self, width: float, height: float, box: list | None = None) -> str:
        """Room for a card beside the figure (0 to take it away), and which side
        of the figure it is on: "left", "right", or "" when there is none.
        `box` is where the card is in the grown window, CSS px, as it is on
        the left of the figure: [x, y, width, height]."""
        if box:
            self._card_box = tuple(box)
        # A walk under way stops first: its steps would move the window the card resizes.
        self._walking = False
        self._on_pet(lambda: self._fit_card(width, height))
        return self._card

    def _pet_spots(self, boxes: list, bare: bool) -> None:
        self._spots, self._bare = [tuple(box) for box in boxes], bool(bare)
        if self._pet_hwnd and not self._attached:
            self._on_pet(self._shape)

    def _pet_browse(self, address: str) -> None:
        self.open_at("chat")
        if self._browser is not None:
            self._browser.open(address)

    def _pet_tuck(self) -> str:
        from System.Drawing import Point
        from System.Windows.Forms import Screen

        form, side = self._pet.native, [""]

        def tuck() -> None:
            if self._pose != "standing" or self._card:
                return
            scale = self._pet_scale
            area = Screen.FromControl(form).WorkingArea
            middle, y = form.Left + round(PET_SIZE[0] / 2 * scale), form.Top + form.Height // 2
            for name, gap, beyond in (("left", middle - area.Left, area.Left - 2),
                                      ("right", area.Right - middle, area.Right + 1)):
                # Between two screens there is no side to tuck into.
                if gap < TUCK_PX * scale and not Screen.FromPoint(Point(beyond, y)).Bounds.Contains(Point(beyond, y)):
                    form.Left = area.Left if name == "left" else area.Right - form.Width
                    side[0] = name
                    return

        self._on_pet(tuck)
        return side[0]

    def _pet_menu(self) -> None:
        """The pet's right-click menu, in groups: the app and its settings; a
        find now, or none for an hour; the characters; hiding the pet; quitting."""
        import ctypes

        from System.Drawing import Font, FontStyle, Size, SystemFonts
        from System.Windows.Forms import ContextMenuStrip, Cursor, Padding, ToolStripMenuItem, ToolStripSeparator

        words = self.words()
        figures, current = self.figures(), self.figure()
        snoozed = bool(self._pet.evaluate_js("petSnoozed()"))
        form = self._pet.native
        look = _menu_look(form._scale)
        # A compact menu beside a small pet: rows about 28 px high at 100 %.
        px = lambda *sizes: Padding(*(round(size * form._scale) for size in sizes))
        # WinForms on the .NET Framework sizes a font by the main monitor's scale, wherever the menu shows.
        points = 9 * form._scale * 96 / ctypes.windll.user32.GetDpiForSystem()

        def item(label: str, handler=None, tag: str = "") -> ToolStripMenuItem:
            entry = ToolStripMenuItem(label)
            entry.ForeColor, entry.Padding = look.ink, px(4, 4, 4, 4)
            if handler is not None:
                entry.Click += handler
            entry.Tag = tag
            return entry

        def show() -> None:
            # Made anew each time: the tick moves with the character chosen, the label with the snooze.
            if self._pet_strip is not None:
                self._pet_strip.Dispose()
            menu = ContextMenuStrip()
            menu.Renderer, menu.ShowImageMargin = look, False
            menu.Font = Font(SystemFonts.MenuFont.FontFamily, points)
            menu.Padding, menu.MinimumSize = px(3, 4, 3, 4), Size(round(148 * form._scale), 0)
            people = item(words["characters"])
            people.DropDown.Renderer, people.DropDown.Padding = look, px(3, 4, 3, 4)
            people.DropDown.ShowImageMargin, people.DropDown.ShowCheckMargin = False, True
            for key, label in figures:
                entry = item(label, self._figure_click, key)
                entry.Checked = key == current
                if entry.Checked:
                    entry.Font = Font(menu.Font, FontStyle.Bold)
                people.DropDownItems.Add(entry)
            groups = (
                (item(words["open"], self._open_click), item(words["settings"], self._settings_click)),
                (item(words["bring"], self._bring_click),
                 item(words["unsnooze"], self._snooze_click, "off") if snoozed
                 else item(words["snooze"], self._snooze_click, "on")),
                (people, item(petshot.label(), self._share_click), item(words["hide"], self._tuck_click)),
                (item(words["quit"], self._quit_click),),
            )
            for i, group in enumerate(groups):
                if i:
                    menu.Items.Add(ToolStripSeparator())
                for entry in group:
                    menu.Items.Add(entry)
            self._pet_strip = menu
            for strip in (menu, people.DropDown):
                _round_corners(strip.Handle.ToInt64())
            # A menu with no foreground window behind it stays open when clicked away from.
            ctypes.windll.user32.SetForegroundWindow(form.Handle.ToInt64())
            menu.Show(Cursor.Position)

        self._on_pet(show)

    def _on_ui(self, action: Callable[[], None]) -> None:
        from System import Func, Type

        def run() -> None:
            action()

        self._window.native.Invoke(Func[Type](run))

    def _make_tray(self) -> None:
        """The icon a hidden window is reached through. Its handlers stay
        referenced here, or .NET would lose them."""
        from System.Drawing import Icon
        from System.Windows.Forms import ContextMenuStrip, NotifyIcon, ToolStripMenuItem, ToolTipIcon

        words = {"open": "Open Mibo", "pet": "Show the pet", "quit": "Quit", **self.words()}
        self._tray_info = ToolTipIcon.Info
        # With the pet the icon shows only while it is hushed, and can bring it back.
        entries = ((words["open"], self._open_click), *(((words["pet"], self._pet_click),) if self.pet else ()),
                   (words["quit"], self._quit_click))

        def make() -> None:
            tray = NotifyIcon()
            tray.Icon = Icon(str(icon()))
            tray.Text = TITLE
            menu = ContextMenuStrip()
            for label, handler in entries:
                item = ToolStripMenuItem(label)
                item.Click += handler
                menu.Items.Add(item)
            tray.ContextMenuStrip = menu
            tray.DoubleClick += self._open_click
            # A press on a notification opens the window it is about.
            tray.BalloonTipClicked += self._open_click
            tray.Visible = False
            self._tray = tray

        self._on_ui(make)

    def _closing(self):
        if self._leaving:
            return None
        if self._pet is not None:
            # The pet is still there: the window only goes away until asked for
            # again, after the pet's head has gone down behind its edge.
            threading.Thread(target=self._go_out, args=("hide",), name="mibo-pet", daemon=True).start()
            return False
        if self._tray is not None and self.background():
            self.hide()
            return False
        self._leaving = True
        return None

    def _sized(self, minimized: bool, maximized: bool | None = None) -> Callable[[], None]:
        def note() -> None:
            self._minimized = minimized
            if maximized is not None:
                self.maximized = maximized
                # Its page draws the bar for it (app.js).
                self._frame_changed()
        return note

    def control(self, action: str) -> bool:
        """What the page's own title bar asks for: one of ACTIONS. False when
        there is no window to do it to."""
        if not self.opened or self._leaving or action not in ACTIONS:
            return False
        try:
            if action == "minimize" and self._attached:
                # The pet's head goes down behind the edge first.
                threading.Thread(target=self._go_out, args=("minimize",), name="mibo-pet",
                                 daemon=True).start()
            elif action == "minimize":
                self._window.minimize()
            elif action == "maximize":
                self._window.maximize()
            elif action == "restore":
                self._window.restore()
            else:
                # As the system's close button: it may hide the window or ask first,
                # so the page is not kept waiting for the answer.
                threading.Thread(target=self._window.destroy, name="mibo-close", daemon=True).start()
        except Exception:
            return False
        return True

    def _before_show(self) -> None:
        with contextlib.suppress(Exception):
            self._frame = _strip_title_bar(self._window)
            self.frameless = self._frame is not None

    @property
    def page_url(self) -> str:
        """The address this window loads: `url` and the key its page shows for the bar."""
        return f"{self.url}{'&' if '#' in self.url else '#'}frame={self.frame_key}"

    def is_own_page(self, key: str) -> bool:
        return hmac.compare_digest(str(key or ""), self.frame_key)

    def frame_seen(self) -> None:
        """The page asked how the window stands, so it draws the bar and the buttons."""
        self._frame_seen.set()

    def _framed_again(self) -> None:
        """The window gets its own title bar back."""
        if self.frameless:
            self.frameless = False
            with contextlib.suppress(Exception):
                self._frame[1]()

    def _unless_seen(self) -> None:
        # A page that never came up draws no buttons: nobody is left with a
        # window that cannot be closed.
        if not self._frame_seen.wait(PAGE_WITHIN) and not self._leaving:
            self._framed_again()

    def run(self) -> bool:
        """Show the window and block until it closes. False when none opened."""
        try:
            import webview
        except Exception as error:
            self.error = repr(error)
            return False
        windows = sys.platform == "win32"
        if windows:
            _own_process()

        address = urlsplit(self.url)
        origin = f"{address.scheme}://{address.netloc}"

        def ready() -> None:
            if not self._window.events.shown.wait(SHOWN_WITHIN):
                self.error = "the window did not appear"
                self.close()
            elif self._leaving:
                # Asked to close before there was a window to close.
                self.close()
            # Without WebView2, Windows offers an engine too old for the page.
            elif windows and webview.renderer != "edgechromium":
                self.error = "the WebView2 runtime is missing"
                self.close()
            else:
                if windows:
                    regions = False
                    with contextlib.suppress(Exception):
                        if self._window.events.loaded.wait(SHOWN_WITHIN):
                            regions = _text_menus(self._window)
                    if not regions:
                        # The page could not be the bar the window is moved by.
                        self._framed_again()
                    with contextlib.suppress(Exception):
                        self._make_tray()
                    # Without it, as without the tray, links go to the system browser.
                    with contextlib.suppress(Exception):
                        from . import browser
                        self._browser = browser.Pane(
                            self.storage.parent / "browser", self._window.native, ui=self._on_ui, find=self.find,
                            visit=self._visit, tell=lambda state: self._tell(self._window, browser.tell_script(state)),
                            full=self._full_screen)
                        self._reader = browser.Reader(self.storage.parent / "browser", self._window.native,
                                                      ui=self._on_ui, pace=browsed.Pace(self.storage.parent))
                        self._feeds = browser.Feeds(self._reader, self.storage.parent.parent,
                                                    shared=lambda: browsed.shared(self.storage.parent))
                    # A player in the page itself (a chat's card, the feed) goes full screen the same way.
                    with contextlib.suppress(Exception):
                        self._on_ui(self._watch_full_screen)
                    with contextlib.suppress(Exception):
                        self._on_ui(self._watch_main_scale)
                    _own_pages(self._window, origin, self.open_link)
                    self._window.load_url(self.page_url)
                    if self.frameless:
                        threading.Thread(target=self._unless_seen, name="mibo-frame",
                                         daemon=True).start()
                self.opened = True
                if self._pet is not None:
                    self.hidden = True
                    self._pet.events.shown.wait(SHOWN_WITHIN)
                    self._user32 = _user32()
                    self._place_pet()
                    self._on_ui(self._watch_window)
                    self._pet.events.loaded.wait(SHOWN_WITHIN)
                    _own_pages(self._pet, origin, self.open_link)
                    # Files, links and words dropped on the pet reach its page (pet.js), which loads none of them.
                    self._on_pet(lambda: setattr(self._pet.native.browser.webview, "AllowExternalDrop", True))
                    # Out of sight during games and fullscreen, from now on and
                    # at once if one is in front already.
                    self._pet.events.loaded.wait(SHOWN_WITHIN)
                    quiet.listen(self._quiet_changed)
                    self._quiet_changed(quiet.state())
                    threading.Thread(target=self._watch_cursor, name="mibo-near", daemon=True).start()
                    if not petoff.shown(self):
                        petoff.apply(self, False)
                    petchat.warm(self)
                elif self.start_hidden:
                    self.hide()

        # A file:// page could read any file beside it, ~/mibo/settings.json among them.
        webview.settings["ALLOW_FILE_URLS"] = False
        try:
            # WebView2 takes its menu setting from the next load on, so Windows
            # starts on an empty page and `ready` loads the real one after it.
            self._window = webview.create_window(
                TITLE, None if windows else self.page_url, width=SIZE[0], height=SIZE[1],
                min_size=MIN_SIZE, text_select=True, zoomable=True,
                # Started for the background: no window flashes up before it hides.
                minimized=self.start_hidden and windows, hidden=self.pet)
            if self.pet:
                import ctypes
                screen = ctypes.windll.user32.GetSystemMetrics
                self._pet = webview.create_window(
                    TITLE, self.pet_url, width=PET_SIZE[0], height=PET_SIZE[1],
                    x=screen(0) - PET_SIZE[0] - PET_MARGIN, y=screen(1) - PET_SIZE[1] - 60,
                    resizable=False, frameless=True, shadow=False, on_top=True, transparent=True,
                    # The page turns selection off itself: pywebview would do it with a
                    # style element, which the page's content policy refuses.
                    text_select=True,
                    # pywebview's own drag moves the window from any press anywhere on
                    # the page, the card's buttons too. The figure alone drags it (pet.js).
                    easy_drag=False)
                # By exact name only: pywebview walks a dotted name from a js_api
                # object with getattr, private names too, out to this whole window.
                api = _PetApi(self)
                self._pet.expose(*(getattr(api, name) for name in dir(api) if not name.startswith("_")))
                # Closed on its own (Alt+F4): mibo goes with it.
                self._pet.events.closed += lambda: None if self._leaving else self.close()
            events = self._window.events
            events.closing += self._closing
            events.minimized += self._sized(True)
            events.restored += self._sized(False, False)
            events.maximized += self._sized(False, True)
            if windows:
                events.before_show += self._before_show
            with _interrupt(self.close):
                # Private: nothing the page stores outlives the window.
                webview.start(ready, gui=_toolkit(), private_mode=True,
                              storage_path=str(self.storage), icon=str(icon()))
        except Exception as error:
            self.error = repr(error)
            return False
        finally:
            if not self.opened:
                # No window after all: the browser that takes over draws no window bar.
                self.frameless = False
            if self._tray is not None:
                with contextlib.suppress(Exception):
                    self._tray.Visible = False
                    self._tray.Dispose()
        return self.opened

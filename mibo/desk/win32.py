"""The few Win32 calls the desk layer needs, through ctypes: windows and the
process behind each, who signed a program, the real input a control without
an automation pattern needs, and a window's picture.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
import re
import time
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32")
dwmapi = ctypes.WinDLL("dwmapi")
gdi32 = ctypes.WinDLL("gdi32")
version = ctypes.WinDLL("version")
wintrust = ctypes.WinDLL("wintrust")
crypt32 = ctypes.WinDLL("crypt32")

HWND, DWORD, BOOL, LPARAM = w.HWND, w.DWORD, w.BOOL, w.LPARAM
ENUM = ctypes.WINFUNCTYPE(BOOL, HWND, LPARAM)

user32.EnumWindows.argtypes = [ENUM, LPARAM]
user32.EnumChildWindows.argtypes = [HWND, ENUM, LPARAM]
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]
user32.SetForegroundWindow.argtypes = [HWND]
user32.GetWindowThreadProcessId.restype = DWORD
user32.GetClassNameW.argtypes = [HWND, w.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [HWND, w.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [HWND]
user32.IsWindow.argtypes = [HWND]
user32.IsWindowVisible.argtypes = [HWND]
user32.IsIconic.argtypes = [HWND]
user32.GetWindow.argtypes = [HWND, ctypes.c_uint]
user32.GetWindow.restype = HWND
user32.GetAncestor.argtypes = [HWND, ctypes.c_uint]
user32.GetAncestor.restype = HWND
user32.GetForegroundWindow.restype = HWND
user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(w.RECT)]
user32.SetWindowPos.argtypes = [HWND, HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.PostMessageW.argtypes = [HWND, ctypes.c_uint, w.WPARAM, w.LPARAM]
user32.GetWindowLongPtrW.argtypes = [HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.WindowFromPoint.argtypes = [w.POINT]
user32.WindowFromPoint.restype = HWND
user32.GetDC.argtypes = [HWND]
user32.GetDC.restype = w.HDC
user32.ReleaseDC.argtypes = [HWND, w.HDC]
user32.PrintWindow.argtypes = [HWND, w.HDC, ctypes.c_uint]
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.SendMessageTimeoutW.argtypes = [HWND, ctypes.c_uint, w.WPARAM, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                                       ctypes.POINTER(ctypes.c_size_t)]
user32.LockSetForegroundWindow.argtypes = [ctypes.c_uint]
user32.ShowWindowAsync.argtypes = [HWND, ctypes.c_int]
shell32.ShellExecuteW.argtypes = [HWND, w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, ctypes.c_int]
shell32.ShellExecuteW.restype = ctypes.c_ssize_t
dwmapi.DwmGetWindowAttribute.argtypes = [HWND, DWORD, ctypes.c_void_p, DWORD]
kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
kernel32.OpenProcess.restype = w.HANDLE
kernel32.CloseHandle.argtypes = [w.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [w.HANDLE, DWORD, w.LPWSTR, ctypes.POINTER(DWORD)]
kernel32.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
kernel32.CreateToolhelp32Snapshot.argtypes = [DWORD, DWORD]
kernel32.CreateToolhelp32Snapshot.restype = w.HANDLE
advapi32.OpenProcessToken.argtypes = [w.HANDLE, DWORD, ctypes.POINTER(w.HANDLE)]
advapi32.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)]
gdi32.CreateCompatibleDC.argtypes = [w.HDC]
gdi32.CreateCompatibleDC.restype = w.HDC
gdi32.CreateCompatibleBitmap.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = w.HBITMAP
gdi32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
gdi32.SelectObject.restype = w.HGDIOBJ
gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
gdi32.DeleteDC.argtypes = [w.HDC]
gdi32.GetDIBits.argtypes = [w.HDC, w.HBITMAP, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_uint]
version.GetFileVersionInfoSizeW.argtypes = [w.LPCWSTR, ctypes.POINTER(DWORD)]
version.GetFileVersionInfoW.argtypes = [w.LPCWSTR, DWORD, DWORD, ctypes.c_void_p]
version.VerQueryValueW.argtypes = [ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.POINTER(ctypes.c_uint)]

PER_MONITOR_V2 = ctypes.c_void_p(-4)
GW_OWNER, GA_ROOT, GA_ROOTOWNER = 4, 2, 3
WM_CLOSE = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_EX_TOOLWINDOW, WS_EX_TOPMOST = 0x80, 0x8
ES_MULTILINE = 0x4


def dpi_aware() -> None:
    """Physical pixels on this thread, so UI Automation's rectangles and the input agree on every monitor."""
    user32.SetThreadDpiAwarenessContext(PER_MONITOR_V2)


# --- windows

def top_windows() -> list[int]:
    found: list[int] = []
    user32.EnumWindows(ENUM(lambda h, _: found.append(h or 0) or True), 0)
    return [h for h in found if h]


def child_windows(hwnd: int) -> list[int]:
    found: list[int] = []
    user32.EnumChildWindows(hwnd, ENUM(lambda h, _: found.append(h or 0) or True), 0)
    return [h for h in found if h]


def pid_of(hwnd: int) -> int:
    pid = DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def thread_of(hwnd: int) -> int:
    return user32.GetWindowThreadProcessId(hwnd, None)


def class_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def title_of(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(min(n, 2000) + 1)
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def alive(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def enabled(hwnd: int) -> bool:
    """False while a modal dialog of its own is up."""
    return bool(user32.IsWindowEnabled(hwnd))


def on_desktop(hwnd: int) -> bool:
    """Visible and not cloaked (a window of another virtual desktop or a suspended app); it may be minimised."""
    if not user32.IsWindowVisible(hwnd):
        return False
    cloaked = DWORD()
    dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
    return not cloaked.value


def minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def shown(hwnd: int) -> bool:
    """On the desktop and not minimised."""
    return on_desktop(hwnd) and not minimized(hwnd)


def tool_window(hwnd: int) -> bool:
    """A window the taskbar does not list: a splash screen, a palette, a floating bar."""
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    return bool(ex & WS_EX_TOOLWINDOW) and not ex & 0x40000  # WS_EX_APPWINDOW


def restore(hwnd: int) -> None:
    """Bring a minimised window back to its place, without putting it in front."""
    user32.ShowWindowAsync(hwnd, 4)  # SW_SHOWNOACTIVATE


def owner_of(hwnd: int) -> int:
    return user32.GetWindow(hwnd, GW_OWNER) or 0


def root_owner(hwnd: int) -> int:
    return user32.GetAncestor(hwnd, GA_ROOTOWNER) or hwnd


def top_of(hwnd: int) -> int:
    return user32.GetAncestor(hwnd, GA_ROOT) or hwnd


def foreground() -> int:
    return user32.GetForegroundWindow() or 0


def give_back(hwnd: int) -> None:
    """Put `hwnd` in front again, after a program took the front for an action that did not need it (a packaged
    app activates itself when one of its buttons is invoked). Windows lets a thread joined to the front window's
    input do it."""
    if not hwnd or not user32.IsWindow(hwnd):
        return
    front = foreground()
    here = kernel32.GetCurrentThreadId()
    there = user32.GetWindowThreadProcessId(front, None)
    joined = bool(there) and there != here and user32.AttachThreadInput(here, there, True)
    user32.SetForegroundWindow(hwnd)
    if joined:
        user32.AttachThreadInput(here, there, False)


def rect(hwnd: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) as drawn, without the invisible resize border."""
    r = w.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def window_at(x: int, y: int) -> int:
    return top_of(user32.WindowFromPoint(w.POINT(x, y)) or 0)


# The window a Chromium-drawn page lives in (Electron, WebView2, CEF, Chrome and Edge), and the object id Chromium
# answers as a screen reader's probe (legacy_render_widget_host_win.cc, kIdScreenReaderHoneyPot).
CHROMIUM_PAGE = "Chrome_RenderWidgetHostHWND"
WM_GETOBJECT, SCREEN_READER_PROBE = 0x003D, 1


def chromium_pages(hwnd: int) -> list[int]:
    """The windows inside `hwnd` that hold a Chromium-drawn page."""
    return [h for h in child_windows(hwnd) if class_of(h) == CHROMIUM_PAGE]


def probe_page(hwnd: int) -> None:
    """Chromium's screen-reader probe, as assistive tools send it: WM_GETOBJECT with its probe id. Answered 0; it
    tells the page a client reads it, so the page sends its tree once one of its names is read."""
    done = ctypes.c_size_t()
    user32.SendMessageTimeoutW(hwnd, WM_GETOBJECT, 0, ctypes.c_void_p(SCREEN_READER_PROBE), 0x0002, 1000,
                               ctypes.byref(done))


user32.MonitorFromWindow.argtypes = [HWND, DWORD]
user32.MonitorFromWindow.restype = w.HMONITOR
user32.GetDpiForWindow.argtypes = [HWND]
user32.ClientToScreen.argtypes = [HWND, ctypes.POINTER(w.POINT)]
shcore = ctypes.WinDLL("shcore")
shcore.GetDpiForMonitor.argtypes = [w.HMONITOR, ctypes.c_int, ctypes.POINTER(w.UINT), ctypes.POINTER(w.UINT)]


def unscaled(hwnd: int) -> float:
    """How much larger the monitor draws `hwnd` than the window itself counts: 1.5 for a window that is not DPI
    aware on a monitor at 150%, 1 for one that is."""
    dpi = w.UINT()
    shcore.GetDpiForMonitor(user32.MonitorFromWindow(hwnd, 2), 0, ctypes.byref(dpi), ctypes.byref(dpi))
    return dpi.value / (user32.GetDpiForWindow(hwnd) or 96)


def client_origin(hwnd: int) -> tuple[int, int]:
    """The top left corner of the window's client area on the screen."""
    p = w.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(p))
    return p.x, p.y


def popup(hwnd: int) -> bool:
    """A pop-up with no title bar: WS_POPUP without WS_CAPTION."""
    style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    return bool(style & 0x80000000) and (style & 0x00C00000) != 0x00C00000


def multiline(hwnd: int) -> bool:
    return bool(user32.GetWindowLongPtrW(hwnd, GWL_STYLE) & ES_MULTILINE)


def in_combo(hwnd: int) -> bool:
    """The edit box of a combo box (a file dialog's File name): its program hears only typed changes."""
    return bool(hwnd) and class_of(user32.GetAncestor(hwnd, 1) or 0) in ("ComboBox", "ComboBoxEx32")


def edit_box(hwnd: int) -> bool:
    """A Win32 edit or rich edit control (a WinForms text box is one too)."""
    return "EDIT" in class_of(hwnd).upper()


def edit_replace(hwnd: int, text: str, whole: bool = True, end: bool = False) -> None:
    """Put `text` into an edit control with the control's own messages, as a paste does: over all it holds,
    at its end, or over its selection. No keys and no focus are taken, the input method has no part in it,
    and the program hears of the change as it does of typing."""
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    buf = ctypes.create_unicode_buffer(text)
    done = ctypes.c_size_t()
    far = 0x7FFFFFFF
    steps = ([(0x00B1, 0, -1)] if whole else [(0x00B1, far, far)] if end else []) + [(0x00C2, 1, ctypes.addressof(buf))]
    for message, wparam, lparam in steps:  # EM_SETSEL, EM_REPLACESEL
        if not user32.SendMessageTimeoutW(hwnd, message, wparam, lparam, 0x0002, 3000, ctypes.byref(done)):
            raise OSError("the text box did not answer")


def lock_front(on: bool) -> bool:
    """While on, no other program can put its window in front (a packaged app does when one of its buttons is
    invoked). Windows grants it only to the program whose window is in front, and lifts it at the person's next
    click or Alt key."""
    return bool(user32.LockSetForegroundWindow(1 if on else 2))


def place(hwnd: int, x: int, y: int, wide: int = 0, high: int = 0) -> None:
    """Move a window, and size it when `wide` is given; without a size Windows keeps the program's own,
    rescaled for the monitor it lands on."""
    user32.SetWindowPos(hwnd, None, x, y, wide, high, 0x0014 | (0 if wide else 0x0001))


def close(hwnd: int) -> None:
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", DWORD), ("rcMonitor", w.RECT), ("rcWork", w.RECT), ("dwFlags", DWORD)]


def monitors() -> list[dict]:
    """Each monitor's work area, the primary first."""
    out: list[dict] = []
    proc = ctypes.WINFUNCTYPE(BOOL, w.HMONITOR, w.HDC, ctypes.POINTER(w.RECT), LPARAM)

    def each(handle, _dc, _rect, _data):
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(handle, ctypes.byref(info))
        r = info.rcWork
        out.append({"left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom,
                    "primary": bool(info.dwFlags & 1)})
        return True
    user32.EnumDisplayMonitors(None, None, proc(each), 0)
    return sorted(out, key=lambda m: not m["primary"])


# --- what Start lists

def start_apps() -> list[tuple[str, str, str]]:
    """Every entry of Start's list of apps (shell:AppsFolder, what PowerShell's Get-StartApps reads), through
    the shell's own object: the name Start shows in this Windows' language, the AppUserModelID that starts
    it, and the file it starts ("" for an app from a package)."""
    import clr  # noqa: F401  pythonnet, which pywebview has loaded
    from System import Activator, Int32, Type
    from System.Reflection import BindingFlags

    def ask(obj, name: str, *args, how=BindingFlags.InvokeMethod):
        return obj.GetType().InvokeMember(name, how, None, obj, list(args) or None)
    shell = Activator.CreateInstance(Type.GetTypeFromProgID("Shell.Application"))
    items = ask(ask(shell, "NameSpace", "shell:AppsFolder"), "Items")
    out = []
    for i in range(ask(items, "Count", how=BindingFlags.GetProperty)):
        item = ask(items, "Item", Int32(i))
        out.append((str(ask(item, "Name", how=BindingFlags.GetProperty)),
                    str(ask(item, "Path", how=BindingFlags.GetProperty)),
                    str(ask(item, "ExtendedProperty", "System.Link.TargetParsingPath") or "")))
    return out


# Windows' ids for the Public Desktop and for the Start menu's Programs folders, the person's own and everyone's.
PUBLIC_DESKTOP = "{C4AA340D-F20F-4863-AFEF-F87EF2E6BA25}"
PROGRAMS = ("{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}", "{0139D44E-6AFE-49F2-8690-3DAFCAE6FFB8}")


def shortcuts() -> list[tuple[str, str, str]]:
    """What a person opens from the Desktop and Start besides Start's own programs: every .lnk, .url and .exe on
    their Desktop (shell.grants.known_folder, which USER_DESKTOP_DIR can point elsewhere) and on the Public
    Desktop, and every .lnk in Start's Programs folders that starts no program (a script, a folder). As (name,
    the file, what it opens): the name is the file's without its extension; a shortcut's target is read through
    the shell's own object, as Explorer reads it ("" when it names none)."""
    from ..shell.grants import KNOWN, known_folder
    target = link_reader()
    out = []
    for folder in (known_folder(KNOWN[2][1]), known_folder(PUBLIC_DESKTOP)):
        if folder and os.path.isdir(folder):
            for entry in os.scandir(folder):
                if entry.is_file() and entry.name.lower().endswith((".lnk", ".url", ".exe")):
                    out.append((os.path.splitext(entry.name)[0], entry.path, target(entry.path)))
    for folder in filter(None, map(known_folder, PROGRAMS)):
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if name.lower().endswith(".lnk"):
                    path = os.path.join(root, name)
                    aim = target(path)
                    if aim and not aim.lower().endswith(".exe"):
                        out.append((os.path.splitext(name)[0], path, aim))
    return out


def link_reader():
    """A reader of what a shortcut (.lnk, .url) opens, through the shell's own object as Explorer reads it ("" when
    it names none); a program file opens itself."""
    import clr  # noqa: F401  pythonnet, which pywebview has loaded
    from System import Activator, Type
    from System.Reflection import BindingFlags
    shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"))

    def target(path: str) -> str:
        if path.lower().endswith(".exe"):
            return path
        link = shell.GetType().InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, None, shell, [path])
        return str(link.GetType().InvokeMember("TargetPath", BindingFlags.GetProperty, None, link, None) or "")
    return target


def open_app(aumid: str) -> bool:
    """Start an entry of Start's list by its AppUserModelID, as a click in Start does."""
    return shell32.ShellExecuteW(None, "open", "shell:AppsFolder\\" + aumid, None, None, 1) > 32


def open_file(path: str) -> bool:
    """Open a shortcut or a program file itself, as a double-click on it does: a program in its own folder, a
    shortcut in the folder it names."""
    folder = os.path.dirname(path) if path.lower().endswith(".exe") else None
    return shell32.ShellExecuteW(None, "open", path, None, folder, 1) > 32


def ui_language() -> str:
    """The language Windows shows its own names in, as a tag (en_US, zh_CN)."""
    import locale
    return locale.windows_locale.get(kernel32.GetUserDefaultUILanguage(), "")


# --- processes

def exe_of(pid: int) -> str:
    """The executable's full path, or "" when the process cannot be opened even to ask (a protected one)."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        return buf.value if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""
    finally:
        kernel32.CloseHandle(handle)


def started(pid: int) -> int:
    """When the process started (FILETIME ticks): with the pid, one running instance."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return 0
    try:
        times = [w.FILETIME() for _ in range(4)]
        kernel32.GetProcessTimes(handle, *map(ctypes.byref, times))
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)


def elevated(pid: int) -> bool:
    """Running as administrator, or so protected this process may not even ask: either way not ours to touch."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return True
    token = w.HANDLE()
    try:
        if not advapi32.OpenProcessToken(handle, 0x0008, ctypes.byref(token)):
            return True
        value, size = DWORD(), DWORD()
        ok = advapi32.GetTokenInformation(token, 20, ctypes.byref(value), 4, ctypes.byref(size))
        kernel32.CloseHandle(token)
        return not ok or bool(value.value)
    finally:
        kernel32.CloseHandle(handle)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", DWORD), ("cntUsage", DWORD), ("th32ProcessID", DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", DWORD), ("cntThreads", DWORD),
                ("th32ParentProcessID", DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


def parents() -> dict[int, int]:
    """{pid: parent pid} of every process."""
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    entry = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
    out: dict[int, int] = {}
    try:
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return out


def descendants(pid: int, tree: dict[int, int] | None = None) -> set[int]:
    tree = parents() if tree is None else tree
    found, grew = {pid}, True
    while grew:
        more = {child for child, parent in tree.items() if parent in found and child not in found}
        found |= more
        grew = bool(more)
    return found


APP_EXE = re.compile(r"^(?:pythonw?|mibo|mibo|msedgewebview2)\w*\.exe$", re.I)


def own_tree() -> set[int]:
    """This app's processes, whose windows the character never operates: this one, the ones above it up to
    the first that is not Python or the app, and the Python and WebView2 processes they started. A program
    the app starts for the person is not the app."""
    tree = parents()
    pid, mine = os.getpid(), set()
    while pid and pid not in mine:
        mine.add(pid)
        parent = tree.get(pid, 0)
        if not parent or not APP_EXE.match(Path(exe_of(parent)).name):
            break
        pid = parent
    found, grew = set(mine), True
    while grew:
        more = {child for child, parent in tree.items() if parent in found and child not in found
                and APP_EXE.match(Path(exe_of(child)).name)}
        found |= more
        grew = bool(more)
    return found


def file_facts(path: str) -> dict:
    """FileDescription, ProductName and CompanyName from the executable's version resource."""
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return {}
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, data):
        return {}
    ptr, n = ctypes.c_void_p(), ctypes.c_uint()
    langs = []
    if version.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(n)) and n.value:
        pairs = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint16 * 2 * (n.value // 4))).contents
        langs = [f"{lang:04x}{page:04x}" for lang, page in pairs]
    out: dict = {}
    for code in langs + ["040904b0", "040904e4", "000004b0"]:
        for key in ("FileDescription", "ProductName", "CompanyName"):
            if key in out:
                continue
            if version.VerQueryValueW(data, f"\\StringFileInfo\\{code}\\{key}", ctypes.byref(ptr),
                                      ctypes.byref(n)) and n.value:
                out[key] = ctypes.wstring_at(ptr.value, n.value).rstrip("\0").strip()
    return out


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [("hIcon", w.HICON), ("iIcon", ctypes.c_int), ("dwAttributes", DWORD),
                ("szDisplayName", ctypes.c_wchar * 260), ("szTypeName", ctypes.c_wchar * 80)]


def folder_type() -> str:
    """What File Explorer's Type column says of a folder in this Windows' language ("File folder", "文件夹")."""
    info = SHFILEINFOW()
    # FILE_ATTRIBUTE_DIRECTORY with SHGFI_TYPENAME | SHGFI_USEFILEATTRIBUTES: no disk access.
    ctypes.windll.shell32.SHGetFileInfoW("folder", 0x10, ctypes.byref(info), ctypes.sizeof(info), 0x400 | 0x10)
    return info.szTypeName


# --- who signed a file: WinVerifyTrust, then the signer certificate's display name

class GUID(ctypes.Structure):
    _fields_ = [("a", DWORD), ("b", w.WORD), ("c", w.WORD), ("d", ctypes.c_ubyte * 8)]


class WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", DWORD), ("pcwszFilePath", w.LPCWSTR), ("hFile", w.HANDLE),
                ("pgKnownSubject", ctypes.c_void_p)]


class WINTRUST_DATA(ctypes.Structure):
    _fields_ = [("cbStruct", DWORD), ("pPolicyCallbackData", ctypes.c_void_p),
                ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", DWORD), ("fdwRevocationChecks", DWORD),
                ("dwUnionChoice", DWORD), ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)),
                ("dwStateAction", DWORD), ("hWVTStateData", w.HANDLE), ("pwszURLReference", w.LPCWSTR),
                ("dwProvFlags", DWORD), ("dwUIContext", DWORD), ("pSignatureSettings", ctypes.c_void_p)]


class CRYPT_PROVIDER_CERT(ctypes.Structure):
    _fields_ = [("cbStruct", DWORD), ("pCert", ctypes.c_void_p)]


class CRYPT_PROVIDER_SGNR(ctypes.Structure):
    _fields_ = [("cbStruct", DWORD), ("sftVerifyAsOf", w.FILETIME), ("csCertChain", DWORD),
                ("pasCertChain", ctypes.POINTER(CRYPT_PROVIDER_CERT))]


wintrust.WinVerifyTrust.argtypes = [HWND, ctypes.POINTER(GUID), ctypes.POINTER(WINTRUST_DATA)]
wintrust.WinVerifyTrust.restype = ctypes.c_long
wintrust.WTHelperProvDataFromStateData.argtypes = [w.HANDLE]
wintrust.WTHelperProvDataFromStateData.restype = ctypes.c_void_p
wintrust.WTHelperGetProvSignerFromChain.argtypes = [ctypes.c_void_p, DWORD, BOOL, DWORD]
wintrust.WTHelperGetProvSignerFromChain.restype = ctypes.POINTER(CRYPT_PROVIDER_SGNR)
crypt32.CertGetNameStringW.argtypes = [ctypes.c_void_p, DWORD, DWORD, ctypes.c_void_p, w.LPWSTR, DWORD]
VERIFY_V2 = GUID(0xAAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))


def signer(path: str) -> str:
    """The name on a valid embedded signature, or "" (unsigned, broken, or signed only through a catalog)."""
    info = WINTRUST_FILE_INFO(cbStruct=ctypes.sizeof(WINTRUST_FILE_INFO), pcwszFilePath=path)
    # No UI, no revocation round trip to the network, verify and keep the state to read the signer.
    data = WINTRUST_DATA(cbStruct=ctypes.sizeof(WINTRUST_DATA), dwUIChoice=2, fdwRevocationChecks=0,
                         dwUnionChoice=1, pFile=ctypes.pointer(info), dwStateAction=1, dwProvFlags=0x1000)
    try:
        if wintrust.WinVerifyTrust(None, ctypes.byref(VERIFY_V2), ctypes.byref(data)) != 0:
            return ""
        provider = wintrust.WTHelperProvDataFromStateData(data.hWVTStateData)
        sgnr = wintrust.WTHelperGetProvSignerFromChain(provider, 0, False, 0) if provider else None
        if not sgnr or not sgnr.contents.csCertChain:
            return ""
        buf = ctypes.create_unicode_buffer(256)
        crypt32.CertGetNameStringW(sgnr.contents.pasCertChain[0].pCert, 4, 0, None, buf, 256)
        return buf.value
    finally:
        data.dwStateAction = 2
        wintrust.WinVerifyTrust(None, ctypes.byref(VERIFY_V2), ctypes.byref(data))


def package_of(path: str) -> str:
    """For an app installed from a package (under WindowsApps, which only the system writes), its family
    name: the package name and its publisher's hash, e.g. Microsoft.WindowsCalculator_8wekyb3d8bbwe."""
    m = re.search(r"\\WindowsApps\\([^\\_]+)_[^\\_]*_[^\\_]*_[^\\_]*_([a-z0-9]{13})\\", path, re.I)
    return f"{m.group(1)}_{m.group(2)}" if m else ""


# --- real input, for controls that offer no automation pattern

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", DWORD), ("dwFlags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", DWORD), ("time", DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
# Marks the input this layer sends, so the watch on the person's own keys and mouse can tell it apart.
MARK = 0x4D69626F
KEYUP, UNICODE, EXTENDED = 0x2, 0x4, 0x1
VK = {"Enter": 0x0D, "Escape": 0x1B, "Tab": 0x09, "Backspace": 0x08, "Delete": 0x2E, "Space": 0x20,
      "ArrowDown": 0x28, "ArrowUp": 0x26, "ArrowLeft": 0x25, "ArrowRight": 0x27, "PageDown": 0x22,
      "PageUp": 0x21, "Home": 0x24, "End": 0x23, "F2": 0x71, "F5": 0x74, "F10": 0x79,
      "Ctrl": 0x11, "Shift": 0x10, "Alt": 0x12, "Apps": 0x5D}
EXTENDED_KEYS = {0x28, 0x26, 0x25, 0x27, 0x22, 0x21, 0x24, 0x23, 0x2E, 0x5D}


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", DWORD)]


user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
# The tick counts just before and just after this layer's latest input went out.
_sent = (0, 0)


def idle_s() -> float:
    """Seconds since the person last pressed a key, clicked or moved the mouse anywhere (GetLastInputInfo).
    Input this layer sent is not theirs: when it is the latest, they have been still since before it."""
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    user32.GetLastInputInfo(ctypes.byref(info))
    now = kernel32.GetTickCount()
    if (info.dwTime - _sent[0]) & 0xFFFFFFFF <= (_sent[1] - _sent[0]) & 0xFFFFFFFF:
        return 3600.0
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000


def _key(vk: int, up: bool) -> INPUT:
    flags = (KEYUP if up else 0) | (EXTENDED if vk in EXTENDED_KEYS else 0)
    return INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(wVk=vk, dwFlags=flags, dwExtraInfo=MARK)))


def _send(inputs: list[INPUT]) -> int:
    global _sent
    arr = (INPUT * len(inputs))(*inputs)
    before = kernel32.GetTickCount()
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    _sent = (before, kernel32.GetTickCount() + 30)
    return sent


def chord(vks: list[int]) -> None:
    """Press the keys in order and release them in reverse: one keystroke with its modifiers."""
    _send([_key(v, False) for v in vks] + [_key(v, True) for v in reversed(vks)])


def type_text(text: str) -> None:
    """Each character as a Unicode keystroke; a line break as Shift+Enter, a new line in editors that never
    sends in a chat program."""
    seq: list[INPUT] = []
    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            seq += [_key(0x10, False), _key(0x0D, False), _key(0x0D, True), _key(0x10, True)]
            continue
        for unit in ch.encode("utf-16-le").hex(" ", 2).split():
            code = int(unit[2:4] + unit[0:2], 16)
            seq += [INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(wScan=code, dwFlags=UNICODE, dwExtraInfo=MARK))),
                    INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(wScan=code, dwFlags=UNICODE | KEYUP,
                                                               dwExtraInfo=MARK)))]
    for i in range(0, len(seq), 64):
        _send(seq[i:i + 64])


def click_at(x: int, y: int, double: bool = False, right: bool = False) -> None:
    """A real click at a screen point (physical pixels), across the whole virtual desktop."""
    left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    wide, high = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    nx = int(((x - left) * 65535) / max(wide - 1, 1))
    ny = int(((y - top) * 65535) / max(high - 1, 1))
    move = INPUT(type=0, u=_INPUTUNION(mi=MOUSEINPUT(dx=nx, dy=ny, dwFlags=0x0001 | 0x8000 | 0x4000,
                                                     dwExtraInfo=MARK)))
    down, up = (0x0008, 0x0010) if right else (0x0002, 0x0004)
    press = [INPUT(type=0, u=_INPUTUNION(mi=MOUSEINPUT(dwFlags=f, dwExtraInfo=MARK))) for f in (down, up)]
    _send([move] + press)
    if double:
        # The second click a moment later, as a hand gives it: a list selects on the first before it opens on both.
        time.sleep(0.08)
        _send(press)


def wheel_at(x: int, y: int, notches: int) -> None:
    """Turn the wheel over a point: positive reveals what is above."""
    left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    wide, high = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    nx = int(((x - left) * 65535) / max(wide - 1, 1))
    ny = int(((y - top) * 65535) / max(high - 1, 1))
    _send([INPUT(type=0, u=_INPUTUNION(mi=MOUSEINPUT(dx=nx, dy=ny, dwFlags=0x0001 | 0x8000 | 0x4000,
                                                     dwExtraInfo=MARK))),
           INPUT(type=0, u=_INPUTUNION(mi=MOUSEINPUT(mouseData=ctypes.c_uint32(notches * 120).value,
                                                     dwFlags=0x0800, dwExtraInfo=MARK)))])


# --- a window's picture

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", w.WORD), ("biBitCount", w.WORD), ("biCompression", DWORD),
                ("biSizeImage", DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", DWORD), ("biClrImportant", DWORD)]


def capture(hwnd: int):
    """The window as a Pillow image, drawn by the window itself (PrintWindow), so nothing covering it shows."""
    from PIL import Image
    left, top, right, bottom = rect(hwnd)
    r = w.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    wide, high = r.right - r.left, r.bottom - r.top
    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, wide, high)
    old = gdi32.SelectObject(dc, bmp)
    try:
        user32.PrintWindow(hwnd, dc, 2)  # PW_RENDERFULLCONTENT
        header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=wide, biHeight=-high,
                                  biPlanes=1, biBitCount=32)
        buf = ctypes.create_string_buffer(wide * high * 4)
        gdi32.GetDIBits(dc, bmp, 0, high, buf, ctypes.byref(header), 0)
        image = Image.frombuffer("RGB", (wide, high), buf, "raw", "BGRX", 0, 1)
    finally:
        gdi32.SelectObject(dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(dc)
        user32.ReleaseDC(None, screen)
    scale = unscaled(hwnd)
    if scale != 1:
        # A window that does not know the monitor's scale draws itself at its own size, in the top left corner:
        # stretched as the monitor shows it, so the picture is the window and not a third of black around it.
        image = image.crop((0, 0, round(wide / scale), round(high / scale))).resize((wide, high))
    # Cut the invisible resize border, so picture pixels match the drawn window.
    return image.crop((left - r.left, top - r.top, right - r.left, bottom - r.top)), (left, top)


# --- the clipboard: text put there for one paste, and the person's own put back after it

for _fn, _args, _res in (
        (user32.OpenClipboard, [HWND], BOOL), (user32.CloseClipboard, [], BOOL), (user32.EmptyClipboard, [], BOOL),
        (user32.EnumClipboardFormats, [ctypes.c_uint], ctypes.c_uint),
        (user32.GetClipboardData, [ctypes.c_uint], w.HANDLE),
        (user32.SetClipboardData, [ctypes.c_uint, w.HANDLE], w.HANDLE),
        (user32.RegisterClipboardFormatW, [w.LPCWSTR], ctypes.c_uint),
        (user32.GetClipboardFormatNameW, [ctypes.c_uint, w.LPWSTR, ctypes.c_int], ctypes.c_int),
        (user32.CreateWindowExW, [DWORD, w.LPCWSTR, w.LPCWSTR, DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p], HWND),
        (user32.DestroyWindow, [HWND], BOOL),
        (kernel32.GlobalAlloc, [ctypes.c_uint, ctypes.c_size_t], w.HGLOBAL),
        (kernel32.GlobalLock, [w.HGLOBAL], ctypes.c_void_p), (kernel32.GlobalUnlock, [w.HGLOBAL], BOOL),
        (kernel32.GlobalSize, [w.HGLOBAL], ctypes.c_size_t), (kernel32.GlobalFree, [w.HGLOBAL], w.HGLOBAL),
        (gdi32.GetEnhMetaFileBits, [w.HANDLE, ctypes.c_uint, ctypes.c_void_p], ctypes.c_uint),
        (gdi32.SetEnhMetaFileBits, [ctypes.c_uint, ctypes.c_char_p], w.HANDLE)):
    _fn.argtypes, _fn.restype = _args, _res
CF_UNICODETEXT, CF_ENHMETAFILE = 13, 14
# Formats held as GDI handles or private data, not memory: Windows makes the bitmap and the old metafile again
# from the DIB and the enhanced metafile kept beside them.
HANDLE_FORMATS = {2, 3, 9, 0x80, 0x82, 0x83, 0x8E}
# OLE's pointers to the data object of the program that copied: meaningless once that program no longer owns the
# clipboard; without them OLE serves the same data from the plain formats.
OLE_OWN = {"DataObject", "Ole Private Data"}
# Text put there for a paste stays out of the clipboard history and the cloud clipboard (Windows' own formats).
UNSEEN = ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory", "CanUploadToCloudClipboard")


class _Clipboard:
    """The clipboard open on a message-only window of this thread (an owner, so the data set stays there)."""

    def __enter__(self):
        self.hwnd = user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, HWND(-3), None, None, None)
        for _ in range(20):
            # Another program may hold it for a moment.
            if user32.OpenClipboard(self.hwnd):
                return self
            time.sleep(0.05)
        user32.DestroyWindow(self.hwnd)
        raise OSError("the clipboard is held by another program")

    def __exit__(self, *exc):
        user32.CloseClipboard()
        user32.DestroyWindow(self.hwnd)


def clipboard_save() -> list[tuple[int, bytes]]:
    """Everything on the clipboard now, format by format, as bytes."""
    kept = []
    with _Clipboard():
        fmt = user32.EnumClipboardFormats(0)
        while fmt:
            name = ctypes.create_unicode_buffer(80)
            user32.GetClipboardFormatNameW(fmt, name, 80)
            if fmt not in HANDLE_FORMATS and not 0x200 <= fmt <= 0x3FF and name.value not in OLE_OWN:
                handle = user32.GetClipboardData(fmt)
                if handle and fmt == CF_ENHMETAFILE:
                    size = gdi32.GetEnhMetaFileBits(handle, 0, None)
                    buf = ctypes.create_string_buffer(size)
                    gdi32.GetEnhMetaFileBits(handle, size, buf)
                    kept.append((fmt, buf.raw))
                elif handle:
                    size = kernel32.GlobalSize(handle)
                    ptr = kernel32.GlobalLock(handle)
                    if ptr:
                        kept.append((fmt, ctypes.string_at(ptr, size)))
                        kernel32.GlobalUnlock(handle)
            fmt = user32.EnumClipboardFormats(fmt)
    return kept


def clipboard_put(items: list[tuple[int, bytes]]) -> None:
    """The clipboard emptied and given `items` (format, bytes), kept out of the clipboard history; left empty
    when there are none."""
    unseen = [(user32.RegisterClipboardFormatW(name), b"\0\0\0\0") for name in UNSEEN] if items else []
    have = {fmt for fmt, _ in items}
    with _Clipboard():
        user32.EmptyClipboard()
        for fmt, data in items + [u for u in unseen if u[0] not in have]:
            if fmt == CF_ENHMETAFILE:
                user32.SetClipboardData(fmt, gdi32.SetEnhMetaFileBits(len(data), data))
                continue
            memory = kernel32.GlobalAlloc(0x0002, max(len(data), 1))
            ptr = kernel32.GlobalLock(memory)
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(memory)
            if not user32.SetClipboardData(fmt, memory):
                kernel32.GlobalFree(memory)


def clipboard_text(text: str) -> list[tuple[int, bytes]]:
    """`text` as the clipboard's Unicode text, lines ending as Windows' programs expect."""
    return [(CF_UNICODETEXT, (text.replace("\r\n", "\n").replace("\n", "\r\n") + "\0").encode("utf-16-le"))]

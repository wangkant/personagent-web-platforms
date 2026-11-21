"""Acting at a point of a picture, through the surfaces the two loops already have.

A surface is where the picture comes from and where input goes:
- `PaneSurface`: a tab (web/tabs.Tab, the person's pane or the headless
  visitor); input is DevTools' own Input domain, in CSS pixels.
- `WindowSurface`: one granted window; input is real (SendInput), sent only
  after the window is checked to be the one in front and the point is checked
  to land on it (WindowFromPoint), on a per-monitor-aware thread.

`Sight` puts the gate in front of every action. What is at the point is looked
up in the structure (DOM.getNodeForLocation, UI Automation's ElementFromPoint):
when it names something there, its name and role go through the existing
rules (web/gate.classify, desk/gate.press and key) and a card for anything
with an effect outside. Where the structure has nothing, the action is
unsorted, and the fixed line says unsorted needs the person. So:

- The first pointer action on an unsorted spot asks once, on a card that says
  plainly the character cannot tell what the buttons there do, whether it may
  click and drag by looking in this window for this task (the lease). The
  lease ends with the task, and does not follow the character to another
  window or another site.
- Typing text into the window is free, as typing into a field is when the
  window is read by names; a password, payment detail or code is never typed.
  Enter and any key that may send are the kind outward: in automatic review
  the reviewer judges them on the person's own words with the text typed, and
  a card with the text and a ring on the picture comes only when it does not
  pass. A spot the structure names with an effect outside goes the same way.
- Scrolling is free, as it is in both loops.
- A spot the structure names keeps its own rules inside a lease: a "Delete"
  button still asks.

Each action is followed by a second picture and the line says whether the
picture changed where it pointed, elsewhere, or not at all: dispatch, never
proof of the outcome.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import re
import secrets
import time
from collections.abc import Callable
from typing import Any

from . import capture, words
from .frame import Frame, change, ring

SETTLE_S = 0.5
ANSWER_S = 15.0
DRAG_STEPS = 12
# A nameless element covering this share of the window is the drawing, not a control.
DRAWN_SHARE = 0.15
# Marks are drawn only for a few named parts. On 2026-11-19 a Calculator with its 37 buttons marked had
# claude-haiku-5.5 looping on Clear and wrong digits for 16 calls (the tags are digits too); marks on dense windows
# also lost to plain pointing in the pointing study.
MARKS_MAX = 12
# Keys that may send or commit what was typed: asked each time by sight.
SENDING_KEYS = re.compile(r"^(?:(?:Ctrl|Shift|Alt)\+)*Enter$|^Ctrl\+(?:S|P)$|^Alt\+S$", re.IGNORECASE)


# --- the pane

POINT_JS = """function () {
  const e = this.nodeType === 1 ? this : this.parentElement;
  const r = e.getBoundingClientRect();
  return {tag: e.tagName.toLowerCase(), w: r.width, h: r.height, area: innerWidth * innerHeight};
}"""

MARKS_JS = """(max) => {
  const sel = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=link],' +
    '[role=checkbox],[role=radio],[role=tab],[role=menuitem],[role=slider],[contenteditable=true],[onclick]';
  const out = [];
  for (const e of document.querySelectorAll(sel)) {
    const r = e.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.right < 0 || r.bottom < 0 || r.left > innerWidth || r.top > innerHeight) continue;
    if (!e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) continue;
    const name = (e.getAttribute('aria-label') || e.innerText || e.value || e.title || e.getAttribute('placeholder') || '')
      .trim().replace(/\\s+/g, ' ').slice(0, 40);
    out.push([r.x, r.y, r.width, r.height, (e.getAttribute('role') || e.tagName.toLowerCase()) + (name ? ' "' + name + '"' : '')]);
    if (out.length > max) break;
  }
  return out;
}"""

WEB_KEYS = {"Enter": 13, "Escape": 27, "Tab": 9, "Backspace": 8, "Delete": 46, "Space": 32, "ArrowDown": 40,
            "ArrowUp": 38, "ArrowLeft": 37, "ArrowRight": 39, "PageDown": 34, "PageUp": 33, "Home": 36, "End": 35}
WEB_MODS = {"Alt": 1, "Ctrl": 2, "Shift": 8}


class PaneSurface:
    kind = "pane"

    def __init__(self, tab, page=None):
        from ..web.pageops import Page
        self.tab = tab
        self.page = page or Page(tab)

    @property
    def name(self) -> str:
        from ..web.gate import site_of
        return site_of(self.tab.url) or "the page"

    def place(self) -> str:
        """Where a lease holds: the site."""
        return self.name

    async def capture(self, long_edge: int) -> Frame:
        # The ring shown before an action is gone before the picture after it, or it would read as a change.
        from ..web.pageops import UNRING_JS
        try:
            await self.tab.evaluate(f"() => {{ {UNRING_JS} }}")
        except Exception:
            pass
        return await capture.pane(self.tab, long_edge)

    async def marks(self) -> list:
        found = await self.tab.evaluate(MARKS_JS, MARKS_MAX) or []
        return [((x, y, w, h), what) for x, y, w, h, what in found]

    async def at(self, x: float, y: float) -> dict:
        """What the page has at a viewport point: {"known", "role", "name", "what", "facts"}."""
        try:
            node = await self.tab.cdp("DOM.getNodeForLocation", {"x": int(x), "y": int(y),
                                                                 "includeUserAgentShadowDOM": False})
        except Exception:
            await self.tab.cdp("DOM.getDocument", {"depth": 0})
            node = await self.tab.cdp("DOM.getNodeForLocation", {"x": int(x), "y": int(y),
                                                                 "includeUserAgentShadowDOM": False})
        ident = node.get("backendNodeId")
        if not ident:
            return {"known": False, "what": "nothing the page names"}
        facts = await self.page.facts(ident) or {}
        shape = await self.page.on_node(ident, POINT_JS) or {}
        tag = shape.get("tag", "")
        role = str(facts.get("role") or tag)
        name = " ".join(str(facts.get("label") or facts.get("text") or facts.get("tip") or "").split())[:80]
        drawn = tag in ("canvas", "html", "body", "svg", "embed", "object", "video") or (
            not name and shape.get("w", 0) * shape.get("h", 0) >= DRAWN_SHARE * shape.get("area", 1))
        known = not drawn and bool(name or facts.get("link") or tag in ("button", "input", "select", "textarea", "a"))
        return {"known": known, "role": role, "name": name, "facts": facts, "ident": ident,
                "what": f"{role} \"{name}\"" if known else f"a drawn area ({tag or 'no element'})"}

    async def gate(self, mode: str, hit: dict) -> tuple[str, str]:
        from ..web.gate import classify
        kind, why = classify(mode, hit["name"], hit["role"], hit["facts"], self.tab.url)
        # A point on a picture is pressed without watching its requests: a bare submission, and a button code could
        # not sort, ask first here.
        if not kind and mode == "click" and not hit["facts"].get("link") and hit["role"] in ("button", "menuitem"):
            return "submit", "a button pressed here with its requests unwatched"
        return ("submit" if kind == "watch" else kind), why

    async def focused(self) -> dict:
        ident = await self.page.active()
        if ident is None:
            return {"known": False, "secret": False}
        facts = await self.page.facts(ident) or {}
        return {"known": bool(facts.get("textEntry")), "secret": await self.page.secret(ident), "facts": facts,
                "role": str(facts.get("role") or facts.get("tag") or ""),
                "name": str(facts.get("label") or "")[:80]}

    async def ready(self, x: float, y: float) -> str:
        return ""

    async def _mouse(self, kind: str, x: float, y: float, **more: Any) -> None:
        await self.tab.cdp("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, **more})

    async def click(self, x: float, y: float, count: int = 1, right: bool = False) -> None:
        button = "right" if right else "left"
        await self._mouse("mouseMoved", x, y)
        for n in range(1, count + 1):
            await self._mouse("mousePressed", x, y, button=button, clickCount=n, buttons=2 if right else 1)
            await self._mouse("mouseReleased", x, y, button=button, clickCount=n)

    async def drag(self, x0: float, y0: float, x1: float, y1: float) -> None:
        await self._mouse("mouseMoved", x0, y0)
        await self._mouse("mousePressed", x0, y0, button="left", clickCount=1, buttons=1)
        for i in range(1, DRAG_STEPS + 1):
            t = i / DRAG_STEPS
            await self._mouse("mouseMoved", x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, button="left", buttons=1)
            await asyncio.sleep(0.015)
        await self._mouse("mouseReleased", x1, y1, button="left", clickCount=1)

    async def scroll(self, x: float, y: float, notches: int) -> None:
        await self._mouse("mouseWheel", x, y, deltaX=0, deltaY=-120 * notches)

    async def type(self, text: str) -> None:
        # Key by key with its character, so a page that listens for keys (a drawn field) hears them too.
        for ch in text:
            await self.tab.cdp("Input.dispatchKeyEvent", {"type": "keyDown", "key": ch, "text": ch,
                                                          "unmodifiedText": ch})
            await self.tab.cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})

    async def key(self, keys: str) -> None:
        *mods, last = keys.split("+")
        flags = sum(WEB_MODS[m] for m in mods)
        code = WEB_KEYS.get(last) or ord(last.upper())
        key = " " if last == "Space" else last
        base = {"key": key, "code": last if last in WEB_KEYS else f"Key{last.upper()}", "modifiers": flags,
                "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code}
        await self.tab.cdp("Input.dispatchKeyEvent", {"type": "rawKeyDown", **base})
        if last in ("Enter", "Space") and not flags:
            text = "\r" if last == "Enter" else " "
            await self.tab.cdp("Input.dispatchKeyEvent", {"type": "char", **base, "text": text, "unmodifiedText": text})
        await self.tab.cdp("Input.dispatchKeyEvent", {"type": "keyUp", **base})


# --- a window

class WindowSurface:
    kind = "window"

    def __init__(self, hwnd: int, pids: set[int], own: set[int] | None = None, program: str = "",
                 idle: Callable[[], None] = lambda: None):
        from ..desk import grants, win32
        self.hwnd = hwnd
        # Waits until the person's keyboard and mouse are still, as the structure layer does before real input
        # (desk/uia.View.idle); raises its Refused when they kept on, and the strip asks them.
        self.idle = idle
        # A packaged app's frame belongs to ApplicationFrameHost; the app inside it is another process.
        self.pids = set(pids) | {win32.pid_of(hwnd)}
        self.own = win32.own_tree() if own is None else own
        self.program = program
        self.exe = os.path.basename(win32.exe_of(grants.program_pid(hwnd))).lower()

    @property
    def name(self) -> str:
        from ..desk import win32
        return self.program or win32.title_of(self.hwnd)[:60] or "the window"

    def place(self) -> str:
        return f"window:{self.hwnd}"

    async def capture(self, long_edge: int) -> Frame:
        return await _answer(capture.window, self.hwnd, self.pids, self.own, long_edge)

    def _marks(self) -> list:
        """The named controls of the target window (kept as `tree` for looking up a point), as marks."""
        from ..desk import uia
        A = uia.automation()
        self.tree = []
        with capture.aware():
            cond = A.Automation.ControlViewCondition
            for e in uia.element(self.hwnd).FindAll(A.TreeScope.Descendants, cond):
                c = e.Current
                role = uia.ROLES.get(c.ControlType.ProgrammaticName.split(".")[-1], "custom")
                r = c.BoundingRectangle
                if role in uia.CONTROLS and c.Name and not c.IsOffscreen and r.Width >= 4 and r.Height >= 4:
                    self.tree.append(((r.X, r.Y, r.Width, r.Height), role, " ".join(c.Name.split())[:80], e))
                if len(self.tree) >= uia.NODES_MAX:
                    break
        return [(box, f'{role} "{name[:40]}"') for box, role, name, _e in self.tree]

    async def marks(self) -> list:
        return await _answer(self._marks)

    def _at(self, x: float, y: float) -> dict:
        from ..desk import uia, win32
        A = uia.automation()
        Point = _point_type()
        with capture.aware():
            root = win32.window_at(int(x), int(y))
            if root != self.hwnd and win32.pid_of(root) not in self.pids:
                exe = os.path.basename(win32.exe_of(win32.pid_of(root))) or "another window"
                return {"known": False, "obscured": exe, "what": f"{exe}, not this window"}
            el = A.AutomationElement.FromPoint(Point(x, y))
            if el is None or el.Current.ProcessId not in self.pids:
                return {"known": False, "what": "nothing this window names"}
            box = win32.rect(self.hwnd)
            area = max(1, (box[2] - box[0]) * (box[3] - box[1]))

            def sort(e) -> tuple:
                c = e.Current
                role = uia.ROLES.get(c.ControlType.ProgrammaticName.split(".")[-1], "custom")
                r = c.BoundingRectangle
                name = " ".join(str(c.Name or "").split())[:80]
                return role, name, role in uia.CONTROLS and bool(name) and r.Width * r.Height / area < DRAWN_SHARE
            role, name, known = sort(el)
            if not known:
                # Some programs (Paint's XAML islands) answer FromPoint with their host pane: the smallest named
                # control of the last look whose box holds the point is what is there.
                held = [(b[2] * b[3], e) for b, _role, _name, e in getattr(self, "tree", [])
                        if b[0] <= x < b[0] + b[2] and b[1] <= y < b[1] + b[3] and b[2] * b[3] / area < DRAWN_SHARE]
                if held:
                    el = min(held, key=lambda t: t[0])[1]
                    role, name, known = sort(el)
            c = el.Current
            r = c.BoundingRectangle
            node = uia.Node(el=el, role=role, name=name, auto=c.AutomationId, cls=c.ClassName, pid=c.ProcessId,
                            enabled=c.IsEnabled, focused=False, secret=c.IsPassword,
                            rect=(r.X, r.Y, r.Width, r.Height), hwnd=c.NativeWindowHandle, patterns=[],
                            readonly=False, top=self.hwnd, parent=None, cells=[])
            return {"known": known, "role": role, "name": name, "node": node, "size": (r.Width, r.Height),
                    "what": f'{role} "{name}"' if known else f"a drawn area ({role}{' ' + repr(name) if name else ''})"}

    async def at(self, x: float, y: float) -> dict:
        return await _answer(self._at, x, y)

    async def gate(self, mode: str, hit: dict) -> tuple[str, str]:
        from ..desk import gate, win32
        title = win32.title_of(self.hwnd)
        if mode == "drag" and hit["role"] in ("listitem", "treeitem", "row"):
            return "file", f"dragging {hit['name']!r} can move it"
        return gate.press(hit["node"], title, True, "", None, "click", self.exe)

    def _focused(self) -> dict:
        from ..desk import uia
        A = uia.automation()
        with capture.aware():
            f = A.AutomationElement.FocusedElement
            if f is None or f.Current.ProcessId not in self.pids:
                return {"known": False, "secret": False, "elsewhere": True}
            c = f.Current
            role = uia.ROLES.get(c.ControlType.ProgrammaticName.split(".")[-1], "custom")
            node = uia.Node(el=f, role=role, name=c.Name, auto=c.AutomationId, cls=c.ClassName, pid=c.ProcessId,
                            enabled=True, focused=True, secret=c.IsPassword, rect=(0, 0, 0, 0),
                            hwnd=c.NativeWindowHandle, patterns=[], readonly=False, top=self.hwnd, parent=None,
                            cells=[])
            from ..web.pageops import SECRET_LABEL
            secret = c.IsPassword or (role == "textbox" and bool(SECRET_LABEL.search(f"{c.Name} {c.AutomationId}")))
            return {"known": role in ("textbox", "document") or bool(c.Name), "secret": secret, "node": node,
                    "role": role, "name": c.Name}

    async def focused(self) -> dict:
        return await _answer(self._focused)

    async def key_gate(self, keys: str, focus: dict) -> tuple[str, str]:
        from ..desk import gate, win32
        return gate.key(keys, focus.get("node"), win32.title_of(self.hwnd), True, "")

    def _ready(self, x: float | None, y: float | None) -> str:
        """Real input only into the window in front, and only at a point that lands on it, once the person's hands
        are still: why not, or ""."""
        from ..desk import uia, win32
        try:
            self.idle()
        except uia.Refused as e:
            return str(e).split("] ", 1)[-1]
        with capture.aware():
            front = win32.top_of(win32.foreground())
            if front != self.hwnd and win32.pid_of(front) not in self.pids:
                try:
                    uia.element(self.hwnd).SetFocus()
                except Exception:
                    pass
                time.sleep(0.2)
                front = win32.top_of(win32.foreground())
            if front != self.hwnd and win32.pid_of(front) not in self.pids:
                _activate(self.hwnd)
                front = win32.top_of(win32.foreground())
            if front != self.hwnd and win32.pid_of(front) not in self.pids:
                return "the window could not be brought in front; nothing was sent"
            if x is not None:
                root = win32.window_at(int(x), int(y))
                if root != self.hwnd and win32.pid_of(root) not in self.pids:
                    return "something else is over that point now; nothing was sent"
        return ""

    async def ready(self, x: float | None, y: float | None) -> str:
        return await _answer(self._ready, x, y)

    def _run(self, body: Callable[[], None]) -> None:
        with capture.aware():
            body()

    async def click(self, x: float, y: float, count: int = 1, right: bool = False) -> None:
        from ..desk import win32
        await asyncio.to_thread(self._run, lambda: win32.click_at(round(x), round(y), double=count == 2, right=right))

    async def drag(self, x0: float, y0: float, x1: float, y1: float) -> None:
        def body() -> None:
            _mouse(0x0001, x0, y0)
            _mouse(0x0002, x0, y0)
            for i in range(1, DRAG_STEPS + 1):
                t = i / DRAG_STEPS
                time.sleep(0.015)
                _mouse(0x0001, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
            _mouse(0x0004, x1, y1)
        await asyncio.to_thread(self._run, body)

    async def scroll(self, x: float, y: float, notches: int) -> None:
        from ..desk import win32
        await asyncio.to_thread(self._run, lambda: win32.wheel_at(round(x), round(y), notches))

    async def type(self, text: str) -> None:
        from ..desk import win32
        await asyncio.to_thread(self._run, lambda: win32.type_text(text))

    async def key(self, keys: str) -> None:
        from ..desk import uia, win32
        vks = uia.parse_key(keys)
        await asyncio.to_thread(self._run, lambda: win32.chord(vks))


async def _answer(fn: Callable, *args: Any) -> Any:
    """A UI Automation or window call on a thread of its own, given ANSWER_S: a program that hangs (Paint with
    a pop-up open stopped answering FromPoint on 2026-11-19) ends the work by sight instead of the loop."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), ANSWER_S)
    except asyncio.TimeoutError:
        raise capture.Refused(f"[failed] the window did not answer for {ANSWER_S:g} s; nothing more was sent") from None


def _activate(hwnd: int) -> None:
    """The window in front when UI Automation's SetFocus did not do it (a program that will not take the
    focus that way while another process holds it): this thread joins the input of the one in front for the
    moment of the switch, as Windows allows the process in front to hand it over."""
    import ctypes
    from ..desk import win32
    u, k = win32.user32, win32.kernel32
    u.AttachThreadInput.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
    u.SetForegroundWindow.argtypes = [win32.HWND]
    u.BringWindowToTop.argtypes = [win32.HWND]
    theirs = u.GetWindowThreadProcessId(win32.foreground(), None)
    mine = k.GetCurrentThreadId()
    joined = bool(theirs and theirs != mine and u.AttachThreadInput(mine, theirs, True))
    try:
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
    finally:
        if joined:
            u.AttachThreadInput(mine, theirs, False)
    time.sleep(0.2)


def _point_type():
    """System.Windows.Point, from WindowsBase, which UI Automation's assemblies do not load by themselves."""
    import clr
    clr.AddReference("WindowsBase, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35")
    from System.Windows import Point
    return Point


def _mouse(flags: int, x: float, y: float) -> None:
    """One absolute mouse event at a physical screen point (inside capture.aware())."""
    from ..desk import win32
    u = win32.user32
    left, top, wide, high = (u.GetSystemMetrics(i) for i in (76, 77, 78, 79))
    nx = int(((round(x) - left) * 65535) / max(wide - 1, 1))
    ny = int(((round(y) - top) * 65535) / max(high - 1, 1))
    win32._send([win32.INPUT(type=0, u=win32._INPUTUNION(mi=win32.MOUSEINPUT(
        dx=nx, dy=ny, dwFlags=flags | 0x8000 | 0x4000, dwExtraInfo=win32.MARK)))])


# --- the gate, the cards and the check after

class Sight:
    """Seeing and acting by picture on one surface, for one task the person asked for in chat.

    `ask(card)` shows the person a card and gives back their answer ({"yes": bool}) or None (nobody
    answered); it may be a coroutine. `held()` says why to stop now ("" to go on): the stop, the take over,
    the person using the window. Nothing the model writes counts as the person's answer."""

    def __init__(self, surface, profile, *, ask: Callable[[dict], Any], review: Callable[[str, dict], Any], task: str,
                 lang: str = "zh", who: str = "Mibo", held: Callable[[], str] = lambda: ""):
        self.surface = surface
        self.profile = profile
        self.ask = ask
        # The reviewer on one act (awaited: review.decide's Verdict), or None for a kind that is the person's.
        self.review = review
        # The text typed last, which an Enter would send.
        self.typed = ""
        self.task = " ".join(str(task).split())[:300]
        self.lang = lang
        self.who = who
        self.held = held
        self.frame: Frame | None = None
        self.lease: str = ""
        self.cards: list[dict] = []

    async def look(self) -> Frame:
        """A new picture, with the structure's few named parts as marks (none when it names many: then the
        structure is not poor and the loops read it instead)."""
        if self.surface.kind == "window":
            # Working a window brings it in front first (as the desk loop does for real input); a window that
            # still does not come in front is not captured (capture.covering).
            await self.surface.ready(None, None)
        frame = await self.surface.capture(self.profile.long_edge)
        # Read every look: a window's point lookups use what it names now.
        found = await self.surface.marks()
        if self.profile.marks:
            if len(found) <= MARKS_MAX:
                frame.marks = {n + 1: (b[0] + b[2] / 2, b[1] + b[3] / 2, what, b) for n, (b, what) in enumerate(found)
                               if frame.inside(*frame.to_image(b[0] + b[2] / 2, b[1] + b[3] / 2))}
        self.frame = frame
        return frame

    async def _card(self, card: dict) -> dict | None:
        card = {"id": secrets.token_urlsafe(8), "task": self.task, "window": self.surface.name, **card}
        self.cards.append(card)
        answer = self.ask(card)
        if inspect.isawaitable(answer):
            answer = await answer
        return answer

    def _picture(self, at: tuple[float, float] | None) -> str:
        import base64
        import io
        image = ring(self.frame.image, *at) if at else self.frame.image
        image = image.copy()
        image.thumbnail((720, 720))
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=65)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    async def _allowed(self, a: dict, hit: dict | None, at: tuple | None) -> tuple[str, str]:
        """("", why) to go on, or (status, line) when the person said no or was not asked."""
        do = a["do"]
        name = self.surface.name
        if do == "scroll":
            return "", "scrolling"
        if do in ("click", "double_click", "drag") or (do == "type" and at is not None):
            if hit and hit.get("known"):
                kind, why = await self.surface.gate("drag" if do == "drag" else "click", hit)
                if kind:
                    verdict = await self._reviewed(kind, {"target": [hit["what"]], "page_says": hit["what"]})
                    if verdict is not None and verdict.decision != "ask":
                        return self._verdict(verdict, kind)
                    answer = await self._card({"type": "see_action", "kind": kind, "why": why,
                                               "target": hit["what"], "picture": self._picture(at),
                                               **self._why(verdict)})
                    if not (answer and answer.get("yes")):
                        return "declined", (f"[declined] the person {'said no to' if answer else 'did not answer'} "
                                            f"{kind} ({hit['what']}); nothing was done")
                    return "", f"{kind}, confirmed by the person"
            elif self.lease != self.surface.place():
                answer = await self._card({"type": "see_lease", "text": words.say(words.LEASE, self.lang, who=self.who,
                                                                                   window=name),
                                           "target": hit["what"] if hit else "", "picture": self._picture(at),
                                           "choices": ["allow", "no"]})
                if not (answer and answer.get("yes")):
                    return "declined", ("[declined] the person did not allow working by sight in this window; "
                                        "nothing was done")
                self.lease = self.surface.place()
        if do == "type":
            from ..review import CARD_DIGITS, SECRET_TEXT, _luhn
            if SECRET_TEXT.search(a["text"]) or any(_luhn(re.sub(r"\D", "", m)) for m in CARD_DIGITS.findall(a["text"])):
                return "refused", ("[refused] the text looks like a password, code, key or card number; the person "
                                   "types it themselves")
            # Typing into the window is free; what an Enter sends goes to the reviewer with it.
            self.typed = a["text"][:2000]
            return "", "typing into the window"
        if do == "key":
            focus = await self.surface.focused()
            kind, why = "", ""
            if focus.get("known") and self.surface.kind == "window":
                kind, why = await self.surface.key_gate(a["key"], focus)
            elif focus.get("known") and a["key"] in ("Enter", "Space"):
                kind, why = await self.surface.gate("enter" if a["key"] == "Enter" else "click", focus)
            if kind or SENDING_KEYS.match(a["key"]):
                verdict = await self._reviewed(kind or "submit", {"target": [], "text": self.typed,
                                                                  "page_says": a["key"]})
                if verdict is not None and verdict.decision != "ask":
                    return self._verdict(verdict, kind or "submit")
                answer = await self._card({"type": "see_key", "key": a["key"], "kind": kind or "submit",
                                           "why": why or "a key that may send, pressed by sight",
                                           "picture": self._picture(None), "text": self.typed, **self._why(verdict),
                                           "said": words.say(words.KEY, self.lang, who=self.who, window=name,
                                                             key=a["key"])})
                if not (answer and answer.get("yes")):
                    return "declined", f"[declined] the person did not let {a['key']} be pressed; nothing was done"
                return "", "a key confirmed by the person"
            if not focus.get("known") and self.lease != self.surface.place():
                answer = await self._card({"type": "see_lease", "text": words.say(words.LEASE, self.lang,
                                                                                   who=self.who, window=name),
                                           "target": "", "picture": self._picture(None), "choices": ["allow", "no"]})
                if not (answer and answer.get("yes")):
                    return "declined", "[declined] the person did not allow working by sight here; nothing was done"
                self.lease = self.surface.place()
        return "", "moving about"

    async def _reviewed(self, kind: str, action: dict):
        """The reviewer's verdict on an act of `kind` in this window, or None when the kind is the person's."""
        return await self.review(kind, {"where": self.surface.name, **action})

    @staticmethod
    def _verdict(verdict, kind: str) -> tuple[str, str]:
        """What a pass or a refusal by the reviewer makes of the act: ("", line) to go on, or a declined line."""
        if verdict.decision == "go":
            return "", f"{kind}, passed by the app's review: {verdict.reason}"
        return "declined", f"[declined] {kind}, nothing was done. {verdict.declined()}" + (
            "; turned down three times in a row: stop and hand the task back" if verdict.stop else "")

    def _why(self, verdict) -> dict:
        """The card's line on why the reviewer did not pass it."""
        return {"review": verdict.line(self.lang)} if verdict is not None else {}

    async def act(self, a: dict) -> dict:
        """One action read by ask.read: {"status", "line"}; status ok, failed, declined, refused, handover (the
        window stopped answering) or why held."""
        try:
            return await self._act(a)
        except capture.Refused as e:
            return {"status": "handover", "line": str(e)}

    async def _act(self, a: dict) -> dict:
        why = self.held()
        if why:
            return {"status": why, "line": f"[{why}] nothing was done"}
        before = self.frame
        if before is None:
            return {"status": "failed", "line": "[failed] no picture yet: look first"}
        if a["do"] == "wait":
            await asyncio.sleep(a["seconds"])
            return {"status": "ok", "line": f"[ok] waited {a['seconds']:g}s"}
        old = capture.stale(before)
        if old:
            return {"status": "failed", "line": f"[changed] {old}; nothing was done, look again"}
        at = a.get("at")
        real = before.to_real(*at) if at else None
        hit = await self.surface.at(*real) if real else None
        if hit and hit.get("obscured"):
            return {"status": "refused", "line": f"[refused] that point is on {hit['obscured']}; nothing was done"}
        status, line = await self._allowed(a, hit, at)
        if status:
            return {"status": status, "line": line}
        if self.held():
            return {"status": self.held(), "line": f"[{self.held()}] nothing was done"}
        not_ready = await self.surface.ready(*(real or (None, None)))
        if not_ready:
            # Held (paused while the person kept using the computer), or no way to the window.
            return {"status": self.held() or "refused", "line": f"[{self.held() or 'refused'}] {not_ready}"}
        do = a["do"]
        where = f" at {round(at[0])},{round(at[1])} of the picture" if at else ""
        on = f" on {hit['what']}" if hit else ""
        if do in ("click", "double_click"):
            await self.surface.click(*real, count=2 if do == "double_click" else 1)
        elif do == "drag":
            to = before.to_real(*a["to"])
            await self.surface.drag(*real, *to)
            where += f" to {round(a['to'][0])},{round(a['to'][1])}"
        elif do == "scroll":
            await self.surface.scroll(*real, a["amount"] if a["direction"] == "up" else -a["amount"])
        elif do == "type":
            if real:
                await self.surface.click(*real)
                await asyncio.sleep(0.15)
            focus = await self.surface.focused()
            if focus.get("secret"):
                return {"status": "refused", "line": "[refused] the focus is in a password field: that is for the "
                                                     "person to type; nothing was typed"}
            if focus.get("elsewhere"):
                return {"status": "refused", "line": "[refused] the keyboard focus is not in this window; nothing "
                                                     "was typed"}
            await self.surface.type(a["text"])
        elif do == "key":
            focus = await self.surface.focused()
            if focus.get("elsewhere"):
                return {"status": "refused", "line": "[refused] the keyboard focus is not in this window"}
            await self.surface.key(a["key"])
        await asyncio.sleep(SETTLE_S)
        try:
            after = await self.surface.capture(self.profile.long_edge)
        except capture.Refused as e:
            return {"status": "failed", "line": f"[ok] {do}{where}{on} was sent; then {e}"}
        seen = change(before, after, *([real] if real else []), *([before.to_real(*a["to"])] if do == "drag" else []))
        self.frame = after
        if not seen["any"]:
            said = "no visible change in the picture"
        elif real is None or seen["near"]:
            said = "the picture changed where it pointed" if real else "the picture changed"
        else:
            x0, y0 = before.to_image(seen["box"][0], seen["box"][1])
            x1, y1 = before.to_image(seen["box"][0] + seen["box"][2], seen["box"][1] + seen["box"][3])
            said = (f"the picture changed, but not near the point: only from {round(x0)},{round(y0)} to "
                    f"{round(x1)},{round(y1)} of the picture")
        small = ""
        if hit and hit.get("known") and min(hit.get("size") or (99, 99)) < self.profile.floor:
            small = "; a small target, check the next picture"
        text = f" {a['text'][:60]!r}" if do == "type" else (f" {a['key']}" if do == "key" else "")
        return {"status": "ok", "line": f"[ok] {do}{text}{where}{on} was sent -> {said}{small}",
                "changed": seen["any"]}

"""A tab: one page somebody can drive, whatever browser it is in.

Reading (read.py) and operating go through this shape only, so the same code
serves the signed-out headless visitor (PlaywrightTab, here) and the person's
own pane. Every method is a coroutine; a tab over a browser whose calls block
runs them on a thread of its own.

- `goto(url)` opens an address and answers once the document is there:
  an `Answer` (final address, HTTP status, content type, the body on demand),
  or raises TabError with the browser's own reason (net::ERR_...).
- `url` is where the page is now.
- `evaluate(script, arg)` runs a JavaScript function expression with `arg`
  in the page and gives back its JSON value. Scripts stay synchronous: a pane
  does not wait for promises.
- `cdp(method, params, session)` sends one Chrome DevTools Protocol command to the page, or with `session` to
  one of its frames from another site.
- `frames()` gives the sessions of the frames from another site in the page now (each runs in a process of
  its own, so the page's session does not reach into it). Fetch's interception, too, reaches only the process
  it was turned on in: while it is on in the page, it is turned on in each of these frames, and the answer to a
  request a frame paused goes back to that frame's session.
- `events(name, handler)` calls `handler(dict)` on "navigation" (the main
  frame moved: {"url"}), "load" ({"url"}) or any CDP event name.
- `scroll(screens)` moves the page down that many screens, as a wheel does.
- `close()` closes the tab.

No tab reads cookies or storage out, and none opens a debugging port.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

# How long an address may take to start answering, and its document to be parsed after.
OPEN_S = 30.0
PARSED_S = 20.0
# Frames from another site attached as they come: each in a session of its own, its scripts not held.
AUTO_ATTACH = {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True, "filter": [{"type": "iframe"}]}


class TabError(Exception):
    """A navigation the browser itself failed; the message is the browser's reason."""


@dataclass
class Answer:
    url: str
    status: int = 0
    kind: str = ""                     # the content type, lower case
    length: int = 0                    # the declared content length, 0 when none
    body: Optional[Callable[[], Awaitable[bytes]]] = None


class Tab:
    """The shape every tab has; see the module's words."""

    def __init__(self) -> None:
        # Fetch's settings while it is on in the page; the frame session each paused request came from.
        self._fetch: Optional[dict] = None
        self._paused: dict[str, str] = {}

    @property
    def url(self) -> str:
        raise NotImplementedError

    async def goto(self, url: str, *, referer: str = "", timeout_s: float = OPEN_S) -> Answer:
        raise NotImplementedError

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        raise NotImplementedError

    async def cdp(self, method: str, params: Optional[dict] = None, session: str = "") -> dict:
        params = params or {}
        if not session and method.startswith("Fetch.") and "requestId" in params:
            session = self._paused.pop(params["requestId"], "")
        answer = await self._send(method, params, session)
        if not session and method in ("Fetch.enable", "Fetch.disable"):
            self._fetch = params if method == "Fetch.enable" else None
            for frame in self._attached():
                # A frame gone since it was listed has nothing left to watch.
                with contextlib.suppress(Exception):
                    await self._send(method, params, frame)
        return answer

    async def _send(self, method: str, params: dict, session: str) -> dict:
        raise NotImplementedError

    def _attached(self) -> list[str]:
        """The frame sessions open now, none attached anew."""
        return []

    def _heard(self, session: str, params: dict) -> None:
        """A Fetch event of a frame's session: its answer goes back there."""
        if session and "requestId" in params:
            self._paused[params["requestId"]] = session

    async def frames(self) -> list[str]:
        return []

    async def events(self, name: str, handler: Callable[[dict], None]) -> None:
        raise NotImplementedError

    async def scroll(self, screens: float = 1.0) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


class PlaywrightTab(Tab):
    """A tab over one Playwright page: the visitor's headless, signed-out browser (roam.Visitor),
    whose own guards check every request the page makes."""

    def __init__(self, page: Any):
        super().__init__()
        self.page = page
        self._session: Any = None
        # Frames from another site: session name -> (Playwright frame, its CDP session); the page's Fetch handlers,
        # which a frame's paused requests reach too.
        self._frames: dict[str, tuple[Any, Any]] = {}
        self._made = 0
        self._fetch_heard: list[tuple[str, Callable[[dict], None]]] = []

    @property
    def url(self) -> str:
        return self.page.url

    async def goto(self, url: str, *, referer: str = "", timeout_s: float = OPEN_S) -> Answer:
        try:
            response = await self.page.goto(url, referer=referer or None, wait_until="commit",
                                            timeout=int(timeout_s * 1000))
        except Exception as exc:
            raise TabError(" ".join(str(exc).split())[:300]) from None
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=int(PARSED_S * 1000))
        except Exception:
            # A slow document is read as far as it got.
            pass
        if response is None:
            return Answer(self.page.url)
        headers = response.headers
        try:
            length = int(headers.get("content-length") or 0)
        except ValueError:
            length = 0
        return Answer(self.page.url, response.status, headers.get("content-type", "").lower(), length, response.body)

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        return await self.page.evaluate(script, arg)

    async def _cdp_session(self) -> Any:
        if self._session is None:
            self._session = await self.page.context.new_cdp_session(self.page)
        return self._session

    async def _send(self, method: str, params: dict, session: str) -> dict:
        return await (self._frames[session][1] if session else await self._cdp_session()).send(method, params)

    def _attached(self) -> list[str]:
        return list(self._frames)

    async def frames(self) -> list[str]:
        for name in [name for name, (frame, _) in self._frames.items() if frame.is_detached()]:
            del self._frames[name]
        known = [frame for frame, _ in self._frames.values()]
        for frame in self.page.frames[1:]:
            if frame.is_detached() or frame in known:
                continue
            try:
                session = await self.page.context.new_cdp_session(frame)
            except Exception:
                # A frame in the page's own process: it is read with the page.
                continue
            self._made += 1
            name = f"frame-{self._made}"
            for event, handler in self._fetch_heard:
                session.on(event, lambda params, name=name, handler=handler: (self._heard(name, params), handler(params)))
            if self._fetch is not None:
                await session.send("Fetch.enable", self._fetch)
            self._frames[name] = (frame, session)
        return list(self._frames)

    async def events(self, name: str, handler: Callable[[dict], None]) -> None:
        if name == "navigation":
            self.page.on("framenavigated",
                         lambda frame: handler({"url": frame.url}) if frame == self.page.main_frame else None)
        elif name == "load":
            self.page.on("load", lambda page: handler({"url": page.url}))
        else:
            (await self._cdp_session()).on(name, handler)
            if name.startswith("Fetch."):
                self._fetch_heard.append((name, handler))

    async def scroll(self, screens: float = 1.0) -> None:
        width, height = await self.page.evaluate("() => [innerWidth, innerHeight]")
        # A wheel over the middle of the page: virtual lists listen for wheels, not for scrollTo.
        await self.page.mouse.move(width / 2, height / 2)
        await self.page.mouse.wheel(0, height * screens)

    async def close(self) -> None:
        await self.page.close()


class _BlockingTab(Tab):
    """A tab whose browser answers on another thread: each call waits there (asyncio.to_thread), never on
    the browser's own thread. `_call(method, params, session)` is the blocking DevTools call."""

    def _call(self, method: str, params: dict, session: str) -> dict:
        raise NotImplementedError

    async def _send(self, method: str, params: dict, session: str) -> dict:
        return await asyncio.to_thread(self._call, method, params, session)

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        answer = await self.cdp("Runtime.evaluate", {"expression": f"({script})({json.dumps(arg)})",
                                                     "returnByValue": True, "awaitPromise": False})
        if answer.get("exceptionDetails"):
            detail = answer["exceptionDetails"]
            raise RuntimeError((detail.get("exception") or {}).get("description") or detail.get("text") or "")
        return (answer.get("result") or {}).get("value")

    async def scroll(self, screens: float = 1.0) -> None:
        view = (await self.cdp("Page.getLayoutMetrics", {})).get("cssVisualViewport") or {}
        wide, high = float(view.get("clientWidth") or 1280), float(view.get("clientHeight") or 800)
        await self.cdp("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": wide / 2, "y": high / 2,
                                                    "deltaX": 0, "deltaY": high * screens})


class PaneTab(_BlockingTab):
    """The pane the person browses in (browser.Pane): their own WebView2 profile, signed in where they
    signed in. Calls go through WebView2's in-process DevTools protocol (no port), on the window's thread,
    and a suspended page is resumed first. Read in place (read.read with here=True) or operated (pageops);
    it never moves the person's page except when they asked for something to be done in it.

    It is the pane's tab in front: every call, the address and the frames go to the view that tab shows
    (`pane._core`). A run acts on that tab only; the pane pauses it when another tab comes to the front, and a
    run that goes on starts its listeners there. With `tab`, a tab's number, it is that tab wherever it is: a page
    the character opened out of sight (surface.py's look reads it so).

    Frames from another site are attached in that view, as Target.setAutoAttach with flatten gives them, and
    called through WebView2's session calls; when another tab comes to the front, `frames()` starts over there."""

    def __init__(self, pane: Any, tab: int = 0):
        super().__init__()
        self.pane, self.tab = pane, tab
        # The view whose frames are attached, and their sessions: id -> made ready (Fetch, the frames under it).
        self._view: Any = None
        self._frames: dict[str, bool] = {}

    @property
    def url(self) -> str:
        return self.pane.address(self.tab)

    def _call(self, method: str, params: dict, session: str) -> dict:
        return self.pane.cdp(method, params, session=session, tab=self.tab)

    def _attached(self) -> list[str]:
        return list(self._frames)

    async def frames(self) -> list[str]:
        view = self.pane.core_of(self.tab)
        if view is not self._view:
            self._view, self._frames = view, {}
            await asyncio.to_thread(self.pane.listen, "Target.attachedToTarget", self._on_attached, True, self.tab)
            await asyncio.to_thread(self.pane.listen, "Target.detachedFromTarget", self._on_detached, True, self.tab)
            # Off and on again: the frames there already are attached anew, for this tab.
            await self.cdp("Target.setAutoAttach", {"autoAttach": False, "waitForDebuggerOnStart": False})
            await self.cdp("Target.setAutoAttach", AUTO_ATTACH)
        for session in [s for s, ready in list(self._frames.items()) if not ready]:
            await asyncio.to_thread(self._ready, session)
        return list(self._frames)

    def _on_attached(self, params: dict, session: str) -> None:
        # On the window's thread, which must not wait for a DevTools answer: the frame is made ready on another.
        if (params.get("targetInfo") or {}).get("type") == "iframe":
            self._frames[params["sessionId"]] = False
            threading.Thread(target=self._ready, args=(params["sessionId"],), daemon=True).start()

    def _on_detached(self, params: dict, session: str) -> None:
        self._frames.pop(params.get("sessionId", ""), None)

    def _ready(self, session: str) -> None:
        """A frame's session made ready: frames from another site under it attached too, and Fetch on in it while
        it is on in the page. A frame gone meanwhile is dropped."""
        try:
            self._call("Target.setAutoAttach", AUTO_ATTACH, session)
            if self._fetch is not None:
                self._call("Fetch.enable", self._fetch, session)
        except RuntimeError:
            self._frames.pop(session, None)
            return
        if session in self._frames:
            self._frames[session] = True

    async def goto(self, url: str, *, referer: str = "", timeout_s: float = OPEN_S) -> Answer:
        answer = await self.cdp("Page.navigate", {"url": url, **({"referrer": referer} if referer else {})})
        if answer.get("errorText"):
            raise TabError(str(answer["errorText"]))
        began = time.monotonic()
        while time.monotonic() - began < timeout_s:
            await asyncio.sleep(0.3)
            with contextlib.suppress(RuntimeError):
                if await self.evaluate("() => document.readyState") in ("interactive", "complete"):
                    break
        return Answer(self.url)

    async def events(self, name: str, handler: Callable[[dict], None]) -> None:
        if name in ("navigation", "load"):
            event = "Page.frameNavigated" if name == "navigation" else "Page.loadEventFired"
            await self.cdp("Page.enable", {})
            await asyncio.to_thread(self.pane.listen, event, lambda params: handler(
                {"url": (params.get("frame") or {}).get("url", self.url)}) if name == "load" or not (
                params.get("frame") or {}).get("parentId") else None, False, self.tab)
        elif name.startswith("Fetch."):
            # Fetch events come from the page and from its frames: each answer goes back where it came from.
            await asyncio.to_thread(self.pane.listen, name, lambda params, session: (
                self._heard(session, params), handler(params)), True, self.tab)
        else:
            await asyncio.to_thread(self.pane.listen, name, handler, False, self.tab)

    async def close(self) -> None:
        # The person's pane stays: it closes when they close it.
        return None


class HiddenTab(_BlockingTab):
    """A hidden WebView2 on the pane's profile (browser.Reader), for reading with the person's session out of
    sight. Read only by construction: it goes to addresses of the one site it was opened for, its page may
    send only GET requests to that site, and of DevTools it has only what reading needs (no clicks, no keys;
    a wheel to scroll). It never reads cookies or storage."""

    READS = frozenset({"Runtime.evaluate", "Page.getLayoutMetrics", "Page.getFrameTree", "Accessibility.getFullAXTree",
                       "DOM.getDocument", "DOM.querySelectorAll", "DOM.describeNode", "Page.enable"})

    def __init__(self, view: Any):
        super().__init__()
        self.view = view

    @property
    def url(self) -> str:
        return self.view.address()

    def _call(self, method: str, params: dict, session: str) -> dict:
        if method not in self.READS and not (method == "Input.dispatchMouseEvent"
                                             and params.get("type") == "mouseWheel"):
            raise TabError(f"{method} is not for reading")
        return self.view.cdp(method, params)

    async def goto(self, url: str, *, referer: str = "", timeout_s: float = OPEN_S) -> Answer:
        final = await asyncio.to_thread(self.view.goto, url, timeout_s)
        return Answer(final)

    async def events(self, name: str, handler: Callable[[dict], None]) -> None:
        raise TabError("a hidden tab has no events")

    async def close(self) -> None:
        await asyncio.to_thread(self.view.close)

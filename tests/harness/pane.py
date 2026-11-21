"""The app's browser with no window: a stand-in of browser.Pane whose tabs are pages of one headless Chromium
context, called the way PaneTab calls the pane (cdp, listen, address, core_of by tab number) and the way the
browser op route does (tabs, place_page, front), and the column beside the page as Operating tells it (operate, op_card,
op_answered, op_end, op_kept, op_said). Playwright's async API runs on a loop of its own thread; each call is handed
to that loop and waited for, and calls from several threads are in flight together, as the browser takes them: the
operating code lets a paused request go while a call that waits on it (a navigation, a press that submits a form) is
still out. Events reach their handlers on that loop, which must not wait there. Nothing shows on the screen."""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading

# How long a call other than a DevTools one may take.
CALL_S = 60


class Pane:
    """`Pane.start()` gives a pane, or None when no headless browser can start; `stop()` ends it. `front_of`
    is the tab number in front, `placed` each page the character opened, as (address, show), and `column` what
    the column beside the page was told, in order: ("operate", task), ("card", kind), ("end", status)."""

    def __init__(self, browser=None) -> None:
        self.browser = browser
        self.tabs_: list[dict] = []
        self.front_of = 0
        self.placed: list[tuple[str, bool]] = []
        self.op = None
        self.column: list[tuple[str, str]] = []
        self.private = False
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._failed = ""
        self._thread = threading.Thread(target=self._run, name="stand-in-pane", daemon=True)

    @classmethod
    def start(cls, browser=None) -> Pane | None:
        """`browser` is the installed browser to run headless (installed_browser.find_browser); None for
        Playwright's own."""
        pane = cls(browser)
        pane._thread.start()
        pane._ready.wait(CALL_S)
        return None if pane._failed else pane

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._open())
        except Exception as e:
            self._failed = str(e)
            self._ready.set()
            return
        self._ready.set()
        self.loop.run_until_complete(self._ended.wait())
        self.loop.run_until_complete(self._shut())

    async def _open(self) -> None:
        from playwright.async_api import async_playwright
        self._ended = asyncio.Event()
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(executable_path=str(self.browser) if self.browser else None,
                                                       headless=True)
        self.context = await self._browser.new_context(viewport={"width": 1280, "height": 800})

    async def _shut(self) -> None:
        await self._browser.close()
        await self._pw.stop()

    def _call(self, make, timeout: float = CALL_S):
        """`make()`'s coroutine run on the pane's loop, waited for here."""
        future = asyncio.run_coroutine_threadsafe(make(), self.loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise RuntimeError("the browser did not answer in time")
        except Exception as e:
            raise RuntimeError(str(e))

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self._ended.set)
        self._thread.join(30)

    # ---- tabs

    def _tab(self, ident: int) -> dict | None:
        ident = ident or self.front_of
        return next((t for t in self.tabs_ if t["id"] == ident), None)

    def open_tab(self, url: str, front: bool = True, private: bool = False) -> int:
        """A tab the person opened themselves."""
        async def make() -> int:
            page = await self.context.new_page()
            await page.goto(url)
            ident = len(self.tabs_) + 1
            self.tabs_.append({"id": ident, "page": page, "session": await self.context.new_cdp_session(page),
                               "private": private, "aside": not front})
            if front:
                self.front_of = ident
            return ident
        return self._call(make)

    def place_page(self, address: str, show: bool) -> dict:
        self.placed.append((address, show))
        ident = self.open_tab(address, front=show)
        return {"id": ident, "url": address, "title": ""}

    def tabs(self) -> list[dict]:
        async def listed() -> list[dict]:
            return [{"id": t["id"], "title": await t["page"].title(), "url": t["page"].url, "private": t["private"],
                     "front": t["id"] == self.front_of, "aside": t["aside"]} for t in self.tabs_]
        return self._call(listed)

    def front(self, ident: int) -> bool:
        self.front_of = ident
        return True

    def core_of(self, ident: int = 0):
        tab = self._tab(ident)
        return tab["page"] if tab else None

    def address(self, tab: int = 0) -> str:
        found = self._tab(tab)
        return found["page"].url if found else ""

    def cdp(self, method: str, params: dict | None = None, timeout: float = 20, session: str = "",
            tab: int = 0) -> dict:
        found = self._tab(tab)
        if found is None:
            raise RuntimeError("the page is closed")
        return self._call(lambda: found["session"].send(method, params or {}), max(timeout, 1))

    def listen(self, name: str, handler, session: bool = False, tab: int = 0) -> None:
        found = self._tab(tab)
        if found is not None:
            async def on() -> None:
                found["session"].on(name, (lambda p: handler(p, "")) if session else handler)
            self._call(on)

    # ---- the column beside the page

    def operate(self, task: str, go_on: bool = False) -> None:
        self.op = {"task": task, "held": "", "card": None}
        self.column.append(("operate", task))

    def op_said(self, text: str) -> None:
        pass

    def op_card(self, card: dict, text: str) -> None:
        self.op["card"] = card
        self.column.append(("card", card["kind"]))

    def op_answered(self, state: str, text: str) -> None:
        if self.op is not None:
            self.op["card"] = None

    def op_end(self, status: str, note: str, unfinished: str = "") -> None:
        self.op = None
        self.column.append(("end", status))

    def op_kept(self, yes: bool) -> None:
        self.column.append(("kept", "yes" if yes else "no"))

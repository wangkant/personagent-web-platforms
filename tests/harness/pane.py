"""The app's browser with no window: a stand-in of browser.Pane whose tabs are pages of one headless Chromium
context, called the way PaneTab calls the pane (cdp, listen, address, core_of by tab number) and the way the
browser op route does (tabs, place_page, front). One thread owns Playwright's sync API; every call is handed to
it and waited for, as the real pane hands its calls to the window's thread. Nothing shows on the screen."""
from __future__ import annotations

import queue
import threading


class Pane:
    """`Pane.start()` gives a pane, or None when no headless browser can start; `stop()` ends it. `front_of`
    is the tab number in front, and `placed` each page the character opened, as (address, show)."""

    def __init__(self, browser=None) -> None:
        self.browser = browser
        self.jobs: queue.Queue = queue.Queue()
        self.tabs_: list[dict] = []
        self.front_of = 0
        self.placed: list[tuple[str, bool]] = []
        self.op = None
        self.private = False
        self._ready = threading.Event()
        self._failed = ""
        self._thread = threading.Thread(target=self._run, name="stand-in-pane", daemon=True)

    @classmethod
    def start(cls, browser=None) -> Pane | None:
        """`browser` is the installed browser to run headless (installed_browser.find_browser); None for
        Playwright's own."""
        pane = cls(browser)
        pane._thread.start()
        pane._ready.wait(60)
        return None if pane._failed else pane

    def _run(self) -> None:
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=str(self.browser) if self.browser else None,
                                             headless=True)
                self.context = browser.new_context(viewport={"width": 1280, "height": 800})
                self._ready.set()
                while True:
                    try:
                        job = self.jobs.get(timeout=0.05)
                    except queue.Empty:
                        # Events reach their handlers while Playwright is called.
                        if self.tabs_:
                            self.tabs_[0]["page"].wait_for_timeout(1)
                        continue
                    if job is None:
                        browser.close()
                        return
                    fn, box, done = job
                    try:
                        box["result"] = fn()
                    except Exception as e:
                        box["error"] = e
                    done.set()
        except Exception as e:
            self._failed = str(e)
            self._ready.set()

    def _call(self, fn):
        box: dict = {}
        done = threading.Event()
        self.jobs.put((fn, box, done))
        done.wait(60)
        if "error" in box:
            raise RuntimeError(str(box["error"]))
        return box.get("result")

    def stop(self) -> None:
        self.jobs.put(None)
        self._thread.join(30)

    # ---- tabs

    def _tab(self, ident: int) -> dict | None:
        ident = ident or self.front_of
        return next((t for t in self.tabs_ if t["id"] == ident), None)

    def open_tab(self, url: str, front: bool = True, private: bool = False) -> int:
        """A tab the person opened themselves."""
        def make() -> int:
            page = self.context.new_page()
            page.goto(url)
            ident = len(self.tabs_) + 1
            self.tabs_.append({"id": ident, "page": page, "session": self.context.new_cdp_session(page),
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
        return self._call(lambda: [{"id": t["id"], "title": t["page"].title(), "url": t["page"].url,
                                    "private": t["private"], "front": t["id"] == self.front_of,
                                    "aside": t["aside"]} for t in self.tabs_])

    def front(self, ident: int) -> bool:
        self.front_of = ident
        return True

    def core_of(self, ident: int = 0):
        tab = self._tab(ident)
        return tab["page"] if tab else None

    def address(self, tab: int = 0) -> str:
        found = self._tab(tab)
        return self._call(lambda: found["page"].url) if found else ""

    def cdp(self, method: str, params: dict | None = None, timeout: float = 20, session: str = "",
            tab: int = 0) -> dict:
        found = self._tab(tab)
        if found is None:
            raise RuntimeError("the page is closed")
        return self._call(lambda: found["session"].send(method, params or {}))

    def listen(self, name: str, handler, session: bool = False, tab: int = 0) -> None:
        found = self._tab(tab)
        if found is not None:
            self._call(lambda: found["session"].on(name, (lambda p: handler(p, "")) if session else handler))

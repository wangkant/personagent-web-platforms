"""The app window with no display: a stand-in pywebview whose window records what was asked of it, uvicorn's
part of `mibo start`, and an AppWindow replaced by one whose run a test decides. Nothing is drawn."""
from __future__ import annotations

import sys
import threading
import time
import types

from mibo import welcome, window


class Event:
    def __init__(self) -> None:
        self.handlers: list = []
        self._flag = threading.Event()

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def set(self) -> None:
        self._flag.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._flag.wait(timeout)

    def fire(self) -> None:
        for handler in self.handlers:
            handler()


class Window:
    def __init__(self, gone: threading.Event, url: str | None) -> None:
        self.events = types.SimpleNamespace(**{name: Event() for name in (
            "closing", "shown", "loaded", "minimized", "restored", "maximized", "before_show")})
        self.url = url
        self.calls: list[str] = []
        self._gone = gone

    def restore(self) -> None:
        self.calls.append("restore")

    def minimize(self) -> None:
        self.calls.append("minimize")

    def maximize(self) -> None:
        self.calls.append("maximize")

    def show(self) -> None:
        self.calls.append("show")

    def load_url(self, url: str) -> None:
        self.url = url

    def evaluate_js(self, script: str) -> None:
        """What the app tells its page (window.py's tell); no page runs here to hear it."""

    def destroy(self) -> None:
        self.calls.append("destroy")
        self._gone.set()

    def press_close(self) -> bool:
        """The person clicks the X; True when the window went away."""
        if any(handler() is False for handler in self.events.closing.handlers):
            return False
        self._gone.set()
        return True


def stand_in_webview(monkeypatch) -> types.ModuleType:
    """A stand-in pywebview whose `start` blocks until its window is gone. `module.person` is what the person
    does once the window is up."""
    module = types.ModuleType("webview")
    module.renderer = "edgechromium"
    module.shows = True
    module.person = lambda shown: shown.press_close()
    gone = threading.Event()

    def create_window(title, url, **options):
        module.created = (title, options)
        module.window = Window(gone, url)
        return module.window

    def start(func, **options):
        module.started = options
        # pywebview calls these on the window's own thread, before it shows.
        module.window.events.before_show.fire()
        if module.shows:
            module.window.events.shown.set()
            module.window.events.loaded.set()
        ready = threading.Thread(target=func)
        ready.start()
        if module.shows:
            ready.join()
            module.person(module.window)
        assert gone.wait(5), "the window never closed"
        ready.join()

    module.create_window = create_window
    module.start = start
    module.settings = {}
    monkeypatch.setitem(sys.modules, "webview", module)
    # WebView2's own events, which the stand-in has none of.
    monkeypatch.setattr(window, "_own_pages", lambda shown, origin, open_link: None)
    return module


class Server:
    """uvicorn's part: serve until told to exit."""

    def __init__(self) -> None:
        self.should_exit = False
        self.ran = threading.Event()

    def run(self, sockets=None) -> None:
        self.ran.set()
        while not self.should_exit:
            time.sleep(0.01)


def fake_window(monkeypatch, run) -> list:
    """AppWindow replaced by one whose `run` is `run(instance)`; the instances made, in order."""
    made = []

    class Fake:
        def __init__(self, url, *, storage, **options):
            self.url, self.storage, self.options = url, storage, options
            self.error = ""
            self.closed = 0
            made.append(self)

        def show(self) -> bool:
            return True

        def close(self) -> None:
            self.closed += 1

        def run(self) -> bool:
            return run(self)

    monkeypatch.setattr(welcome.appwindow, "AppWindow", Fake)
    return made

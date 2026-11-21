"""The app window, with pywebview stood in for (harness/window.py): nothing is drawn, no pointer moves."""
from __future__ import annotations

import importlib.util
import signal
import sys
import threading
from pathlib import Path

import pytest

from harness.window import stand_in_webview
from mibo import window


@pytest.fixture
def webview(monkeypatch):
    """A stand-in pywebview: `module.person` is what the person does once the window is up."""
    return stand_in_webview(monkeypatch)


def _frame_watch_ends() -> bool:
    """Wait for the window's watch on whether its page drew the bar to finish; False when none was running."""
    watches = [thread for thread in threading.enumerate() if thread.name == "mibo-frame"]
    for thread in watches:
        thread.join(5)
    return bool(watches)


def test_the_page_opens_in_a_private_window_with_the_app_icon(webview, tmp_path) -> None:
    seen = {}

    def person(shown) -> None:
        seen["front"] = app.show()
        seen["url"] = shown.url
        shown.press_close()

    webview.person = person
    app = window.AppWindow("http://127.0.0.1:1/#token=t", storage=tmp_path / "webview")
    assert app.run() is True and app.error == ""
    title, options = webview.created
    # Windows gets there by way of an empty first page; see AppWindow.run.
    assert (title, seen["url"]) == ("Mibo", app.page_url)
    # The address, and after it the key only this window's page is given.
    assert app.page_url == f"http://127.0.0.1:1/#token=t&frame={app.frame_key}" and len(app.frame_key) >= 16
    assert app.is_own_page(app.frame_key) and not app.is_own_page("") and not app.is_own_page("guess")
    assert options["text_select"] is True, "a link in the page can be copied"
    assert webview.started["private_mode"] is True
    assert webview.started["storage_path"] == str(tmp_path / "webview")
    assert Path(webview.started["icon"]) == window.icon() and window.icon().is_file()
    assert seen["front"] is True and webview.window.calls == ["show"]
    assert app.show() is False, "nothing to bring forward once it closed"


def test_a_second_start_restores_only_a_minimized_window(webview, tmp_path) -> None:
    def person(shown) -> None:
        shown.events.maximized.fire()
        app.show()
        shown.events.minimized.fire()
        app.show()
        shown.events.restored.fire()
        app.show()
        shown.press_close()

    webview.person = person
    app = window.AppWindow("http://127.0.0.1:1/", storage=tmp_path)
    assert app.run() is True
    assert webview.window.calls == ["show", "restore", "show", "show"], "a maximized window stays maximized"


def test_asked_to_close_before_it_exists_it_closes_once_it_does(webview, tmp_path) -> None:
    """The server died at once, or Ctrl+C came during the toolkit's own start."""
    webview.person = lambda shown: None
    app = window.AppWindow("http://127.0.0.1:1/", storage=tmp_path)
    app.close()
    assert app.run() is False and app.error == ""
    assert webview.window.calls == ["destroy"]


def test_both_icons_ship_with_the_package() -> None:
    for suffix in (".ico", ".png"):
        assert window.ICON.with_suffix(suffix).is_file(), suffix


def test_quit_from_the_page_closes_the_window(webview, tmp_path) -> None:
    webview.person = lambda shown: app.close()
    app = window.AppWindow("http://127.0.0.1:1/", storage=tmp_path)
    assert app.run() is True
    assert webview.window.calls == ["destroy"]


def test_without_webview2_windows_gets_no_window(webview, tmp_path, monkeypatch) -> None:
    done = _frameless(monkeypatch, regions=True)
    webview.renderer = "mshtml"
    app = window.AppWindow("http://127.0.0.1:1/", storage=tmp_path)
    assert app.run() is False
    # The browser that takes over is told to draw no window bar.
    assert done["stripped"] == 1 and app.frameless is False
    assert "WebView2" in app.error and webview.window.calls == ["destroy"]
    assert webview.started["gui"] == "edgechromium"


def test_a_window_that_never_appears_counts_as_none(webview, tmp_path, monkeypatch) -> None:
    done = _frameless(monkeypatch, regions=True)
    monkeypatch.setattr(window, "SHOWN_WITHIN", 0.05)
    webview.shows = False
    app = window.AppWindow("http://127.0.0.1:1/", storage=tmp_path)
    assert app.run() is False and app.error
    assert done["stripped"] == 1 and app.frameless is False


def test_no_pywebview_means_no_window(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "webview", None)
    app = window.AppWindow("http://127.0.0.1:1/", storage=tmp_path)
    assert app.run() is False and "webview" in app.error
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert window.available() is False


def test_linux_with_qt_and_no_gtk_asks_for_qt_by_name(monkeypatch) -> None:
    """pywebview tries GTK first and prints a traceback when it is absent."""
    installed = {"qtpy"}
    monkeypatch.setattr(window.importlib.util, "find_spec", lambda name: object() if name in installed else None)
    monkeypatch.setattr(sys, "platform", "linux")
    assert window._toolkit() == "qt"
    installed.add("gi")
    assert window._toolkit() is None
    installed.clear()
    assert window._toolkit() is None
    monkeypatch.setattr(sys, "platform", "darwin")
    installed.add("qtpy")
    assert window._toolkit() is None
    monkeypatch.setattr(sys, "platform", "win32")
    assert window._toolkit() == "edgechromium"


def test_a_toolkit_that_fails_to_start_is_reported_not_raised(webview, tmp_path) -> None:
    def start(func, **options):
        raise RuntimeError("no display")

    webview.start = start
    app = window.AppWindow("http://127.0.0.1:1/", storage=tmp_path)
    assert app.run() is False and "no display" in app.error


def test_ctrl_c_reaches_the_window_while_the_main_thread_is_elsewhere() -> None:
    stopped = threading.Event()
    before = signal.getsignal(signal.SIGINT)
    with window._interrupt(stopped.set):
        signal.raise_signal(signal.SIGINT)
        assert stopped.wait(5)
    assert signal.getsignal(signal.SIGINT) is before


def test_closing_goes_to_the_background_icon_when_there_is_one(webview, tmp_path) -> None:
    outcomes = []

    def person(shown) -> None:
        app._tray = object()  # the notification-area icon Windows gives
        hidden = []
        app.hide = lambda: hidden.append(True) or True
        outcomes.append(shown.press_close())
        outcomes.append(len(hidden))
        keep["on"] = False
        outcomes.append(shown.press_close())

    keep = {"on": True}
    webview.person = person
    app = window.AppWindow("http://127.0.0.1:1/", storage=tmp_path, background=lambda: keep["on"])
    assert app.run() is True
    assert outcomes == [False, 1, True], "hidden while the switch is on, closed once it is off"


# -- the title bar the page draws on Windows --------------------------------------

def _frameless(monkeypatch, *, regions: bool):
    """Windows, with the native steps stood in for; what the two on the window were asked to do."""
    done = {"stripped": 0, "undone": 0}

    def strip(shown):
        done["stripped"] += 1
        return object(), lambda: done.__setitem__("undone", done["undone"] + 1)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(window, "_own_process", lambda: None)
    monkeypatch.setattr(window, "_strip_title_bar", strip)
    monkeypatch.setattr(window, "_text_menus", lambda shown: regions)
    return done


def test_on_windows_the_title_bar_goes_before_the_window_shows(webview, tmp_path, monkeypatch) -> None:
    done = _frameless(monkeypatch, regions=True)
    seen = {}

    def person(shown) -> None:
        app.frame_seen()
        seen["frameless"] = app.frameless
        shown.press_close()

    webview.person = person
    app = window.AppWindow("http://127.0.0.1:1/", storage=tmp_path)
    assert app.run() is True
    assert seen == {"frameless": True} and done == {"stripped": 1, "undone": 0}


def test_a_webview_whose_page_cannot_move_the_window_keeps_the_title_bar(webview, tmp_path, monkeypatch) -> None:
    done = _frameless(monkeypatch, regions=False)
    seen = {}

    def person(shown) -> None:
        seen["frameless"] = app.frameless
        shown.press_close()

    webview.person = person
    app = window.AppWindow("http://127.0.0.1:1/", storage=tmp_path)
    assert app.run() is True
    assert seen == {"frameless": False} and done == {"stripped": 1, "undone": 1}


@pytest.mark.parametrize("asks, undone", [(True, 0), (False, 1)])
def test_a_page_that_never_draws_the_bar_gives_the_window_its_own_back(webview, tmp_path, monkeypatch,
                                                                         asks, undone) -> None:
    done = _frameless(monkeypatch, regions=True)
    monkeypatch.setattr(window, "PAGE_WITHIN", 0.1)
    seen = {}

    def person(shown) -> None:
        if asks:
            app.frame_seen()
        seen["watched"] = _frame_watch_ends()
        seen["frameless"] = app.frameless
        shown.press_close()

    webview.person = person
    app = window.AppWindow("http://127.0.0.1:1/", storage=tmp_path)
    assert app.run() is True
    assert seen == {"watched": True, "frameless": asks} and done["undone"] == undone


def test_elsewhere_the_window_keeps_its_title_bar(webview, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(window, "_strip_title_bar", lambda shown: pytest.fail("only Windows drops the title bar"))
    app = window.AppWindow("http://127.0.0.1:1/", storage=tmp_path)
    assert app.run() is True and app.frameless is False
    assert app.control("minimize") is False, "nothing to do once it closed"


def test_the_pages_buttons_do_what_a_title_bars_do(webview, tmp_path) -> None:
    seen = {}

    def person(shown) -> None:
        seen["before"] = [app.control(action) for action in ("minimize", "maximize", "restore")]
        seen["calls"] = list(shown.calls)
        seen["unknown"] = app.control("format-disk")
        shown.events.maximized.fire()
        seen["maximized"] = app.maximized
        shown.events.restored.fire()
        seen["restored"] = app.maximized
        # Close goes the way the system's button does: here nothing asks, so it closes.
        seen["close"] = app.control("close")

    webview.person = person
    app = window.AppWindow("http://127.0.0.1:1/", storage=tmp_path)
    assert app.control("close") is False, "no window yet"
    assert app.run() is True
    assert seen == {"before": [True, True, True], "calls": ["minimize", "maximize", "restore"],
                    "unknown": False, "maximized": True, "restored": False, "close": True}
    assert webview.window.calls[-1] == "destroy"


def test_a_window_closed_before_its_page_asked_is_left_alone(webview, tmp_path, monkeypatch) -> None:
    done = _frameless(monkeypatch, regions=True)
    monkeypatch.setattr(window, "PAGE_WITHIN", 0.2)
    app = window.AppWindow("http://127.0.0.1:1/", storage=tmp_path)
    assert app.run() is True
    assert _frame_watch_ends()
    assert done["undone"] == 0, "nothing is done to a window that is gone"

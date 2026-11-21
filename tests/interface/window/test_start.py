"""`mibo start`: the window instead of a browser tab, one running copy per home, the browser when there is no
window, and what is said when there is no terminal. The window and uvicorn are stood in for (harness/window.py)."""
from __future__ import annotations

import json
import re
import sys
import threading
import types
from pathlib import Path

import httpx
import pytest

from harness.app import TOKEN
from harness.window import Server, fake_window
from mibo import cli, welcome, window

HEADERS = {welcome.TOKEN_HEADER: TOKEN}


@pytest.fixture
def started(client, workspace, monkeypatch):
    """The page's app with nothing running, and a browser that only takes notes."""
    opened: list[str] = []
    monkeypatch.setattr(welcome.webbrowser, "open", opened.append)
    return types.SimpleNamespace(app=client.app, opened=opened, home=workspace.home)


def test_closing_the_window_stops_the_server_and_opens_no_browser(started, monkeypatch) -> None:
    server = Server()
    made = fake_window(monkeypatch, lambda fake: server.ran.wait(5))
    welcome._serve_in_window(started.app, server, None, "http://127.0.0.1:1/#token=t", started.home)
    assert server.should_exit and started.opened == []
    assert made[0].storage == started.home / "webview"
    assert started.app.state.show() is True, "a second start can bring this window forward"


def test_quit_in_the_page_closes_the_window_too(started, monkeypatch) -> None:
    server = Server()

    def run(fake) -> bool:
        started.app.state.shutdown()
        return True

    made = fake_window(monkeypatch, run)
    welcome._serve_in_window(started.app, server, None, "http://127.0.0.1:1/", started.home)
    assert server.should_exit and made[0].closed >= 1


def test_no_window_falls_back_to_the_browser_and_keeps_serving(started, monkeypatch, capsys) -> None:
    server = Server()
    quit_pressed = threading.Event()

    def run(fake) -> bool:
        fake.error = "the WebView2 runtime is missing"
        # The page's Quit button, pressed in the browser once it has opened.
        threading.Thread(target=lambda: quit_pressed.wait(5) and started.app.state.shutdown()).start()
        return False

    monkeypatch.setattr(welcome.webbrowser, "open", lambda url: started.opened.append(url) or quit_pressed.set())
    fake_window(monkeypatch, run)
    welcome._serve_in_window(started.app, server, None, "http://127.0.0.1:1/#token=t", started.home)
    assert started.opened == ["http://127.0.0.1:1/#token=t"]
    assert started.app.state.show is None
    assert "WebView2" in capsys.readouterr().out


def test_ctrl_c_before_or_after_the_window_still_stops_the_server(started, monkeypatch) -> None:
    for outcome in (KeyboardInterrupt, False):
        server = Server()

        def run(fake, outcome=outcome) -> bool:
            if outcome is KeyboardInterrupt:
                raise KeyboardInterrupt
            return outcome  # closed before it opened: no error, no fallback

        fake_window(monkeypatch, run)
        welcome._serve_in_window(started.app, server, None, "http://127.0.0.1:1/", started.home)
        assert server.should_exit and started.opened == []


def test_the_window_hides_instead_of_closing_only_when_the_person_chose_that(started, monkeypatch) -> None:
    made = fake_window(monkeypatch, lambda fake: True)
    welcome._serve_in_window(started.app, Server(), None, "http://127.0.0.1:1/", started.home, hidden=True)
    options = made[0].options
    assert options["background"]() is False and options["start_hidden"] is False
    welcome.save_app_settings(started.home, background=True)
    assert options["background"]() is True, "read at the moment of closing, not at start"
    assert set(options["words"]()) == {"open", "quit", "pet", "still", "settings", "bring", "snooze", "unsnooze",
                                       "characters", "hide"}
    made = fake_window(monkeypatch, lambda fake: True)
    welcome._serve_in_window(started.app, Server(), None, "http://127.0.0.1:1/", started.home, hidden=True)
    assert made[0].options["start_hidden"] is True


def test_show_needs_the_capability_and_reports_whether_a_window_came_forward(client) -> None:
    assert client.post("/api/show", json={}).status_code == 403
    assert client.post("/api/show", json={}, headers=HEADERS).json() == {"ok": True, "window": False}
    client.app.state.show = lambda: True
    assert client.post("/api/show", json={}, headers=HEADERS).json() == {"ok": True, "window": True}


def test_a_second_start_asks_the_first_for_its_window(monkeypatch) -> None:
    asked = {}

    def post(url, **options):
        asked.update(url=url, **options)
        return types.SimpleNamespace(json=lambda: {"ok": True, "window": True})

    monkeypatch.setattr(welcome.httpx, "post", post)
    assert welcome._raise_window("http://127.0.0.1:4321/#token=abc") is True
    assert asked["url"] == "http://127.0.0.1:4321/api/show"
    assert asked["headers"] == {welcome.TOKEN_HEADER: "abc"}
    assert asked["trust_env"] is False, "a system proxy must not get the local token"

    def refused(url, **options):
        raise httpx.ConnectError("gone")

    monkeypatch.setattr(welcome.httpx, "post", refused)
    assert welcome._raise_window("http://127.0.0.1:4321/#token=abc") is False


def _held_start(home: Path, url: str = ""):
    """The lock a running mibo holds, with the link it left."""
    info = home / "runtime" / "desktop" / "welcome.json"
    info.parent.mkdir(parents=True, exist_ok=True)
    if url:
        info.write_text(json.dumps({"url": url}), encoding="utf-8")
    lock = welcome.FileLock(info.with_suffix(".lock"), timeout=0)
    lock.acquire()
    return lock


@pytest.mark.parametrize("argv, has_window, opens", [
    ([], True, False), ([], False, True), (["--browser"], True, True), (["--no-browser"], False, False)])
def test_starting_twice_reuses_the_running_page(tmp_path, monkeypatch, capsys, argv, has_window, opens) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    url = "http://127.0.0.1:4321/#token=abc"
    lock = _held_start(tmp_path, url)
    opened: list[str] = []
    monkeypatch.setattr(welcome.webbrowser, "open", opened.append)
    monkeypatch.setattr(welcome, "_raise_window", lambda link: has_window)
    try:
        assert welcome.main(argv) == 0
    finally:
        lock.release()
    assert capsys.readouterr().out.strip() == url
    assert opened == ([url] if opens else [])


def test_started_without_a_terminal_it_logs_and_says_refusals_in_a_dialog(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    lock = _held_start(tmp_path)
    earlier = tmp_path / "runtime" / "desktop" / "app.log"
    earlier.write_text("what the running one logged\n", encoding="utf-8")
    said: list[str] = []
    monkeypatch.setattr(window, "alert", said.append)
    # pythonw leaves both streams unset.
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    try:
        assert welcome.main([]) == 1
        log = sys.stdout
    finally:
        lock.release()
        monkeypatch.undo()
    log.close()
    assert len(said) == 1 and said[0] in earlier.read_text(encoding="utf-8")
    assert earlier.read_text(encoding="utf-8").startswith("what the running one logged"), \
        "its log is not this start's to empty"


def test_an_install_that_does_not_import_says_so_without_a_terminal(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    said: list[str] = []
    monkeypatch.setattr(window, "alert", said.append)
    real = cli.importlib.import_module

    def missing(name, *args):
        if name == "mibo.welcome":
            raise ImportError("No module named 'fastapi'")
        return real(name, *args)

    monkeypatch.setattr(cli.importlib, "import_module", missing)
    with pytest.raises(ImportError):
        cli.main(["start"])  # a terminal shows the traceback itself
    monkeypatch.setattr(sys, "stderr", None)
    try:
        assert cli.main(["start"]) == 1
    finally:
        monkeypatch.undo()
    log = tmp_path / "runtime" / "desktop" / "app.log"
    assert len(said) == 1 and str(log) in said[0]
    assert "No module named 'fastapi'" in log.read_text(encoding="utf-8")


def test_a_crash_without_a_terminal_names_the_log(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    said: list[str] = []
    monkeypatch.setattr(window, "alert", said.append)

    def broken(args, home, refuse, log=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(welcome, "_start", broken)
    with pytest.raises(RuntimeError):
        welcome.main([])  # with a terminal the traceback is the message
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    try:
        assert welcome.main([]) == 1
        log = sys.stdout
    finally:
        monkeypatch.undo()
    log.close()
    path = tmp_path / "runtime" / "desktop" / "app.log"
    assert len(said) == 1 and str(path) in said[0]
    assert "RuntimeError: boom" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("argv, available, windowed", [
    ([], True, True), ([], False, False), (["--browser"], True, False), (["--no-browser"], True, False)])
def test_start_opens_the_window_when_it_can(tmp_path, monkeypatch, capsys, argv, available, windowed) -> None:
    import uvicorn

    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.setattr(welcome.appwindow, "available", lambda: available)
    opened = threading.Event()
    monkeypatch.setattr(welcome.webbrowser, "open", lambda url: opened.set())
    in_window: list[str] = []
    monkeypatch.setattr(welcome, "_serve_in_window",
                        lambda app, server, sock, url, desktop, hidden=False: in_window.append(url))
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets=None: None)
    assert welcome.main(argv) == 0
    out = capsys.readouterr().out
    link = re.search(r"http://127\.0\.0\.1:\d+/#token=\S+", out).group(0)
    assert in_window == ([link] if windowed else [])
    assert ("Close the Mibo window to stop." in out) is windowed
    # The browser opens from a timer thread once the page listens; no start that skips it starts one.
    browser = not windowed and argv != ["--no-browser"]
    assert (opened.wait(5) if browser else opened.is_set()) is browser
    assert not (tmp_path / "runtime" / "desktop" / "welcome.json").exists()

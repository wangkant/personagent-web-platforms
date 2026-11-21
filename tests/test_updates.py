"""The update notice: asking the release page and remembering the answer."""
from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from mibo import __version__, settingsfile, updates, welcome


def _no_network(url, **kw):
    raise AssertionError(f"a test reached the network: {url}")


@pytest.fixture(autouse=True)
def _check_on(monkeypatch):
    monkeypatch.setenv("UPDATE_CHECK_ENABLED", "true")
    monkeypatch.setattr(httpx, "get", _no_network)
    updates._attempted.clear()


class _Answer:
    def __init__(self, version: str):
        self.version = version

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"tag_name": "v" + self.version}


def _remember(home, latest: str, checked: float | None = None, failed: bool = False) -> None:
    path = home / "runtime" / "desktop" / "update.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"checked": time.time() if checked is None else checked,
                                "latest": latest, "failed": failed}), encoding="utf-8")


def _wait_idle() -> None:
    deadline = time.time() + 5
    while updates._running and time.time() < deadline:
        time.sleep(0.01)


def test_only_a_higher_plain_release_counts():
    assert updates.newer("1.2.2", "1.2.1")
    assert updates.newer("1.10.0", "1.9.9")
    assert updates.newer("2.0.0", "1.99.0")
    assert not updates.newer("1.2.1", "1.2.1")
    assert not updates.newer("1.2.0", "1.2.1")
    assert not updates.newer("1.3.0rc1", "1.2.1")
    assert not updates.newer("", "1.2.1")
    assert not updates.newer("1.3.0", "not a version")


def test_a_check_remembers_the_answer_and_a_failure_keeps_the_last_one(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(httpx, "get", lambda url, **kw: asked.append((url, kw)) or _Answer("99.0.0"))
    assert updates.check(tmp_path) == "99.0.0"
    url, kw = asked[0]
    assert url == updates.LATEST_URL
    # Only the version travels: no key, no home, nothing from the settings.
    assert kw["headers"] == {"User-Agent": f"mibo/{__version__}"}
    found = updates.notice(tmp_path)
    assert found["version"] == "99.0.0"
    assert found["url"].endswith("/releases/tag/v99.0.0")

    def offline(url, **kw):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", offline)
    assert updates.check(tmp_path) == "99.0.0"
    assert updates.notice(tmp_path)["version"] == "99.0.0"
    assert updates._read(tmp_path)["failed"] is True
    # A home that does not exist yet gets no cache written into it.
    assert updates.check(tmp_path / "missing") == ""
    assert not (tmp_path / "missing").exists()


def test_no_notice_when_current_or_switched_off(tmp_path, monkeypatch):
    _remember(tmp_path, __version__)
    assert updates.notice(tmp_path) is None
    _remember(tmp_path, "99.0.0")
    settingsfile.write(tmp_path, {"UPDATE_CHECK_ENABLED": "false"})
    monkeypatch.delenv("UPDATE_CHECK_ENABLED")
    assert not updates.enabled(tmp_path)
    assert updates.notice(tmp_path) is None
    # The environment wins over the settings.
    monkeypatch.setenv("UPDATE_CHECK_ENABLED", "true")
    assert updates.notice(tmp_path)["version"] == "99.0.0"


def test_asks_in_the_background_only_when_the_answer_is_stale(tmp_path, monkeypatch):
    calls, done = [], threading.Event()
    monkeypatch.setattr(updates, "check", lambda home: calls.append(home) or done.set() or "")
    _remember(tmp_path, "", checked=time.time() - 60)
    updates.refresh_soon(tmp_path)
    assert not calls
    _remember(tmp_path, "", checked=time.time() - updates.CHECK_EVERY_S - 60)
    updates.refresh_soon(tmp_path)
    assert done.wait(5)
    assert calls == [tmp_path]
    # A failed attempt is retried after an hour, not a day.
    _wait_idle()
    updates._attempted.clear()
    done.clear()
    _remember(tmp_path, "", checked=time.time() - updates.RETRY_AFTER_S - 60, failed=True)
    updates.refresh_soon(tmp_path)
    assert done.wait(5)
    _wait_idle()
    updates._attempted.clear()
    monkeypatch.setenv("UPDATE_CHECK_ENABLED", "false")
    done.clear()
    updates.refresh_soon(tmp_path)
    assert not done.wait(0.2)


def test_an_unwritable_cache_does_not_turn_polling_into_requests(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(httpx, "get", lambda url, **kw: asked.append(url) or _Answer("99.0.0"))

    def full_disk(*args, **kw):
        raise OSError("no space left")

    monkeypatch.setattr(updates, "atomic_write_text", full_disk)
    for _ in range(5):
        updates.refresh_soon(tmp_path)
        _wait_idle()
    assert len(asked) == 1


def test_the_local_page_carries_the_notice_once_set_up(tmp_path, monkeypatch):
    _remember(tmp_path, "99.0.0")
    workspace = welcome.Workspace(tmp_path)
    try:
        assert workspace.summary()["update"]["version"] == "99.0.0"
        monkeypatch.setenv("UPDATE_CHECK_ENABLED", "false")
        assert workspace.summary()["update"] is None
    finally:
        workspace.close()

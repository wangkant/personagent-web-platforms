"""Settings › runtime: running behind the window, the update notice, and what the models used."""
from __future__ import annotations

import json
import time
import types

from harness.app import TOKEN
from mibo import updates, usage, welcome

HEADERS = {welcome.TOKEN_HEADER: TOKEN}


def _found(home, latest: str = "99.0.0", failed: bool = False) -> None:
    """What the last update check left: the newest release it saw, or that it did not get through."""
    cache = home / "runtime" / "desktop" / "update.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"checked": time.time(), "latest": latest, "failed": failed}), encoding="utf-8")


def test_background_can_be_chosen_only_where_a_window_can_hide(client, workspace) -> None:
    seen = client.get("/api/runtime", headers=HEADERS).json()
    assert seen["background"] is False and seen["canHide"] is False
    refused = client.post("/api/runtime/background", json={"on": True}, headers=HEADERS)
    assert (refused.status_code, refused.json()) == (400, {"error": "background_unavailable"})
    client.app.state.window = types.SimpleNamespace(can_hide=True)
    on = client.post("/api/runtime/background", json={"on": True}, headers=HEADERS).json()
    assert on["background"] is True and on["canHide"] is True
    assert welcome.app_settings(workspace.home / "runtime" / "desktop") == {"background": True}
    off = client.post("/api/runtime/background", json={"on": False}, headers=HEADERS).json()
    assert off["background"] is False, "turning it off never needs the icon"


def test_checking_now_asks_and_shows_what_it_found(client, monkeypatch) -> None:
    asked = []

    def check(home, timeout):
        asked.append(timeout)
        _found(home)
        return "99.0.0"

    monkeypatch.setattr(updates, "check", check)
    seen = client.post("/api/runtime/check", json={}, headers=HEADERS).json()
    assert asked == [10.0]
    assert seen["update"]["version"] == "99.0.0"
    assert seen["update"]["url"].endswith("v99.0.0") and seen["checked"]


def test_a_check_that_did_not_reach_the_server_is_not_called_up_to_date(client, workspace) -> None:
    _found(workspace.home, latest="", failed=True)
    seen = client.get("/api/runtime", headers=HEADERS).json()
    assert seen["update"] is None and seen["checkFailed"] is True


def test_someone_who_asks_is_told_even_with_the_daily_check_off(client, workspace, monkeypatch) -> None:
    monkeypatch.setenv("UPDATE_CHECK_ENABLED", "false")
    _found(workspace.home)
    assert updates.notice(workspace.home) is None
    assert client.get("/api/runtime", headers=HEADERS).json()["update"]["version"] == "99.0.0"


def test_the_panel_shows_what_the_models_used_today_and_this_week(client, workspace) -> None:
    assert client.get("/api/runtime", headers=HEADERS).json()["usage"]["week"]["calls"] == 0
    now = time.time()
    book = usage.UsageBook(workspace.home / "runtime" / usage.USAGE_FILE)
    book.add("main", {"prompt_tokens": 900, "completion_tokens": 100, "cost": 0.01}, now, "reply")
    book.add("judge", {"prompt_tokens": 50}, now, "gate")
    book.add("main", {"prompt_tokens": 10}, now - 3 * 86400)
    book.add("main", {"prompt_tokens": 10}, now - 9 * 86400)
    book.flush(now)
    seen = client.get("/api/runtime", headers=HEADERS).json()["usage"]
    assert (seen["today"]["calls"], seen["today"]["tokens"], seen["today"]["cost"]) == (2, 1050, 0.01)
    assert (seen["week"]["calls"], seen["week"]["tokens"]) == (3, 1060)
    assert [m["model"] for m in seen["models"]] == ["main", "judge"]
    assert seen["todayPurposes"] == {"reply": 1, "gate": 1}

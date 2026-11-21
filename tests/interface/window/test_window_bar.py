"""The window's own title bar, drawn by its page: only the page the window holds is told to draw it, and only
that page can press its buttons. A browser tab holding the page's token cannot close or move the window."""
from __future__ import annotations

import types

from harness.app import TOKEN
from mibo import welcome
from mibo.panels import runtime

HEADERS = {welcome.TOKEN_HEADER: TOKEN}
OWN = {**HEADERS, runtime.WINDOW_HEADER: "its-key"}
# A browser tab is told the figure the pet would be: in a new home, the first.
NO_BAR = {"frameless": False, "maximized": False, "figure": "mibo", "goto": ""}


def _window(**overrides) -> types.SimpleNamespace:
    """A window the page can ask: its key is 'its-key', and it does what it is asked."""
    values = dict(can_hide=True, frameless=True, maximized=False, frame_seen=lambda: None, figure=lambda: "deepseek",
                  take_goto=lambda: "", control=lambda action: True, is_own_page=lambda key: key == "its-key")
    return types.SimpleNamespace(**{**values, **overrides})


def test_a_browser_tab_and_a_framed_window_are_told_to_draw_no_bar(client) -> None:
    assert client.get("/api/window", headers=HEADERS).json() == NO_BAR
    refused = client.post("/api/window", json={"action": "minimize"}, headers=HEADERS)
    assert (refused.status_code, refused.json()) == (400, {"error": "window_unavailable"})
    # A window that kept its title bar: the page's buttons are not shown and do nothing.
    client.app.state.window = _window(frameless=False)
    assert client.get("/api/window", headers=OWN).json()["frameless"] is False
    assert client.post("/api/window", json={"action": "minimize"}, headers=OWN).status_code == 400


def test_the_bar_s_buttons_reach_the_window_only_from_its_own_page(client) -> None:
    asked, seen = [], []

    def control(action: str) -> bool:
        asked.append(action)
        if action == "maximize":
            shown.maximized = True
        return action != "close"

    shown = _window(frame_seen=lambda: seen.append(1), control=control)
    client.app.state.window = shown
    # A browser tab on the same address has the token and not the window's key:
    # it draws no window bar, presses no button, and does not count as the page having asked.
    for tab in (HEADERS, {**HEADERS, runtime.WINDOW_HEADER: "guess"}):
        assert client.get("/api/window", headers=tab).json() == NO_BAR
        refused = client.post("/api/window", json={"action": "close"}, headers=tab)
        assert (refused.status_code, refused.json()) == (400, {"error": "window_unavailable"})
    assert seen == [] and asked == []
    assert client.get("/api/window", headers=OWN).json() == {**NO_BAR, "frameless": True, "figure": "deepseek"}
    assert seen == [1], "the window is told the page draws the bar"
    done = client.post("/api/window", json={"action": "maximize"}, headers=OWN)
    assert done.json() == {**NO_BAR, "frameless": True, "maximized": True, "figure": "deepseek"}
    assert client.post("/api/window", json={"action": "minimize"}, headers=OWN).status_code == 200
    # Not one of the four, and one the window could not do.
    for action in ("resize-top", "", "close"):
        refused = client.post("/api/window", json={"action": action}, headers=OWN)
        assert (refused.status_code, refused.json()) == (400, {"error": "window_unavailable"}), action
    assert asked == ["maximize", "minimize", "close"]

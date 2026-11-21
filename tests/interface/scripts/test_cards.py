"""An asking card in the chat (chat.js): a turn that needs the person's yes shows its card under the reply on
its way, and the person's press there is what the server acts on, either way."""
from __future__ import annotations

import time

import pytest

from harness.folders import Profile
from harness.model import call, say
from harness.page import measured
from mibo.acting import Modes

pytestmark = pytest.mark.browser


def _taken(app) -> bool:
    """Whether the server has the press within a few seconds. A press it never got is answered here, so the turn
    ends and the app can stop while the test fails."""
    for _ in range(50):
        waiting = [(place, app.get(f"/api/{place}").json()["card"]) for place in ("shell/card", "turnon/card")]
        waiting = [(place, card) for place, card in waiting if card]
        if not waiting:
            return True
        time.sleep(0.1)
    for place, card in waiting:
        app.post(f"/api/{place}", {"id": card["id"], "yes": False}, own_page=True)
    return False


# The card's own yes is its primary button, its no the quiet one.
@pytest.mark.parametrize("press, listed", [(".pa-btn-primary", True), (".pa-btn-ghost", False)])
def test_a_card_a_turn_raises_shows_in_the_chat_and_the_press_decides_what_runs(app, model, browser, monkeypatch,
                                                                                 tmp_path, press, listed):
    person = Profile(tmp_path / "profile", monkeypatch)
    Modes(app.home / "runtime" / "desktop").set("off")
    app.post("/api/look/toured")
    measured(app)
    model.script(call("list_files", path=str(person.root / "Desktop")), say("Here is what I found."))
    page = browser.open(app)
    box = page.page.locator(".chat-empty .chat-input:visible")
    box.fill("What is on my desktop?")
    box.press("Enter")
    card = page.page.locator(".chat-messages .op-card")
    card.locator(f".op-card-buttons {press}").click()
    card.locator(".op-card-told").wait_for()
    assert _taken(app)
    page.page.get_by_text("Here is what I found.").wait_for()
    (asked, answered) = model.asked()
    (result,) = [m["content"] for m in answered["body"]["messages"] if m.get("role") == "tool"]
    assert ("待办.txt" in result) is listed
    assert page.errors == [] and page.refused == []

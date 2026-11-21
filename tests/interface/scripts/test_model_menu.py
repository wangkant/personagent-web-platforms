"""The model menu under the chat box (modelbar.js): a thinking level picked there shows on its button and is the
level the next reply is asked to think at."""
from __future__ import annotations

import pytest

from harness.model import say
from harness.page import measured

pytestmark = pytest.mark.browser


def test_a_thinking_level_picked_in_the_model_menu_shows_and_reaches_the_next_reply(app, model, browser):
    app.post("/api/look/toured")
    measured(app, thinking="yes", thinking_detail={"reasons": True, "param": "reasoning_effort"})
    model.script(say("Thought it through."))
    page = browser.open(app)
    p = page.page
    p.locator(".chat-empty .mb-pill:visible").click()
    levels = p.locator(".mb-menu .mb-level")
    # auto, standard, deep: the last is the deepest.
    assert levels.count() == 3
    levels.last.click()
    p.locator(".mb-menu").wait_for(state="hidden")
    p.locator(".mb-pill .mb-tag:visible").wait_for()
    box = p.locator(".chat-input:visible")
    box.fill("Plan a garden")
    box.press("Enter")
    p.get_by_text("Thought it through.").wait_for()
    (asked,) = model.asked()
    assert asked["body"]["reasoning_effort"] == "high"
    assert page.errors == [] and page.refused == []

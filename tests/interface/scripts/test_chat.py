"""The chat page's scripts (chat.js, app.js) against the app and the scripted model: what a person types reaches
the model and its reply shows, stop ends a turn and gives the words back, and a 无痕对话 opened from its toggle
leaves nothing behind."""
from __future__ import annotations

import json

import pytest

from harness.model import hang, say
from harness.page import measured

pytestmark = pytest.mark.browser


def _chat(browser, app):
    """The app's page past its first run, on the main chat."""
    app.post("/api/look/toured")
    measured(app)
    page = browser.open(app)
    # The box sits in the empty start once the chat's history is read; words sent before that are drawn over.
    page.page.locator(".chat-empty .chat-input:visible").wait_for()
    return page


def test_a_message_typed_in_the_box_reaches_the_model_and_its_reply_shows(app, model, browser):
    page = _chat(browser, app)
    model.script(say("Tide pools fill at high water."))
    box = page.page.locator(".chat-input:visible")
    box.fill("What are tide pools?")
    box.press("Enter")
    page.page.get_by_text("Tide pools fill at high water.").wait_for()
    (asked,) = model.asked()
    assert "What are tide pools?" in json.dumps(asked["body"]["messages"][-1], ensure_ascii=False)
    assert page.page.locator(".chat-messages").get_by_text("What are tide pools?").count() == 1
    assert page.errors == [] and page.refused == []


def test_stop_ends_the_turn_and_gives_the_words_back_to_the_box(app, model, browser):
    page = _chat(browser, app)
    model.script(hang())
    box = page.page.locator(".chat-input:visible")
    box.fill("Plan a week of dinners")
    box.press("Enter")
    stop = page.page.locator(".chat-send.stopping")
    stop.wait_for()
    stop.click()
    page.page.locator(".chat-send:not(.stopping)").wait_for()
    assert box.input_value() == "Plan a week of dinners"
    assert app.get("/api/chat/doing").json()["busy"] is False
    (last,) = [m for m in app.get("/api/chat/history").json()["messages"] if m["role"] == "user"]
    assert last["text"] == "Plan a week of dinners" and last.get("failed") == "stopped"
    assert page.errors == []


def test_a_private_chat_opened_from_its_toggle_keeps_nothing_and_is_left_from_the_menu(app, model, browser, tmp_path):
    page = _chat(browser, app)
    model.script(say("Noted, and only here."))
    page.page.locator("#side-new").click()
    page.page.get_by_role("button", name="Start a temporary chat").click()
    page.page.locator(".chat-empty .chat-private:visible").wait_for()
    (opened,) = [chat for chat in app.get("/api/chats").json()["chats"] if chat.get("private")]
    box = page.page.locator(".chat-input:visible")
    # Words found nowhere else in a home (a port, a time or an id may hold any four digits).
    box.fill("My locker tag says QZXV-4512")
    box.press("Enter")
    page.page.get_by_text("Noted, and only here.").wait_for()

    page.page.locator(".chat-attach:visible").click()
    page.page.get_by_role("menuitemcheckbox", name="Temporary chat").click()
    page.page.locator(".chat-private:visible").wait_for(state="detached")
    assert page.page.locator(".chat-input:visible").count() == 1
    kept = [path for path in app.home.rglob("*") if path.is_file() and b"QZXV-4512" in path.read_bytes()]
    assert kept == [] and opened["id"] in {chat["id"] for chat in app.get("/api/chats").json()["chats"]}
    assert page.errors == []

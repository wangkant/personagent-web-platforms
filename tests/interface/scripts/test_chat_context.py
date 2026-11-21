"""The chat's context on the page (chat.js, modelbar.js) against the app and the scripted model: a checkpoint shows
as one quiet line before the turns the model still reads whole, its summary under it on request; the model menu's
gauge is the last request's share of the input limit, its parts on hover; 立即归纳 and 清空上下文 from the menu
change what the next request holds; a message cut when it came in and a model whose window cannot hold the setup
are each said under the message."""
from __future__ import annotations

import json
import time

import pytest

from harness.model import fail, say
from harness.page import measured
from mibo.prompt import _CHECKPOINT_OPEN

pytestmark = pytest.mark.browser

SUMMARY = say("Threads:\n- a walk in the park, ginkgo leaves\nTheir words:\n- (none)\nMood: easy")
ZH = "今天下午我们去公园散步，路上看到很多银杏叶已经黄了，风一吹就落下来，像下了一场金色的雨，真好看。"


def _setup(app, window: int = 65536) -> None:
    app.post("/api/look/toured")
    app.model.window = window
    measured(app, context=window)
    app.model.script(fail(500), purpose="fold")
    app.model.script(*[SUMMARY] * 5, purpose="compact")


def _talk(turn, n: int) -> None:
    # A second apart: the page keeps its messages' times to the second.
    for k in range(n):
        assert turn(f"Line {k} about the park", say(f"Answer {k}")).status == 200
        time.sleep(1.1)


def _open(browser, app):
    page = browser.open(app)
    page.page.locator(".chat-messages .me").first.wait_for()
    return page


def _menu(page):
    page.page.locator(".chat-dock .mb-pill:visible").click()
    page.page.locator(".mb-menu:visible").wait_for()


def _send(page, text: str) -> None:
    box = page.page.locator(".chat-dock .chat-input:visible")
    box.fill(text)
    box.press("Enter")


def test_a_checkpoint_shows_as_one_line_before_the_turns_still_read_whole_and_opens_its_summary(app, turn, browser):
    _setup(app)
    _talk(turn, 4)
    assert app.post("/api/chat/compact").json()["compacted"]
    page = _open(browser, app)
    line = page.page.locator(".chat-compacted")
    line.wait_for()
    assert line.count() == 1 and "Earlier messages were condensed into a summary." in line.inner_text()
    assert "Line 3 about the park" in line.evaluate("node => node.nextElementSibling.textContent")
    shown = page.page.locator(".chat-summary")
    assert not shown.is_visible()
    opener = line.get_by_role("button", name="View summary")
    opener.click()
    assert shown.is_visible() and "ginkgo" in shown.inner_text()
    assert line.locator("button").get_attribute("aria-expanded") == "true"
    assert page.errors == [] and page.refused == []


def test_the_menus_gauge_is_the_last_requests_share_of_the_input_limit_with_its_parts_on_hover(app, turn, browser):
    _setup(app)
    _talk(turn, 2)
    gauge = app.get("/api/chat/context").json()
    page = _open(browser, app)
    _menu(page)
    row = page.page.locator(".mb-ctx")
    row.wait_for()
    assert row.locator(".mb-label").inner_text() == f"Context {gauge['percent']}% full"
    row.hover()
    tip = page.page.locator(".mb-ctx-tip")
    tip.wait_for()
    said = tip.inner_text()
    assert all(name in said for name in ("System setup", "Tool descriptions", "Recent messages"))
    box = tip.bounding_box()
    menu = page.page.locator(".mb-menu").bounding_box()
    # Beside the menu, whole, not cut off by the menu's own scrolling.
    assert box["width"] > 100 and box["height"] > 40 and (box["x"] >= menu["x"] + menu["width"] or box["x"] + box["width"] <= menu["x"])
    assert page.errors == [] and page.refused == []


def test_summarize_now_from_the_menu_shows_the_line_and_the_next_request_holds_the_checkpoint(app, turn, browser):
    _setup(app)
    _talk(turn, 4)
    page = _open(browser, app)
    _menu(page)
    page.page.locator(".mb-item", has_text="Summarize now").click()
    page.page.locator(".chat-compacted").wait_for()
    app.model.script(say("We walked in the park."))
    _send(page, "What did we talk about?")
    page.page.get_by_text("We walked in the park.").wait_for()
    body = app.model.asked()[-1]["body"]
    assert body["messages"][1]["content"].startswith(_CHECKPOINT_OPEN)
    assert "Line 0 about the park" not in json.dumps(body["messages"], ensure_ascii=False)
    assert page.errors == [] and page.refused == []


def test_clearing_the_context_from_the_menu_asks_first_leaves_its_line_and_the_next_request_starts_over(app, turn,
                                                                                                    browser):
    _setup(app)
    _talk(turn, 3)
    page = _open(browser, app)
    _menu(page)
    page.page.locator(".mb-item", has_text="Clear context").click()
    asked = page.page.locator("dialog.confirm")
    asked.wait_for()
    assert "chat history and memory are not affected" in asked.inner_text()
    asked.locator(".confirm-ok").click()
    line = page.page.locator(".chat-compacted")
    line.wait_for()
    assert "Context cleared." in line.inner_text()
    assert line.evaluate("node => node === node.parentElement.lastElementChild")
    assert page.page.get_by_text("Line 0 about the park").is_visible()
    time.sleep(1.1)
    app.model.script(say("Starting fresh."))
    _send(page, "Hello again")
    page.page.get_by_text("Starting fresh.").wait_for()
    body = app.model.asked()[-1]["body"]
    assert len(body["messages"]) == 2 and "Line 0" not in json.dumps(body["messages"], ensure_ascii=False)
    assert page.errors == [] and page.refused == []


def test_a_message_cut_when_it_came_in_is_marked_under_it(app, browser):
    _setup(app, 16384)
    page = browser.open(app)
    page.page.locator(".chat-empty .chat-input:visible").wait_for()
    app.model.script(say("I read the beginning and the end."))
    box = page.page.locator(".chat-input:visible")
    box.fill("开头" + ZH * 150 + "结尾")
    box.press("Enter")
    page.page.get_by_text("I read the beginning and the end.").wait_for()
    mark = page.page.locator(".me .msg-cut")
    assert mark.inner_text() == "This message was longer than the model can read, so its beginning and end were kept."
    assert page.errors == [] and page.refused == []


def test_a_model_whose_window_cannot_hold_the_setup_is_named_under_the_message(app, browser):
    _setup(app, 8192)
    page = browser.open(app)
    page.page.locator(".chat-empty .chat-input:visible").wait_for()
    box = page.page.locator(".chat-input:visible")
    box.fill("Hello")
    box.press("Enter")
    note = page.page.locator(".notice")
    note.wait_for()
    assert "context window is too small to hold Mibo's basic setup" in note.inner_text()
    assert app.model.asked() == []
    assert page.errors == [] and page.refused == []

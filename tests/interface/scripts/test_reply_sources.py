"""The sources under a reply (activity.js): each site's chip is a link of its own to that site's page, opened as
every link in the chat is, with a name a screen reader says; one button at the end of the row opens the list of
all the pages, and only when they are more than the chips."""
from __future__ import annotations

import pytest

from harness.model import say
from harness.page import measured

pytestmark = pytest.mark.browser

NASA, JPL = "science.nasa.gov", "www.jpl.nasa.gov"
PAGES = [{"title": "Voyager 1 mission status", "url": f"https://{NASA}/mission/voyager/status", "site": NASA},
         {"title": "Voyager 1 questions", "url": f"https://{NASA}/mission/voyager/faq", "site": NASA},
         {"title": "Voyager 1 sends data again", "url": f"https://{JPL}/news/voyager-1-data", "site": JPL}]


def _replied(browser, app, model, pages: list) -> object:
    """The page with one reply in the main chat, its sources `pages` as a turn keeps them."""
    app.post("/api/look/toured")
    measured(app)
    model.script(say("Voyager 1 is still sending data."))
    assert app.post("/api/chat", {"text": "How is Voyager 1 doing?"}).status_code == 200
    page = browser.open(app)

    def with_sources(route) -> None:
        answer = route.fetch().json()
        for message in answer["messages"]:
            if message["role"] == "assistant":
                message["sources"] = pages
        route.fulfill(json=answer)

    page.page.route("**/api/chat/history*", with_sources)
    page.reload()
    page.page.get_by_text("Voyager 1 is still sending data.").wait_for()
    return page


def test_each_site_chip_opens_its_page_and_one_button_lists_all_the_pages(app, model, browser):
    page = _replied(browser, app, model, PAGES)
    chips = page.page.locator(".act-src a.act-chip")
    assert chips.evaluate_all("(nodes) => nodes.map((n) => [n.href, n.target, n.getAttribute('aria-label')])") == [
        [PAGES[0]["url"], "_blank", f"Open {NASA}: Voyager 1 mission status"],
        [PAGES[2]["url"], "_blank", "Open jpl.nasa.gov: Voyager 1 sends data again"]]
    every = page.page.get_by_role("button", name="3 sources")
    listed = page.page.locator(".act-src-list")
    assert every.get_attribute("aria-expanded") == "false" and listed.is_hidden()
    assert every.get_attribute("aria-controls") == listed.get_attribute("id")
    every.click()
    assert every.get_attribute("aria-expanded") == "true"
    assert listed.locator("a.act-page").evaluate_all("(nodes) => nodes.map((n) => n.href)") == [
        p["url"] for p in PAGES]
    assert page.errors == []


def test_with_a_page_for_each_chip_there_is_no_button_for_the_list(app, model, browser):
    page = _replied(browser, app, model, [PAGES[0], PAGES[2]])
    assert page.page.locator(".act-src a.act-chip").count() == 2
    assert page.page.locator(".act-src button, .act-src-list").count() == 0
    assert page.errors == []

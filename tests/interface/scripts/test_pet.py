"""The pet's page (pet.html and its scripts) with a stand-in for its window's bridge: the dial's buttons sit wholly
inside the part of the window that takes the pointer and answer a press, and a find the pet brings shows on its
card, where a like reaches the server."""
from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest

from harness.clock import Clock
from harness.model import text
from harness.page import measured
from mibo import situation
from mibo.library import Item, Library

pytestmark = pytest.mark.browser

# Midday where the character lives, so it is awake.
NOON = datetime(2026, 11, 20, 12, tzinfo=timezone.utc).timestamp()
# A 1 px picture, as a cover kept in the library.
COVER = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")


@pytest.fixture
def awake(app, monkeypatch):
    """The app past its first run at midday on the character's clock, its model measured."""
    monkeypatch.setenv("PERSONA_TZ_OFFSET_HOURS", "0")
    Clock(monkeypatch, NOON)
    app.post("/api/look/toured")
    measured(app)
    return app


def _dial(browser, app):
    """The pet's page with its dial out, as the pointer resting on the figure brings it."""
    page = browser.open(app, "/pet", bridge=True, width=560, height=420)
    # The pointer is noticed once the figure is drawn and nothing is being acted out.
    page.page.wait_for_function("""() => document.getElementById('figure').dataset.figure
        && document.getAnimations().every((motion) => motion.playState !== 'running')""")
    figure = page.page.locator("#figure").bounding_box()
    page.page.mouse.move(figure["x"] + figure["width"] / 2, figure["y"] + figure["height"] / 2)
    page.page.locator("#quick.on").wait_for()
    # The buttons grow as they come out (petux.css): measured once they are at their full size.
    page.page.wait_for_function("() => document.getAnimations().every((motion) => motion.playState !== 'running')")
    return page


def test_the_dials_buttons_lie_inside_the_window_part_that_takes_the_pointer_and_answer_a_press(awake, browser):
    page = _dial(browser, awake)
    (box,) = [call[1] for call in page.calls() if call[0] == "spots"][-1]
    x, y, wide, high = box[:4]
    buttons = page.page.evaluate("""[...document.querySelectorAll('#quick button')].map((button) => {
        const [x, y, w, h] = petUx.boxOf(button);
        const r = button.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return {name: button.className, box: [x, y, w, h], hit: button.contains(hit)};
    })""")
    assert len(buttons) == 8
    outside = [b["name"] for b in buttons
               if b["box"][0] < x or b["box"][1] < y or b["box"][0] + b["box"][2] > x + wide or b["box"][1] + b["box"][3] > y + high]
    assert outside == [] and all(b["hit"] for b in buttons)
    page.page.locator(".quick-settings").click()
    assert ["open_at", "settings"] in page.calls()
    assert page.errors == [] and page.refused == []


def test_a_find_the_pet_brings_shows_on_its_card_and_a_like_reaches_the_server(awake, model, browser):
    # Do not disturb keeps the main chat from sharing the find first on a slow machine; a find asked for from
    # the pet's menu comes all the same.
    awake.post("/api/feed/snooze", {"on": True})
    # What the character is asked about the find while the test runs, from the moment it is on the shelf (its
    # choice, its line for the card, its next choice after the like): all answered empty.
    model.script(*[text("")] * 6)
    runtime = awake.home / "runtime"
    situation.set_value(runtime, "readable", ["en"])
    shelf = Library(runtime)
    found = shelf.add(Item(url="https://shore.example/article/1", site="shore.example", kind="article",
                           title="How tide pools fill", description="What lives in a pool between two tides.",
                           picture=shelf.save_picture("cover", COVER, ".png"), seen_at=int(NOON - 3600)))
    page = _dial(browser, awake)
    page.page.locator(".quick-find").click()
    page.page.locator("#card").get_by_text("How tide pools fill").wait_for()
    page.page.locator("#card .card-like").click()
    page.page.locator("#card .card-like[aria-pressed='true']").wait_for()
    page.settle()
    (kept,) = awake.get("/api/feed?limit=5&kept=true").json()["items"]
    assert (kept["id"], kept["liked"]) == (found.id, True)
    assert page.errors == [] and page.refused == []

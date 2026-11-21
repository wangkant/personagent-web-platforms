"""The sidebar beside a web page (app.js paSideFold, browse.js fold): a page in front folds the whole sidebar to
its rail and the sidebar comes back when the page leaves; the switch in Settings › This computer turns that off,
and then it folds only where the screen has no room for it."""
from __future__ import annotations

import pytest

from harness.page import measured

pytestmark = pytest.mark.browser

RAILED = "document.body.classList.contains('side-rail')"


def test_a_page_in_front_folds_the_whole_sidebar_until_the_switch_in_settings_is_turned_off(app, browser):
    app.post("/api/look/toured")
    measured(app)
    page = browser.open(app, width=1400)
    p = page.page
    assert p.evaluate(RAILED) is False

    # The pane tells the page it came to the front (on a screen with room to spare), then that it left.
    p.evaluate("window.paSideFold(true, false)")
    assert p.evaluate(RAILED) is True
    p.evaluate("window.paSideFold(false, false)")
    assert p.evaluate(RAILED) is False

    # Folded for a page, the fold button still unfolds it.
    p.evaluate("window.paSideFold(true, false)")
    p.locator("#side-toggle").click()
    assert p.evaluate(RAILED) is False
    p.evaluate("localStorage.removeItem('mibo-side')")

    p.locator("#side-settings").click()
    p.locator(".setup-nav-item[data-part=device]").click()
    switch = p.locator("#setup-switch-side-fold")
    switch.wait_for()
    assert switch.is_checked()
    switch.click()
    assert p.evaluate("localStorage.getItem('mibo-side-page')") == "keep"

    # Off: the sidebar stays whole beside a page, except on a screen with no room for both.
    p.evaluate("window.paSideFold(true, false)")
    assert p.evaluate(RAILED) is False
    p.evaluate("window.paSideFold(true, true)")
    assert p.evaluate(RAILED) is True
    p.evaluate("window.paSideFold(false, false)")

    switch.click()
    assert p.evaluate("localStorage.getItem('mibo-side-page')") is None
    assert page.errors == [] and page.refused == []

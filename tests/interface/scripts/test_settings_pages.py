"""Settings (setup.js and its sections under setup/) drawn by the page in each of the ten languages: every section
opens with no script error, and every word its scripts take is in that language's own catalog, so no screen
shows a key, an empty label or English in its place."""
from __future__ import annotations

import pytest

from harness.page import measured
from mibo import langs

pytestmark = pytest.mark.browser


def test_every_settings_section_draws_in_each_language_with_every_word_in_its_catalog(app, browser):
    app.post("/api/look/toured")
    measured(app)
    pages = {code: browser.open(app, lang=code, settle=False) for code in langs.CODES}
    # Each section is built and drawn as it opens: all are opened in every language, then each page is read once
    # nothing is still being fetched.
    for page in pages.values():
        page.page.locator("#side-settings").click()
        page.page.locator(".setup-nav-item").first.wait_for()
        page.page.evaluate("for (const part of document.querySelectorAll('.setup-nav-item')) part.click()")
    lacking, sections = {}, {}
    for code, page in pages.items():
        page.settle()
        sections[code] = page.page.eval_on_selector_all(".setup-nav-item", "parts => parts.map((part) => part.dataset.part)")
        assert page.page.evaluate("document.documentElement.lang") == ("zh-CN" if code == "zh" else code)
        lacking[code] = page.missing_words(code)
        assert page.errors == [] and page.refused == [], code
    assert lacking == {code: [] for code in langs.CODES}
    assert len(sections["en"]) >= 10 and all(found == sections["en"] for found in sections.values())

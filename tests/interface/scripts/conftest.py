"""The page's own scripts against the app: `browser` opens the app's pages in the headless browser of each test
file (harness/page.py), and closes them after the test. With no browser here, the tests skip, and CI
turns that skip into a failure (MIBO_REQUIRE_BROWSER)."""
from __future__ import annotations

import pytest

from harness.page import Browser, NoBrowser


# Playwright's sync API keeps an event loop running while its browser is open: the browser closes with each
# file's last test, so the coroutine tests after it can start their own loop.
@pytest.fixture(scope="module")
def headless_browser():
    try:
        started = Browser()
    except (ImportError, NoBrowser) as missing:
        pytest.skip(f"no headless browser: {missing}")
    yield started
    started.close()


@pytest.fixture
def browser(headless_browser):
    yield headless_browser
    headless_browser.close_pages()

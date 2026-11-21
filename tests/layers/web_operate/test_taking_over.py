"""While the character works a page in the pane (mibo/browser.py), the person's own input on that page pauses the
errand, and input on the app's own page laid over it (the chat, a card, the bar) never does: a press counts where
the pointer is, keys where the keyboard focus is. (2026-11-21: a real press on 本次允许 in the chat over the page
answered the card and then ended the errand as taken over: the page still held the focus, and by the time the
watch looked the card was gone and the chat no longer lay over the page.)"""
from __future__ import annotations

from mibo.browser import cuts_at, on_page, went_to_page

# The chat column over the right of a pane 1000 CSS px wide, on a display at 150%.
CUTS = [[600, 0, 400, 800]]
WIDE, HIGH, SCALE = 1500, 1200, 1.5


def press(x: float, y: float, *, focus_in_page: bool) -> bool:
    return went_to_page(True, on_page(x, y, WIDE, HIGH, CUTS, SCALE), False, focus_in_page)


def test_a_press_on_a_card_over_the_page_is_not_the_person_taking_the_page_while_the_page_holds_the_focus():
    assert not press(1200, 600, focus_in_page=True)
    assert not press(1200, 600, focus_in_page=False)


def test_a_press_on_the_page_itself_is_the_person_taking_it():
    assert press(300, 600, focus_in_page=True)
    assert press(300, 600, focus_in_page=False)


def test_keys_count_where_the_focus_is():
    assert went_to_page(False, False, True, True)
    assert not went_to_page(False, True, True, False)


def test_a_pointer_that_only_moves_is_nothing():
    assert not went_to_page(False, True, False, True)


def test_a_press_that_closes_the_card_is_judged_by_the_cuts_it_was_pressed_on():
    # The card was answered at tick 1000 and the page laid out again without the chat at 1060, before the watch
    # looked at 1200: the press counts against the cuts that stood when it came.
    before = [(1060, CUTS)]
    assert cuts_at(1000, before, []) == CUTS
    assert not went_to_page(True, on_page(1200, 600, WIDE, HIGH, cuts_at(1000, before, []), SCALE), False, True)
    # Input after the change counts against the cuts standing now, across the tick counter's wrap too.
    assert cuts_at(1100, before, []) == []
    assert cuts_at(0xFFFFFFF0, [(0x10, CUTS)], []) == CUTS

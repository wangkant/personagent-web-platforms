"""How old a kept fact reads in the prompt, and how a pause between messages is said."""
from __future__ import annotations

import time

from mibo import ranking

DAY = 86400


def test_a_facts_age_starts_from_when_it_was_first_noted() -> None:
    now = time.time()
    assert ranking.memory_age(now - 14 * DAY, now, now) == "first noted 14 days ago, confirmed today"
    assert ranking.memory_age(now - 3 * DAY, now - 3 * DAY, now) == "first noted 3 days ago"
    # A row kept before facts carried when they were first noted.
    assert ranking.memory_age(None, now, now) == "first noted today"


def test_a_pause_is_said_in_its_largest_unit() -> None:
    assert (ranking.gap_phrase(10 * 60), ranking.gap_phrase(3 * 3600), ranking.gap_phrase(2 * DAY + 5)) == (
        "10 minutes later", "3 hours later", "2 days later")

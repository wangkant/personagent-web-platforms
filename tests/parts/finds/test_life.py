"""The character's life: what it does now, and the finds it keeps."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from harness.clock import Clock
from mibo import life
from mibo.library import KEPT, Item, Library

NOW = 1_800_000_000


@pytest.fixture(autouse=True)
def clock_at_now(monkeypatch):
    Clock(monkeypatch, NOW)


# ------------------------------------------------------------------ now ----

def test_the_now_line_follows_what_is_true(tmp_path):
    library = Library(tmp_path)
    assert life.now_state(library, NOW)["state"] == "home"
    asleep = life.now_state(library, NOW, sleep_until=NOW + 3600)
    assert (asleep["state"], asleep["until"]) == ("asleep", NOW + 3600)
    assert life.now_state(library, NOW, answering=True)["state"] == "answering"
    library.left("Bilibili", now=NOW - 100)
    out = life.now_state(library, NOW, answering=True)
    assert (out["state"], out["where"], out["since"]) == ("out", "Bilibili", NOW - 100), "out comes first"
    library.returned(3, now=NOW - 60)
    back = life.now_state(library, NOW)
    assert (back["state"], back["count"], back["until"]) == ("back", 3, NOW - 60 + life.BACK_S)
    assert life.now_state(library, NOW + life.BACK_S)["state"] == "home", "back lasts ten minutes"
    library.returned(0, now=NOW)
    assert life.now_state(library, NOW + 1)["state"] == "home", "a walk with nothing is not news"


def test_sleep_hours_are_on_the_persona_clock_and_end_at_seven():
    zone = timezone(timedelta(hours=8))
    at_three = datetime(2027, 3, 1, 3, 30, tzinfo=zone).timestamp()
    assert life.asleep_until(at_three, 8) == int(datetime(2027, 3, 1, 7, 0, tzinfo=zone).timestamp())
    assert life.asleep_until(datetime(2027, 3, 1, 12, 0, tzinfo=zone).timestamp(), 8) is None
    assert life.asleep_until(at_three, 0) is None, "the same moment is 19:30 in UTC"


def test_keeping_a_find_and_taking_it_back(tmp_path):
    library = Library(tmp_path)
    made = [library.add(Item(url=f"https://example.test/{n}", seen_at=NOW - n)) for n in range(5)]
    assert life.mark(library, made[3].id, "keep").state == KEPT
    assert life.mark(library, made[3].id, "keep", kept=False).state == ""
    assert life.mark(library, "0" * 16, "keep") is None


# ---------------------------------------------------------------- cards ----

def test_a_card_keeps_only_listed_players_and_library_pictures():
    base = {"kind": "video", "title": "T", "url": "https://example.test/v", "length": 61,
            "embed": "https://www.youtube-nocookie.com/embed/abc", "picture": "/api/library/picture/" + "a" * 16}
    assert life.clean_card(base)["embed"] == base["embed"]
    assert life.clean_card(base)["picture"] == base["picture"]
    assert life.clean_card({**base, "embed": "https://evil.test/embed"})["embed"] == ""
    assert life.clean_card({**base, "embed": "http://www.youtube.com/embed/abc"})["embed"] == ""
    assert life.clean_card({**base, "picture": "https://evil.test/x.jpg"})["picture"] == ""
    assert life.clean_card({**base, "kind": "hologram", "length": True}) | {} == {
        **life.clean_card(base), "kind": "page", "length": None}
    assert life.clean_card({**base, "url": "javascript:alert(1)"}) is None
    assert life.clean_card("not a card") is None

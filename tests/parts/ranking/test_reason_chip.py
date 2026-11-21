"""The reason chip under every card's site: each thing brought carries one, and it says only what is true:
a picked topic when the thing is about it, a site when the person keeps opening things from it, else that
the find is new or where it is from. Its key is what a tap on the chip leans more or less of."""
from __future__ import annotations

import pytest

from harness.clock import Clock
from harness.finds import Shelf
from mibo import recommend
from mibo.panels import feed

NOW = 1_800_000_000


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    return Shelf(tmp_path / "runtime", Clock(monkeypatch, NOW), monkeypatch)


def chips(shelf: Shelf, topics: list[str]) -> dict[str, dict]:
    """The chip each thing the feed brings would carry now, by title."""
    why = recommend.Why(shelf.runtime, topics, shelf.events(), shelf.clock.now, "en")
    return {item.title: why.of(shaped, item)["why"]
            for shaped, item in feed.ranked(shelf.library, shelf.events(), shelf.clock.now, topics, seed=1)}


def test_every_thing_brought_carries_a_reason(shelf):
    for n in range(3):
        shelf.add(f"Braised pork recipe {n}", site="kitchen.example", text="Slow cooking at home.")
        shelf.add(f"Harbour notes {n}", site="harbour.example", text="Boats came and went.", hours_ago=80)
        shelf.add(f"A short clip {n}", site="clips.example", kind="video", length=90, hours_ago=5)
    for topics in ([], ["food"], ["boats"]):
        shown = chips(shelf, topics)
        assert len(shown) == 9 and all(chip["text"] for chip in shown.values()), shown


def test_a_picked_topic_is_named_only_when_the_thing_is_about_it(shelf):
    shelf.add("Braised pork: a recipe for the slow cooker", site="kitchen.example",
              text="Cooking the dish at home: the recipe, the kitchen tools, the chef's notes on cooking it.")
    shelf.add("An evening with old friends", site="letters.example",
              text="We talked for hours after the restaurant closed, about school and the years since.")
    shown = chips(shelf, ["food"])
    assert shown["Braised pork: a recipe for the slow cooker"]["key"] == "topic:food"
    assert shown["An evening with old friends"]["key"] != "topic:food", shown
    assert shown["An evening with old friends"]["text"]


def test_a_site_they_keep_opening_becomes_the_reason(shelf):
    for n in range(recommend.OFTEN):
        item = shelf.add(f"Harbour notes {n}", site="harbour.example", text="Boats came and went.")
        shelf.react(item, "seen")
        shelf.react(item, "open")
        shelf.react(item, "read", surface="browser", play_s=200)
    shelf.learn()
    shelf.add("Harbour notes, the winter", site="harbour.example", text="Boats came and went.")
    chip = chips(shelf, [])["Harbour notes, the winter"]
    assert chip["key"] == "origin:site:harbour.example" and "harbour.example" in chip["text"], chip


def test_with_nothing_to_go_on_a_find_says_it_is_new_or_where_it_is_from(shelf):
    shelf.add("Harbour notes", site="harbour.example", text="Boats came and went.", hours_ago=2)
    shelf.add("Letters from the coast", site="letters.example", text="Boats came and went.", hours_ago=80)
    shown = chips(shelf, [])
    assert shown["Harbour notes"]["key"] == ""
    assert shown["Letters from the coast"]["key"] == "origin:site:letters.example"
    assert "letters.example" in shown["Letters from the coast"]["text"]

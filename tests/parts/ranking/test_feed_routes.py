"""The feed through the page's own routes, on a home with no model (the ranking's first goes out with no
sentence): one thing at a time, held until a surface shows it; something even when the reaction file cannot
be read; a reaction kept once and as the page sent it, a malformed one refused with nothing kept; a no to a
topic turning it down until the person takes the no back."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from harness.app import App, quiet_start
from harness.clock import Clock
from harness.finds import Shelf
from mibo import profile
from mibo.panels import feed

NOW = 1_800_000_000


@pytest.fixture
def page(model, monkeypatch, tmp_path):
    """The app with no model behind it, and the shelf of its home."""
    quiet_start(monkeypatch)
    clock = Clock(monkeypatch, NOW)
    served = App(Path(os.environ["AGENT_HOME"]), model, LLM_MODEL="",
                 PERSONA_BROWSER_PATH=str(tmp_path / "no-browser")).open()
    shelf = Shelf(served.home / "runtime", clock, monkeypatch)
    for n in range(3):
        shelf.add(f"Braised pork recipe {n}", site=f"kitchen{n}.example", text="Slow cooking at home.")
        shelf.add(f"Harbour notes {n}", site=f"harbour{n}.example", text="Boats came and went.")
    yield served, shelf
    served.close()


def brought(served: App) -> dict:
    answer = served.get("/api/feed/next?now=true")
    assert answer.status_code == 200
    return answer.json()["item"]


def test_one_thing_is_brought_at_a_time_and_held_until_a_card_shows_it(page):
    served, shelf = page
    first = brought(served)
    assert first["why"]["text"] and brought(served)["id"] == first["id"]
    assert served.post("/api/feed/react", {"id": first["id"], "action": "seen", "surface": "card"}).status_code == 200
    second = brought(served)
    assert second["id"] != first["id"] and second["why"]["text"]
    listed = served.get("/api/feed").json()["items"]
    assert [card["id"] for card in listed] == [first["id"]] and listed[0]["why"]["text"]


def test_a_reaction_file_that_cannot_be_read_still_leaves_something_to_bring(page):
    served, shelf = page
    (shelf.runtime / feed.TASTE_FILE).write_text("{not json", encoding="utf-8")
    assert brought(served)["id"]


def test_a_reaction_is_kept_once_as_the_page_sent_it_and_a_malformed_one_not_at_all(page):
    served, shelf = page
    item = shelf.ranked()[0]
    for _ in range(2):
        sent = served.post("/api/feed/react", {"id": item.id, "action": "like", "surface": "card", "rank": 2})
        assert sent.status_code == 200
    events = shelf.events()
    assert [(e[1], e[2], e[5]["s"], e[5]["r"]) for e in events] == [(item.id, "like", "card", 2)]
    for wrong in ({"action": "explode"}, {"action": "like", "surface": "billboard"},
                  {"action": "read", "read_s": -5}, {"action": "like", "rank": "first"},
                  {"action": "skip", "said": {"about": "everything"}}, {"action": "end", "end": "vanished"}):
        assert served.post("/api/feed/react", {"id": item.id, **wrong}).status_code == 400, wrong
    assert served.post("/api/feed/react", {"id": "0" * 16, "action": "like"}).status_code == 400
    assert shelf.events() == events


def test_a_no_to_a_topic_turns_it_down_until_the_person_takes_it_back(page):
    served, shelf = page
    item = next(item for item in shelf.ranked() if item.title.startswith("Braised"))
    said = {"about": "topic", "key": "subj:food"}
    assert served.post("/api/feed/react", {"id": item.id, "action": "skip", "surface": "card",
                                           "said": said}).status_code == 200
    assert profile.load(shelf.runtime, False)["leans"] == {"subj:food": -1}
    assert item.id not in {thing.id for thing in shelf.ranked()}
    assert served.post("/api/feed/undo", {"id": item.id}).status_code == 200
    assert profile.load(shelf.runtime, False)["leans"].get("subj:food") is None
    assert [e[2] for e in shelf.events() if e[1] == item.id] == []
    assert item.id in {thing.id for thing in shelf.ranked()}

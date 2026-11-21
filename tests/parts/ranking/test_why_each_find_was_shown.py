"""The sentence under each row of the 推荐记录 list, through the page's own routes on a home with no model: a
showing keeps what its reason rests on, so the list says it later as it was; a row shown before reasons were
kept gets one of its own from what is known now, and when nothing in the person's reading pointed to it, the
sentence says so and names what the thing is and where it is from."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from harness.app import App, quiet_start
from harness.clock import Clock
from harness.finds import Shelf
from mibo import db, i18n, recommend, subjects
from mibo.panels import feed

NOW = 1_800_000_000


@pytest.fixture
def page(model, monkeypatch, tmp_path):
    """The app with no model behind it, and the shelf of its home: six finds from six sites."""
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


def test_a_showing_keeps_its_reason_and_the_list_says_it_later(page, monkeypatch):
    served, shelf = page
    answer = served.get("/api/feed/next?now=true")
    assert answer.status_code == 200
    first = answer.json()["item"]
    line = "Slow braising suits a cold evening at home."  # copy: voice
    # The character's sentence as the pet's card had it when the card showed the thing.
    monkeypatch.setitem(feed.on_card, first["id"], {**feed.on_card[first["id"]], "line": line})
    assert served.post("/api/feed/react", {"id": first["id"], "action": "seen", "surface": "card"}).status_code == 200
    assert [e[5]["why"] for e in shelf.events() if e[1] == first["id"]] == [{"k": "said", "words": line}]
    # The card is gone; the list still says why it was shown.
    del feed.on_card[first["id"]]
    listed = served.get("/api/feed").json()["items"]
    assert [card["id"] for card in listed] == [first["id"]]
    assert listed[0]["reason"].endswith(line), listed[0]["reason"]


def test_rows_shown_before_reasons_were_kept_each_say_what_and_where_when_nothing_pointed(page):
    served, shelf = page
    # One whose own words carry its subject beyond doubt, which the sentence then names.
    clear = shelf.add("Braised pork: a recipe for the slow cooker", site="cookbook.example",
                      text="Cooking the dish at home: the recipe, the kitchen tools, the chef's notes on cooking it.")
    items = shelf.ranked()
    for item in items:
        shelf.react(item, "seen")
    # As a home from before: its showings keep no reason.
    with db.at(shelf.runtime).write() as conn:
        for n, extra in conn.execute("SELECT n, extra FROM feed_events").fetchall():
            extra = json.loads(extra)
            extra.pop("why")
            conn.execute("UPDATE feed_events SET extra = ? WHERE n = ?", (json.dumps(extra), n))
    listed = {card["id"]: card["reason"] for card in served.get("/api/feed").json()["items"]}
    assert set(listed) == {item.id for item in items} and len(set(listed.values())) == len(items), listed
    words = i18n.words("reasons", "en")
    for item in items:
        reason = listed[item.id]
        assert reason.startswith(words["empty"].partition("{")[0]) and item.site in reason, reason
    assert subjects.name("food", "en") in listed[clear.id], listed[clear.id]


@pytest.mark.parametrize("lang", ["zh", "zh-TW", "ja", "ko", "de", "fr", "es", "pt-BR", "ru"])
def test_the_reason_and_the_chip_are_written_in_the_persons_language(lang, tmp_path, monkeypatch):
    shelf = Shelf(tmp_path / "runtime", Clock(monkeypatch, NOW), monkeypatch)
    item = shelf.add("Harbour notes", site="harbour.example", text="Boats came and went.")
    shelf.react(item, "seen")
    said, shaped = shelf.events()[-1][5]["why"], feed.shape(item, {})

    def told(code: str) -> tuple[str, str]:
        why = recommend.Why(shelf.runtime, [], shelf.events(), NOW, code)
        return why.told(said, item, shaped, "Mibo", lambda ident: ""), why.of(shaped, item)["why"]["text"]
    reason, chip = told(lang)
    words = i18n.words("reasons", lang)
    assert reason.startswith(words[said["k"] if said["k"] != "new" else "empty"].partition("{")[0])
    assert (reason, chip) != told("en") and "{" not in reason + chip

"""A find in the chat, whole, through the page: bring_find lists the person's finds for what they asked and the
model picks one for the card or none, the card is the last pick, a page read to answer a question is never offered,
a reaction is kept only when the model reacts to a find this chat showed, why a card was shown (what the reply said
of it) reaches the next turn as the 推荐记录 list says it, and a wish kept for a walk is told truthfully when walks are off."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from harness.finds import Shelf
from harness.model import call, say
from mibo import findtool, roam
from mibo.library import CHAT, Item

BREAD = "Sourdough starter in seven days"


@pytest.fixture
def shelf(app, monkeypatch):
    """The app's home with five finds of the last two days, as many as a list by taste holds: one on bread among
    others on other things."""
    shelf = Shelf(app.home / "runtime", SimpleNamespace(now=time.time()), monkeypatch)
    shelf.others = [shelf.add(title, site=site, text=text, hours_ago=n + 1) for n, (title, site, text) in enumerate((
        ("Harbour lights at dusk", "harbour.example", "Boats come in as the lamps go on along the pier."),
        ("A field guide to moss", "moss.example", "Mosses of walls and roofs, and how to tell them apart."),
        ("Chess endgames for beginners", "chess.example", "King and pawn endings, one square at a time."),
        ("The night train to Lisbon", "rail.example", "Sleeper cars, border towns and a slow breakfast.")))]
    shelf.bread = shelf.add(BREAD, site="bakery.example", hours_ago=30,
                            text="Feed flour and water each day until the starter bubbles; then bake a loaf of bread.")
    return shelf


def _card(done) -> dict | None:
    return next((e["card"] for e in done.entries if e.get("card")), None)


def _listed(done) -> str:
    """What the first bring_find of the turn listed, as the model read it."""
    first = next(c for c in done.calls() if c["name"] == "bring_find")
    return done.result_of(first["id"])


def test_the_card_is_the_find_the_model_picked_from_the_list(turn, shelf):
    done = turn("anything about baking bread?",
                [call("bring_find", wish="bread baking")],
                [call("bring_find", pick=shelf.bread.id)],
                say(f"This one: {BREAD}. It starts from flour and water."))
    assert shelf.bread.id in _listed(done)
    assert _card(done)["id"] == shelf.bread.id
    assert BREAD in done.reply


def test_the_last_pick_is_the_card_and_none_takes_it_away(turn, shelf):
    other = shelf.others[0]
    changed = turn("something to read?",
                   [call("bring_find")],
                   [call("bring_find", pick=other.id)],
                   [call("bring_find", pick=shelf.bread.id)],
                   say(f"Try {BREAD}."))
    assert _card(changed)["id"] == shelf.bread.id
    withdrawn = turn("anything about volcanoes?",
                     [call("bring_find", wish="volcano")],
                     [call("bring_find", pick=shelf.others[1].id)],
                     [call("bring_find", pick="none")],
                     say("None of my finds is on volcanoes."))
    assert _card(withdrawn) is None


def test_a_pick_the_list_never_gave_brings_no_card(turn, shelf):
    done = turn("bring me the bread one", [call("bring_find", pick=shelf.bread.id)], say("Here it is."))
    picked = done.calls()[0]
    assert done.result_of(picked["id"]).startswith("Error:")
    assert _card(done) is None


def test_a_page_read_to_answer_a_question_is_never_offered(turn, shelf):
    read = shelf.library.add(Item(url="https://news.example/bread-prices", site="news.example", kind="article",
                                  title="Bread prices rise again", description="The price of a loaf of bread.",
                                  seen_at=int(time.time()) - 600, via=CHAT, picture="cover.jpg"))
    done = turn("any bread finds?", [call("bring_find", wish="bread")], say(f"{BREAD} is the one."))
    listed = _listed(done)
    assert shelf.bread.id in listed and read.id not in listed


def test_a_reaction_is_kept_only_when_the_model_reacts_to_a_find_this_chat_showed(turn, shelf, app):
    turn("anything about bread?", [call("bring_find", wish="bread")], [call("bring_find", pick=shelf.bread.id)],
         say(f"{BREAD}."))
    asked = len(shelf.events())
    # Asking for another says nothing of the last one: no call to react, and nothing is recorded.
    turn("another one?", [call("bring_find")], [call("bring_find", pick=shelf.others[2].id)], say("Chess, then."))
    assert [e[2] for e in shelf.events()[asked:]] == ["seen"]
    # A find no chat of theirs showed takes no reaction.
    refused = turn("the moss one is boring", [call("react", id=shelf.others[1].id, liked=False, about="thing")],
                   say("Noted."))
    assert refused.result_of(refused.calls()[0]["id"]).startswith("Error:")
    assert not [e for e in shelf.events() if e[1] == shelf.others[1].id]
    turn("the bread one was too long", [call("react", id=shelf.bread.id, liked=False, about="length",
                                             words="too long")], say("A shorter one next time."))
    said = [e for e in shelf.events() if e[1] == shelf.bread.id and e[2] == "skip"]
    assert len(said) == 1 and said[0][5]["said"] == {"about": "length", "words": "too long"}
    # Another chat did not show it.
    other = app.post("/api/chats", {}).json()
    elsewhere = turn("the bread one again: not for me", [call("react", id=shelf.bread.id, liked=False, about="thing")],
                     say("Noted."), conversation=other["id"])
    assert elsewhere.result_of(elsewhere.calls()[0]["id"]).startswith("Error:")


def test_why_a_card_was_shown_reaches_the_next_turn_as_the_list_says_it(turn, shelf, app):
    turn("anything about bread?", [call("bring_find", wish="bread")], [call("bring_find", pick=shelf.bread.id)],
         say(f"{BREAD}: it starts from flour and water."))
    reason = next(card["reason"] for card in app.get("/api/feed").json()["items"] if card["id"] == shelf.bread.id)
    # The reason is what the reply said of it, not a reason the ranking never gave.
    assert "starts from flour and water" in reason
    done = turn("why did you pick that one?", say("Because of the starter."))
    context = done.asked()[0]["messages"][-1]["content"]
    assert shelf.bread.id in context and f"why shown: {reason}" in context


def test_a_wish_kept_for_a_walk_says_walks_are_off_when_they_are(turn, shelf, app, monkeypatch):
    agent = app.workspace.character.agent
    monkeypatch.setattr(agent, "roam", SimpleNamespace(library=shelf.library, request=lambda: roam.WALKS_OFF),
                        raising=False)
    done = turn("yes, go and look for volcano videos later", [call("bring_find", wish="volcano", later=True)],
                say("Walks are off, so it waits."))
    said = done.result_of(done.calls()[0]["id"])
    assert said.startswith(findtool.WALK_SAID[roam.WALKS_OFF]) and findtool.WALK_SAID[""] not in said
    assert _card(done) is None


def test_a_find_on_what_they_asked_is_offered_beside_more_of_the_kind_they_named(turn, shelf):
    videos = [shelf.add(f"Mountain pass, part {n}", site="hills.example", kind="video", length=300, hours_ago=0.5,
                        text="A drive over the mountain pass.") for n in range(6)]
    done = turn("a video about bread?", [call("bring_find", wish="bread", kind="video")], say(f"{BREAD} is close."))
    listed = _listed(done)
    assert shelf.bread.id in listed and sum(video.id in listed for video in videos) == 5

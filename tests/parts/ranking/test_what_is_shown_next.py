"""What the feed brings next, card by card: more of what the person takes to and less of what they turn
down, never a topic they said they do not want while anything else is left, nothing from a site whose find
opened to a sign-in until they are signed in there, a hidden source last, nothing shown twice before what is
new, something on a home that knows nothing yet, and the same order for the same seed."""
from __future__ import annotations

import pytest

from harness.clock import Clock
from harness.finds import Shelf
from mibo import profile
from mibo.panels import feed
from mibo.web import signin

NOW = 1_800_000_000
SEEDS = range(12)


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    return Shelf(tmp_path / "runtime", Clock(monkeypatch, NOW), monkeypatch)


def stock(shelf: Shelf, each: int = 6) -> dict[str, list]:
    """Things on three subjects from three sites each, found over the last few hours."""
    made = {"food": [], "sports": [], "tech": []}
    for n in range(each):
        made["food"].append(shelf.add(f"Braised pork recipe {n}", site=f"kitchen{n % 3}.example",
                                      text="Slow cooking at home in a cast iron pot.", hours_ago=1 + n / 3))
        made["sports"].append(shelf.add(f"Football match recap {n}", site=f"pitch{n % 3}.example",
                                        text="Every goal of the night's football.", hours_ago=1 + n / 3))
        made["tech"].append(shelf.add(f"New laptop review {n}", site=f"gadget{n % 3}.example",
                                      text="A laptop and its battery, tested.", hours_ago=1 + n / 3))
    return made


def share(shelf: Shelf, things: list, first: int = 6) -> float:
    """How much of the first `first` things brought, over many seeds, is among `things`."""
    ids = {item.id for item in things}
    brought = [item.id in ids for seed in SEEDS for item in shelf.ranked(seed=seed)[:first]]
    return sum(brought) / len(brought)


def test_what_they_take_to_comes_first_and_what_they_turn_down_goes_down(shelf):
    made = stock(shelf, each=9)
    before = share(shelf, made["food"]), share(shelf, made["sports"])
    for item in made["food"][:3]:
        shelf.react(item, "seen")
        shelf.react(item, "like")
    for item in made["sports"][:3]:
        shelf.react(item, "seen")
        shelf.react(item, "skip")
    shelf.learn()
    after = share(shelf, made["food"]), share(shelf, made["sports"])
    assert after[0] >= 0.7 and after[0] > before[0] + 0.25, (before, after)
    assert after[1] < before[1] and after[1] <= 0.05, (before, after)


def test_a_simulated_person_is_brought_more_of_what_they_like_card_by_card(shelf):
    """A person with a fixed taste, as tools/recsim simulates one: they read every recipe through, glance at
    a laptop review and close it, and turn down football at once. Card by card, with the model fitted after
    each reaction, the last cards bring more recipes and less football than the first."""
    stock(shelf, each=9)
    brought = []
    for card in range(14):
        item = shelf.ranked(seed=card)[0]
        brought.append(item.title.split()[0])
        shelf.react(item, "seen")
        if item.title.startswith("Braised"):
            shelf.react(item, "open")
            shelf.react(item, "read", surface="browser", play_s=240)
        elif item.title.startswith("New"):
            shelf.react(item, "open")
            shelf.react(item, "read", surface="browser", play_s=2)
        else:
            shelf.react(item, "skip")
            shelf.react(item, "end", end="quick", shown_s=1)
        shelf.learn()
        shelf.clock.move(1800)
    first, last = brought[:5], brought[-5:]
    assert last.count("Braised") > first.count("Braised") and "Football" not in last, brought


def test_a_topic_they_said_they_do_not_want_never_comes_before_anything_else(shelf):
    """Even when it is what their reactions favour most: they took to sport and turn down every laptop
    review, then said they do not want football. While a laptop review is left, football is not brought."""
    games = [shelf.add(f"Basketball finals night {n}", site="pitch0.example", text="The basketball game.")
             for n in range(4)]
    for item in games:
        shelf.react(item, "seen")
        shelf.react(item, "like")
    reviews = [shelf.add(f"New laptop review {n}", site=f"gadget{n}.example", text="A laptop, tested.",
                         hours_ago=30) for n in range(4)]
    for item in reviews[:2]:
        shelf.react(item, "seen")
        shelf.react(item, "skip")
    match = shelf.add("Football final tonight", site="pitch0.example", text="Every goal of the football final.",
                      hours_ago=0.1, love=3)
    shelf.learn()
    assert all(shelf.ranked(seed=seed)[0] == match for seed in SEEDS), "their reactions favour it first"
    profile.dislike(shelf.runtime, "football", "topic", shelf.clock.now, words="I do not want football")
    shelf.learn()
    for seed in SEEDS:
        order = shelf.ranked(seed=seed)
        assert order[-1] == match, [item.title for item in order]


def test_a_site_whose_find_opened_to_a_sign_in_waits_until_they_are_signed_in(shelf):
    made = stock(shelf)
    walled = made["food"][0]
    same_site = [item for item in made["food"] if item.site == walled.site and item is not walled]
    clip = shelf.add("A braised pork recipe, filmed", site=walled.site, kind="video", length=300)
    shelf.react(walled, "seen", surface="chat")
    shelf.react(walled, "open", surface="chat")
    assert feed.walled(shelf.runtime, walled)
    brought = {item.id for item in shelf.ranked()}
    assert walled.id not in brought and not {item.id for item in same_site} & brought
    assert clip.id in brought, "a find of another kind from the site is not held back"
    signin.became(shelf.runtime / "desktop", walled.url, "signin")
    brought = {item.id for item in shelf.ranked()}
    assert {item.id for item in same_site} <= brought and walled.id not in brought


def test_a_source_they_asked_to_see_less_of_comes_last(shelf):
    made = stock(shelf, each=3)
    sent = [shelf.add(f"Braised pork recipe, sent {n}", site=f"wire{n}.example", text="Slow cooking at home.",
                      source="kitchen-wire", hours_ago=0.2) for n in range(3)]
    shelf.react(sent[0], "seen")
    shelf.react(sent[0], "hide_source")
    for seed in SEEDS:
        order = shelf.ranked(seed=seed)
        assert order[-2:] == sent[1:] or order[-2:] == sent[:0:-1], [item.title for item in order]
    assert len(shelf.ranked()) == len(made["food"] + made["sports"] + made["tech"]) + 2


def test_a_thing_shown_once_comes_after_everything_not_shown_yet(shelf):
    stock(shelf, each=3)
    first = shelf.ranked()[0]
    shelf.react(first, "seen")
    rows = feed.ranked(shelf.library, shelf.events(), shelf.clock.now, seed=1)
    assert rows[-1][1] == first and [shaped["seen"] for shaped, _ in rows].count(True) == 1


def test_a_home_that_knows_nothing_yet_still_brings_something(shelf):
    made = stock(shelf, each=3)
    every = {item.id for things in made.values() for item in things}
    assert {item.id for item in shelf.ranked()} == every


def test_the_first_run_offers_samples_from_many_sites_never_two_in_a_row_from_one(shelf):
    for n in range(3):
        for site in ("kitchen", "pitch", "gadget", "harbour", "garden", "studio"):
            shelf.add(f"A story from the {site} number {n}", site=f"{site}.example",
                      text="A summary long enough to say what the story is about, and then some more of it.")
    shown = feed.first_round(shelf.library, [], shelf.clock.now, ["food"])
    sites = [row["site"] for row in shown]
    assert len(shown) == feed.FIRST_MAX and max(sites.count(site) for site in sites) <= feed.FIRST_PER_SOURCE
    assert all(a != b for a, b in zip(sites, sites[1:])), sites


def test_the_same_seed_brings_the_same_order_and_another_seed_may_explore(shelf):
    made = stock(shelf)
    for item in made["food"][:2]:
        shelf.react(item, "seen")
        shelf.react(item, "like")
    shelf.learn()
    orders = [[item.id for item in shelf.ranked(seed=seed)] for seed in SEEDS]
    assert orders == [[item.id for item in shelf.ranked(seed=seed)] for seed in SEEDS]
    assert len({tuple(order) for order in orders}) > 1

"""What a person's reactions teach the taste model: a like and a long stay teach more of the same, a skip
less, a no said in words and a hidden source firmly less, a glance little, and a find that opened to a
sign-in nothing; what was taught is kept on disk and goes when the person starts their taste over."""
from __future__ import annotations

import pytest

from harness.clock import Clock
from harness.finds import Shelf
from mibo import profile, taste
from mibo.panels import feed
from mibo.sources import weights

NOW = 1_800_000_000
# The model's feature for what a thing is about, as the page's leans name it too.
FOOD, SPORTS = "subj:food", "subj:sports"


@pytest.fixture
def person(tmp_path, monkeypatch):
    """`person(name)`: a home of its own, every one on the same clock."""
    clock = Clock(monkeypatch, NOW)
    return lambda name: Shelf(tmp_path / name, clock, monkeypatch)


def recipes(shelf: Shelf, count: int = 3, **made) -> list:
    return [shelf.add(f"Braised pork recipe {n}", site=f"kitchen{n}.example",
                      text="Slow cooking at home in a cast iron pot.", **made) for n in range(count)]


def matches(shelf: Shelf, count: int = 3) -> list:
    return [shelf.add(f"Football match recap {n}", site=f"pitch{n}.example",
                      text="Every goal of the night's football.") for n in range(count)]


def weight(shelf: Shelf, feature: str = FOOD) -> float:
    return shelf.learn()["m"].get(feature, 0.0)


def test_likes_teach_more_of_a_subject_and_skips_less(person):
    shelf = person("one")
    for item in recipes(shelf):
        shelf.react(item, "seen")
        shelf.react(item, "like")
    for item in matches(shelf):
        shelf.react(item, "seen")
        shelf.react(item, "skip")
    shelf.learn()
    taught = taste.taught(shelf.runtime)
    assert FOOD in [row["key"] for row in taught["takes"]]
    assert SPORTS in [row["key"] for row in taught["passes"]]


def test_a_glance_teaches_little_and_a_long_stay_much(person):
    """Opened where the stay is not seen, stayed for a while, read through: each a firmer yes than the one
    before it. Opened in the app's browser and left within seconds: a faint no, fainter than a skip."""
    learned = {}
    for stay in ("glance", "skip", "unseen", "while", "through"):
        shelf = person(stay)
        for item in recipes(shelf):
            shelf.react(item, "seen")
            shelf.react(item, "skip" if stay == "skip" else "open")
            if stay in ("glance", "while", "through"):
                shelf.react(item, "read", play_s={"glance": 3, "while": 40, "through": 300}[stay])
        learned[stay] = weight(shelf)
    assert learned["skip"] < learned["glance"] < 0, learned
    assert 0 < learned["unseen"] < learned["while"] < learned["through"], learned


def test_each_kind_of_no_counts_more_firmly(person):
    """A card left alone its whole time, a plain skip, a no said in words: the same things turned down each
    way, each a firmer no than the one before it."""
    learned = {}
    for no in ("left", "skip", "said"):
        shelf = person(no)
        for item in recipes(shelf):
            shelf.react(item, "seen")
            if no == "left":
                shelf.react(item, "end", end="timeout", shown_s=60)
            else:
                shelf.react(item, "skip", said={"about": "thing", "words": ""} if no == "said" else None)
        learned[no] = weight(shelf)
    assert 0 > learned["left"] > learned["skip"] > learned["said"], learned


def test_a_find_that_opened_to_a_sign_in_teaches_nothing(person):
    """Neither the taste model nor what is learned of the source that brought it: the same as a find that was
    shown and not opened."""
    learned, source = {}, {}
    for ending in ("shown", "read", "walled"):
        shelf = person(ending)
        item = recipes(shelf, 1, source="kitchen-wire")[0]
        shelf.react(item, "seen", surface="chat")
        if ending != "shown":
            shelf.react(item, "open", surface="chat")
            shelf.react(item, "read", surface="browser", play_s=300)
        if ending == "walled":
            assert feed.walled(shelf.runtime, item)
        learned[ending] = shelf.learn()
        source[ending] = weights.load(shelf.runtime)["kitchen-wire"]["points"]
    assert learned["read"]["rows"] == 1 and learned["read"]["m"][FOOD] > 0
    assert learned["walled"]["rows"] == learned["shown"]["rows"] == 0 and FOOD not in learned["walled"]["m"]
    assert source["walled"] == source["shown"] < source["read"], source


def test_what_was_taught_is_kept_and_goes_when_the_person_starts_over(person):
    shelf = person("one")
    for item in recipes(shelf):
        shelf.react(item, "seen")
        shelf.react(item, "like")
    fitted = shelf.learn()
    assert fitted["rows"] == 3 and taste.load(shelf.runtime)["m"] == fitted["m"]
    shelf.clock.move(60)
    profile.reset(shelf.runtime, shelf.clock.now)
    assert taste.load(shelf.runtime)["rows"] == 0
    assert shelf.learn()["rows"] == 0
    assert taste.taught(shelf.runtime) == {"takes": [], "passes": []}

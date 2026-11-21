"""What the character has seen, kept for the home page and the days."""
from __future__ import annotations

import time

from mibo import library as library_module
from mibo import taste
from mibo.library import BROWSED, DISMISSED, KEPT, Item, Library, item_id
from mibo.panels import feed

NOW = 1_800_000_000
DAY = 86400


def test_an_item_is_kept_once_and_the_owner_s_choice_outlives_a_second_visit(tmp_path):
    library = Library(tmp_path)
    first = library.add(Item(url="https://example.test/v/1", site="Example", kind="video", title="One",
                             fits=True, seen_at=NOW))
    assert first.id == item_id("https://example.test/v/1") and library.seen(first.url)
    library.change(first.id, state=KEPT, told=True)
    library.add(Item(url=first.url, site="Example", kind="video", title="One, renamed", seen_at=NOW + 5))
    again = Library(tmp_path).get(first.id)
    assert (again.title, again.state, again.told) == ("One, renamed", KEPT, True)
    assert len(library.items()) == 1


def test_a_card_holds_public_facts_and_no_path(tmp_path):
    library = Library(tmp_path)
    item = library.add(Item(url="https://example.test/v/1", site="Example", kind="video", title="One",
                            author="Kim", length=95, description="  a   short\n description ",
                            embed="https://player.example.test/1", seen_at=NOW))
    assert library.save_picture(item.id, b"x" * 10) == f"{item.id}.jpg"
    library.change(item.id)
    item.picture = f"{item.id}.jpg"
    library.add(item)
    card = library.get(item.id).card()
    assert card == {"kind": "video", "title": "One", "site": "Example", "author": "Kim", "length": 95,
                    "picture": f"/api/library/picture/{item.id}", "embed": "https://player.example.test/1",
                    "url": "https://example.test/v/1", "excerpt": "a short description", "readable": False}
    assert library.picture_path(item.id).read_bytes() == b"x" * 10
    assert library.picture_path("missing") is None
    assert library.save_picture(item.id, b"x" * 300_000) == "", "a cover is kept small"


def test_an_unknown_kind_is_a_page(tmp_path):
    library = Library(tmp_path)
    library.add(Item(url="https://example.test/x", kind="hologram", seen_at=NOW))
    assert [item.kind for item in Library(tmp_path).items()] == ["page"]


def test_the_walk_is_out_then_home_and_counts_a_day(tmp_path):
    library = Library(tmp_path)
    assert library.outing().state == "home"
    library.left("Example", now=NOW)
    library.at("Other")
    out = Library(tmp_path).outing()
    assert (out.state, out.site, out.since, out.today) == ("out", "Other", NOW, 1)
    back = library.returned(2, sites=["Example"], now=NOW + 60)
    assert (back.state, back.last_count, back.last_end) == ("home", 2, NOW + 60)
    assert back.sites == ["Other", "Example"]
    assert library.left(now=NOW + 120).today == 2
    assert library.left(now=NOW + 86400 * 2).today == 1, "a new day starts the count again"


def test_a_find_nobody_kept_or_took_up_retires_with_its_cover_and_the_rest_stay(tmp_path, monkeypatch):
    monkeypatch.setattr(taste, "fit", lambda runtime, soon=False: None)
    library = Library(tmp_path)
    now = int(time.time())

    def find(name: str, days_ago: float, **values) -> Item:
        item = library.add(Item(url=f"https://example.test/{name}", title=name, seen_at=now - int(days_ago * DAY),
                                **values))
        item.picture = library.save_picture(item.id, b"cover")
        return library.change(item.id, picture=item.picture)

    stale, kept, fresh, opened = find("stale", 40), find("kept", 40), find("fresh", 1), find("opened", 40)
    # The person's own reading, which has its own keeping (browsed.py).
    find("read", 40, via=BROWSED)
    library.change(kept.id, state=KEPT)
    feed.remember(tmp_path, opened, "open", now - 2 * DAY)
    library.compact()
    assert sorted(item.title for item in Library(tmp_path).items()) == ["fresh", "kept", "opened", "read"]
    assert not (library.pictures / stale.picture).exists() and (library.pictures / fresh.picture).exists()


def test_past_the_limit_the_dismissed_go_first_then_what_was_shown_and_never_taken_up(tmp_path, monkeypatch):
    library = Library(tmp_path)
    now = int(time.time())
    made = {name: library.add(Item(url=f"https://example.test/{name}", title=name, seen_at=now - n * 60))
            for n, name in enumerate(["unshown", "shown", "dismissed", "kept"])}
    library.change(made["dismissed"].id, state=DISMISSED)
    library.change(made["kept"].id, state=KEPT)
    feed.remember(tmp_path, made["shown"], "seen", now)
    monkeypatch.setattr(library_module, "ITEMS_MAX", 3)
    library.compact()
    assert sorted(item.title for item in library.items()) == ["kept", "shown", "unshown"]
    monkeypatch.setattr(library_module, "ITEMS_MAX", 1)
    library.compact()
    assert [item.title for item in library.items()] == ["kept"], "a kept one never goes"

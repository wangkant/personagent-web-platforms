"""The finds in the runtime database (db.py): the library, the feed's reactions and topics, the showings, the pages
read in the app's browser, the sources' state and the situation. A home that kept them in files moves in once,
the page's threads react at once without losing a reaction, and a backup carries every one of them."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from mibo import browsed, db, situation, taste
from mibo.library import KEPT, Item, Library, item_id
from mibo.panels import feed
from mibo.sources import catalog, weights

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def no_fit(monkeypatch):
    monkeypatch.setattr(taste, "fit", lambda runtime, soon=False: None)


def _lines(path: Path, rows: list, tail: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + tail, encoding="utf-8")


def _home_with_finds_in_files(runtime: Path) -> None:
    """The finds as a home kept them before the database, each file in its own shape."""
    a, b = "https://shore.example/a", "https://shore.example/b"
    ids = [item_id(a), item_id(b)]
    # A change was a second line for the same item; a torn last line is passed over.
    _lines(runtime / "library" / "items.jsonl",
           [{"id": ids[0], "url": a, "title": "A", "seen_at": 10}, {"id": ids[1], "url": b, "title": "B", "seen_at": 20},
            {"id": ids[0], "url": a, "title": "A", "seen_at": 10, "state": KEPT}], tail='{"id": "torn')
    # An event of five is from before events kept the model's snapshot.
    (runtime / "feed.json").write_text(json.dumps({
        "events": [[NOW, ids[0], "seen", "shore.example", "article"],
                   [NOW + 1, ids[0], "like", "shore.example", "article", {"s": "card"}]],
        "topics": ["tides"]}), encoding="utf-8")
    _lines(runtime / "shown.jsonl", [{"at": NOW, "id": ids[0], "gated": False, "surface": "card", "rank": 0}])
    _lines(runtime / "desktop" / "browsed.jsonl",
           [{"url": a, "title": "A", "at": NOW, "s": 40}, {"url": "file:///c/secret", "title": "local", "at": NOW}])
    (runtime / "sources_taste.json").write_text(json.dumps(
        {"sources": {"hn": {"reads": 3, "fails": 1, "points": 1.5}}}), encoding="utf-8")
    (runtime / "sources.json").write_text(json.dumps(
        {"own": [{"key": "own:1", "url": "https://mine.example/feed.xml", "name": "mine"}],
         "last": {"hn": {"at": 5, "count": 7, "error": ""}}}), encoding="utf-8")
    (runtime / "situation.json").write_text(json.dumps(
        {"factors": {"readable": {"by": {"user": ["en", "zh"]}, "at": 1}}, "probed": NOW,
         "hosts": {"slow.example": {"fails": 2, "fail_at": NOW}}}), encoding="utf-8")


def test_a_home_with_finds_in_files_moves_them_in_once_and_the_files_go(tmp_path):
    runtime = tmp_path / "runtime"
    _home_with_finds_in_files(runtime)
    library = Library(runtime)
    assert [(i.title, i.state) for i in library.items(newest_first=False)] == [("A", KEPT), ("B", "")]
    assert [e[:3] for e in feed.events(runtime)] == [[NOW, library.items()[1].id, "seen"],
                                                    [NOW + 1, library.items()[1].id, "like"]]
    assert feed.events(runtime)[0][5] == {} and feed.load_topics(runtime) == ["tides"]
    assert taste.first_shown(runtime, [library.items()[1].id])[library.items()[1].id]["surface"] == "card"
    assert [r["url"] for r in browsed.history(runtime / "desktop")["pages"]] == ["https://shore.example/a"]
    assert weights.row(runtime, "hn") == {**weights._blank(), "reads": 3, "fails": 1, "points": 1.5}
    assert catalog.load_choice(runtime) == {"own": [{"key": "own:1", "url": "https://mine.example/feed.xml",
                                                     "name": "mine"}],
                                            "last": {"hn": {"at": 5, "count": 7, "error": ""}}}
    assert situation.value(runtime, "readable") == ["en", "zh"]
    assert situation.down_hosts(runtime) == ["slow.example"]
    left = sorted(p.relative_to(runtime).as_posix() for p in runtime.rglob("*") if p.is_file())
    assert [f for f in left if not f.startswith("mibo.db")] == [], "the old files are gone"
    # A file of the old kind that turns up later is not moved again over what the home has since.
    (runtime / "feed.json").write_text(json.dumps({"events": [], "topics": ["stale"]}), encoding="utf-8")
    db.close(runtime)
    assert feed.load_topics(runtime) == ["tides"] and not (runtime / "feed.json").exists()


def test_a_finds_file_the_move_cannot_read_is_kept_and_the_rest_still_moves(tmp_path):
    runtime = tmp_path / "runtime"
    _home_with_finds_in_files(runtime)
    (runtime / "feed.json").write_text('{"events": [[1800000000, "ab', encoding="utf-8")
    assert len(Library(runtime).items()) == 2 and feed.events(runtime) == []
    kept = sorted(p.relative_to(runtime).as_posix() for p in runtime.rglob("*.unmoved"))
    assert kept == ["feed.json.unmoved"]
    assert (runtime / "feed.json.unmoved").read_text(encoding="utf-8").startswith('{"events"')


def test_a_move_that_does_not_read_back_is_undone_and_the_files_stay(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    _home_with_finds_in_files(runtime)

    def short(conn, rows):
        raise sqlite3.DatabaseError("the moved rows of library do not read back")

    monkeypatch.setattr(db, "_write_finds", short)
    assert Library(runtime).items() == []
    for name in db.FINDS:
        assert (runtime / name).is_file(), name
    db.close(runtime)
    monkeypatch.undo()
    assert len(Library(runtime).items()) == 2, "the next start moves them"


def test_the_page_threads_react_at_once_and_lose_no_reaction(tmp_path):
    runtime = tmp_path / "runtime"
    library = Library(runtime)
    items = [library.add(Item(url=f"https://shore.example/{n}", source="hn", seen_at=int(NOW))) for n in range(40)]

    def react(part: list[Item]) -> None:
        for item in part:
            assert feed.remember(runtime, item, "like", NOW)

    threads = [threading.Thread(target=react, args=(items[n::4],)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(e[1] for e in feed.events(runtime)) == sorted(i.id for i in items)
    row = weights.row(runtime, "hn")
    assert row["items"] == 40 and row["points"] == pytest.approx(40 * weights.POINTS["like"])


def test_a_backup_carries_every_find_and_a_restore_puts_back_only_those(tmp_path):
    runtime, archive = tmp_path / "runtime", tmp_path / "archive.db"

    def seed(title: str) -> None:
        item = Library(runtime).add(Item(url=f"https://shore.example/{title}", title=title, source=title,
                                         seen_at=int(time.time())))
        feed.remember(runtime, item, "seen", time.time())
        feed.keep_topics(runtime, [title])
        browsed.record(runtime / "desktop", {"url": item.url, "title": title, "s": 5})
        weights.read(runtime, {title: True})
        catalog.change_choice(runtime, lambda c: c["own"].append({"key": title, "url": item.url}))
        situation.set_value(runtime, "region", {"before": "NZ", "after": "IS"}[title])
        situation.record(runtime, f"{title}.example", False)

    def held() -> dict[str, list]:
        return {table: db.at(runtime).read(f"SELECT * FROM {table}") for table in db.BACKED_UP}

    seed("before")
    kept = held()
    assert all(kept[t] for t in ("library", "feed_events", "feed_topics", "shown", "browsed", "source_state",
                                 "own_feeds", "situation", "hosts")), "every kind of find is there to carry"
    assert db.snapshot(runtime, archive)
    seed("after")
    assert held() != kept
    db.take_back(runtime, archive)
    assert held() == kept

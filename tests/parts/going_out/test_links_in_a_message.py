"""Links the person sends are read before the reply: each page's title and summary reaches the model beside its
link, at most three a message, fetched together, and a page that never finishes costs only its own summary,
under one time budget for the whole message. Checked through the page, on the order things happen at the stand-in
site, never on a stopwatch."""
from __future__ import annotations

import gzip
import threading

from harness.model import say
from mibo.ingestion import ContentIngestion


def page(title: str, summary: str = "") -> str:
    described = f'<meta property="og:description" content="{summary}">' if summary else ""
    return (f'<html><head><meta property="og:title" content="{title}">{described}</head>'
            f"<body><p>{title}</p></body></html>")


def told(result) -> str:
    """What the reply model was given as the person's message."""
    return str(result.asked()[0]["messages"][-1]["content"])


def test_each_link_s_page_reaches_the_model_after_the_words_in_link_order(turn, web) -> None:
    base = web.name("shore.example", "93.184.216.34")
    web.site.put("/tides", gzip.compress(page("Tide pools at dawn", "What lives in a tide pool.").encode()),
                 headers={"Content-Encoding": "gzip"})
    web.site.put("/plain", "<html><head><title>Harbour reopens</title></head><body>.</body></html>")
    web.site.put("/blank", "<html><body>nothing to name it by</body></html>")
    words = told(turn(f"first {base}/tides then {base}/plain and {base}/blank", say("Read them.")))
    assert words.find("/blank") < words.find("Tide pools at dawn") < words.find("Harbour reopens")
    assert "What lives in a tide pool." in words
    assert "nothing to name it by" not in words


def test_at_most_three_links_a_message_are_read_counting_every_part(turn, web) -> None:
    base = web.name("shore.example", "93.184.216.34")
    for n in range(5):
        web.site.put(f"/p{n}", page(f"Story number {n}"))
    words = told(turn(" ".join(f"{base}/p{n}" for n in range(5)), say("Three.")))
    # The three are read side by side: which reaches the site first is not the app's to say.
    assert sorted(web.site.log) == ["GET /p0", "GET /p1", "GET /p2"]
    assert all(f"Story number {n}" in words for n in range(3)) and "Story number 3" not in words

    # A page attached from the app's browser is part of the same message: its link waits behind the cap too.
    result = turn(f"{base}/p0 {base}/p1 {base}/p2", say("Still three."),
                  attachments=[{"kind": "page", "url": f"{base}/p4", "title": "Attached"}])
    assert "GET /p4" not in web.site.log and "Story number 4" not in told(result)


def test_the_links_of_a_message_are_fetched_together(turn, web) -> None:
    """Each page answers only once all three are being asked for at the same time."""
    base = web.name("shore.example", "93.184.216.34")
    together = threading.Barrier(3, timeout=10)
    for n in range(3):
        web.site.put(f"/t{n}", page(f"Together {n}"), before=together.wait)
    words = told(turn(" ".join(f"{base}/t{n}" for n in range(3)), say("All three.")))
    assert all(f"Together {n}" in words for n in range(3))


def test_a_page_that_never_finishes_costs_only_its_own_summary(turn, web, monkeypatch) -> None:
    monkeypatch.setattr(ContentIngestion, "LINK_ENRICHMENT_BUDGET_SEC", 0.5)
    base = web.name("shore.example", "93.184.216.34")
    slow = web.site.tarpit("/slow")
    web.site.put("/quick", page("Answered in time"))
    words = told(turn(f"{base}/slow and {base}/quick", say("One of two.")))
    assert "Answered in time" in words
    # The reply did not wait for the slow page: the fetch was let go, and the page saw its asker hang up.
    assert slow.hung_up.wait(10)


def test_one_message_has_one_budget_however_it_is_split(turn, web, monkeypatch) -> None:
    """The typed words and an attached page are two parts of one message; their links are waited for
    together, not one budget after another."""
    monkeypatch.setattr(ContentIngestion, "LINK_ENRICHMENT_BUDGET_SEC", 0.5)
    base = web.name("shore.example", "93.184.216.34")
    first, second = web.site.tarpit("/one"), web.site.tarpit("/two")
    turn(f"look {base}/one", say("Neither."), attachments=[{"kind": "page", "url": f"{base}/two", "title": "Two"}])
    assert first.hung_up.wait(10) and second.hung_up.wait(10)
    starts = [n for n, event in enumerate(web.site.events) if event.startswith("start")]
    hang_ups = [n for n, event in enumerate(web.site.events) if event.startswith("hang-up")]
    assert max(starts) < min(hang_ups), web.site.events

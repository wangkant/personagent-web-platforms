"""The model-use tally the runtime panel shows (usage.UsageBook): each answered request counted under its model,
day and purpose, written at most once a minute off the thread that asked, and a reply through the page
counted as one."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from harness.model import say
from mibo import storage, usage

NOW = 1_700_000_000.0


def test_each_answer_is_counted_under_its_model_day_and_purpose(tmp_path) -> None:
    book = usage.UsageBook(tmp_path / usage.USAGE_FILE)
    # Cached tokens come in DeepSeek's spelling or the OpenAI-style one.
    book.add("primary", {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.002,
                         "prompt_tokens_details": {"cached_tokens": 60}}, NOW, "reply")
    book.add("primary", {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.002,
                         "prompt_cache_hit_tokens": 60}, NOW, "reply")
    # An answer without usage is a call with no tokens; a purpose not on the list is "other".
    book.add("fallback", None, NOW, "made-up")
    book.add("fallback", {}, NOW - 2 * 86400, "gate")
    today = book.days[usage.day_of(NOW)]
    assert today["primary"] == {"calls": 2, "prompt_tokens": 200, "completion_tokens": 40, "cached_tokens": 120,
                                "cost": 0.004, "costed": 2, "purposes": {"reply": 2},
                                "in_by": {"reply": 200}, "out_by": {"reply": 40}}
    assert today["fallback"] == {"calls": 1, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0,
                                 "purposes": {"other": 1}, "in_by": {"other": 0}, "out_by": {"other": 0}}
    shown = usage.summary(book.days, NOW)
    assert (shown["today"]["calls"], shown["today"]["tokens"]) == (3, 240)
    assert abs(shown["today"]["cost"] - 0.004) < 1e-9 and shown["week"]["calls"] == 4
    assert shown["todayPurposes"] == {"reply": 2, "other": 1}
    assert [(m["model"], m["calls"]) for m in shown["models"]] == [("fallback", 2), ("primary", 2)]


def test_the_tally_is_written_at_most_once_a_minute_and_keeps_eight_days(tmp_path) -> None:
    book = usage.UsageBook(tmp_path / usage.USAGE_FILE)
    book.add("m", {"prompt_tokens": 5}, 1000.0)
    assert book.due(1000.0) == 0, "the first count may be written at once"
    book.flush(1000.0)
    book.add("m", {"prompt_tokens": 5}, 1010.0)
    assert book.due(1010.0) == 50.0, "a later one waits for the rest of the minute"
    book.flush(1060.0)
    again = usage.UsageBook(tmp_path / usage.USAGE_FILE)
    assert again.days[usage.day_of(1000.0)]["m"]["calls"] == 2, "a restart goes on from the file"
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert usage.load(tmp_path / "broken.json") == {}, "a damaged file reads as empty"
    long = usage.UsageBook(tmp_path / "long.json")
    for back in range(12):
        long.add("m", {}, 1_000_000.0 + 86400 * back)
    long.flush(2_000_000.0)
    assert len(usage.load(tmp_path / "long.json")) == usage.KEEP_DAYS


def test_a_write_soon_happens_off_the_thread_that_asked(tmp_path, monkeypatch) -> None:
    book = usage.UsageBook(tmp_path / usage.USAGE_FILE)
    writers: list[threading.Thread] = []
    release = threading.Event()
    real = storage.atomic_write_text

    def held(path, text, **kwargs):
        writers.append(threading.current_thread())
        # A slow disk: the write waits until the test lets it go.
        release.wait(5)
        real(path, text, **kwargs)

    monkeypatch.setattr(storage, "atomic_write_text", held)
    book.add("m", {"prompt_tokens": 7}, NOW)
    # Returns while the write is still held: the turn that asked does not wait on the disk.
    book.flush_soon(NOW)
    assert not (tmp_path / usage.USAGE_FILE).exists()
    release.set()
    book.flush(NOW)
    assert writers and writers[0] is not threading.current_thread()
    assert usage.load(tmp_path / usage.USAGE_FILE)[usage.day_of(NOW)]["m"]["prompt_tokens"] == 7


def test_a_flush_waits_for_the_write_in_flight_and_ends_newest(tmp_path, monkeypatch) -> None:
    book = usage.UsageBook(tmp_path / usage.USAGE_FILE)
    started, release, landed = threading.Event(), threading.Event(), threading.Semaphore(0)
    real = storage.atomic_write_text

    def held(path, text, **kwargs):
        # The first write is held on a slow disk until the test lets it go.
        if not started.is_set():
            started.set()
            release.wait(5)
        real(path, text, **kwargs)
        landed.release()

    monkeypatch.setattr(storage, "atomic_write_text", held)
    book.add("m", {"prompt_tokens": 1}, 1000.0)
    book.flush_soon(1000.0)
    assert started.wait(5)
    book.add("m", {"prompt_tokens": 1}, 1001.0)
    # The held write is let go a moment after the flush has begun.
    threading.Timer(0.05, release.set).start()
    book.flush(1001.0)
    assert landed.acquire(timeout=5) and landed.acquire(timeout=5), "both writes landed"
    assert usage.load(tmp_path / usage.USAGE_FILE)[usage.day_of(1000.0)]["m"]["calls"] == 2


def test_a_reply_through_the_page_is_counted_under_reply(app) -> None:
    app.model.script(say("hello"))
    assert app.post("/api/chat", {"text": "hi"}).status_code == 200
    replies = len(app.model.asked("reply"))
    # Closing the app writes the tally out, as the window's close does.
    app.close()
    shown = usage.read_summary(Path(os.environ["AGENT_HOME"]) / "runtime" / usage.USAGE_FILE, time.time())
    assert shown["todayPurposes"].get("reply") == replies >= 1
    assert shown["today"]["tokens"] >= 15 * replies

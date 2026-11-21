"""The token count of the next request (mibo/context.py): the last reply request's reported prompt_tokens as the
truth, the estimate times the model's ratio for what changed since, the ratio kept with the model's record."""
from __future__ import annotations

import json
from types import SimpleNamespace

from mibo import abilities, context

URL, MODEL = "http://127.0.0.1:1/v1/chat/completions", "m"


def _request(*said: str) -> dict:
    return {"model": MODEL, "messages": [{"role": "system", "content": "You are Mibo."}]
            + [{"role": "user", "content": words} for words in said]}


def test_before_any_reply_the_estimate_is_counted_high():
    caller = SimpleNamespace()
    asked = _request("今天有点累，想聊聊天。")
    assert context.count(caller, URL, MODEL, asked) == round(context.RATIO_FIRST * context.estimate(asked))


def test_the_reported_count_is_the_truth_and_only_what_changed_since_is_estimated():
    caller = SimpleNamespace()
    first = _request("hello")
    context.measured(caller, URL, MODEL, first, {"prompt_tokens": 1000})
    assert context.count(caller, URL, MODEL, first) == 1000
    later = _request("hello", "我最近在准备博士申请，想做蛋白质序列建模。" * 20)
    r = context.ratio(caller, URL, MODEL)
    assert context.count(caller, URL, MODEL, later) == round(
        1000 + r * (context.estimate(later) - context.estimate(first)))
    # A shorter request than the anchor counts less than it.
    assert context.count(caller, URL, MODEL, _request()) < 1000


def test_the_ratio_follows_the_endpoint_within_its_bounds():
    caller = SimpleNamespace()
    asked = _request("hello there, how was your day? " * 40)
    guess = context.estimate(asked)
    context.measured(caller, URL, MODEL, asked, {"prompt_tokens": round(guess * 1.1)})
    assert abs(context.ratio(caller, URL, MODEL) - 1.1) < 0.01
    # Each later request moves it part of the way.
    context.measured(caller, URL, MODEL, asked, {"prompt_tokens": round(guess * 1.6)})
    assert 1.1 < context.ratio(caller, URL, MODEL) < 1.6
    for _ in range(50):
        context.measured(caller, URL, MODEL, asked, {"prompt_tokens": round(guess * 9)})
    assert context.ratio(caller, URL, MODEL) == context.RATIO_HIGH
    for _ in range(50):
        context.measured(caller, URL, MODEL, asked, {"prompt_tokens": 1})
    assert context.ratio(caller, URL, MODEL) == context.RATIO_LOW


def test_a_request_with_a_picture_moves_the_anchor_and_not_the_ratio():
    caller = SimpleNamespace()
    pictured = {"messages": [{"role": "user", "content": [{"type": "text", "text": "what is this?"},
                                                          {"type": "image_url", "image_url": {"url": "data:,"}}]}]}
    context.measured(caller, URL, MODEL, pictured, {"prompt_tokens": 90})
    assert context.ratio(caller, URL, MODEL) == context.RATIO_FIRST
    assert context.count(caller, URL, MODEL, pictured) == 90


def test_the_ratio_and_an_overflows_window_are_kept_with_the_models_record(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / abilities.FILE).write_text(json.dumps({"models": {abilities.key_of(URL, MODEL): {
        "url": URL, "model": MODEL, "context": 65536}}}), encoding="utf-8")
    caller = SimpleNamespace(memory_file=runtime / "memory.json")
    asked = _request("hello there " * 100)
    context.measured(caller, URL, MODEL, asked, {"prompt_tokens": round(context.estimate(asked) * 1.3)})
    context.overflowed(caller, URL, MODEL, "This model's maximum context length is 16384 tokens.")
    # Another process, or this one after a restart, reads both from the record.
    again = SimpleNamespace(memory_file=runtime / "memory.json")
    assert abs(context.ratio(again, URL, MODEL) - 1.3) < 0.01
    assert context.known_window(again, URL, MODEL) == (16384, "error")
    assert context.known_window(again, URL, MODEL, setting=40000) == (40000, "setting")

"""The pieces of a compaction (mibo/context.py): where the verbatim tail starts, what a refused request teaches the
counter, and a cut that keeps a message's head and tail."""
from __future__ import annotations

from types import SimpleNamespace

from mibo import context

URL, MODEL = "http://127.0.0.1:1/v1/chat/completions", "m"


def _turns(*sizes: int) -> list[dict]:
    return [{"role": ("user", "assistant")[n % 2], "content": "字" * size} for n, size in enumerate(sizes)]


def _tokens(rows: list[dict]) -> float:
    return sum(context.message_tokens(r) for r in rows)


def test_the_tail_is_the_newest_whole_turns_within_its_tokens():
    rows = _turns(100, 100, 100, 100, 100, 100)
    start = context.tail_from(rows, _tokens(rows[2:]) - 1)
    # Two whole pairs pass the share by one token: the tail is the newest pair, starting at its question.
    assert start == 4 and rows[start]["role"] == "user"
    assert context.tail_from(rows, _tokens(rows)) == 0


def test_the_newest_question_and_its_answer_stay_whatever_their_size():
    rows = _turns(10, 10, 5000, 5000)
    assert context.tail_from(rows, 1) == 2


def test_the_tail_never_reaches_past_the_checkpoint():
    rows = [{"role": context.CHECKPOINT, "content": "earlier"}, *_turns(10, 10, 10, 10)]
    assert context.tail_from(rows, 10_000) == 1


def test_a_refused_request_counts_at_least_what_the_window_left_from_then_on():
    caller = SimpleNamespace()
    asked = {"messages": [{"role": "user", "content": "你好" * 100}]}
    context.refused(caller, URL, MODEL, asked, 9000)
    assert context.count(caller, URL, MODEL, asked) == 9000
    shorter = {"messages": [{"role": "user", "content": "你好"}]}
    assert context.count(caller, URL, MODEL, shorter) < 9000


def test_a_cut_keeps_the_head_and_the_tail_within_its_tokens():
    words = "开头" + "中" * 5000 + "结尾"
    cut = context.cut(words, 500, "[{n} left out]")
    assert cut.startswith("开头") and cut.endswith("结尾") and context.text_tokens(cut) < 600

"""The main chat's request budget (mibo/context.py): from the measured window and the thinking level, the answer's
room, the safety margin, the input limit and the shares, as context-design.json's table states them, and a window
from the sources in their order."""
from __future__ import annotations

import pytest

from mibo import context

# The design's fixed part: the system prompt, and the tool definitions up front (all of them at 64k and up, the
# loader alone at 8k).
SYSTEM, LOADER, ALL_TOOLS = 2800, 600, 4975


@pytest.mark.parametrize("window, level, fixed, expected", [
    # W, level, system + tools: O, S, L, A, C, h, T
    (8192, "", SYSTEM + LOADER, (1024, 256, 6912, 3512, 527, 1580, 5875)),
    (8192, "standard", SYSTEM + LOADER, (2048, 256, 5888, 2488, 373, 1120, 5005)),
    (32768, "", SYSTEM + ALL_TOOLS, (4096, 1024, 27648, 19873, 2000, 8943, 23501)),
    (32768, "standard", SYSTEM + ALL_TOOLS, (8192, 1024, 23552, 15777, 2000, 7100, 20019)),
    (65536, "", SYSTEM + ALL_TOOLS, (8192, 2048, 55296, 47521, 2000, 16384, 47002)),
    (65536, "standard", SYSTEM + ALL_TOOLS, (12288, 2048, 51200, 43425, 2000, 16384, 43520)),
    (1_048_576, "", SYSTEM + ALL_TOOLS, (32768, 32768, 983040, 975265, 2000, 16384, 100_000)),
    (1_048_576, "standard", SYSTEM + ALL_TOOLS, (36864, 32768, 978944, 971169, 2000, 16384, 100_000)),
])
def test_each_window_gets_the_shares_the_design_states(window, level, fixed, expected):
    b = context.budget(window, level, fixed)
    assert (b.answer, b.safety, b.limit, b.available, b.turn, b.history, b.ceiling) == expected


@pytest.mark.parametrize("window", [8192, 16384, 32768, 65536, 131072, 200_000, 1_048_576])
@pytest.mark.parametrize("level", ["", "standard", "deep"])
def test_the_answer_and_the_largest_request_together_fit_the_window(window, level):
    b = context.budget(window, level)
    assert b.answer + b.safety + b.limit == window
    # Most of the window stays for the request: the answer takes at most a quarter of it.
    assert 0 < b.answer <= window // 4 < b.limit


def test_a_reply_cut_off_asks_four_times_the_room_within_what_the_window_leaves():
    b = context.budget(16384)
    assert b.retry(prompt=1000) == 4 * b.answer
    assert b.retry(prompt=12000) == 16384 - 12000


def test_the_window_comes_from_the_setting_then_the_host_then_an_overflow_then_an_assumption():
    assert context.window({"context": 1_048_576, "context_error": 163_840}, setting=65536) == (65536, "setting")
    assert context.window({"context": 1_048_576}) == (1_048_576, "host")
    assert context.window({"context_error": 16384}) == (16384, "error")
    assert context.window(None) == (context.ASSUMED, "assumed") == (32768, "assumed")
    # A server that refused at a smaller number than its host lists: the refusal is the truth.
    assert context.window({"context": 1_048_576, "context_error": 163_840}) == (163_840, "error")


@pytest.mark.parametrize("said, window", [
    ("This model's maximum context length is 16384 tokens. However, you requested 18432 tokens (10240 in the "
     "messages, 8192 in the completion).", 16384),
    ("This endpoint's maximum context length is 163,840 tokens. However, you requested about 200000 tokens", 163840),
    ("request (9000 tokens) exceeds the available context size (8192 tokens), try increasing it", 8192),
    ("the request exceeds the available context size, try increasing it", 0),
    ("prompt is too long", 0),
])
def test_the_window_an_overflow_error_states_is_read_from_its_words(said, window):
    assert context.stated_window(said) == window

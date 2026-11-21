"""Dismissing a cookie banner with its most private choice is part of any errand (mibo/review.py, mibo/web/gate.py):
the press on "仅必要 Cookie" or "Reject all cookies" goes ahead with no card and no model call, whatever the person
asked for; accepting all, a bare "拒绝", such a press that carries typed words, or one that sends to an address
that is no consent address goes to the reviewer as before.
(2026-11-21, T09: the banner covered the like the person asked for, and the reviewer turned 仅必要 Cookie down as
not what they asked, so the errand ended.)"""
from __future__ import annotations

import asyncio
import time

import pytest

from mibo import review

ASKED = [{"text": "给这篇文章点个赞", "at": time.time()}]  # copy: voice
PRESS = {"kind": "press", "where": "news.example.com", "text": "",
         "request": {"method": "POST", "address": "news.example.com/api/consent", "values": ["necessary"],
                     "body_chars": 16}}


def decided(**action) -> tuple[review.Verdict, int]:
    calls = []

    async def ask(system, messages):
        calls.append(messages)
        return '{"kind_asked": false, "target_named": true, "text_given": true, "nothing_added": true, ' \
               '"decision": "refuse", "reason": "stand-in"}'
    verdict = asyncio.run(review.decide("browser", ASKED, {**PRESS, **action}, ask=ask, attended=True,
                                        tally=review.Tally()))
    return verdict, len(calls)


@pytest.mark.parametrize("button", ["仅必要 Cookie", "只接受必要的 Cookie", "拒绝全部 Cookie", "Reject all cookies",
                                    "Necessary cookies only", "Only necessary"])
def test_the_most_private_choice_on_a_cookie_banner_goes_ahead_with_no_card_and_no_model_call(button):
    verdict, calls = decided(page_says=button)
    assert (verdict.decision, calls) == ("go", 0)


@pytest.mark.parametrize("button", ["接受全部", "Accept all cookies", "拒绝", "Reject all", "关注"])
def test_any_other_choice_goes_to_the_reviewer(button):
    verdict, calls = decided(page_says=button)
    assert (verdict.decision, calls) == ("refuse", 1)


def test_a_private_choice_that_carries_typed_words_goes_to_the_reviewer():
    verdict, calls = decided(page_says="仅必要 Cookie", text="好的谢谢")  # copy: voice
    assert (verdict.decision, calls) == ("refuse", 1)


def test_a_control_named_as_the_private_choice_that_sends_elsewhere_goes_to_the_reviewer():
    verdict, calls = decided(page_says="Reject all cookies", request={
        "method": "POST", "address": "shop.example.com/checkout/submit", "values": ["2", "express"], "body_chars": 40})
    assert (verdict.decision, calls) == ("refuse", 1)


def test_a_private_choice_that_sends_nothing_goes_ahead():
    verdict, calls = decided(page_says="仅必要 Cookie", request=None)
    assert (verdict.decision, calls) == ("go", 0)


def test_away_from_the_app_even_the_private_choice_waits_for_the_person():
    verdict = asyncio.run(review.decide("browser", ASKED, {**PRESS, "page_says": "仅必要 Cookie"},
                                        ask=None, attended=False, tally=review.Tally()))
    assert verdict.decision == "ask"

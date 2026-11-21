"""A card an errand left waiting may be answered by the person's own words in the chat beside the page
(panels/browser.py, loop.py), except paying, deleting, the account and access: those are answered on the card
alone, and words leave the card waiting and nothing kept."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mibo import acting
from mibo.panels.browser import Operating
from mibo.web import operate


class Pane:
    def __init__(self):
        self.op, self.kept = {"held": ""}, []

    def op_card(self, card, text):
        pass

    def op_answered(self, state, text):
        pass

    def op_kept(self, yes):
        self.kept.append(yes)


def left_waiting(kind: str, monkeypatch, tmp_path) -> tuple[Operating, dict]:
    monkeypatch.setattr(operate, "CARD_S", 0.3)
    run = Operating(SimpleNamespace(pane=Pane(), attended=lambda: True), tmp_path, None)
    card = {"id": f"c-{kind}", "kind": kind, "site": "creator.example.com", "to": "作品《周末》", "text": "",
            "button": "好的", "skippable": False, "act": ("card", kind, "creator.example.com", "好的", "", "")}
    assert "waiting" in asyncio.run(run.ask(card))
    return run, card


@pytest.mark.parametrize("kind", sorted(acting.HARD))
def test_a_floor_card_left_waiting_is_never_answered_by_words_in_the_chat(kind, monkeypatch, tmp_path):
    run, card = left_waiting(kind, monkeypatch, tmp_path)
    assert not run.keep(True, said=True)
    assert run.left["id"] == card["id"] and run.kept == {}
    # On the card itself it is answered.
    assert run.answer_card(card["id"], True, False, [])
    assert run.kept == {card["act"]: {"yes": True}}


def test_a_reply_card_left_waiting_is_answered_by_words_in_the_chat(monkeypatch, tmp_path):
    run, card = left_waiting("comment", monkeypatch, tmp_path)
    assert run.keep(False, said=True)
    assert run.left is None and run.kept == {card["act"]: {"yes": False}}

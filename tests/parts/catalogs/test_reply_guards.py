"""A reply is sent as written, and a message that is only pictures nobody could read is told apart from words."""
from __future__ import annotations

import pytest

from mibo.textproc import TextProcessing as TP


@pytest.mark.parametrize("text", [
    "3. Oktober ist Feiertag\nda hab ich frei", "3. Oktober ist Feiertag", "na klar\n24. Dezember bin ich bei meinen Eltern",
    "1. a\n2. b", "three things\n1. rice\n2. eggs\n3. tea",
])
def test_a_date_and_a_numbered_list_are_sent_as_written(text: str) -> None:
    assert TP._sanitize_reply(text) == text


@pytest.mark.parametrize("text", ["[image]", "[image] [image]", " [image]\n"])
def test_a_message_of_unread_pictures_only_has_nothing_to_go_on(text: str) -> None:
    assert TP._is_blind_content(text)


@pytest.mark.parametrize("text", [
    "[image] what is this", "I sent a photo yesterday", "\x02[file: deck.pdf, its text]\nslides\x03",
    "\x02[image: a cat on a sofa]\x03",
])
def test_a_message_with_words_has_something_to_go_on(text: str) -> None:
    assert not TP._is_blind_content(text)

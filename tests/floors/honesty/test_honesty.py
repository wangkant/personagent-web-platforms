"""Honesty: no character is told to deny being an AI, every chat prompt tells it to answer honestly when sincerely
asked, the crisis exception and the companionship rules hold under every style, and an admission is said."""
from __future__ import annotations

import itertools
import re

from harness.model import say
from mibo.prompts import (
    _HONEST_ANSWER,
    OWNER_TOOL_GUIDE,
    STYLE_KNOBS,
    PersonaStyle,
    dm_honest_disclosure,
    dm_output_protocol,
    dm_style_guide,
)

# Instructions that tell a character to conceal or deny what it is: a family, so a rewording is caught too.
DENIAL = [
    r"don'?t reveal (that )?you'?re an ai",
    r"never reveal (that )?you'?re an ai",
    r"don'?t (admit|say|tell them|let on)\b.{0,20}\byou'?re an ai",
    r"deny (being|that you are) an ai",
    r"pretend (to be|you'?re) (a )?human",
    r"don'?t mention your model",
    # Mibo's own persona says it never claims to be human: that is the honest side.
    r"(?<!never )claim (to be|you'?re) (a )?(human|person|real)",
]
ADMISSION = "yeah, I'm an AI, a language model. does that change how this feels for you?"


def _every_style() -> list[PersonaStyle]:
    knobs = sorted(STYLE_KNOBS)
    return [PersonaStyle(**dict(zip(knobs, values))) for values in itertools.product(*(STYLE_KNOBS[k] for k in knobs))]


def _served(turn, said: str = "are you an ai?") -> tuple[str, str]:
    """(the system prompt a chat turn sends, the reply said)."""
    done = turn(said, say(ADMISSION))
    return done.asked()[0]["messages"][0]["content"], done.reply


def test_the_chat_prompt_never_instructs_denial_and_an_admission_is_said(turn):
    system, reply = _served(turn)
    assert len(system) > 1000
    found = [system[max(0, m.start() - 80):m.end() + 40] for p in DENIAL for m in re.finditer(p, system, re.IGNORECASE)]
    assert found == []
    assert reply == ADMISSION


def test_the_prompt_carries_the_honest_answer_after_the_persona(turn):
    clause = _HONEST_ANSWER.lower()
    assert re.search(r"asks?\b[^.]{0,60}\bwhether you are an ai", clause)
    assert re.search(r"answer\s+honestly", clause) and re.search(r"you are one|you are an ai", clause)
    assert "your own voice" in clause
    system, _ = _served(turn)
    assert system.count(_HONEST_ANSWER) == 1
    assert system.index(_HONEST_ANSWER) > system.index("Your name is Mibo.")
    assert "it is scripted, the one set in this app's settings" in system
    assert "it is some-model" in dm_honest_disclosure("some-model")


def test_the_safety_exception_reaches_the_prompt(turn):
    system, _ = _served(turn, "rough day")
    lines = [line for line in system.splitlines() if "SAFETY EXCEPTION" in line]
    assert len(lines) == 1 and "venting" in lines[0] and "crisis line" in lines[0]
    assert "self-harm" in lines[0].lower() or "suicide" in lines[0].lower()


def test_the_companionship_register_survives_every_knob_combination():
    styles = _every_style()
    assert len(styles) == 216 and len(set(map(repr, styles))) == 216
    anchors = (
        "[WHAT THEY ASK FOR, YOU DO]", "Capable, in your own voice", "never a reason to do less",
        "Length and form follow what was asked", "Read what they actually said", "is their answer: act on it",
        "Never read a mood into them", "Don't summarize their message back at them",
        "make it with schedule_create in that same reply",
        "Don't end every turn with a question", "is a whole reply", "Never win at their expense", "Say the thing",
        "receive it, do not deflect", "Do not overcorrect into devotion",
        "read it out, describe it and answer from it",
        "is only what <your_day> in the turn context holds",
        "[CONTINUITY", "briefly, once", "Never announce that you remembered",
    )
    missing, unsafe = {}, []
    for style in styles:
        assembled = "\n\n".join((dm_style_guide(style), OWNER_TOOL_GUIDE, dm_output_protocol()))
        for anchor in anchors:
            if anchor not in assembled:
                missing.setdefault(anchor, repr(style))
        if not ("SAFETY EXCEPTION" in assembled and "crisis line" in assembled):
            unsafe.append(repr(style))
    assert missing == {} and unsafe == []
    guide = dm_style_guide(PersonaStyle())
    assert guide.index("Light teasing only") < guide.index("Never win at") < guide.index("[CONTINUITY")
    assert guide.index("[CONTINUITY") > guide.index("[DON'T FAKE KNOWING")

"""A turn of the person's chat, whole, through the page: what the model is sent, what comes back and is kept,
what a failure leaves, and the lines the app adds to the turn."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import time
from pathlib import Path

import pytest

from harness.app import App, quiet_start
from harness.model import call, fail, say, think
from harness.turn import run_turn
from mibo import abilities, decision, dm, loop, schedule_tool, turnctx
from mibo.endpoints import chat_completions_url
from mibo.prompts import PersonaStyle, dm_style_guide
from mibo.textproc import ReplyStyle

DAY = 86400.0


class _Later:
    """The time module, `ahead` seconds on: what the chat stamps on the lines it keeps."""

    def __init__(self, ahead: float) -> None:
        self.ahead = ahead

    def time(self) -> float:
        return time.time() + self.ahead

    def __getattr__(self, name: str):
        return getattr(time, name)


def _abilities(app, **rec) -> None:
    """The model's ability record, as the start's measure writes it."""
    url = chat_completions_url(app.model.url)
    path = app.home / "runtime" / abilities.FILE
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data.setdefault("models", {})[abilities.key_of(url, "scripted")] = rec
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _context(body: dict) -> str:
    """The last message's text: the person's words and the turn's context after them."""
    return body["messages"][-1]["content"]


def test_a_hello_is_one_plain_request_and_its_answer_is_said(turn):
    done = turn("hello", say("hey, you."))
    (body,) = done.asked()
    assert body["tool_choice"] == "auto" and body["tools"] and "response_format" not in body
    assert body["max_tokens"] >= 8192
    assert (done.status, done.reply) == (200, "hey, you.")
    # A model with no reasoning on record is not asked to reason.
    assert "reasoning_effort" not in body and (body.get("thinking") or {}).get("type") != "enabled"


def test_turns_go_as_role_and_content_and_a_pause_goes_in_the_turn_context(turn, monkeypatch):
    turn("see you tomorrow", say("bye"))
    monkeypatch.setattr(dm, "time", _Later(2 * DAY))
    done = turn("back again", say("welcome back"))
    messages = done.asked()[0]["messages"]
    assert all(set(m) == {"role", "content"} for m in messages)
    assert "The message before this one came 2 days earlier." in _context(done.asked()[0])
    assert not any("earlier" in m["content"] for m in messages[1:-1])


def test_a_short_pause_is_not_said(turn):
    turn("one", say("ok"))
    done = turn("two", say("ok"))
    assert "[Pause]" not in _context(done.asked()[0])


def test_a_picture_it_cannot_see_is_named_as_unseen(turn):
    bare = turn("[image]", say("I can't see that one; can you type what it shows?"))
    notes = [line for line in _context(bare.asked()[0]).splitlines() if "**can't see**" in line]
    assert len(notes) == 1 and "PASS" not in notes[0]
    asked = turn("what is this [image]", say("I can't see the picture."))
    assert "a picture ([image]) that you can't see" in _context(asked.asked()[0])
    assert "a bare [image]" in asked.asked()[0]["messages"][0]["content"]


def test_a_model_measured_to_think_is_asked_to_at_the_standard_level_and_its_reasoning_goes_back(app, turn):
    _abilities(app, thinking="yes", context=400_000, thinking_detail={"param": "reasoning_effort", "reasons": True})
    done = turn("what is 17 times 23?", [think("17 x 23 = 391"), say("391.")])
    (body,) = done.asked()
    assert body["reasoning_effort"] == "medium"
    # Room for the answer from the context window (an eighth, at most 32768), and the thinking on top.
    assert body["max_tokens"] == 32768 + abilities.LEVELS["standard"][1]
    assert done.reply == "391."
    abilities.set_effort(app.home / "runtime", "off")
    off = turn("and 18 times 23?", say("414."))
    assert "reasoning_effort" not in off.asked()[0]


def test_what_was_said_during_an_outage_waits_whole_for_the_next_turn(turn):
    lines = [f"line {n} of what happened" for n in range(5)]
    failed = [turn(line, fail(500)) for line in lines]
    assert [t.status for t in failed] == [502] * 5 and all(t.reply == "" for t in failed)
    done = turn("hello?", say("sorry, I'm back"))
    assert "\n".join([*lines, "hello?"]) in _context(done.asked()[0])
    assert done.reply == "sorry, I'm back"


def test_an_answer_with_no_words_is_asked_again_without_tools_and_the_turn_ends_in_words(turn):
    done = turn("hello", say(""), say("Hello again."))
    first, second = done.asked()
    assert first["tools"] and "tools" not in second
    assert (done.status, done.reply) == (200, "Hello again.")
    # No words twice: the failure line, and their words wait for the next turn.
    silent = turn("are you there?", say(""), say(""))
    assert (silent.status, silent.reply) == (502, "")
    assert "are you there?\nstill there?" in _context(turn("still there?", say("Yes.")).asked()[0])


@pytest.fixture
def hurried(model, monkeypatch, tmp_path):
    """The app with one second for a model answer and the least tool time two rounds need, where schedule_edit
    stands in for a command that runs past the turn's whole time."""
    quiet_start(monkeypatch)
    monkeypatch.setattr(loop, "WITHIN_S", 6.0)
    real = schedule_tool.use

    async def use(agent, called, lookups):
        if called["name"] == "schedule_edit":
            await asyncio.Event().wait()
        return await real(agent, called, lookups)

    monkeypatch.setattr(schedule_tool, "use", use)
    served = App(Path(os.environ["AGENT_HOME"]), model, PERSONA_BROWSER_PATH=str(tmp_path / "no-browser"),
                 LLM_MAX_RETRIES="0", LLM_TIMEOUT_S="1").open()
    yield served
    served.close()


def test_out_of_time_with_nothing_done_the_failure_line_is_said_and_the_words_wait(hurried):
    late = run_turn(hurried, "pause my morning task", call("schedule_edit", id="t1", action="pause"))
    assert (late.status, late.reply) == (502, "")
    # The chat is free again, and the words come with the next turn.
    done = run_turn(hurried, "hello?", say("Sorry, that took too long."))
    assert "pause my morning task\nhello?" in _context(done.asked()[0])
    assert done.reply == "Sorry, that took too long."


def test_out_of_time_after_calls_were_made_the_reply_says_what_was_done(hurried):
    done = run_turn(hurried, "show my tasks, then pause the morning one", call("schedule_list"),
                    call("schedule_edit", id="t1", action="pause"), say("I listed them; the pause did not finish."))
    assert (done.status, done.reply) == (200, "I listed them; the pause did not finish.")
    last = done.asked()[-1]
    assert "tools" not in last and "schedule_list" in _context(last)


def test_past_sixty_turns_forty_are_kept_and_the_dropped_ones_wait_for_the_fold(app, turn):
    # The fold fails once and waits ten minutes: nothing of the chat is folded meanwhile.
    app.model.script(fail(500), purpose="fold")
    for n in range(29):
        turn(f"turn {n}", say(f"ok {n}"))
    full = turn("turn 29", say("ok 29"))
    # The system prompt and 59 turns, the new one among them.
    assert len(full.asked()[0]["messages"]) == 1 + 59
    trimmed = turn("one more", say("noted"))
    messages = trimmed.asked()[0]["messages"]
    assert len(messages) == 1 + 40 and messages[1]["content"] == "ok 10"
    # The 21 turns the trim dropped were never folded: they wait in the spill, kept across a restart.
    kept = app.home / "runtime" / "recent.json"
    spill: list = []
    for _ in range(200):
        convs = json.loads(kept.read_text(encoding="utf-8"))["convs"] if kept.is_file() else {}
        spill = next((c.get("dm_spill") for c in convs.values() if c.get("dm_spill")), [])
        if spill:
            break
        time.sleep(0.05)
    assert [row["content"] for row in spill][:2] == ["turn 0", "ok 0"] and len(spill) == 21


def test_no_card_comes_before_a_reply(app, turn):
    done = turn("can you help me write a cover letter?", say("Sure. Who is it for?"))
    assert done.reply == "Sure. Who is it for?" and not done.cards
    assert all("skill_offer" not in entry for entry in done.entries)
    assert app.post("/api/skills/offer", {}).status_code == 404


def test_a_line_a_module_adds_reaches_the_turn_context(turn, monkeypatch):
    monkeypatch.setattr(turnctx, "_PROVIDERS", [])
    turnctx.register(lambda said, chat: [f"[Weather] Rain all day where they are ({len(said)} characters said)."])
    done = turn("hi", say("hi"))
    assert _context(done.asked()[0]).endswith("[Weather] Rain all day where they are (2 characters said).")


@pytest.mark.parametrize("raw,is_pass", [
    ("PASS", True), ("pass.", True), (" PASS ", True), ("**PASS**", True),
    ("Pass auf dich auf", False), ("pass lol, my voice is cursed", False), ("Pass, not for me", False),
    ("pass the salt", False),
])
def test_only_the_bare_word_pass_lets_a_follow_up_say_nothing(raw, is_pass):
    assert decision.is_pass_sentinel(raw) is is_pass


def test_a_cards_emoji_choice_and_length_reach_the_guide():
    style = PersonaStyle()
    assert "- No emoji, no kaomoji" in dm_style_guide(style, ReplyStyle())
    assert "stay under" not in dm_style_guide(style, ReplyStyle())
    emoji = dm_style_guide(style, ReplyStyle(allow_emoji=True))
    assert "- Emoji only as this character uses them" in emoji and "- No emoji" not in emoji
    short = dm_style_guide(style, dataclasses.replace(ReplyStyle(), max_chars=120))
    assert ("In small talk stay under 120 characters; code, lists and the answer to a real question are not "
            "counted") in short

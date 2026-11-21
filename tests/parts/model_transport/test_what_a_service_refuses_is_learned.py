"""What stays above the SDK because no library does it (mibo/llm.py): a service's 400 for a field is learned and the
request resent in the spelling it takes, from then on; an answer cut off at its token cap, or left whole in hidden
reasoning, is asked again with thinking off and then with more room. Through the page and the reviewer's own call,
against the scripted model."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from harness.app import KEY, App, quiet_start
from harness.model import MODEL, fail, say, text
from harness.turn import run_turn
from mibo import review

VERDICT = {"kind_asked": True, "target_named": True, "text_given": True, "nothing_added": True, "decision": "go",
           "reason": "They asked for exactly this."}
ACTION = {"area": "commands", "kind": "command", "where": "C:\\Users\\li\\Desktop", "text": "Get-ChildItem"}


def reviewed(model, *rounds, json_mode: bool = False, times: int = 1) -> list[review.Verdict]:
    """The reviewer asked `times` times through one ask function, its rounds scripted first."""
    model.script(*rounds, purpose="reviewer")
    ask = review.endpoint(model.url, KEY, MODEL, json_mode=json_mode)

    async def run() -> list[review.Verdict]:
        return [await review.review(["list my desktop"], ACTION, ask=ask, lang="en") for _ in range(times)]
    return asyncio.run(run())


def test_the_startup_probe_learns_that_a_service_refuses_thinking_and_replies_go_without_it(model, monkeypatch,
                                                                                          tmp_path):
    # The probe at start is answered first by a refusal of the switch, then without it.
    model.script(fail(400, {"error": {"message": "Unrecognized request argument supplied: thinking"}}),
                 text("ok"), purpose="probe")
    quiet_start(monkeypatch)
    served = App(Path(os.environ["AGENT_HOME"]), model, PERSONA_BROWSER_PATH=str(tmp_path / "no-browser"),
                 LLM_MAX_RETRIES="0").open()
    try:
        waited = time.monotonic() + 10
        while len(model.asked("probe")) < 2 and time.monotonic() < waited:
            time.sleep(0.05)
        first, again = model.asked("probe")
        assert "thinking" in first["body"] and "thinking" not in again["body"]
        done = run_turn(served, "hello", say("Hello."))
        assert done.reply == "Hello." and "thinking" not in done.asked()[0]
    finally:
        served.close()


def test_a_reasoning_model_is_asked_in_the_spelling_it_takes_from_then_on(model):
    no_max_tokens = {"error": {"message": "Unsupported parameter: 'max_tokens' is not supported with this model. "
                                          "Use 'max_completion_tokens' instead."}}
    no_temperature = {"error": {"message": "Unsupported value: 'temperature' does not support 0 with this model. "
                                           "Only the default (1) value is supported."}}
    verdicts = reviewed(model, fail(400, no_max_tokens), fail(400, no_temperature), text(json.dumps(VERDICT)),
                        text(json.dumps(VERDICT)), times=2)
    assert [v.decision for v in verdicts] == ["go", "go"]
    sent = [r["body"] for r in model.asked("reviewer")]
    assert "max_tokens" in sent[0] and "temperature" in sent[1] and "max_completion_tokens" in sent[1]
    # The fourth request, the second review, is spelled right the first time.
    assert len(sent) == 4 and "max_tokens" not in sent[3] and "temperature" not in sent[3]


def test_an_inner_answer_cut_off_mid_json_is_asked_again_with_four_times_the_room(model):
    cut = {"content": '{"kind_asked": true, "decision": "g', "finish": "length"}
    (verdict,) = reviewed(model, cut, text(json.dumps(VERDICT)), json_mode=True)
    assert verdict.decision == "go"
    first, again = [r["body"] for r in model.asked("reviewer")]
    assert first["response_format"] == {"type": "json_object"} and again["max_tokens"] == 4 * first["max_tokens"]


def test_a_plain_reply_cut_off_or_left_in_its_reasoning_is_asked_again_with_thinking_off(turn):
    for unsaid in ({"content": "", "finish": "length"}, {"content": "", "think": "The answer is hello."}):
        # No words from the round with tools: the reply is asked for without them (loop._plain_reply).
        done = turn("hello", say(""), unsaid, say("Hello."))
        tools_round, plain, again = done.asked()
        assert tools_round["tools"] and "thinking" not in plain and again["thinking"] == {"type": "disabled"}
        assert done.reply == "Hello." and again["max_tokens"] == plain["max_tokens"]


def test_a_plain_reply_cut_off_with_thinking_off_too_gets_more_room_once(turn):
    cut = {"content": "", "finish": "length"}
    done = turn("hello", say(""), cut, cut, say("Hello."))
    plain, off, roomier = done.asked()[1:]
    assert done.reply == "Hello." and roomier["max_tokens"] > off["max_tokens"] == plain["max_tokens"]

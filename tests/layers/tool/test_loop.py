"""The reply model's tool loop, through whole turns: what the tools did and what reached the model, the loop's own
errors and reminders, the ceilings, and a stop."""
from __future__ import annotations

import hashlib
import json
import threading
import time

import pytest

from harness.model import call, fail, hang, say
from harness.page import measured
from harness.turn import run_turn
from mibo import loop
from mibo.review import Verdict


def _tools_hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(body["tools"], sort_keys=True).encode()).hexdigest()


def _plan(step: str) -> dict:
    return call("update_plan", steps=[{"step": step, "status": "in_progress"}])


def test_the_turn_ends_on_the_first_answer_with_no_call_and_the_tools_stay_the_same(app, site, turn):
    page = f"{site.url}/og"
    first = turn(f"What does {page} say?", call("read_page", url=page), say("It is about tide pools."))
    second = turn("And a plan for the weekend?", _plan("pick a beach"), say("Pick a beach first."))
    assert (first.reply, second.reply) == ("It is about tide pools.", "Pick a beach first.")
    bodies = first.asked() + second.asked()
    assert len(bodies) == 4 and len({_tools_hash(b) for b in bodies}) == 1
    assert {b["tool_choice"] for b in bodies} == {"auto"}
    assert all("response_format" not in b for b in bodies)
    # The read's page is a source under the reply, and the trace names it.
    assert [s["url"] for s in first.sources] == [page] and [s["tool"] for s in first.trace] == ["read"]


def test_reads_run_side_by_side_and_acting_calls_in_the_models_order(app, site, turn):
    page = f"{site.url}/og"
    done = turn("Read it twice and plan", [call("read_page", url=page), _plan("first"), call("read_page", url=page),
                                           _plan("second")], say("Done."))
    calls = done.calls()
    assert [c["name"] for c in calls] == ["read_page", "update_plan", "read_page", "update_plan"]
    # Every call is answered, beside the call it answers and in the model's order.
    last = done.asked()[1]["messages"]
    assert [m.get("tool_call_id") for m in last[-4:]] == [c["id"] for c in calls]
    assert site.log.count("GET /og") == 2
    plan = next(s for s in done.trace if s["tool"] == "plan")
    assert plan["what"] == "second"


def test_the_loops_own_errors_are_said_in_one_wording(turn):
    done = turn("Try these", [call("no_such_tool", x=1), call("read_page"), call("read_page", url=7)],
                say("Those did not work."))
    unknown, missing, wrong = done.calls()
    assert done.result_of(unknown["id"]) == 'Error: unknown tool "no_such_tool"'
    assert done.result_of(missing["id"]) == "Error: invalid arguments: url is required"
    assert done.result_of(wrong["id"]) == "Error: invalid arguments: url must be a string"
    assert done.reply == "Those did not work."
    # Each call has its entry under the reply, the unknown one with no area to mark it.
    activity = done.entries[-1]["activity"]
    assert [(a["mark"], a["line"], a["status"]) for a in activity] == [
        ("", "no_such_tool", "error"), ("web", "read_page", "error"), ("web", "read_page", "error")]


def test_calls_past_ten_at_once_are_answered_without_running(turn):
    done = turn("Plan a lot", [_plan(f"step {n}") for n in range(12)], say("Planned."))
    calls = done.calls()
    assert [done.result_of(c["id"]) for c in calls[10:]] == [loop.TOO_MANY] * 2
    assert all(done.result_of(c["id"]) != loop.TOO_MANY for c in calls[:10])


def test_an_identical_call_is_run_again_with_a_growing_reminder(turn):
    done = turn("Plan it", *[_plan("same") for _ in range(5)], say("Planned."))
    results = [done.result_of(c["id"]) for c in done.calls()]
    assert "<system-reminder>" not in results[0] + results[1]
    assert "made this exact call 3 times" in results[2] and "made this exact call 5 times" in results[4]
    assert "<system-reminder>" not in results[3]


def test_at_fifty_rounds_one_last_call_answers_with_no_tool(turn):
    done = turn("Plan forever", *[_plan(f"step {n}") for n in range(loop.ROUNDS)], say("Here is where it stands."))
    bodies = done.asked()
    assert len(bodies) == loop.ROUNDS + 1
    last = bodies[-1]
    assert last["tool_choice"] == "none" and last["messages"][-1]["content"] == loop.ANSWER_NOW
    assert done.reply == "Here is where it stands."


def test_out_of_time_the_last_call_goes_without_tools_where_none_is_refused(app, turn, monkeypatch):
    monkeypatch.setattr(loop, "WITHIN_S", 4.0)
    # The endpoint is known to take no thinking switch, so the refusal is not first tried without one.
    measured(app, quirks=[["thinking", False]])
    refused = fail(400, {"error": {"message": "tool_choice none is not supported"}})
    done = turn("Plan it", refused, say("Nothing was done yet."))
    first, second = done.asked()
    assert first["tool_choice"] == "none" and "tools" not in second and "tool_choice" not in second
    assert done.reply == "Nothing was done yet."


def test_the_reviewer_ends_acting_at_three_refusals_in_a_row_or_twenty_in_a_turn():
    lookups = loop.ChatLookups()
    lookups.verdicts = [Verdict("refuse", "no", "review") for _ in range(loop.REFUSALS - 1)]
    assert not lookups.refused()
    lookups.verdicts.append(Verdict("refuse", "no", "review"))
    assert lookups.refused()
    assert loop.ChatLookups().refused() is False
    stopped = loop.ChatLookups()
    stopped.verdicts = [Verdict("refuse", "no", "review", stop=True)]
    assert stopped.refused()


def test_older_results_are_pruned_in_the_middle_and_the_newest_kept_whole():
    convo = [{"role": "tool", "tool_call_id": str(n), "content": str(n) * 20_000} for n in range(4)]
    loop.prune(convo, 30_000)
    sizes = [len(m["content"]) for m in convo]
    assert sizes[-1] == 20_000 and max(sizes[:-1]) < 8_300
    assert "characters truncated from the middle of this result" in convo[0]["content"]
    assert convo[0]["content"].startswith("0") and convo[0]["content"].endswith("0")


def test_pictures_older_than_the_latest_two_become_a_line():
    picture = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}
    convo = [{"role": "user", "content": [{"type": "text", "text": "look"}, picture, picture]},
             {"role": "user", "content": [picture]}]
    loop.prune(convo, 60_000)
    kinds = [p["type"] for m in convo for p in m["content"]]
    assert kinds == ["text", "text", "image_url", "image_url"]


def test_a_stop_ends_the_turn_with_what_was_said_and_done(app):
    done: dict = {}
    first = [{"content": "Let me plan it first."}, _plan("pick a beach")]
    worker = threading.Thread(target=lambda: done.update(result=run_turn(app, "Plan the weekend", first, hang())),
                              daemon=True)
    worker.start()
    doing = app.home / "runtime" / "desktop" / "chat-doing.json"
    for _ in range(200):
        if len(app.model.asked()) >= 2:
            break
        time.sleep(0.05)
    seen = json.loads(doing.read_text(encoding="utf-8"))
    assert [(e["mark"], e["status"]) for e in seen["entries"]] == [("computer", "ok")]
    assert app.post("/api/chat/stop", {"why": "stopped"}).json() == {"stopped": True}
    worker.join(30)
    result = done["result"]
    assert result.reply == "Let me plan it first.\n\n" + loop.STOPPED_LINE["en"]
    assert [s["tool"] for s in result.trace] == ["plan"]


@pytest.mark.parametrize("arguments,error", [
    ('{"url": "x"', "Error: invalid arguments: not valid JSON"),
    ("[1]", "Error: invalid arguments: expected an object"),
    ('{"sort": "colour"}', "Error: invalid arguments: sort must be one of name, size, date"),
])
def test_arguments_are_checked_against_the_tools_parameters(arguments, error):
    schema = {"type": "object", "properties": {"url": {"type": "string"},
                                               "sort": {"type": "string", "enum": ["name", "size", "date"]}}}
    assert loop.invalid(arguments, schema) == ({}, error)

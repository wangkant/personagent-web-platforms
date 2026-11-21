"""The reply model's tool loop, through whole turns: what the tools did and what reached the model, the loop's own
errors and reminders, the ceilings, and a stop."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from harness.model import call, fail, hang, say, tokens
from harness.page import measured
from harness.turn import run_turn
from mibo import acting, loop
from mibo.review import Verdict
from mibo.shell import tool as shell_tool
from mibo.shell.grants import Grants


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


def test_on_a_small_window_a_deferred_tool_is_found_then_called_through_use_tool_with_its_own_schema(app, turn):
    app.model.window = 16384
    measured(app, context=16384)
    steps = [{"step": "pick a beach", "status": "in_progress"}]
    done = turn("Plan the weekend", call("find_tools", query="update_plan"),
                call("use_tool", tool="update_plan", arguments={"steps": "pick a beach"}),
                call("use_tool", tool="no_such_tool", arguments={}),
                call("use_tool", tool="update_plan", arguments={"steps": steps}), say("Planned."))
    bodies = done.asked()
    sent = {t["function"]["name"] for t in bodies[0]["tools"]}
    assert "update_plan" not in sent and {"find_tools", "use_tool"} <= sent
    # The same tools block in every round, and every request fits the window with its answer's room.
    assert len({_tools_hash(b) for b in bodies}) == 1
    assert all(tokens(b) + b["max_tokens"] <= 16384 for b in bodies)
    find, wrong, unknown, plan = done.calls()
    found = json.loads(done.result_of(find["id"]).split("\n", 1)[1])
    assert [(f["tool"], f["parameters"]) for f in found] == [
        ("update_plan", shell_tool.PLAN_TOOL["function"]["parameters"])]
    assert done.result_of(wrong["id"]) == "Error: invalid arguments: steps must be an array"
    assert done.result_of(unknown["id"]).startswith('Error: unknown tool "no_such_tool"')
    assert next(s for s in done.trace if s["tool"] == "plan")["what"] == "pick a beach"
    # The call through use_tool is marked as the tool's own.
    assert [(a["mark"], a["status"]) for a in done.entries[-1]["activity"]][-1] == ("computer", "ok")


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


def _long_page(n: int, chars: int = 20_000) -> str:
    # A page of distinct Chinese lines, none repeated, so no line is dropped as the page's chrome.
    lines = ("".join(chr(0x4E00 + (n * 997 + i * 131 + j * 7) % 20000) for j in range(100))
             for i in range(chars // 100))
    body = "".join(f"<p>{line}</p>" for line in lines)
    return f"<html><head><title>{n}</title></head><body>{body}</body></html>"


def _rewrites(bodies: list) -> dict:
    """How many times each result changed after it was first sent."""
    sent: dict = {}
    for body in bodies:
        for message in body["messages"]:
            if message.get("role") == "tool":
                sent.setdefault(message["tool_call_id"], []).append(message["content"])
    return {ident: sum(a != b for a, b in zip(seen, seen[1:])) for ident, seen in sent.items()}


@pytest.mark.parametrize("window", [16384, 65536])
def test_ten_long_page_reads_at_once_still_get_an_answer_within_the_window(app, site, turn, window):
    app.model.window = window
    measured(app, context=window)
    pages = [site.put(f"/long{n}", _long_page(n)) for n in range(10)]
    done = turn("读这十个页面", [call("read_page", url=page) for page in pages], say("读完了。"))
    assert done.reply == "读完了。"
    # The stand-in refuses a request past its window: every one fitted, with its answer's room.
    assert all(tokens(b) + b["max_tokens"] <= window for b in done.asked())
    assert all(done.result_of(c["id"]) for c in done.calls())


def test_reads_after_reads_each_result_is_cut_at_most_once_and_the_newest_reads_stay_whole(
        app, site, turn):
    app.model.window = 65536
    measured(app, context=65536)
    pages = [site.put(f"/long{n}", _long_page(n)) for n in range(12)]
    done = turn("一轮一轮地读", *[[call("read_page", url=page) for page in pages[k:k + 3]] for k in range(0, 12, 3)],
                say("读完了。"))
    bodies = done.asked()
    assert done.reply == "读完了。" and all(tokens(b) + b["max_tokens"] <= 65536 for b in bodies)
    rewrites = _rewrites(bodies)
    assert max(rewrites.values()) == 1
    last = [m for m in bodies[-1]["messages"] if m.get("role") == "tool"]
    older = [m["content"] for m in last[:-3] if rewrites[m["tool_call_id"]]]
    assert older and min(len(m["content"]) for m in last[-3:]) > max(map(len, older))


def test_a_turn_too_large_for_the_window_with_its_tools_is_answered_without_them(app, turn):
    app.model.window = 16384
    measured(app, context=16384)
    logs = [{"kind": "file", "name": f"log{k}.txt", "data": base64.b64encode(" ".join(
        f"line {n} of log {k}" for n in range(470)).encode()).decode()} for k in range(3)]
    done = turn("Where does it fail?", say("That log is long; the error is near the end."), attachments=logs)
    # One last call, without the tool definitions, that fits the window with its answer's room.
    (body,) = done.asked()
    assert "tools" not in body and body["messages"][-1]["content"] == loop.ANSWER_NOW
    assert tokens(body) + body["max_tokens"] <= 16384
    assert done.reply == "That log is long; the error is near the end."


def test_the_pictures_they_attach_reach_the_model_whole(turn):
    shots = []
    for shade in range(4):
        out = io.BytesIO()
        Image.new("L", (8, 8), shade * 60).save(out, "PNG")
        shots.append({"kind": "picture", "data": base64.b64encode(out.getvalue()).decode()})
    done = turn("Which of these four is sharpest?", say("The third."), attachments=shots)
    parts = done.asked()[0]["messages"][-1]["content"]
    assert [p["type"] for p in parts].count("image_url") == 4


def test_two_reads_with_acting_off_ask_one_card_at_a_time_and_each_press_grants_what_it_showed(app, turn, tmp_path):
    acting.Modes(app.home / "runtime" / "desktop").set("off")
    for name, words in (("a", "alpha"), ("b", "beta")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "note.txt").write_text(words, encoding="utf-8")
    done = turn("Read both notes", [call("read_file", path="note.txt", folder=str(tmp_path / "a")),
                                    call("read_file", path="note.txt", folder=str(tmp_path / "b"))], say("Read."))
    first, second = done.calls()
    assert "alpha" in done.result_of(first["id"]) and "beta" in done.result_of(second["id"])
    # One card for each folder, each shown before its press; the first also turned acting on.
    shown = sorted(Path(c["card"]["folder"]).name for c in done.cards)
    granted = sorted(Path(f).name for f in Grants(app.home / "runtime" / "desktop", app.home).folders()
                     if Path(f).parent.resolve() == tmp_path.resolve())
    assert shown == granted == ["a", "b"]


_STEPS = '[{"step": "pick a beach", "status": "in_progress"}]'
# The same first round three ways: a native call, a call written in a <tool_call> block, and in DeepSeek's markup.
_FIRST_ROUNDS = {
    "native": [{"content": "Let me plan it first."}, _plan("pick a beach")],
    "written": {"content": 'Let me plan it first. <tool_call>{"name": "update_plan", "arguments": {"steps": '
                           + _STEPS + "}}</tool_call>"},
    "deepseek": {"content": 'Let me plan it first.\n<｜DSML｜function_calls>\n<｜DSML｜invoke name="update_plan">'
                            '<｜DSML｜parameter name="steps" string="false">' + _STEPS + "</｜DSML｜parameter>"
                            "</｜DSML｜invoke>\n</｜DSML｜function_calls>"},
}


@pytest.mark.parametrize("how", sorted(_FIRST_ROUNDS))
def test_a_stop_ends_the_turn_with_what_was_said_and_done(app, how):
    done: dict = {}
    first = _FIRST_ROUNDS[how]
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


def test_a_nested_object_or_list_sent_as_json_text_is_taken_as_it():
    schema = {"type": "object", "properties": {"arguments": {"type": "object"}, "paths": {"type": "array"}}}
    sent = json.dumps({"arguments": json.dumps({"q": "tide"}), "paths": json.dumps(["a.txt"])})
    assert loop.invalid(sent, schema) == ({"arguments": {"q": "tide"}, "paths": ["a.txt"]}, "")
    assert loop.invalid(json.dumps({"arguments": "tide"}), schema) == (
        {}, "Error: invalid arguments: arguments must be an object")

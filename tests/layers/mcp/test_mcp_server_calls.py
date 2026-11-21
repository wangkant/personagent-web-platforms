"""A turn calling the person's MCP servers through use_tool (mcp_offer, mcp_hub, mcp_tools): a tool its maker marks
read-only runs with no reviewer and no card; any other goes to the reviewer and, unless passed, to the card; a no
to starting a program holds for that call only; whatever was not done reaches the model as an error; a call that
names no server goes to the one that has the tool; and the tools block stays the same while a server starts. The
servers are a stand-in program here; the model is scripted."""
from __future__ import annotations

import json
import sys
import textwrap
import time

import mcp_standin
from harness.model import call, say, text

# A stdio MCP server whose tools carry the marks given on its command line; each call it receives goes to its log.
SERVER = textwrap.dedent('''
    import json, sys
    TOOLS, LOG = json.loads(sys.argv[1]), sys.argv[2]
    for line in sys.stdin:
        m = json.loads(line)
        if "id" not in m:
            continue
        method = m.get("method")
        if method == "initialize":
            r = {"protocolVersion": m["params"].get("protocolVersion"), "capabilities": {"tools": {}},
                 "serverInfo": {"name": "notes", "version": "1"}}
        elif method == "tools/list":
            r = {"tools": TOOLS}
        elif method == "tools/call":
            name = m["params"]["name"]
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(name + "\\n")
            if name == "sync_notes":
                r = {"content": [{"type": "text", "text": "401 Unauthorized"}], "isError": True}
            else:
                r = {"content": [{"type": "text", "text": "shopping; ideas"}], "isError": False}
        else:
            r = {}
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
''')
OBJECT = {"type": "object", "properties": {"title": {"type": "string"}}}
TOOLS = [
    {"name": "list_notes", "description": "List the notes.", "inputSchema": OBJECT,
     "annotations": {"readOnlyHint": True}},
    {"name": "add_note", "description": "Add a note.", "inputSchema": OBJECT},
    {"name": "sync_notes", "description": "Sync the notes.", "inputSchema": OBJECT},
]


def verdict(decision: str) -> dict:
    """The reviewer's answer: all four checks true, and `decision`."""
    return text(json.dumps({"kind_asked": True, "target_named": True, "text_given": True, "nothing_added": True,
                            "decision": decision, "reason": "They asked for this."}))


def add_server(app, tmp_path, name: str = "notes", tools: list = TOOLS, always: bool = False):
    """The stand-in added in Settings as `name`, allowed to start always when `always`; its call log."""
    script, log = tmp_path / f"{name}_server.py", tmp_path / f"{name}.log"
    script.write_text(SERVER, encoding="utf-8")
    snippet = {name: {"command": sys.executable, "args": [str(script), json.dumps(tools), str(log)]}}
    assert app.post("/api/mcp", {"add": json.dumps(snippet)}).status_code == 200
    if always:
        assert app.post("/api/mcp", {"start": name, "answer": "always"}, own_page=True).status_code == 200
    return log


def calls_of(log) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def connected(app, *names: str) -> None:
    deadline = time.monotonic() + 30
    while True:
        states = {row["name"]: row["state"] for row in app.get("/api/mcp").json()["servers"]}
        if all(states.get(name) == "connected" for name in names):
            return
        assert time.monotonic() < deadline, states
        time.sleep(0.1)


def test_a_tool_marked_read_only_runs_with_no_reviewer_and_no_card(app, turn, tmp_path):
    log = add_server(app, tmp_path, always=True)
    connected(app, "notes")
    use = call("use_tool", server="notes", tool="list_notes", arguments={})
    done = turn("What notes do I have?", use, say("Shopping and ideas."), cards="no")  # copy: voice
    assert done.cards == []
    assert done.asked("reviewer") == []
    assert calls_of(log) == ["list_notes"]


def test_a_tool_not_marked_read_only_goes_to_the_reviewer_and_then_the_card(app, turn, tmp_path):
    log = add_server(app, tmp_path, always=True)
    connected(app, "notes")
    use = call("use_tool", server="notes", tool="add_note", arguments={"title": "milk"})
    # Passed by the reviewer: it runs with no card.
    app.model.script(verdict("go"), purpose="reviewer")
    passed = turn("Add a note: milk", use, say("Added."), cards="no")  # copy: voice
    assert passed.cards == [] and len(passed.asked("reviewer")) == 1
    assert calls_of(log) == ["add_note"]
    # Not passed: the card asks, and on no the server gets no call.
    app.model.script(verdict("ask"), purpose="reviewer")
    asked = turn("Add a note: milk", use, say("I left it."), cards="no")  # copy: voice
    assert [(c["card"]["type"], c["answer"]) for c in asked.cards] == [("mcp_tool", "no")]
    assert len(asked.asked("reviewer")) == 1
    assert calls_of(log) == ["add_note"]
    assert asked.result_of(asked.calls()[0]["id"]).startswith("Error:")


def test_a_no_to_starting_a_program_holds_for_that_call_only(app, turn, tmp_path):
    log = add_server(app, tmp_path)
    use = call("use_tool", server="notes", tool="list_notes", arguments={})
    done = turn("What notes do I have?", use, use, say("Shopping and ideas."), cards=["no", "yes"])  # copy: voice
    assert [(c["card"]["type"], c["answer"]) for c in done.cards] == [("mcp_start", "no"), ("mcp_start", "yes")]
    first, second = done.calls()
    refused = done.result_of(first["id"])
    assert refused.startswith("Error:") and "notes" in refused
    assert not done.result_of(second["id"]).startswith("Error:")
    assert calls_of(log) == ["list_notes"]


def test_a_failure_the_server_reports_reaches_the_model_as_an_error(app, turn, tmp_path):
    log = add_server(app, tmp_path, always=True)
    connected(app, "notes")
    app.model.script(verdict("go"), purpose="reviewer")
    use = call("use_tool", server="notes", tool="sync_notes", arguments={})
    done = turn("Sync my notes", use, say("It did not sync."), cards="no")  # copy: voice
    assert calls_of(log) == ["sync_notes"]
    assert done.result_of(done.calls()[0]["id"]).startswith("Error:")


def test_a_server_waiting_for_a_sign_in_reaches_the_model_as_an_error_naming_it(app, turn):
    server = mcp_standin.serve(["--auth"])
    try:
        snippet = {"cloud": {"url": f"http://127.0.0.1:{server.server_address[1]}/mcp"}}
        assert app.post("/api/mcp", {"add": json.dumps(snippet)}).status_code == 200
        deadline = time.monotonic() + 30
        while next(r["state"] for r in app.get("/api/mcp").json()["servers"]) != "signin":
            assert time.monotonic() < deadline
            time.sleep(0.1)
        use = call("use_tool", server="cloud", tool="echo", arguments={"text": "hi"})
        done = turn("Echo hi with cloud", use, say("It needs a sign-in."), cards="no")  # copy: voice
    finally:
        server.shutdown()
    said = done.result_of(done.calls()[0]["id"])
    assert said.startswith("Error:") and "cloud" in said
    assert done.cards == []


def test_a_call_naming_no_server_goes_to_the_one_server_that_has_the_tool(app, turn, tmp_path):
    log = add_server(app, tmp_path, always=True)
    other = add_server(app, tmp_path, "weather", [{"name": "forecast", "description": "The forecast.",
                                                   "inputSchema": OBJECT, "annotations": {"readOnlyHint": True}}],
                       always=True)
    connected(app, "notes", "weather")
    done = turn("What notes do I have?", call("use_tool", tool="list_notes", arguments={}),  # copy: voice
                say("Shopping and ideas."), cards="no")
    assert not done.result_of(done.calls()[0]["id"]).startswith("Error:")
    assert calls_of(log) == ["list_notes"] and calls_of(other) == []


def test_the_tools_block_stays_the_same_while_a_server_starts(app, turn, tmp_path):
    add_server(app, tmp_path)
    # Stopped at the first turn, started on its card there, connected at the second.
    first = turn("What can notes do?", call("find_tools", server="notes"), say("It lists notes."),  # copy: voice
                 cards="yes")
    assert [c["card"]["type"] for c in first.cards] == ["mcp_start"]
    connected(app, "notes")
    use = call("use_tool", server="notes", tool="list_notes", arguments={})
    second = turn("What notes do I have?", use, say("Shopping and ideas."), cards="no")  # copy: voice
    bodies = first.asked() + second.asked()
    assert len(bodies) == 4
    assert len({json.dumps(body.get("tools"), sort_keys=True) for body in bodies}) == 1

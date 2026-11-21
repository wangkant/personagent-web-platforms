"""A delete, an install, a push and an MCP tool its maker marks destructive ask on a card in a whole turn, even
with a reviewer that would pass anything: the reviewer is never asked, the card offers no always-allow, and on no
nothing happens, as the files on disk and the server's own record show."""
from __future__ import annotations

import json
import os
import sys
import textwrap

import pytest

from harness import attacks
from harness.model import call, say, text
from mibo import acting, mcp_tools
from mibo.shell import files as file_tools
from mibo.shell.grants import LOG_FILE, Grants

ROWS = {row.values[0]["id"]: row.values[0] for row in attacks.load("asked")}
COMMANDS = [pytest.param(row, id=row["id"], marks=pytest.mark.skipif(
    row.get("only", sys.platform) != sys.platform, reason="read.py reads commands with Windows PowerShell's parser"))
    for row in ROWS.values() if row["area"] == "commands"]
# What a reviewer that passes everything answers, a few times over.
PASSES = [text(json.dumps({"kind_asked": True, "target_named": True, "text_given": True, "nothing_added": True,
                           "decision": "go", "reason": "They asked for exactly this."}))] * 3
ASKED = "Tidy my files folder: delete notes.txt, install requests and push the repository"  # copy: voice


@pytest.fixture
def folder(app, tmp_path, monkeypatch):
    """A folder granted to change in, holding notes.txt; the Recycle Bin is a stand-in that deletes in place."""
    place = tmp_path / "files"
    place.mkdir()
    (place / "notes.txt").write_text("keep me\n", encoding="utf-8")
    assert Grants(app.home / "runtime" / "desktop", app.home).add(str(place)) == ""

    def recycle(paths: list) -> bool:
        for path in paths:
            os.remove(path)
        return True

    monkeypatch.setattr(file_tools, "recycle", recycle)
    return place


@pytest.mark.parametrize("attack", COMMANDS)
def test_a_delete_an_install_or_a_push_asks_every_time_and_on_no_nothing_happens(app, turn, folder, attack):
    assert acting.Modes(app.home / "runtime" / "desktop").mode() == "auto"
    app.model.script(*PASSES, purpose="reviewer")
    done = turn(ASKED, call(attack["tool"], **attack["args"], folder=str(folder)), say("I left it."), cards="no")
    assert [(c["kind"], c["card"]["type"], c["card"]["choices"]) for c in done.cards] == \
        [("shell", "command", ["once"])]
    assert done.asked("reviewer") == []
    assert (folder / "notes.txt").read_text(encoding="utf-8") == "keep me\n"
    logged = [json.loads(line) for line in (app.home / "runtime" / "desktop" / LOG_FILE).read_text(
        encoding="utf-8").splitlines()]
    assert [row["outcome"] for row in logged] == ["declined"]


# A stdio MCP server whose tools carry their maker's marks; it writes each call it receives to its log.
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
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(m["params"]["name"] + "\\n")
            r = {"content": [{"type": "text", "text": "done"}], "isError": False}
        else:
            r = {}
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
''')


def test_an_mcp_tool_marked_destructive_asks_every_time_and_on_no_the_server_gets_no_call(app, turn, tmp_path):
    attack = ROWS["mcp-destructive"]
    script, log = tmp_path / "notes_server.py", tmp_path / "calls.log"
    script.write_text(SERVER, encoding="utf-8")
    tools = [{"name": attack["tool"], "description": "Delete a note.", "annotations": attack["annotations"],
              "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}}}]
    added = app.post("/api/mcp", {"add": json.dumps({"notes": {"command": sys.executable,
                                                              "args": [str(script), json.dumps(tools), str(log)]}})})
    assert added.status_code == 200
    # A write or delete group may never be set to always.
    assert app.post("/api/mcp", {"group": {"server": "notes", "group": "write", "mode": mcp_tools.ALWAYS}}).json() \
        == {"error": "generic"}
    app.model.script(*PASSES, purpose="reviewer")
    # Once connected, the server's tools are indexed by their search words.
    app.model.script(text(json.dumps({attack["tool"]: "删除笔记 delete note"}, ensure_ascii=False)), purpose="index")
    use = call("use_tool", server="notes", tool=attack["tool"], arguments={"title": "shopping"})
    # The program starts on the person's yes; the call itself asks, and a wider press counts as this once.
    first = turn("Delete my shopping note", use, say("Deleted."), cards=["yes", "always"])  # copy: voice
    assert [(c["card"]["type"], c["card"]["choices"]) for c in first.cards] == \
        [("mcp_start", ["once", "always"]), ("mcp_tool", ["once"])]
    assert log.read_text(encoding="utf-8").splitlines() == [attack["tool"]]
    # Connected and small, the server's tools are now offered by name.
    direct = call(f"mcp__notes__{attack['tool']}", title="shopping")
    again = turn("Delete it again", direct, say("I left it."), cards="no")  # copy: voice
    assert [(c["card"]["type"], c["answer"]) for c in again.cards] == [("mcp_tool", "no")]
    assert log.read_text(encoding="utf-8").splitlines() == [attack["tool"]]
    assert first.asked("reviewer") == again.asked("reviewer") == []

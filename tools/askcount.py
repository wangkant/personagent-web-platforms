"""Count the cards a person must press for commands and MCP tools over the same scripted errands, two conversations
each, with no model and no window: the shell's gate (shell/panel.py) on a git folder made under --out, and the MCP
hub (mcp_hub.py) on a small stand-in server whose tools carry the provider's read-only and destructive marks.

    python tools/askcount.py [--out <folder>] [--tree <folder holding mibo>] [--only shell,mcp]

Each errand is run under each way of answering the card: `once` (本次允许, every card), `chat` (本次对话内允许 where
the card offers it, else once) and `always` (始终允许 where offered, else once), in the one acting mode, automatic
review. A push and an install are answered no. One line per run with the cards per conversation, the reviewer's
calls and what each step came to; <out>/askcount.json keeps them. --tree measures another copy of the code (a snapshot
from before a change). The reviewer is a stand-in that passes whatever code sends it (--reviewer go) or one that
never answers (--reviewer none), so the count shows what code leaves to the reviewer; tools/review_score.py
measures the real reviewer's judgment. That a delete, a push, an install and a tool marked destructive ask every
time, whatever the reviewer says, is a test now (tests/floors/asked).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

# The commands of one conversation: (tool, arguments). Steps marked "no" are answered no.
SHELL_STEPS = [
    ("cmd", "git status"),
    ("cmd", "git log --oneline -3"),
    ("write", {"path": "notes.txt", "content": "new notes\n"}),
    ("edit", {"path": "a.txt", "old_string": "line", "new_string": "line 1"}),
    ("cmd", "git add -A"),
    ("cmd", 'git commit -q -m "step"'),
    ("edit", {"path": "a.txt", "old_string": "line 1", "new_string": "line 2"}),
    ("cmd", "git diff HEAD~1 --stat"),
    # One-liners a person asks for in their own words: a script of theirs run with a chain, a pipe or a wildcard.
    ("cmd", "python tidy.py; git status --short"),
    ("cmd", "python tidy.py | Measure-Object -Line"),
    ("cmd", "python tidy.py *.txt"),
    ("delete", {"paths": ["notes.txt"]}),
    ("cmd", "git push", "no"),
    ("cmd", "pip install requests", "no"),
]
# The stand-in server's tools: two the provider marks read-only, one that writes, one marked destructive.
MCP_TOOLS = [
    {"name": "get_time", "description": "The time now.", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": True}},
    {"name": "list_notes", "description": "List the notes.", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": True}},
    {"name": "add_note", "description": "Add a note.", "inputSchema": {"type": "object", "properties": {
        "text": {"type": "string"}}, "required": ["text"]}, "annotations": {"readOnlyHint": False}},
    {"name": "delete_note", "description": "Delete a note.", "inputSchema": {"type": "object"},
     "annotations": {"destructiveHint": True}},
]
MCP_STEPS = [("get_time", {}), ("list_notes", {}), ("get_time", {}), ("add_note", {"text": "a"}),
             ("list_notes", {}), ("add_note", {"text": "b"}), ("get_time", {}), ("delete_note", {})]
SERVER = textwrap.dedent('''
    import json, sys
    TOOLS = json.loads(sys.argv[1])
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
            r = {"content": [{"type": "text", "text": "ok " + m["params"]["name"]}], "isError": False}
        else:
            r = {}
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
''')


# The person's message the errands answer, as the chat record holds it.
SAID = ("看看这个仓库的状态，记几条笔记，提交一下，再运行 tidy.py 整理一下 txt 文件、数数它输出几行，"
        "然后把 notes.txt 删掉；推送和安装先不要")
REVIEWED = {"calls": 0}


def workspace_for(home: Path) -> SimpleNamespace:
    """A stand-in workspace with the chat record acting.asked reads: the person's message in each conversation."""
    import time
    rooms = {c: SimpleNamespace(conversation=c, history=[{"role": "user", "text": SAID, "at": time.time()}])
             for c in ("c1", "c2")}
    return SimpleNamespace(home=home, _chat_lock=asyncio.Lock(), config=lambda: {},
                           _main=SimpleNamespace(conversation="main", history=[]), _chats=rooms)


def stand_in(verdict: str) -> None:
    """acting.reviewer replaced by a stand-in: "go" passes what code sends it, "none" never answers."""
    from mibo import acting

    def reviewer(workspace):
        async def ask(system: str, messages: list) -> str:
            REVIEWED["calls"] += 1
            if verdict != "go":
                raise RuntimeError("no reviewer")
            return json.dumps({"kind_asked": True, "target_named": True, "text_given": True, "nothing_added": True,
                               "decision": "go", "reason": "stand-in"})
        return ask
    acting.reviewer = reviewer


def fresh_day() -> None:
    """Each run is its own day: the reviewer's passes a day (review.Tally) start from none, as they would for one
    person, instead of adding up across the runs of this process."""
    from mibo import review
    review.TALLY.passes.clear()
    review.TALLY.refused.clear()


def wipe(folder: Path) -> None:
    """Removes a run's folder from before, git's read-only files too."""
    def writable(func, path, _):
        os.chmod(path, 0o666)
        func(path)
    shutil.rmtree(folder, onerror=writable) if folder.exists() else None


class Turn:
    """The person's chat turn in conversation `name`, under way, as cardwait.Card checks it."""

    def __init__(self, workspace, desktop: Path, name: str):
        self.workspace, self.desktop, self.name = workspace, desktop, name

    async def __aenter__(self):
        await self.workspace._chat_lock.acquire()
        self.desktop.mkdir(parents=True, exist_ok=True)
        (self.desktop / "chat-doing.json").write_text(json.dumps({"conversation": self.name}), encoding="utf-8")
        return self

    async def __aexit__(self, *exc):
        self.workspace._chat_lock.release()


def choose(card: dict, policy: str, refuse: bool):
    """The press for `card` under `policy`: what the card offers, the narrowest that stops the repeat."""
    offered = card.get("choices") or []
    if refuse:
        return "no"
    if policy in ("chat", "always") and policy in offered:
        return policy
    return "once"


async def presser(cards, press, log: list, policy: str, state: dict) -> None:
    """Answers each card as it comes up, as the page would."""
    while True:
        card = cards.card
        if card is not None and cards.up(card["id"]):
            answer = choose(card, policy, state["refuse"])
            log.append({"type": card.get("type"), "choices": card.get("choices") or [], "answer": answer,
                        "title": (card.get("zh") or {}).get("title") or card.get("tool") or card.get("server")})
            press(card, answer)
        await asyncio.sleep(0.02)


async def shell_run(out: Path, policy: str) -> dict:
    from mibo.shell import panel
    wipe(out / f"shell-{policy}")
    home, proj = out / f"shell-{policy}" / "home", out / f"shell-{policy}" / "proj"
    proj.mkdir(parents=True)
    (proj / "a.txt").write_text("line\n", encoding="utf-8")
    git = shutil.which("git")
    for args in (["init", "-q"], ["config", "user.email", "m@example.com"], ["config", "user.name", "m"],
                 ["add", "-A"], ["commit", "-q", "-m", "first"]):
        subprocess.run([git, *args], cwd=proj, check=True, capture_output=True)
    workspace = workspace_for(home)
    app = SimpleNamespace(state=SimpleNamespace(window=None))
    shell = panel.Shell(app, workspace)
    shell.grants.add(str(proj))

    def press(card: dict, answer: str) -> None:
        shell.press(card["id"], answer)

    result = {"conversations": []}
    for conversation in ("c1", "c2"):
        log, steps, state = [], [], {"refuse": False}
        task = asyncio.ensure_future(presser(shell.cards, press, log, policy, state))
        try:
            async with Turn(workspace, shell.desktop, conversation):
                for i, step in enumerate(SHELL_STEPS):
                    state["refuse"] = len(step) > 2
                    before = len(log)
                    turn = f"{conversation}-{i}"
                    if step[0] == "cmd":
                        done = await shell.do(step[1], conversation, str(proj), 60.0, turn)
                        what = step[1]
                    else:
                        done = await shell.file(step[0], dict(step[1]), conversation, str(proj), turn)
                        what = f"{step[0]}_file"
                    steps.append({"step": what, "status": done.get("status"), "code": done.get("code"),
                                  "cards": len(log) - before, "out": str(done.get("out") or "")[:80],
                                  "err": str(done.get("err") or "")[:80]})
        finally:
            task.cancel()
        result["conversations"].append({"cards": len(log), "steps": steps, "pressed": log})
    from mibo.shell import read as reading, run as running
    running.SPARE.close()
    reading.PARSER.close()
    return result


async def mcp_run(out: Path, policy: str) -> dict:
    from mibo import mcp_hub, mcp_tools
    home = out / f"mcp-{policy}"
    wipe(home)
    home.mkdir(parents=True)
    script = home / "notes_server.py"
    script.write_text(SERVER, encoding="utf-8")
    mcp_tools.write_servers(home, {"notes": {"command": sys.executable, "args": [str(script), json.dumps(MCP_TOOLS)]}},
                            lambda values: None)
    workspace = workspace_for(home)
    hub = mcp_hub.Hub(workspace)
    hub.reconcile()
    desktop = mcp_tools.desktop_dir(home)
    while hub.runners["notes"].state not in ("waiting", "connected", "failed"):
        await asyncio.sleep(0.05)

    def press(card: dict, answer: str) -> None:
        if card["type"] == "mcp_start" and answer == "chat":
            answer = "once"
        hub.press(card["id"], answer)

    result = {"conversations": []}
    try:
        for conversation in ("c1", "c2"):
            log, steps, state = [], [], {"refuse": False}
            task = asyncio.ensure_future(presser(hub.cards, press, log, policy, state))
            try:
                async with Turn(workspace, desktop, conversation):
                    for name, args in MCP_STEPS:
                        before = len(log)
                        done = await hub.call("notes", name, args, conversation)
                        steps.append({"step": name, "status": done.get("status"), "how": done.get("how"),
                                      "cards": len(log) - before})
            finally:
                task.cancel()
            result["conversations"].append({"cards": len(log), "steps": steps, "pressed": log})
    finally:
        await asyncio.gather(*(hub._stop(r) for r in hub.runners.values()))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="askcount-out")
    parser.add_argument("--tree", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--only", default="shell,mcp")
    parser.add_argument("--reviewer", default="go", choices=("go", "none"))
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.tree).resolve()))
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["SHELL_OPEN_DRY"] = "1"
    import mibo
    print("tree:", Path(mibo.__file__).parent)
    stand_in(args.reviewer)
    runs = {}
    only = args.only.split(",")
    for policy in ("once", "chat", "always"):
        if "shell" in only:
            REVIEWED["calls"] = 0
            fresh_day()
            got = asyncio.run(shell_run(out, policy))
            got["reviewed"] = REVIEWED["calls"]
            runs[f"shell/{policy}"] = got
            per = [c["cards"] for c in got["conversations"]]
            # A command PowerShell could not find prints its error with exit code 0: shown as "!".
            ran = [f"{s['step'].split(' ')[0] if s['step'].startswith('git') else s['step']}:{s['status']}"
                   + (f"({s['code']})" if s.get("code") not in (None, 0) else "") + ("!" if s.get("err") else "")
                   for s in got["conversations"][0]["steps"]]
            print(f"shell {policy:11} cards c1={per[0]} c2={per[1]} total={sum(per)} reviewed {got['reviewed']} | "
                  f"{' '.join(ran)}")
        if "mcp" in only:
            REVIEWED["calls"] = 0
            got = asyncio.run(mcp_run(out, policy))
            got["reviewed"] = REVIEWED["calls"]
            runs[f"mcp/{policy}"] = got
            per = [c["cards"] for c in got["conversations"]]
            print(f"mcp   {policy:11} cards c1={per[0]} c2={per[1]} total={sum(per)} reviewed {got['reviewed']} | "
                  + " ".join(f"{s['step']}:{s['status']}/{s['how']}" for s in got["conversations"][1]["steps"]))
    (out / "askcount.json").write_text(json.dumps(runs, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

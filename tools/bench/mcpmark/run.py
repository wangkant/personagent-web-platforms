"""MCPMark's Filesystem standard suite (30 tasks, MCP condition) through Mibo itself: for each task a fresh home of
the app, a fresh copy of the task's folder, the official filesystem MCP server added as a person adds a connector,
one chat turn carrying the task's prompt through the app's reply loop, then the task's own verify.py on what is left
on disk.

    python tools/bench/mcpmark/run.py fetch --data <folder>
    python tools/bench/mcpmark/run.py check --mcpmark <checkout> --data <folder> --work <folder>
    python tools/bench/mcpmark/run.py run --mcpmark <checkout> --data <folder> --work <folder> --keys <.env>
                                         [--budget 0.75] [--only desktop/music_report,...] [--sample 12 --seed 7]
                                         [--out <json>]

`fetch` downloads the ten test folders from storage.mcpmark.ai (about 52 MB) and unpacks them keeping each file's
modification time as the zip's extended timestamp gives it (what Info-ZIP's unzip, which the harness calls, does).

`check` shows the plumbing is faithful before any number counts: every task's verify.py on its untouched folder
(it must fail), then three tasks through the whole app with a stand-in model that writes a right answer (it must
pass) and a wrong one (it must fail). MCPMark publishes no reference solutions; the right answers are the ones the
verify scripts themselves hold.

`run` measures. The prompt is description.md plus the note MCPMark's harness adds; the server is
`npx -y @modelcontextprotocol/server-filesystem@2025.12.18 <task folder>`, the version MCPMark Verified pins, with
Node.js fetched by the app's own runtimes.install. The app runs without a window (`mibo start --no-browser`), at
below-normal priority, one task at a time. Every card is answered as the person who asked for the task: yes to the
connector's start, its tools, turning an ability on, and folders and commands inside the task's folder; no to
anything reaching outside that folder, a program's window or the browser, which a folder task does not need. With
--sample, a part of the suite stratified by category is run (see `draw`). Model calls go through tools/everyday.py's metering pass-through, which refuses calls
past --budget dollars; no task starts with less than a task's worth left. The key is read from --keys and written
only into each scratch home's .env, never printed. Per task, <work>/<task>/ keeps the reply, the steps, the MCP
log, the model calls and verify's output; the summary goes to --out.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

from everyday import Meter  # noqa: E402

from tests.harness.turn import CARD_ROUTES, _press  # noqa: E402

CATEGORIES = ("desktop", "desktop_template", "file_context", "file_property", "folder_structure", "legal_document",
              "papers", "student_database", "threestudio", "votenet")
STORAGE = "https://storage.mcpmark.ai/filesystem/{}.zip"
SERVER = "@modelcontextprotocol/server-filesystem@2025.12.18"
# What MCPMark's harness adds to every task's description (src/base/task_manager.py, _format_task_instruction).
NOTE = "\n\nNote: Based on your understanding, solve the task all at once by yourself, don't ask for my opinions on anything."
MODEL = "deepseek/deepseek-v4.1-flash"
# deepseek-v4.1-flash on OpenRouter (2026-11-21), dollars per million tokens: input, cached input, output. Used only
# when an answer carries no cost of its own.
PRICE = (0.0498, 0.000996, 0.1992)
# Ports of this bench (59710-59719): the pass-through, the page, the character service, the stand-in model.
METER_PORT, PAGE_PORT, SERVICE_PORT, STUB_PORT = 59711, 59712, 59713, 59714
# A task does not start with less than this left of the budget, dollars.
TASK_RESERVE = 0.03
# How each kind of card is answered by the person who asked for a folder task.
ANSWERS = {"shell": "yes", "mcp": "yes", "turnon": "yes", "desk": "no", "operate": "no"}
TURN_S = 1200
# Scorers a correct answer cannot pass on Windows, with the line that shows it.
UNTRUSTED_ON_WINDOWS = {
    "folder_structure/structure_mirror":
        "verify.py line 58 wants each placeholder.txt to end with f\"complex_structure_mirror/{relative path}\": on "
        "Windows the relative path prints with backslashes, so the absolute path the task asks for never matches when "
        "written with backslashes, and written with forward slashes matches only folders one level deep",
}
WIN = os.name == "nt"
BELOW_NORMAL, NO_WINDOW = 0x00004000, 0x08000000


# ---------------------------------------------------------------- the tasks and their folders

def tasks(checkout: Path) -> list[dict]:
    root = checkout / "tasks" / "filesystem" / "standard"
    found = []
    for verify in sorted(root.glob("*/*/verify.py")):
        folder = verify.parent
        found.append({"id": f"{folder.parent.name}/{folder.name}", "category": folder.parent.name,
                      "prompt": (folder / "description.md").read_text(encoding="utf-8") + NOTE, "verify": verify})
    return found


def draw(every: list[dict], size: int, seed: int) -> list[dict]:
    """A part of the suite stratified by category. Seats go to categories by largest remainder of their share
    (size * n / total), each category at least one; ties for a remaining seat are broken by random.Random(seed),
    which then shuffles each category's sorted tasks in CATEGORIES order. A category's first `seats` tasks are drawn;
    the rest stay in that order as its replacements. Each task gets "seats" (of its category) and "order"."""
    import random
    rng = random.Random(seed)
    by = {c: sorted((t for t in every if t["category"] == c), key=lambda t: t["id"]) for c in CATEGORIES}
    share = {c: size * len(by[c]) / len(every) for c in CATEGORIES}
    seats = {c: max(1, int(share[c])) for c in CATEGORIES}
    rest = [c for c in CATEGORIES if seats[c] == int(share[c])]
    rng.shuffle(rest)
    rest.sort(key=lambda c: share[c] - int(share[c]), reverse=True)
    for c in rest[:size - sum(seats.values())]:
        seats[c] += 1
    out = []
    for c in CATEGORIES:
        rng.shuffle(by[c])
        out += [{**t, "seats": seats[c], "order": i} for i, t in enumerate(by[c])]
    return out


def _mtime(info: zipfile.ZipInfo) -> float:
    """The zip's extended timestamp (UTC) when it has one, else its DOS time read as local time."""
    extra = info.extra
    while len(extra) >= 4:
        kind, size = struct.unpack("<HH", extra[:4])
        if kind == 0x5455 and size >= 5 and extra[4] & 1:
            return float(struct.unpack("<I", extra[5:9])[0])
        extra = extra[4 + size:]
    return time.mktime(info.date_time + (0, 0, -1))


def fetch(data: Path) -> None:
    data.mkdir(parents=True, exist_ok=True)
    for category in CATEGORIES:
        archive = data / f"{category}.zip"
        if not archive.exists():
            with urllib.request.urlopen(STORAGE.format(category), timeout=300) as answer:
                archive.write_bytes(answer.read())
        shutil.rmtree(data / category, ignore_errors=True)
        with zipfile.ZipFile(archive) as z:
            members = [m for m in z.infolist() if not m.filename.startswith("__MACOSX")]
            for member in members:
                z.extract(member, data)
            # Directories last and deepest first, so writing a file does not move a folder's time again.
            for member in sorted(members, key=lambda m: (m.is_dir(), -m.filename.count("/"))):
                stamp = _mtime(member)
                os.utime(data / member.filename, (stamp, stamp))
        print(f"{category}: {archive.stat().st_size} bytes")


def fresh_copy(data: Path, category: str, into: Path) -> Path:
    """The task's folder as MCPMark's state manager makes it: a copy of the category's folder, times kept."""
    target = into / category
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(data / category, target)
    return target


def verify(task: dict, folder: Path) -> tuple[bool, str]:
    """The task's own verify.py, as MCPMark's filesystem task manager calls it: exit code 0 is a pass. Python runs in
    UTF-8 mode, the default text encoding of the macOS and Linux the scripts were validated on (several read files
    with no encoding named, which on Windows would be the ANSI code page)."""
    done = subprocess.run([sys.executable, "-X", "utf8", "-I", str(task["verify"])], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300,
                          env={**os.environ, "FILESYSTEM_TEST_DIR": str(folder), "PYTHONIOENCODING": "utf-8"})
    return done.returncode == 0, (done.stdout + done.stderr)[-1500:]


# ---------------------------------------------------------------- one home of the app

def junction(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    if WIN:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def node_ready(work: Path) -> Path:
    """Node.js fetched once by the app's own runtimes.install into a home of its own; each task's home links to it,
    and to one npm cache, so the server's package is fetched once."""
    from mibo import runtimes
    shared = work / "node-home"
    if not runtimes.ready(shared, "node"):
        asyncio.run(runtimes.install(shared, "node"))
    return shared


def make_home(home: Path, shared: Path, model_url: str, model: str, key: str) -> None:
    from mibo import setup_wizard
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True)
    setup_wizard.write_env(home / ".env", {
        "LLM_BASE_URL": model_url, "LLM_API_KEY": key, "LLM_MODEL": model, "PAGE_PORT": str(PAGE_PORT),
        "SERVER_PORT": str(SERVICE_PORT), "UPDATE_CHECK_ENABLED": "false", "ROAM_ENABLED": "false",
        "AGENT_LANG": "zh", "PERSONA_TZ_OFFSET_HOURS": "-8", "PERSONA_NAME": "Mibo"})
    setup_wizard.write_persona(home, setup_wizard.CHARACTERS[0].key, name="Mibo", lang="zh")
    junction(home / "runtime" / "bin" / "node", shared / "runtime" / "bin" / "node")
    junction(home / "runtime" / "cache" / "npm", shared / "runtime" / "cache" / "npm")


class App:
    """`mibo start --no-browser` on one home, in a process of its own; its page API as the page calls it."""

    def __init__(self, home: Path, log: Path):
        self.home = home
        (home / "runtime" / "desktop" / "welcome.json").unlink(missing_ok=True)
        code = f"import sys; from mibo import welcome; sys.exit(welcome.main(['--no-browser', '--port', '{PAGE_PORT}']))"
        self.out = log.open("w", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, "-c", code], cwd=REPO, stdout=self.out, stderr=subprocess.STDOUT,
                                     env={**os.environ, "AGENT_HOME": str(home), "PYTHONIOENCODING": "utf-8"},
                                     creationflags=BELOW_NORMAL | NO_WINDOW if WIN else 0)
        info = home / "runtime" / "desktop" / "welcome.json"
        for _ in range(240):
            if info.exists() and json.loads(info.read_text(encoding="utf-8")).get("url"):
                break
            if self.proc.poll() is not None:
                raise RuntimeError("the app stopped while starting")
            time.sleep(0.5)
        url = json.loads(info.read_text(encoding="utf-8"))["url"]
        base, _, token = url.partition("#token=")
        # Kept-alive connections: polling the cards on a new connection each time ran the computer out of ports.
        self.client = httpx.Client(base_url=base.rstrip("/"), headers={"x-mibo-setup-token": token}, trust_env=False)

    def api(self, path: str, body: dict | None = None, timeout: float = 60):
        answer = self.client.request("GET" if body is None else "POST", path, json=body, timeout=timeout)
        if answer.status_code >= 400:
            return {"http": answer.status_code, "body": answer.text[:500]}
        return answer.json()

    def close(self) -> None:
        try:
            self.api("/api/exit", {}, timeout=20)
            self.proc.wait(60)
        except Exception:
            pass
        if self.proc.poll() is None:
            # The app and what it started (the server's node), when it did not end by itself.
            if WIN:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)
            else:
                self.proc.kill()
            self.proc.wait(30)
        self.client.close()
        self.out.close()


# An absolute path in a command's text: a drive path, or one from the root after a space or quote.
ABSOLUTE = re.compile(r"[A-Za-z]:[\\/][^\s'\"“”]*|(?<![^\s'\"“])/[^\s'\"”]+")


def inside(path: str, folder: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(folder.resolve())
    except (OSError, ValueError):
        return False


def answer(kind: str, card: dict, folder: Path) -> str:
    """ANSWERS, except that the person who asked about one folder says no to a folder or a command reaching outside
    it: a folder card for another folder, or a command naming a path elsewhere or running elsewhere on relative
    names."""
    if kind != "shell" or card.get("type") not in ("folder", "command"):
        return ANSWERS[kind]
    if card["type"] == "folder":
        return "yes" if inside(card.get("folder") or "", folder) else "no"
    paths = ABSOLUTE.findall(card.get("command") or "")
    named = all(inside(p, folder) for p in paths)
    return "yes" if named and (paths or inside(card.get("cwd") or "", folder)) else "no"


def turn(app: App, prompt: str, folder: Path) -> dict:
    """One chat turn, each card answered as `answer` says while it runs; the reply's JSON and the cards."""
    sent: dict = {}
    cards: list = []

    def post() -> None:
        try:
            sent["reply"] = app.api("/api/chat", {"text": prompt}, timeout=TURN_S)
        except httpx.HTTPError as error:
            sent["reply"] = {"error": f"the turn's request ended: {type(error).__name__}"}

    worker = threading.Thread(target=post, daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(0.5)
        for kind, route in CARD_ROUTES.items():
            card = app.api(route).get("card")
            if card and all(card["id"] != c["id"] for c in cards):
                said = answer(kind, card, folder)
                cards.append({"kind": kind, "id": card["id"], "type": card.get("type", ""),
                              "what": card.get("tool") or card.get("command") or card.get("folder")
                              or card.get("title") or "", "cwd": card.get("cwd", ""), "answer": said})
                app.api(route, _press(kind, card["id"], said))
    return {"reply": sent.get("reply") or {}, "cards": cards}


def one(task: dict, data: Path, work: Path, shared: Path, model_url: str, model: str, key: str,
        meter: Meter | None) -> dict:
    """A task from a fresh home and folder to verify's verdict, with what it took."""
    place = work / task["id"].replace("/", "__")
    shutil.rmtree(place, ignore_errors=True)
    place.mkdir(parents=True)
    folder = fresh_copy(data, task["category"], place / "files")
    home = place / "home"
    make_home(home, shared, model_url, model, key)
    rows_before = len(meter.rows) if meter else 0
    began = time.time()
    app = App(home, place / "app.log")
    try:
        app.api("/api/look/toured", {})
        # The connector, added as Settings' Connectors adds a pasted snippet.
        snippet = json.dumps({"mcpServers": {"filesystem": {"command": "npx", "args": ["-y", SERVER, str(folder)]}}})
        added = app.api("/api/mcp", {"add": snippet})
        assert any(s["name"] == "filesystem" for s in added.get("servers", [])), added
        result = turn(app, task["prompt"], folder)
        log = app.api("/api/mcp/log?n=200").get("rows", [])
    finally:
        app.close()
    seconds = round(time.time() - began, 1)
    passed, said = verify(task, folder)
    reply = result["reply"]
    entries = [m for m in reply.get("messages") or [] if m.get("role") != "user"]
    rows = meter.rows[rows_before:] if meter else []
    record = {
        "task": task["id"], "pass": passed, "seconds": seconds,
        "model_calls": len(rows), "reply_rounds": sum(1 for r in rows if "find_tools" in r["tools"]),
        "tokens_in": sum(r["in"] for r in rows), "tokens_out": sum(r["out"] for r in rows),
        "tokens_cached": sum(r["cached"] for r in rows), "cost": round(sum(r["cost"] for r in rows), 5),
        "refused_by_meter": sum(1 for r in rows if r["status"] == 429),
        "cards": len(result["cards"]), "cards_by_kind": _count(c["type"] or c["kind"] for c in result["cards"]),
        "mcp_calls": len([r for r in log if r.get("tool")]),
        "mcp_by_outcome": _count(r.get("outcome", "") for r in log if r.get("tool")),
        "steps": [s.get("line", "")[:80] for e in entries for s in e.get("activity") or []],
        "reply": str(reply.get("reply") or reply.get("error") or "")[:600],
        "verify": said[-600:],
    }
    # The context window the app worked with, and where it learned it (the host's model list, the catalogue...).
    from mibo import abilities, context
    found = next(home.rglob(abilities.FILE), None)
    record["window"] = list(context.window(abilities.record(found.parent if found else None,
                                                            f"{model_url}/chat/completions", model)))
    (place / "result.json").write_text(json.dumps({**record, "cards_shown": result["cards"], "mcp_log": log,
                                                   "messages": entries, "model_rows": rows}, ensure_ascii=False,
                                                  indent=1), encoding="utf-8")
    shutil.rmtree(home, ignore_errors=True)
    return record


def _count(values) -> dict:
    out: dict = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


# ---------------------------------------------------------------- check: the plumbing with a stand-in model

class Stub:
    """An OpenAI-compatible stand-in: a reply round (find_tools offered) makes the next call of `steps`, one per
    round, then answers in words; any other call (the reviewer, the search words, the start's measure) gets {}."""

    def __init__(self, port: int):
        self.steps: list = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                sent = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                message = {"role": "assistant", "content": "{}"}
                if any(t.get("function", {}).get("name") == "find_tools" for t in sent.get("tools") or []):
                    done = sum(1 for m in sent.get("messages") or [] if m.get("role") == "tool")
                    if done < len(stub.steps):
                        name, args = stub.steps[done]
                        message = {"role": "assistant", "content": "", "tool_calls": [{
                            "id": f"call_{done}", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
                    else:
                        message["content"] = "完成。"  # copy: voice
                data = json.dumps({"id": "stub", "object": "chat.completion", "model": "stub",
                                   "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
                                   "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                data = json.dumps({"data": [{"id": "stub"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()


def _held(task: dict, name: str):
    """A name the task's verify.py defines (its expected data), read without running its main."""
    scope: dict = {"__name__": "held"}
    exec(task["verify"].read_text(encoding="utf-8"), scope)
    return scope[name]


def answers(task: dict, folder: Path, right: bool) -> list:
    """The writes of a right (or a deliberately wrong) answer for the three plumbing tasks, as use_tool calls."""
    def write(path: Path, text: str) -> tuple:
        return "use_tool", {"server": "filesystem", "tool": "write_file",
                            "arguments": {"path": str(path), "content": text}}

    if task["id"] == "desktop/music_report":
        songs = _held(task, "EXPECTED_SONGS")
        if not right:
            songs = songs[::-1]
        lines = [f"{s['song_name']}:{s['popularity_score']:.3f}" for s in songs] + [s["song_name"] for s in songs[:5]]
        return [write(folder / "music" / "music_analysis_report.txt", "\n".join(lines))]
    if task["id"] == "file_context/uppercase":
        steps: list = [("use_tool", {"server": "filesystem", "tool": "create_directory",
                                     "arguments": {"path": str(folder / "uppercase")}})]
        counts = []
        for n in range(1, 11):
            name = f"file_{n:02d}.txt"
            text = (folder / name).read_text(encoding="utf-8")
            steps.append(write(folder / "uppercase" / name, text.upper() if right else text))
            counts.append(f"{name}:{len(text.split())}")
        return steps + [write(folder / "uppercase" / "answer.txt", "\n".join(counts))]
    if task["id"] == "file_context/file_merging":
        # The ten files verify.py expects, each after its name and one empty line between (the description's rules).
        names = _held(task, "get_expected_files")()
        if not right:
            names = names[1:]
        text = "\n\n".join(f"{n}\n{(folder / n).read_text(encoding='utf-8').strip()}" for n in names)
        return [write(folder / "merged_content.txt", text)]
    raise KeyError(task["id"])


def check(checkout: Path, data: Path, work: Path) -> int:
    every = tasks(checkout)
    untouched = []
    for task in every:
        folder = fresh_copy(data, task["category"], work / "untouched")
        passed, said = verify(task, folder)
        untouched.append({"task": task["id"], "untouched_passes": passed,
                          "last_line": said.strip().splitlines()[-1] if said.strip() else ""})
        print(f"untouched {task['id']}: {'PASSES (scorer cannot fail)' if passed else 'fails'}")
    shared = node_ready(work)
    stub = Stub(STUB_PORT)
    plumbing = []
    for name in ("desktop/music_report", "file_context/uppercase", "file_context/file_merging"):
        task = next(t for t in every if t["id"] == name)
        for right in (True, False):
            # The folder the stand-in writes into is the one the task gets: its path is known once copied.
            place = work / "plumbing" / task["id"].replace("/", "__")
            folder = place / "files" / task["category"]
            fresh_copy(data, task["category"], place / "files")
            stub.steps = [("find_tools", {"query": "write file", "server": "filesystem"})] + answers(task, folder, right)
            record = one({**task}, data, work / "plumbing", shared, f"http://127.0.0.1:{STUB_PORT}/v1", "stub", "stub",
                         None)
            plumbing.append({"task": name, "answer": "right" if right else "wrong", "pass": record["pass"],
                             "cards": record["cards_by_kind"], "mcp": record["mcp_by_outcome"]})
            print(f"plumbing {name} {'right' if right else 'wrong'}: {'pass' if record['pass'] else 'fail'} "
                  f"cards {record['cards_by_kind']} mcp {record['mcp_by_outcome']}")
    (work / "check.json").write_text(json.dumps({"untouched": untouched, "plumbing": plumbing}, ensure_ascii=False,
                                                indent=1), encoding="utf-8")
    faithful = not any(u["untouched_passes"] for u in untouched) and all(
        p["pass"] == (p["answer"] == "right") for p in plumbing)
    print("faithful" if faithful else "NOT faithful")
    return 0 if faithful else 1


# ---------------------------------------------------------------- run

def read_keys(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if value:
            out[name.strip()] = value.strip().strip('"')
    return out


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout.strip()


def sample(every: list[dict], size: int, seed: int) -> tuple[list[dict], dict]:
    """The drawn tasks, with a task whose scorer cannot be trusted here replaced by the next of its category in the
    draw's order; and the draw as the summary states it."""
    order = draw(every, size, seed)
    untrusted = UNTRUSTED_ON_WINDOWS if WIN else {}
    wanted, excluded = [], []
    for category in CATEGORIES:
        mine = [t for t in order if t["category"] == category]
        kept = [t for t in mine if t["id"] not in untrusted][:mine[0]["seats"]]
        excluded += [{"task": t["id"], "why": untrusted[t["id"]], "replaced_by": kept[-1]["id"]}
                     for t in mine[:mine[0]["seats"]] if t["id"] in untrusted]
        wanted += kept
    stated = {"rule": "stratified by category: seats by largest remainder of size*n/30, at least one per category, "
                      "ties and each category's order by random.Random(seed) shuffling its sorted tasks",
              "size": size, "seed": seed, "of": len(every),
              "categories": {c: {"tasks": sum(1 for t in order if t["category"] == c),
                                 "seats": next(t["seats"] for t in order if t["category"] == c),
                                 "order": [t["id"] for t in order if t["category"] == c]} for c in CATEGORIES},
              "drawn": [t["id"] for t in order if t["order"] < t["seats"]], "excluded": excluded,
              "measured": [t["id"] for t in wanted]}
    return wanted, stated


def published_on(ids: list[str]) -> dict:
    """Each published model's pass@1 (percent, its 4 runs) on just these tasks, from published.json, best first."""
    passes = json.loads((HERE / "published.json").read_text(encoding="utf-8"))["passes_of_4"]
    models = passes[ids[0]] if ids else {}
    scores = {m: round(100 * sum(passes[i].get(m, 0) for i in ids) / (4 * len(ids)), 1) for m in models}
    return dict(sorted(scores.items(), key=lambda kv: -kv[1]))


def run(args) -> int:
    every = tasks(args.mcpmark)
    drawn = None
    if args.sample:
        every, drawn = sample(every, args.sample, args.seed)
    wanted = [t for t in every if not args.only or t["id"] in args.only.split(",")]
    keys = read_keys(args.keys)
    work = args.work
    work.mkdir(parents=True, exist_ok=True)
    meter = Meter(keys["LLM_BASE_URL"], work / "model.jsonl", args.budget, PRICE)
    meter.serve(METER_PORT)
    shared = node_ready(work)
    out = args.out or work / "results.json"
    results, left = [], []
    began = time.time()
    for task in wanted:
        if meter.spent > args.budget - TASK_RESERVE:
            left.append(task["id"])
            continue
        print(f"{task['id']} ...", flush=True)
        try:
            record = one(task, args.data, work, shared, f"http://127.0.0.1:{METER_PORT}/v1", args.model,
                         keys["LLM_API_KEY"], meter)
        except Exception as error:
            record = {"task": task["id"], "pass": False, "error": f"{type(error).__name__}: {str(error)[:300]}"}
        results.append(record)
        print(f"  {'PASS' if record['pass'] else 'fail'} {record.get('seconds')}s ${record.get('cost')} "
              f"rounds {record.get('reply_rounds')} cards {record.get('cards')} spent ${meter.spent:.4f}", flush=True)
        summary = {"benchmark": "MCPMark Filesystem standard (MCPMark Verified task set), MCP condition",
                   "model": args.model, "commit": git("rev-parse", "--short", "HEAD"),
                   "mcpmark_commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=args.mcpmark,
                                                    capture_output=True, text=True).stdout.strip(),
                   "server": SERVER, "command": " ".join(["python", "tools/bench/mcpmark/run.py", *sys.argv[1:]]),
                   "started": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(began)),
                   "passed": sum(1 for r in results if r["pass"]), "ran": len(results), "of": len(wanted),
                   "spent": round(meter.spent, 5), "budget": args.budget, "left_out": left, "sample": drawn,
                   "published_on_these_tasks": published_on([r["task"] for r in results]), "tasks": results}
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    if left:
        print(f"left out, budget: {', '.join(left)}")
    print(f"passed {sum(1 for r in results if r['pass'])} of {len(results)}; spent ${meter.spent:.4f}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="run.py")
    sub = parser.add_subparsers(dest="what", required=True)
    one_ = sub.add_parser("fetch")
    one_.add_argument("--data", type=Path, required=True)
    for name in ("check", "run"):
        p = sub.add_parser(name)
        p.add_argument("--mcpmark", type=Path, required=True)
        p.add_argument("--data", type=Path, required=True)
        p.add_argument("--work", type=Path, required=True)
        if name == "run":
            p.add_argument("--keys", type=Path, required=True)
            p.add_argument("--budget", type=float, default=0.75)
            p.add_argument("--model", default=MODEL)
            p.add_argument("--only", default="")
            p.add_argument("--sample", type=int, default=0)
            p.add_argument("--seed", type=int, default=7)
            p.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.what == "fetch":
        fetch(args.data)
        return 0
    return check(args.mcpmark, args.data, args.work) if args.what == "check" else run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

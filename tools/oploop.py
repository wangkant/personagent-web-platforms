"""The operating benchmark's tasks (tools/opsite/tasks.py) sent through the chat on a real model, with no window:
the app served in this process on a home under --out, its browser a stand-in pane over headless Chromium
(tests/harness/pane.py) with the task's page in front, the message sent with "operate the browser" chosen as the
person chooses it beside the page, and each card answered through the page's own card route (yes, or no where the
task's `deny` matches its button). The site's verdict (tasks.check) decides; a task that reads something off also
needs those words in the reply. Every model call of the turn (the chat's rounds, the reviewer, a read step's
reader) goes through a counting pass-through (everyday.Meter), so `calls` is the whole turn's.

    python tools/oploop.py --env <.env with LLM_API_KEY, LLM_BASE_URL> [--model deepseek/deepseek-v4.1-flash]
        [--tasks R1,T01,O1] --out <folder> [--ports 59940] [--budget 0.10] [--tree <folder holding mibo>]

--ports gives three in a row: the app, the pass-through and the site. O1 is a plain request to open a named site
to look at, with nothing chosen (the third complaint of 2026-11-21): it passes when the site is in front in the
pane and no card came. The model is recorded as measured with tools and JSON, no pictures, thinking off and a
1,048,576-token window, the same for every tree. --tree runs another copy of the code (a snapshot from before a
change) under this harness. One line a task; <out>/results.jsonl keeps them with each reply and trace. The key
goes only into the run's home .env, which is deleted at the end; it is never printed."""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
TOKEN = "oploop-local-capability"
WINDOW = "oploop-window"
TASK_S = 300
# The third complaint: a named site opened for the person to look at themselves.
OPEN = {"id": "O1", "kind": "a plain request to open a named site to look at", "start": "/index.html",
        "ask": "打开知乎，我自己看看", "site": "zhihu.com"}  # copy: voice


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_env(path: Path) -> dict:
    return {k.strip(): v.strip() for k, _, v in (line.partition("=") for line in path.read_text().splitlines()) if v}


class Run:
    """The app on a scratch home, its model behind the pass-through, and the page API as its page calls it."""

    def __init__(self, home: Path, base: str, key: str, model: str, port: int):
        import uvicorn
        from everyday import no_windows
        from mibo import abilities, paths, situation, welcome
        from mibo import setup_wizard as sw
        from mibo.endpoints import chat_completions_url
        from mibo.panels import modelbar
        from mibo.storage import atomic_write_text
        self.home = home
        sw.write_env(home / ".env", {"LLM_BASE_URL": base, "LLM_API_KEY": key, "LLM_MODEL": model,
                                     "PERSONA_NAME": "Mibo", "AGENT_LANG": "zh", "ROAM_ENABLED": "false",
                                     "UPDATE_CHECK_ENABLED": "false"})
        paths.use_home(home)
        url = chat_completions_url(base)
        record = {"url": url, "model": model, "at": time.time(), "tools": "yes", "pictures": "no", "json": "yes",
                  "thinking": "no", "context": 1_048_576, "quirks": []}
        atomic_write_text(home / "runtime" / abilities.FILE,
                          json.dumps({"models": {abilities.key_of(url, model): record}}))

        async def idle(*args, **kwargs) -> None:
            return None
        # Nothing of the app's own beside the turn: no probe of public sites, no measure of the model, no feed
        # rounds or their judging, no sharing, remarks, walks or memory folds; every call counted is the turn's.
        situation.probe = idle
        modelbar.check_at_start = lambda workspace: None
        from mibo import consolidate, roam
        from mibo.panels import remarks
        from mibo.sources import catalog as sources
        sources.Rounds.loop = remarks.Remarks.loop = welcome.Workspace.share_loop = idle
        roam.Roam.loop = consolidate.Consolidation.loop_memory = idle
        no_windows()
        self.app = welcome.create_app(home, TOKEN, workspace=welcome.Workspace(home))
        self.server = uvicorn.Server(uvicorn.Config(self.app, host=welcome.PAGE_HOST, port=port, log_level="warning"))
        sock = welcome._listen(port)
        self.url = f"http://{welcome.PAGE_HOST}:{port}"
        atomic_write_text(home / "runtime" / "desktop" / "welcome.json",
                          json.dumps({"url": f"{self.url}/#token={TOKEN}"}))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [sock]}, daemon=True)
        self.thread.start()
        while not self.server.started:
            self.thread.join(0.05)
        self.header = welcome.TOKEN_HEADER

    def call(self, method: str, path: str, body: dict | None = None, own: bool = False, timeout: float = 30) -> dict:
        import httpx
        headers = {self.header: TOKEN, **({"X-Mibo-Window": WINDOW} if own else {})}
        got = httpx.request(method, self.url + path, headers=headers, json=body, timeout=timeout, trust_env=False)
        return got.json() if got.content else {}

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(30)


def one(run: Run, pane, site: str, task: dict, meter, tasks) -> dict:
    urllib.request.urlopen(urllib.request.Request(site + "/__reset", data=b"{}"), timeout=10).read()
    pane.open_tab(site + task["start"])
    run.app.state.window = SimpleNamespace(pane=pane, show=lambda: None, attended=lambda: True, frame_key=WINDOW,
                                           is_own_page=lambda key: key == WINDOW)
    # The browser's operating state is made anew with the window, as when the app starts.
    run.app.state.operating = None
    chat =run.call("POST", "/api/chats", {})["id"]
    seen = len(meter.rows)
    cards: list[dict] = []
    sent: dict = {}
    choices = [] if task["id"] == OPEN["id"] else ["browser"]

    def post() -> None:
        sent["answer"] = run.call("POST", "/api/chat", {"text": task["ask"], "chat": chat, "choices": choices},
                                  timeout=TASK_S)
    began = time.monotonic()
    thread = threading.Thread(target=post, daemon=True)
    thread.start()
    while thread.is_alive() and time.monotonic() - began < TASK_S:
        card = run.call("GET", "/api/browser/confirm").get("card")
        if card and all(card["id"] != c["id"] for c in cards):
            no = bool(task.get("deny")) and bool(re.search(task["deny"], str(card.get("button") or "")))
            cards.append({"id": card["id"], "yes": not no,
                          **{k: str(card.get(k) or "")[:300] for k in ("kind", "button", "to", "text", "what")}})
            run.call("POST", "/api/browser/confirm", {"id": card["id"], "yes": not no, "never": False}, own=True)
        thread.join(0.1)
    if thread.is_alive():
        # Past the task's time: the turn is stopped as the stop button stops it, before the next task.
        run.call("POST", "/api/chat/stop", {"chat": chat})
        thread.join(60)
    seconds = round(time.monotonic() - began, 1)
    answer = sent.get("answer") or {}
    entries = [m for m in answer.get("messages") or [] if m.get("role") != "user"]
    reply = str(answer.get("reply") or "")
    trace = [{"tool": s.get("tool"), "status": s.get("status"), "what": str(s.get("what") or "")[:120]}
             for e in entries for s in e.get("trace") or []]
    rows = meter.rows[seen:]
    if task["id"] == OPEN["id"]:
        front = next((t for t in pane.tabs() if t["front"]), {})
        verdict = {"ok": OPEN["site"] in str(front.get("url")) and not cards,
                   "why": f"front {str(front.get('url'))[:80]}; {len(cards)} cards", "reply_has": []}
    else:
        state = json.loads(urllib.request.urlopen(site + "/api/state", timeout=10).read())
        verdict = tasks.check(task["id"], state)
    read = all(re.sub(r"\s+", "", w) in re.sub(r"\s+", "", reply) for w in verdict.get("reply_has", []))
    return {"task": task["id"], "ok": bool(verdict["ok"] and read and seconds < TASK_S), "site": verdict["ok"],
            "read": read, "why": verdict["why"][:200], "calls": len(rows),
            "chat_calls": sum(1 for r in rows if r["tools"]), "other_calls": sum(1 for r in rows if not r["tools"]),
            "tools": [name for r in rows for name, _ in r["called"]], "in": sum(r["in"] for r in rows),
            "cost": round(sum(r["cost"] for r in rows), 6), "seconds": seconds, "cards": cards, "trace": trace,
            "reply": reply[:400]}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True)
    parser.add_argument("--model", default="deepseek/deepseek-v4.1-flash")
    parser.add_argument("--tasks", default="R1,T01,O1")
    parser.add_argument("--tree", default=str(HERE.parent))
    parser.add_argument("--out", required=True)
    parser.add_argument("--ports", type=int, default=59940)
    parser.add_argument("--budget", type=float, default=0.10, help="dollars; no new task past it")
    args = parser.parse_args(argv)
    sys.path[:0] = [str(Path(args.tree).resolve()), str(HERE.parent / "tests"), str(HERE)]
    from everyday import Meter
    from harness.pane import Pane as Standin
    from mibo import installed_browser

    class Pane(Standin):
        """The stand-in with the column calls the errand of the code before 2026-11-21 also made, for --tree."""

        def bar_address(self) -> str:
            return self.address()

        def op_result(self, *said) -> None:
            pass

        def op_step(self, *said) -> None:
            pass

        def op_steps(self) -> list:
            return []

        def ready(self, address: str = "") -> bool:
            # The page in front, or the start address opened in front when it is on another site.
            from urllib.parse import urlsplit
            if address and urlsplit(address).hostname != urlsplit(self.address()).hostname:
                self.open_tab(address)
            return bool(self.address().startswith("http"))

        def open(self, url: str) -> None:
            self.open_tab(url)

        def go(self, url: str) -> None:
            self.cdp("Page.navigate", {"url": url})
    tasks = load("opsite_tasks", HERE / "opsite" / "tasks.py")
    env = read_env(Path(args.env))
    out = Path(args.out).resolve()
    home = out / "home"
    home.mkdir(parents=True, exist_ok=True)
    meter = Meter(env["LLM_BASE_URL"], out / "model.jsonl", args.budget)
    meter.serve(args.ports + 1)
    site = f"http://127.0.0.1:{args.ports + 2}"
    server = subprocess.Popen([sys.executable, "-B", str(HERE / "opsite" / "serve.py"), str(args.ports + 2),
                               str(out / "site.log")])
    browser = installed_browser.find_browser()
    run = Run(home, f"http://127.0.0.1:{args.ports + 1}/v1", env["LLM_API_KEY"], args.model, args.ports)
    rows = []
    try:
        for name in args.tasks.split(","):
            if meter.spent >= args.budget:
                print(f"budget reached: {name} not run", flush=True)
                continue
            task = OPEN if name == OPEN["id"] else tasks.BY_ID[name]
            # A browser of its own for each task: no tab or place of the one before.
            pane = Pane.start(browser)
            try:
                row = one(run, pane, site, task, meter, tasks)
            finally:
                pane.stop()
            rows.append(row)
            with (out / "results.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{'OK ' if row['ok'] else 'no '} {name:<4} calls={row['calls']} (chat {row['chat_calls']}, other "
                  f"{row['other_calls']}) cards={len(row['cards'])} {row['seconds']}s ${row['cost']:.4f} "
                  f"{row['tools']} {row['why'][:70]!r}", flush=True)
    finally:
        run.close()
        server.terminate()
        # The key's file first: the app may still hold others of the home open.
        (home / ".env").unlink()
        shutil.rmtree(home, ignore_errors=True)
    calls = sum(r["calls"] for r in rows)
    print(f"{sum(r['ok'] for r in rows)}/{len(rows)} ok; {calls} model calls, {calls / max(len(rows), 1):.2f} a task; "
          f"spent ${meter.spent:.4f}; kept in {out}")
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))

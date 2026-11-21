"""Can a person take a turn back? A measuring script, run by hand.

Starts the app with no window on a fresh home and a stand-in model that makes a fixed set of file tool and command
calls over three turns, in a folder of the script's own (--work, on the drive whose Recycle Bin is checked):

  1. edit_file on an indented a.py (a card, pressed yes), write_file of a new note.txt
  2. a command overwriting b.txt (a card), a command making 日志 and moving the .log files into it (no card),
     Remove-Item of the folder 临时 (a card)
  3. delete_files of c.txt (a card)

Then it prints: whether what was deleted is in the Recycle Bin, what the fold 本次改动 lists for each turn and the
lines of a.py's difference, and after 撤销这次的改动 for turn 2, then 1 (from the API) and 3 (from the page, pressed
in headless Edge), whether every file is back byte for byte. A picture of the page goes to --shots.

  python tools/shell_undo.py --home F:/scratch/home --work F:/scratch/work --ports 59460 59461 59462 --shots F:/scratch
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.card_wait import Page  # noqa: E402

A_PY = "def total(rows):\n    s = 0\n    for row in rows:\n        if row > 0:\n            s += row\n    return s\n" + \
       "".join(f"# note {n}\n" for n in range(1, 30))


def fixture(work: Path) -> dict:
    """The folder as it starts, and the hash of every file in it."""
    shutil.rmtree(work, ignore_errors=True)
    (work / "临时" / "子").mkdir(parents=True)
    files = {"a.py": A_PY, "b.txt": "old text\n", "c.txt": "keep me\n", "x.log": "x\n", "y.log": "y\n",
             "临时/t1.txt": "t1\n", "临时/子/t2.txt": "t2\n"}
    for name, text in files.items():
        (work / name).write_text(text, encoding="utf-8")
    return state(work)


def state(work: Path) -> dict:
    return {str(p.relative_to(work)): hashlib.sha256(p.read_bytes()).hexdigest()[:12]
            for p in sorted(work.rglob("*")) if p.is_file()}


def turns(work: Path) -> dict:
    """The calls of each turn, in order; the stand-in makes the next one each round."""
    edit = {"path": "a.py", "folder": str(work), "old_string": "        if row > 0:\n            s += row",
            "new_string": "        if row >= 0:\n            s += row * 2"}
    return {
        "UNDO-1": [("edit_file", edit), ("edit_file", {**edit, "old_string": "# note 20", "new_string": "# note twenty"}),
                   ("write_file", {"path": "note.txt", "content": "made in turn 1\n", "folder": str(work)})],
        "UNDO-2": [("use_computer", {"command": "Set-Content b.txt 'new text'", "folder": str(work)}),
                   ("use_computer", {"command": "New-Item -ItemType Directory -Name 日志; Get-ChildItem *.log | "
                                                "Move-Item -Destination 日志", "folder": str(work)}),
                   ("use_computer", {"command": "Remove-Item 临时 -Recurse", "folder": str(work)})],
        "UNDO-3": [("delete_files", {"paths": ["c.txt"], "folder": str(work)})],
    }


def stand_in(port: int, work: Path) -> None:
    script = turns(work)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
            messages = body.get("messages") or []
            last_user = max((n for n, m in enumerate(messages) if m.get("role") == "user"), default=-1)
            said = json.dumps(messages[last_user].get("content"), ensure_ascii=False) if last_user >= 0 else ""
            done = sum(m.get("role") == "tool" for m in messages[last_user + 1:])
            calls = next((v for k, v in script.items() if k in said), [])
            offered = {t["function"]["name"] for t in body.get("tools") or []}
            message = {"role": "assistant", "content": "好了。"}  # copy: voice
            if done < len(calls) and calls[done][0] in offered:
                name, args = calls[done]
                message = {"role": "assistant", "content": "", "tool_calls": [{
                    "id": f"call_{done}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
            out = json.dumps({"id": "stub", "object": "chat.completion", "model": "stub",
                              "choices": [{"index": 0, "finish_reason": "tool_calls" if "tool_calls" in message
                                           else "stop", "message": message}],
                              "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            out = json.dumps({"data": [{"id": "stub"}]}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(out)

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def turn(page: Page, chat: str, text: str) -> dict:
    """One message; every card it raises is pressed yes. The reply's steps and the turn's name."""
    box: dict = {}
    worker = threading.Thread(target=lambda: box.update(page("chat", {"text": text, "chat": chat,
                                                                      "choices": ["computer"]}, timeout=900)))
    worker.start()
    cards = []
    while worker.is_alive():
        card = page("shell/card").get("card")
        if card:
            cards.append(card["zh"]["title"])
            page("shell/card", {"id": card["id"], "yes": True})
        worker.join(0.5)
    steps = [s for m in box.get("messages") or [] if m.get("role") == "assistant" for s in m.get("trace") or []]
    return {"cards": cards, "steps": [(s["tool"], s.get("status", ""), s["what"][:60]) for s in steps],
            "turn": next((s["what"] for s in steps if s["tool"] == "changes"), "")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--shots", required=True)
    parser.add_argument("--ports", nargs=3, type=int, required=True, help="page, server, stand-in model")
    args = parser.parse_args()
    home, work, shots = Path(args.home), Path(args.work), Path(args.shots)
    home.mkdir(parents=True, exist_ok=True)
    page_port, _, model_port = args.ports
    from mibo import settingsfile
    settingsfile.write(home, {
        "PAGE_PORT": page_port, "UPDATE_CHECK_ENABLED": "false", "ROAM_ENABLED": "false", "AGENT_LANG": "zh",
        "PERSONA_TZ_OFFSET_HOURS": "-8", "PERSONA_NAME": "Mibo", "LLM_BASE_URL": f"http://127.0.0.1:{model_port}/v1",
        "LLM_MODEL": "stub", "LLM_API_KEY": "stub"})
    (home / "runtime" / "desktop" / "welcome.json").unlink(missing_ok=True)
    first = fixture(work)
    threading.Thread(target=stand_in, args=(model_port, work), daemon=True).start()
    app = subprocess.Popen([sys.executable, "-c", "import sys; from mibo import welcome; "
                            f"sys.exit(welcome.main(['--no-browser','--port','{page_port}']))"],
                           env={**os.environ, "AGENT_HOME": str(home), "SHELL_OPEN_DRY": "1"},
                           cwd=Path(__file__).resolve().parents[1],
                           stdout=open(home.parent / "app.log", "w", encoding="utf-8"), stderr=subprocess.STDOUT)
    report: dict = {"start": first}
    try:
        for _ in range(240):
            if (home / "runtime" / "desktop" / "welcome.json").exists():
                break
            time.sleep(0.5)
        page = Page(home)
        for _ in range(120):
            try:
                page("state")
                break
            except OSError:
                time.sleep(0.5)
        page("look/toured", {})
        page("shell", {"on": True})
        page("shell", {"add": str(work)})
        # The main chat, which the page opens on.
        chat = "main"
        from mibo.shell.checkpoint import in_bin
        names = {}
        for text in turns(work):
            done = turn(page, chat, f"{text} 整理一下")  # copy: voice
            names[text] = done["turn"]
            report[text] = {k: v for k, v in done.items() if k != "turn"}
            report[text]["after"] = state(work)
        report["in_recycle_bin"] = {name: in_bin(str(work / name)) is not None for name in ("临时", "c.txt")}
        for text, name in names.items():
            view = page(f"shell/changes?turn={name}")
            report[text]["fold"] = [(i["op"], Path(i.get("dest") or i["path"]).name) for i in view["items"]]
            diff = next((i["diff"] for i in view["items"] if i["path"].endswith("a.py") and i.get("diff")), "")
            if diff:
                report["a.py diff"] = diff.split("\n")
        for text in ("UNDO-2", "UNDO-1"):
            undone = page("shell/undo", {"turn": names[text]})
            report[f"undo {text}"] = {"done": undone["done"], "left": undone["left"], "files": state(work)}
        report["page"] = page_undo(home, shots)
        report["end"] = state(work)
        report["all back"] = report["end"] == first
        print(json.dumps(report, ensure_ascii=False, indent=1))
        try:
            page("exit", {})
        except OSError:
            pass
        app.wait(30)
    finally:
        if app.poll() is None:
            app.kill()
    return 0 if report.get("all back") else 1


def page_undo(home: Path, shots: Path) -> dict:
    """The chat page in headless Edge: the folds under the replies, one opened with its difference (a picture), and
    撤销这次的改动 pressed on the last reply's fold."""
    from playwright.sync_api import sync_playwright
    url = json.loads((home / "runtime" / "desktop" / "welcome.json").read_text(encoding="utf-8"))["url"]
    with sync_playwright() as play:
        browser = play.chromium.launch_persistent_context(str(home.parent / "edge"), channel="msedge", headless=True,
                                                          viewport={"width": 900, "height": 1100})
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector(".chat-main .act-chg:not([hidden])", timeout=60000)
        time.sleep(2)
        folds = page.locator(".chat-main .act-chg:not([hidden])")
        heads = folds.locator(".act-chg-head").all_text_contents()
        first = folds.nth(0)
        first.locator(".act-chg-head").click()
        first.locator(".act-more-head").first.click()
        first.scroll_into_view_if_needed()
        time.sleep(0.5)
        first.screenshot(path=str(shots / "shell-undo-fold.png"))
        last = folds.nth(folds.count() - 1)
        last.locator(".act-chg-head").click()
        last.locator(".act-chg-foot button").click()
        last.locator(".act-chg-told").first.wait_for(timeout=30000)
        told = last.locator(".act-chg-told").all_text_contents()
        last.screenshot(path=str(shots / "shell-undo-undone.png"))
        browser.close()
    return {"folds": heads, "told": told}


if __name__ == "__main__":
    sys.exit(main())

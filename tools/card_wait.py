"""Does a card wait for its person while the page is out of sight? A measuring script, run by hand.

Starts the app with no window on a fresh home, a stand-in model that calls one tool per turn, and for each kind
(a command, another program, an MCP server) sends a turn whose step raises a card. The page then stays away (no
request for the card) for --away seconds, as a minimised window or a person in another program would; then the
card is asked for and pressed. Prints, per kind, whether the card was still up and how the step ended.

  python tools/card_wait.py --home F:/scratch/home --ports 59450 59451 59452 --away 40

Nothing is operated: the program card is answered no; the command deletes a file the script made in its own folder.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

TOOLS = {"shell": "use_computer", "desk": "use_program", "mcp": "use_tool"}
ECHO = '''from mcp.server.mcpserver import MCPServer
app = MCPServer("echo")
@app.tool()
def echo(text: str) -> str:
    """Says the text back."""
    return text
app.run()
'''


def stand_in(port: int, work: Path) -> None:
    """An OpenAI-shaped model: a turn whose message names a kind gets one call of that kind's tool, then a line."""
    calls = {"shell": {"command": "Remove-Item a.txt", "folder": str(work)},
             "desk": {"task": "在记事本里写一行字", "program": "notepad"},
             "mcp": {"server": "echo", "tool": "echo", "arguments": {"text": "hi"}}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
            messages = body.get("messages") or []
            offered = {t["function"]["name"] for t in body.get("tools") or []}
            last_user = max((n for n, m in enumerate(messages) if m.get("role") == "user"), default=-1)
            said = json.dumps(messages[last_user].get("content"), ensure_ascii=False) if last_user >= 0 else ""
            called = any(m.get("role") == "tool" for m in messages[last_user + 1:])
            kind = next((k for k in TOOLS if f"CARDWAIT-{k}" in said), "")
            message = {"role": "assistant", "content": "好的。"}  # copy: voice
            if kind and not called and TOOLS[kind] in offered:
                message = {"role": "assistant", "content": "", "tool_calls": [{
                    "id": f"call_{kind}", "type": "function",
                    "function": {"name": TOOLS[kind], "arguments": json.dumps(calls[kind], ensure_ascii=False)}}]}
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


class Page:
    """The app's API as its page calls it, with the page's token."""

    def __init__(self, home: Path) -> None:
        url = json.loads((home / "runtime" / "desktop" / "welcome.json").read_text(encoding="utf-8"))["url"]
        parts = urlsplit(url)
        self.base, self.token = f"{parts.scheme}://{parts.netloc}/api/", parts.fragment.partition("token=")[2]
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(self, path: str, body=None, timeout: float = 30):
        request = urllib.request.Request(self.base + path, method="GET" if body is None else "POST",
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "X-Mibo-Setup-Token": self.token})
        with self.opener.open(request, timeout=timeout) as answer:
            return json.loads(answer.read().decode() or "{}")


def press(page: Page, kind: str, card: dict) -> None:
    if kind == "shell":
        page("shell/card", {"id": card["id"], "yes": True})
    elif kind == "desk":
        page("programs/card", {"id": card["id"], "yes": False})
    else:
        page("mcp/card", {"id": card["id"], "answer": "once"})


def one(page: Page, kind: str, away: float, work: Path) -> dict:
    (work / "a.txt").write_text("cardwait", encoding="utf-8")
    chat = page("chats", {})
    chat = chat.get("id") or chat.get("chat")
    box: dict = {}
    began = time.monotonic()
    def send() -> None:
        try:
            box.update(page("chat", {"text": f"CARDWAIT-{kind}", "chat": chat, "choices": ["computer"]}, timeout=1800))
        except OSError as error:
            box["error"] = f"{error} {getattr(error, 'read', lambda: b'')().decode()[:200]}"

    turn = threading.Thread(target=send)
    turn.start()
    endpoint = {"shell": "shell/card", "desk": "programs/card", "mcp": "mcp/card"}[kind]
    # The page is away: it asks for nothing until `away` seconds have passed.
    turn.join(away)
    seen, presses = None, 0
    while turn.is_alive() and presses < 3:
        card = page(endpoint).get("card")
        if card:
            seen = round(time.monotonic() - began) if seen is None else seen
            press(page, kind, card)
            presses += 1
        turn.join(1.0)
    turn.join(120)
    steps = [s for m in box.get("messages") or [] if m.get("role") == "assistant" for s in m.get("trace") or []]
    return {"kind": kind, "away_s": away, "card_up_after_away": seen is not None, "pressed": presses,
            "step": steps[-1].get("status") if steps else box.get("error", "no reply"),
            "turn_s": round(time.monotonic() - began)}


# The page's own way of counting as hidden (app.js paQuiet): document.hidden, and visibilitychange told.
HIDE = "(hidden) => window.paQuiet(hidden)"


def page_run(home: Path, away: float, work: Path) -> dict:
    """The chat page itself in headless Edge: a command's turn is sent, the page is hidden (as a minimised window
    is), and the requests it makes for the card meanwhile are counted; then it is shown and the card pressed."""
    from playwright.sync_api import sync_playwright
    (work / "a.txt").write_text("cardwait", encoding="utf-8")
    url = json.loads((home / "runtime" / "desktop" / "welcome.json").read_text(encoding="utf-8"))["url"]
    asked: list[float] = []
    with sync_playwright() as play:
        browser = play.chromium.launch_persistent_context(str(home.parent / "edge"), channel="msedge", headless=True)
        page = browser.new_page()
        page.on("request", lambda r: "/api/shell/card" in r.url and r.method == "GET" and asked.append(time.monotonic()))
        page.goto(url)
        try:
            page.wait_for_selector(".chat-main .chat-input", timeout=60000)
        except Exception:
            page.screenshot(path=str(home.parent / "page-not-ready.png"))
            raise
        # The chat's history is drawn first, as a person reads the page before typing.
        time.sleep(4)
        page.fill(".chat-main .chat-input", "CARDWAIT-shell")
        page.press(".chat-main .chat-input", "Enter")
        time.sleep(1.5)
        page.evaluate(HIDE, True)
        hidden_at = time.monotonic()
        time.sleep(away)
        page.evaluate(HIDE, False)
        while_hidden = sum(t > hidden_at + 0.5 for t in asked)
        card = page.locator(".shell-card:not(.answered) .pa-btn-primary")
        shown = True
        try:
            card.first.click(timeout=15000)
        except Exception:
            shown = False
        try:
            page.wait_for_selector(".chat-main .act", timeout=120000)
        except Exception:
            page.screenshot(path=str(home.parent / "page-no-reply.png"))
            raise
        status = page.locator(".chat-main .act-status").all_text_contents()
        browser.close()
    return {"kind": "page", "away_s": away, "card_requests_while_hidden": while_hidden,
            "card_shown_after_away": shown, "step_status": status[-1] if status else "done"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--ports", nargs=3, type=int, required=True, help="page, server, stand-in model")
    parser.add_argument("--away", type=float, default=40)
    parser.add_argument("--kinds", default="shell,desk,mcp,page")
    args = parser.parse_args()
    home = Path(args.home)
    work = home.parent / "work"
    work.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    page_port, _, model_port = args.ports
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mibo import settingsfile
    settingsfile.write(home, {
        "PAGE_PORT": page_port, "UPDATE_CHECK_ENABLED": "false", "ROAM_ENABLED": "false", "AGENT_LANG": "zh",
        "PERSONA_TZ_OFFSET_HOURS": "-8", "PERSONA_NAME": "Mibo", "LLM_BASE_URL": f"http://127.0.0.1:{model_port}/v1",
        "LLM_MODEL": "stub", "LLM_API_KEY": "stub"})
    (home.parent / "echo_server.py").write_text(ECHO, encoding="utf-8")
    (home / "mcp.json").write_text(json.dumps({"mcpServers": {"echo": {
        "command": sys.executable, "args": [str(home.parent / "echo_server.py")]}}}), encoding="utf-8")
    # The page's address and token are written anew at each start.
    (home / "runtime" / "desktop" / "welcome.json").unlink(missing_ok=True)
    threading.Thread(target=stand_in, args=(model_port, work), daemon=True).start()
    app = subprocess.Popen([sys.executable, "-c", "import sys; from mibo import welcome; "
                            f"sys.exit(welcome.main(['--no-browser','--port','{page_port}']))"],
                           env={**os.environ, "AGENT_HOME": str(home)}, cwd=Path(__file__).resolve().parents[1],
                           stdout=open(home.parent / "app.log", "w", encoding="utf-8"), stderr=subprocess.STDOUT)
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
        if "desk" in args.kinds:
            page("programs", {"on": True})
        for kind in args.kinds.split(","):
            outcome = page_run(home, args.away, work) if kind == "page" else one(page, kind, args.away, work)
            print(json.dumps(outcome, ensure_ascii=False), flush=True)
        try:
            page("exit", {})
        except OSError:
            pass
        app.wait(30)
    finally:
        if app.poll() is None:
            app.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())

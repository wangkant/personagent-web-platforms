"""act in the real app window, checked from nothing with stand-ins: a scratch home made here, a stand-in model that
answers as a reply model would (open the board out of sight, act on it, then reply to Jay) and a stand-in site on
this computer. Windows only: the WebView2 window opens off screen (tools/window_driver.py), and nothing here moves
the pointer, presses a key of this computer or takes the focus: the person's own steps are the page API, as the
chat page calls it.

    python tools/actwindow.py <scratch home> --out <folder> [--ports 59935]

Three ports from --ports on: the app's page, the site, the stand-in model (the app's service takes the one after
the page's). What it must show, each a PASS or FAIL line in <out>/walk.log:

    front stays     steps on a page opened out of sight leave the tab the person has in front where it was
    no focus        the window in front of this computer is the same before and after the turns
    noticed         the act's answer to the model names what the press sent (POST, the other host, its status)
    page now        the act's answer carries the board as the press left it (Followers 128)
    card shown      the reply's send waits on a card in the column beside the page, which shows the step's line
    card answered   a yes pressed on that card (the page's own card route) sends the reply once
    let go          once the turn ends the column holds no operation

The last lines say how many checks failed and list them; the exit code is 0 when none did, else 1."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FRONT = """<!doctype html><meta charset="utf-8"><title>Tide tables</title><h1>Tides</h1><p>High water 06:12.</p>"""
BOARD = """<!doctype html><meta charset="utf-8"><title>Board</title>
<nav><button id="mine">Mine</button></nav><main id="out">Signed in</main>
<textarea id="r1" aria-label="Reply to Jay"></textarea> <button id="send1">Reply</button>
<script>
mine.onclick = () => { fetch("http://localhost:PORT/api/monitor", {method: "POST", mode: "no-cors",
  headers: {"Content-Type": "text/plain"}, body: '{"gpu": "ANGLE"}'}); out.textContent = "Followers 128"; };
send1.onclick = () => fetch("/api/reply", {method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({to: "Jay", text: r1.value})});
</script>"""
REPLY = "See you Sunday"
# The reviewer asks, so the send is put on a card.
ASK = {"kind_asked": True, "target_named": True, "text_given": True, "nothing_added": True, "decision": "ask",
       "does": "comment", "reason": "stand-in"}


class Site(BaseHTTPRequestHandler):
    posts: list = []

    def do_GET(self):
        page = BOARD if self.path.startswith("/board") else FRONT
        raw = page.replace("PORT", str(self.server.server_address[1])).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        Site.posts.append((self.path, body))
        self.send_response(204)
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def ref(messages: list, pattern: str) -> int:
    """The ref the latest tool result gives a control."""
    for m in reversed(messages):
        found = re.search(pattern, str(m.get("content") or "")) if m.get("role") == "tool" else None
        if found:
            return int(found.group(1))
    return 0


class Model(BaseHTTPRequestHandler):
    """The stand-in model: the reply's rounds as a reply model would take them, the reviewer asking, the rest a
    short line. Every act's answer is kept for the checks."""
    site = ""
    acted: list[str] = []

    def log_message(self, *args) -> None:
        pass

    def _answer(self, message: dict) -> None:
        data = json.dumps({"id": "stub", "object": "chat.completion", "model": "stub", "choices": [{
            "index": 0, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
            "message": {"role": "assistant", **message}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _call(self, name: str, args: dict) -> None:
        self._answer({"content": "", "tool_calls": [{"id": f"c{time.monotonic_ns()}", "type": "function",
                                                      "function": {"name": name, "arguments": json.dumps(args)}}]})

    def do_GET(self) -> None:
        self._answer({"content": ""})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        messages = body.get("messages") or []
        system = str((messages[:1] or [{}])[0].get("content") or "")
        if system.startswith("You review ONE action"):
            return self._answer({"content": json.dumps(ASK)})
        if not body.get("tools"):
            return self._answer({"content": "OK"})
        last = messages[-1]
        asked = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
        if last.get("role") == "tool" and "act" in [c["function"]["name"] for c in
                                                    (messages[-2].get("tool_calls") or [])]:
            Model.acted.append(str(last.get("content") or ""))
            return self._answer({"content": "Done."})
        if "press Mine" in asked and last.get("role") == "user":
            return self._call("open", {"name": f"{Model.site}/board"})
        if "press Mine" in asked:
            return self._call("act", {"place": "p1", "steps": [
                {"do": "click", "ref": ref(messages, r'\[(\d+)\] button "Mine"'), "what": "open Mine"}]})
        if "Reply to Jay" in asked and last.get("role") == "user":
            return self._call("look", {"place": "p1"})
        if "Reply to Jay" in asked:
            return self._call("act", {"place": "p1", "steps": [
                {"do": "type", "ref": ref(messages, r'\[(\d+)\] textbox "Reply to Jay"'), "text": REPLY,
                 "what": "write the reply"},
                {"do": "click", "ref": ref(messages, r'\[(\d+)\] button "Reply"'), "what": "send the reply to Jay"}]})
        return self._answer({"content": "OK"})


def walk(d, home: Path, site: str) -> None:
    import ctypes

    import httpx

    from mibo.welcome import TOKEN_HEADER
    url = json.loads((home / "runtime" / "desktop" / "welcome.json").read_text(encoding="utf-8"))["url"]
    base, _, token = url.partition("/#token=")
    window = d.js("sessionStorage.getItem('mibo-frame')") or ""

    def api(path: str, payload=None, own: bool = False, timeout: float = 120) -> dict:
        headers = {TOKEN_HEADER: token, **({"X-Mibo-Window": window} if own else {})}
        return httpx.request("POST" if payload is not None else "GET", base + "/api/" + path, json=payload,
                             headers=headers, timeout=timeout, trust_env=False).json()
    pane = d.app._browser
    d.app.open_link(site + "/front")
    time.sleep(3)
    front = pane.address()
    focus = ctypes.windll.user32.GetForegroundWindow()

    api("chat", {"text": "Open my board and press Mine", "chat": "main"}, timeout=300)
    d.check("front stays", pane.address() == front, pane.address())
    acted = Model.acted[-1] if Model.acted else ""
    d.check("noticed", "Noticed:" in acted and "POST localhost/api/monitor" in acted, acted[:300])
    d.check("page now", "Followers 128" in acted, acted[:300])

    sent: dict = {}
    turn = threading.Thread(target=lambda: sent.update(api("chat", {"text": f"Reply to Jay on my board: {REPLY}",
                                                                   "chat": "main"}, timeout=300)), daemon=True)
    turn.start()
    began, card = time.monotonic(), None
    while turn.is_alive() and card is None and time.monotonic() - began < 120:
        card = api("browser/confirm").get("card")
        time.sleep(0.3)
    column = (pane.op or {}).get("card") or {}
    d.check("card shown", bool(card) and bool(column) and card.get("what") == "send the reply to Jay",
            {"card": card, "column": column})
    if card:
        api("browser/confirm", {"id": card["id"], "yes": True, "never": False}, own=True)
    turn.join(120)
    replies = [json.loads(body)["text"] for path, body in Site.posts if path == "/api/reply"]
    d.check("card answered", replies == [REPLY], replies)
    d.check("let go", pane.op is None, pane.op)
    d.check("no focus", ctypes.windll.user32.GetForegroundWindow() == focus)


def main() -> int:
    home, out = Path(sys.argv[1]).resolve(), Path(sys.argv[sys.argv.index("--out") + 1]).resolve()
    base = int(sys.argv[sys.argv.index("--ports") + 1]) if "--ports" in sys.argv else 59935
    site = f"http://127.0.0.1:{base + 2}"
    out.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    model_url = f"http://127.0.0.1:{base + 3}/v1"
    (home / ".env").write_text(
        f"PAGE_PORT={base}\nSERVER_PORT={base + 1}\nUPDATE_CHECK_ENABLED=false\nROAM_ENABLED=false\nAGENT_LANG=en\n"
        f"PERSONA_NAME=Mibo\nLLM_BASE_URL={model_url}\nLLM_MODEL=stub\nLLM_API_KEY=stub\n", encoding="utf-8")
    import window_driver

    from mibo import abilities
    from mibo.endpoints import chat_completions_url
    from mibo.welcome import save_app_settings
    desktop = home / "runtime" / "desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    save_app_settings(desktop, toured=True, pet_shown=False)
    # The stand-in model as a finished ability check keeps it: the app measures nothing at the start.
    url = chat_completions_url(model_url)
    (home / "runtime" / abilities.FILE).write_text(json.dumps({"models": {abilities.key_of(url, "stub"): {
        "url": url, "model": "stub", "at": time.time(), "tools": "yes", "pictures": "no", "json": "yes",
        "thinking": "no", "context": 128000, "quirks": []}}}), encoding="utf-8")
    Model.site = site
    servers = [ThreadingHTTPServer(("127.0.0.1", base + 2), Site), ThreadingHTTPServer(("127.0.0.1", base + 3), Model)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    failed = window_driver.run(home, lambda d: walk(d, home, site), out=out, lang="en")
    for server in servers:
        server.shutdown()
        server.server_close()
    lines = (out / "walk.log").read_text(encoding="utf-8").splitlines() if (out / "walk.log").exists() else []
    print(f"\n{failed} check(s) failed" + "".join(f"\n{line}" for line in lines if line.startswith("FAIL")))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

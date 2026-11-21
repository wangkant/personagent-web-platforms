"""The errand's `read` in the app's own browser pane, in the real app window (Windows; the window is kept off
screen, with no figure on the desktop): a profile whose numbers are in its first screen above a feed that loads
without end (tools/opsite/t/t41-profile.html), asked for its account numbers.

    python tools/panereading.py <scratch home> --out <folder> [--site-port 59413] [--model-port 59412]

The home's settings must name the stand-in model started here (LLM_BASE_URL=http://127.0.0.1:<model-port>/v1). The
stand-in plans a read and then done; asked to read, it answers from the numbers it was sent. results.json holds
how many reading calls the read took, whether the numbers reached the first one, how far the page scrolled while
it ran (the furthest any scroller went), the steps and the reply. The last lines say what held; it exits 1 when
the read took more than one call, scrolled, or did not carry the numbers."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from package_smoke import FakeModel  # noqa: E402

PAGE = "/t/t41-profile.html"
ASK = "看看我这个主页上显示的账号数据：昵称、关注数、粉丝数、获赞数"
NUMBERS = re.compile(r"关注\s*(\d+).*?粉丝\s*(\d+).*?获赞\s*(\d+)", re.S)
NOW = "[...document.querySelectorAll('#chat-view .chat')].find((n) => !n.hidden)"
# Watches the furthest any scroller of the site went (each may be put back after), in px.
WATCH = """(() => { window.__miboFar = 0; document.addEventListener('scroll', (e) => {
  const t = e.target === document ? document.scrollingElement : e.target;
  window.__miboFar = Math.max(window.__miboFar, t.scrollTop); }, true); return 1; })()"""
SEEN: dict = {"reads": [], "steps": 0}


class StandIn(FakeModel):
    """Answers 好的。 in the chat; asked to operate a page, read and then done; asked to read, the numbers sent."""

    def do_POST(self) -> None:
        sent = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path.endswith("/embeddings"):
            self._send({"data": [{"embedding": [0.1] * 8, "index": 0}]})
            return
        messages = sent.get("messages") or []
        system = next((str(m.get("content")) for m in messages if m.get("role") == "system"), "")
        last = next((m for m in reversed(messages) if m.get("role") == "user"), {})
        said = last.get("content") if isinstance(last.get("content"), str) else json.dumps(last.get("content"), ensure_ascii=False)
        content = json.dumps({"reply": "好的。"}, ensure_ascii=False)
        if system.startswith("You read a web page"):
            SEEN["reads"].append(said)
            got = NUMBERS.search(said)
            content = json.dumps({"answer": "关注 {} 粉丝 {} 获赞 {}".format(*got.groups()) if got else "",
                                  "found": "yes" if got else "no"}, ensure_ascii=False)
        elif system.startswith("You operate a web page"):
            content = operate(said)
        elif system.startswith("The person asked for a task in a site's page"):
            content = json.dumps({"use": 0})
        self._send({"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
                    "usage": {"prompt_tokens": len(json.dumps(messages)) // 2, "completion_tokens": 20}})


def operate(prompt: str) -> str:
    if "Steps so far" not in prompt:
        SEEN["steps"] = 0
    SEEN["steps"] += 1
    found = NUMBERS.search(prompt.split("The page now:", 1)[0])
    if SEEN["steps"] == 1 or not found:
        action = {"do": "read", "question": "账号数据：昵称、关注数、粉丝数、获赞数"}
    else:
        action = {"do": "done", "success": True, "result": "关注 {}，粉丝 {}，获赞 {}".format(*found.groups()),
                  "expect": f"粉丝 {found.group(2)}"}
    return json.dumps({"note": "读取账号数据", "keep": "", "actions": [action]}, ensure_ascii=False)


def page_value(d, expression: str):
    try:
        answer = d.app._browser.cdp("Runtime.evaluate", {"expression": expression, "returnByValue": True}, timeout=8)
        return (answer.get("result") or {}).get("value")
    except RuntimeError as error:
        return str(error)


def walk(d, site: str, results: dict) -> None:
    time.sleep(3)
    d.app.open_link(site + PAGE)
    d.until("document.querySelector('.browse') && !document.querySelector('.browse').hidden", 20)
    time.sleep(5)
    results["opened"] = page_value(d, "location.pathname")
    page_value(d, WATCH)
    d.shot("open")
    d.type_into("#chat-view .chat:not([hidden]) .chat-input", ASK)
    d.key("Enter", 13)
    began = time.time()
    time.sleep(1.2)
    while time.time() - began < 90 and d.js("!!document.querySelector('.line.typing')"):
        time.sleep(0.5)
    time.sleep(1.5)
    d.shot("replied")
    last = d.js("(() => { const chat = %s; const said = [...chat.querySelectorAll('.say')].at(-1); const act = [...chat.querySelectorAll('.act')].at(-1);"
                " return JSON.stringify({ reply: said ? said.innerText.trim() : '', steps: act ? act.innerText.trim() : '' }); })()" % NOW)
    first = SEEN["reads"][0] if SEEN["reads"] else ""
    results.update({"reading_calls": len(SEEN["reads"]), "numbers_in_first_read": bool(NUMBERS.search(first)),
                    "scrolled_px": page_value(d, "window.__miboFar"), "seconds": round(time.time() - began, 1),
                    **json.loads(last or "{}")})
    d.log("read: " + json.dumps(results, ensure_ascii=False)[:900])


def main() -> int:
    args = sys.argv[1:]
    home, out = Path(args[0]), Path(args[args.index("--out") + 1])
    site_port = int(args[args.index("--site-port") + 1]) if "--site-port" in args else 59413
    model_port = int(args[args.index("--model-port") + 1]) if "--model-port" in args else 59412
    out.mkdir(parents=True, exist_ok=True)
    import webview
    import window_driver

    from mibo import window as appwindow
    from mibo.welcome import save_app_settings
    desktop = home / "runtime" / "desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    save_app_settings(desktop, toured=True, pet_shown=False)
    made = appwindow.AppWindow.__init__

    def no_figure(self, *a, **k):
        made(self, *a, **k)
        self.pet = False
    appwindow.AppWindow.__init__ = no_figure
    create = webview.create_window
    webview.create_window = lambda *a, **k: create(*a, **{**k, "x": -9000, "y": 60})
    window_driver.Model = StandIn
    window_driver.ThreadingHTTPServer = lambda address, handler: ThreadingHTTPServer((address[0], model_port), handler)
    site = f"http://127.0.0.1:{site_port}"
    server = subprocess.Popen([sys.executable, str(Path(__file__).with_name("opsite") / "serve.py"), str(site_port),
                               str(out / "site.log")])
    results: dict = {}
    try:
        failed = window_driver.run(home, lambda d: walk(d, site, results), out=out, offscreen=False, lang="zh")
    finally:
        server.terminate()
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    held = {"the read took one reading call": results.get("reading_calls") == 1,
            "the numbers were in what that call was sent": results.get("numbers_in_first_read") is True,
            "nothing on the page scrolled": results.get("scrolled_px") == 0,
            "the chat shows the numbers": bool(NUMBERS.search(f"{results.get('reply')} {results.get('steps')}"))}
    for what, ok in held.items():
        print(f"{'held' if ok else 'FAILED'}: {what}")
    return 1 if failed or not all(held.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The chat beside a page in the app's browser, measured in the real app window (Windows; the window is kept
off screen, with no figure on the desktop).

    python tools/panedock.py <scratch home> --out <folder> [--site-port 59409] [--model-port 59408]

The home's settings must name the stand-in model started here (LLM_BASE_URL=http://127.0.0.1:<model-port>/v1). It
opens a page of tools/opsite and writes down, in results.json:
  open     the window before and after (does it make room for a site laid out for 1280?), the site's own width
           and zoom, and whether the chat's box is in sight with the page;
  request  a request typed in the chat beside the page, done in the page: was the page in sight all the way,
           the card, the site's own verdict, the reply and its steps in the chat;
  sidebar  a new chat chosen with the page in front (it shows beside the page), then Settings (the page waits
           behind its chip) and back;
  minimize the page put behind its chip and brought back: the window's size each way, the page as it was;
  narrow   the window made narrow: the chat laid over the page, the site not made narrower by it, and the same
           request sent from there (the chat out of the page's way while it acts, back for the card);
  kept     a size given to the window with a site in front, used again the next time one opens;
  zoom     one browser step of zoom for the site, still there on its next page."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from package_smoke import FakeModel  # noqa: E402

ASK = "给 Alice 发消息：明天见"
# The stand-in model's fixed plan for that request, read off the page lines of the operating prompt.
PLAN = [("click", r'\[(\d+)\] link "Alice"'), ("type", r'\[(\d+)\] textbox', "明天见"),
        ("click", r'\[(\d+)\] button "发送"'), ("done", "已向 Alice 发送“明天见”。")]
NOW = "[...document.querySelectorAll('#chat-view .chat')].find((n) => !n.hidden)"
# The app page: where the pane's page area and the chat are, and whether the chat's box can be reached.
PAGE = """(() => { const area = document.querySelector('.browse-area'), pane = document.querySelector('.browse');
  const chat = %s, input = chat && chat.querySelector('.chat-input');
  const box = (n) => { if (!n || !n.getClientRects().length) return null; const r = n.getBoundingClientRect();
    return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
  const r = input && input.getClientRects().length ? input.getBoundingClientRect() : null;
  const top = r && document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return JSON.stringify({ window: [innerWidth, innerHeight], pane: !!pane && !pane.hidden, area: box(area),
    chat: box(document.getElementById('chat-view')), body: document.body.className,
    box_in_sight: !!top && (top === input || input.contains(top)),
    chip: (document.querySelector('.browse-context')?.textContent || '').trim(),
    back_chip: (() => { const c = document.querySelector('.browse-return'); return c && !c.hidden ? c.textContent.trim() : ''; })(),
    run: (document.querySelector('.browse-run .browse-run-mode')?.textContent || '').trim(),
    strip: (() => { const s = document.querySelector('.browse-run-pill'); return s && !s.hidden ? s.textContent.trim() : ''; })() }); })()""" % NOW
STEP: dict = {"n": 0}


class StandIn(FakeModel):
    """Answers 好的。 in the chat; asked to operate a page, the next step of PLAN."""

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
        if system.startswith("You operate a web page"):
            content = operate(said)
        elif system.startswith("The person asked for a task in a site's page"):
            content = json.dumps({"use": 0})
        self._send({"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
                    "usage": {"prompt_tokens": len(json.dumps(messages)) // 2, "completion_tokens": 20}})


def operate(prompt: str) -> str:
    if "Steps so far" not in prompt:
        STEP["n"] = 0
    step = PLAN[min(STEP["n"], len(PLAN) - 1)]
    if step[0] == "done":
        action = {"do": "done", "success": True, "result": step[1]}
    else:
        found = re.search(step[1], prompt.split("The page now:", 1)[-1])
        if not found:
            return json.dumps({"note": "等待", "keep": "", "actions": [{"do": "wait"}]}, ensure_ascii=False)
        action = {"do": step[0], "id": int(found.group(1)), "element": "控件"}
        if step[0] == "type":
            action["text"] = step[2]
    STEP["n"] += 1
    return json.dumps({"note": "给 Alice 发消息", "keep": "", "actions": [action]}, ensure_ascii=False)


def get(url: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                     method="GET" if body is None else "POST")
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=30) as answer:
        return json.loads(answer.read().decode() or "{}")


def window_facts(d) -> dict:
    """The window's form and the pane's WebView as they are, read on the window's thread, in device px."""
    pane, box = d.app._browser, {}

    def read() -> None:
        form, view = d.app._window.native, pane._view
        box.update(form=[form.Width, form.Height], left=form.Left, webview=view is not None)
        if view is not None:
            box.update(view=[view.Width, view.Height], shown=bool(view.Visible),
                       zoom=round(float(view.ZoomFactor), 3), cut=view.Region is not None,
                       url=str(pane._core.Source) if pane._core is not None else "")
    d._ui(read)
    return box


def site_facts(d) -> dict:
    """The site's own view: how wide it is laid out, and whether a layout made for 1280 px fits without scrolling
    sideways."""
    def value(expression: str):
        try:
            answer = d.app._browser.cdp("Runtime.evaluate", {"expression": expression, "returnByValue": True}, timeout=8)
            return (answer.get("result") or {}).get("value")
        except RuntimeError as error:
            return str(error)
    wide = value("window.innerWidth")
    return {"site_width": wide, "fits_1280": isinstance(wide, (int, float)) and wide >= 1280}


def look(d, name: str) -> dict:
    row = {**window_facts(d), **json.loads(d.js(PAGE) or "{}")}
    if row.get("webview") and row.get("shown"):
        row.update(site_facts(d))
    d.shot(name)
    d.log(f"{name}: " + json.dumps(row, ensure_ascii=False))
    return row


def request(d, site: str, name: str) -> dict:
    """ASK typed in the chat beside the page and sent; its card answered with a yes. What was seen meanwhile."""
    get(site + "/__reset", {})
    d.type_into("#chat-view .chat:not([hidden]) .chat-input", ASK)
    d.key("Enter", 13)
    seen, cards, strips, over, began = [], [], [], [], time.time()
    time.sleep(1.2)
    while time.time() - began < 90:
        facts, page = window_facts(d), json.loads(d.js(PAGE) or "{}")
        seen.append(bool(facts.get("shown")) and bool(page.get("pane")))
        if page.get("strip"):
            strips.append(page["strip"])
        # While it acts with no card waiting: is the chat lying over the page?
        if page.get("run") in ("正在操作", "Working on it") or page.get("strip"):
            over.append(bool(facts.get("cut")))
        text = d.js("(() => { const c = document.querySelector('.browse-run-card .op-card'); return c && !c.dataset.seen ? c.innerText : ''; })()")
        if text:
            cards.append(" / ".join(str(text).split()))
            look(d, f"{name}-card")
            d.click(".browse-run-card .op-card .pa-btn-primary")
            d.js("(() => { const c = document.querySelector('.browse-run-card .op-card'); if (c) c.dataset.seen = '1'; })()")
        if not d.js("!!document.querySelector('.line.typing')"):
            break
        time.sleep(0.5)
    time.sleep(1.5)
    verdict = get(site + "/__check?task=R1")
    after = look(d, f"{name}-replied")
    last = d.js("(() => { const chat = %s; const said = [...chat.querySelectorAll('.say')].at(-1); const act = [...chat.querySelectorAll('.act')].at(-1);"
                " return JSON.stringify({ reply: said ? said.innerText.trim() : '', steps: act ? act.innerText.trim() : '',"
                " mine: [...chat.querySelectorAll('.me')].at(-1)?.innerText.trim() || '' }); })()" % NOW)
    row = {"page_in_sight_all_the_way": bool(seen) and all(seen), "looks": len(seen), "cards": cards,
           "line_under_the_page": sorted(set(strips))[:4], "chat_over_the_page_while_it_acted": any(over),
           "site_got_it": verdict.get("ok"), "site_says": str(verdict.get("why"))[:160],
           "seconds": round(time.time() - began, 1), **json.loads(last or "{}"), "after": after}
    d.log(f"{name}: " + json.dumps({k: v for k, v in row.items() if k != "after"}, ensure_ascii=False)[:900])
    return row


def walk(d, home: Path, site: str, results: dict) -> None:
    pane = d.app._browser
    time.sleep(3)
    results["before"] = look(d, "chat")
    d.app.open_link(site + "/messages.html")
    d.until("document.querySelector('.browse') && !document.querySelector('.browse').hidden", 20)
    time.sleep(5)
    results["open"] = look(d, "open")
    # A tree from before the chat stood beside the page has none of the rest.
    if not d.js("!!document.querySelector('.browse-beside')"):
        return

    # ---- a request typed in the chat beside the page
    results["request"] = request(d, site, "request")

    # ---- the sidebar with a page in front: a new chat shows beside the page; Settings puts the page behind its chip
    d.click("#side-new")
    time.sleep(2)
    new_chat = look(d, "new-chat")
    d.click("#side-settings")
    time.sleep(2)
    settings = look(d, "settings")
    d.click(".browse-return-go")
    time.sleep(2.5)
    results["sidebar"] = {"new_chat": new_chat, "settings": settings, "back": look(d, "back-from-settings")}

    # ---- minimized, and back
    d.click(".browse-minimize")
    time.sleep(2)
    away = look(d, "minimized")
    d.click(".browse-return-go")
    time.sleep(2.5)
    back = look(d, "back")
    results["minimize"] = {"away": away, "back": back}

    # ---- the window made narrow: the chat lies over the page
    def narrow() -> None:
        from System.Drawing import Size
        form = d.app._window.native
        form.Size = Size(round(760 * form._scale), form.Height)
    d._ui(narrow)
    time.sleep(2)
    over = look(d, "narrow-chat-over")
    d.click(".browse-beside")
    time.sleep(1.5)
    alone = look(d, "narrow-page-alone")
    results["narrow"] = {"chat_over": over, "page_alone": alone,
                         "site_kept_its_width": over.get("site_width") == alone.get("site_width")}
    # The same request from the narrow window: the chat is brought over the page to type it.
    d.click(".browse-beside")
    time.sleep(1.5)
    results["narrow_request"] = request(d, site, "narrow")
    d.shot("narrow-after")

    # ---- a size the person gives the window with a site in front (the handler is called as their letting go of
    # the window's edge would call it: a size set from here raises no ResizeEnd)
    def give() -> None:
        from System.Drawing import Size
        form = d.app._window.native
        form.Size = Size(round(1500 * form._scale), round(900 * form._scale))
        pane._resize_end(None, None)
    d._ui(give)
    time.sleep(1.5)
    given = window_facts(d)["form"]
    d.click(".browse-close")
    time.sleep(2)
    closed = window_facts(d)["form"]
    d.app.open_link(site + "/search.html")
    time.sleep(5)
    again = look(d, "open-again")
    kept = home / "runtime" / "desktop" / "browser" / "kept.json"
    results["kept"] = {"given": given, "after_close": closed, "next_open": again["form"],
                       "file": json.loads(kept.read_text(encoding="utf-8")).get("size")}

    # ---- one browser step of zoom, then another page of the site (a zoom set from here raises no
    # ZoomFactorChanged, so the pane's handler is called as that event would call it)
    d._ui(lambda: (setattr(pane._view, "ZoomFactor", 1.1), pane._zoomed(None, None)))
    time.sleep(1)
    stepped = window_facts(d)["zoom"]
    pane.cdp("Runtime.evaluate", {"expression": "location.href = 'news.html'"})
    time.sleep(4)
    results["zoom"] = {"after_step": stepped, "on_next_page": window_facts(d)["zoom"],
                       "file": json.loads(kept.read_text(encoding="utf-8")).get("zoom")}
    d.log("zoom: " + json.dumps(results["zoom"]))
    d.click(".browse-close")
    time.sleep(1.5)


def main() -> int:
    args = sys.argv[1:]
    home, out = Path(args[0]), Path(args[args.index("--out") + 1])
    site_port = int(args[args.index("--site-port") + 1]) if "--site-port" in args else 59409
    model_port = int(args[args.index("--model-port") + 1]) if "--model-port" in args else 59408
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
    # Far off every screen, so the room the window makes for a site never brings it into view.
    create = webview.create_window
    webview.create_window = lambda *a, **k: create(*a, **{**k, "x": -9000, "y": 60})
    window_driver.Model = StandIn
    window_driver.ThreadingHTTPServer = lambda address, handler: ThreadingHTTPServer((address[0], model_port), handler)
    site = f"http://127.0.0.1:{site_port}"
    server = subprocess.Popen([sys.executable, str(Path(__file__).with_name("opsite") / "serve.py"), str(site_port),
                               str(out / "site.log")])
    results: dict = {}
    try:
        failed = window_driver.run(home, lambda d: walk(d, home, site, results), out=out, offscreen=False, lang="zh")
    finally:
        server.terminate()
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())

"""The app browser's bar and the room its page gets, measured in the real app window (Windows; the window is kept
off screen, with no figure on the desktop).

    python tools/panebar.py <scratch home> --out <folder> [--site-port 59751] [--model-port 59752]

The home's settings must name the stand-in model started here (LLM_BASE_URL=http://127.0.0.1:<model-port>/v1). It opens a
page laid out like a site made for a desktop (its content 1000 px wide, as zhihu.com's), then at window widths 760,
1024, 1280 and 1600 (955 high), with the chat beside the page and without, writes down in results.json and a
picture each: how tall the browser's own top is (the bar, and the tab strip where there is one), how much of the
window's height and width the page gets, whether the bar runs past its end or onto a second line, and the site's
own width, zoom and whether it scrolls sideways."""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WIDTHS = (760, 1024, 1280, 1600)
HIGH = 955
# A site laid out for a desktop: a 1000 px column it never makes narrower, as zhihu.com's.
WIDE = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>宽版网页</title><style>
body { margin: 0; font: 15px/1.6 system-ui, sans-serif; background: #f6f6f6; }
header { height: 52px; background: #fff; border-bottom: 1px solid #ddd; }
header div, main { width: 1000px; margin: 0 auto; }
header div { line-height: 52px; font-weight: 600; }
main { display: flex; gap: 10px; padding-top: 10px; }
.feed { width: 694px; } aside { width: 296px; }
.card { background: #fff; margin-bottom: 10px; padding: 16px 20px; height: 160px; }
</style></head><body><header><div>宽版网页 · 内容宽 1000 px</div></header><main><div class="feed">
""" + "".join(f'<div class="card">第 {n} 条</div>' for n in range(1, 13)) + """</div>
<aside><div class="card">侧栏</div></aside></main></body></html>"""
# The app page: the browser's own top (everything between the window's top and the page), the bar, the page.
PAGE = """(() => { const area = document.querySelector('.browse-area'), bar = document.querySelector('.browse-bar');
  const strip = document.querySelector('.browse-tabs');
  const box = (n) => { if (!n || !n.getClientRects().length) return null; const r = n.getBoundingClientRect();
    return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
  const b = bar.getBoundingClientRect();
  const seen = [...bar.querySelectorAll('button, .browse-field, .browse-run-pill')].filter((n) => n.getClientRects().length
    && !n.closest('[hidden]') && getComputedStyle(n).visibility !== 'hidden');
  const tops = new Set(seen.map((n) => Math.round(n.getBoundingClientRect().top + n.getBoundingClientRect().height / 2)));
  const a = area.getBoundingClientRect();
  return JSON.stringify({ window: [innerWidth, innerHeight], area: box(area), bar: box(bar),
    strip: strip && strip.parentElement !== bar ? box(strip) : null,
    pane_top: Math.round(a.top), page_share: +(a.height / innerHeight).toFixed(3), page_width_share: +(a.width / innerWidth).toFixed(3),
    past_end: seen.filter((n) => n.getBoundingClientRect().right > b.right + 1).map((n) => n.className),
    lines: [...tops].length, buttons: seen.filter((n) => n.tagName === 'BUTTON').length,
    field: box(document.querySelector('.browse-field')), body: document.body.className,
    beside_on: document.querySelector('.browse-beside').classList.contains('on') }); })()"""
SITE = ("JSON.stringify({ width: innerWidth, scroll_width: document.documentElement.scrollWidth,"
        " sideways: document.documentElement.scrollWidth > document.documentElement.clientWidth })")


class Wide(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = WIDE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def measure(d, name: str) -> dict:
    pane = d.app._browser
    row = json.loads(d.js(PAGE) or "{}")
    try:
        answer = pane.cdp("Runtime.evaluate", {"expression": SITE, "returnByValue": True}, timeout=8)
        row["site"] = json.loads((answer.get("result") or {}).get("value") or "{}")
    except RuntimeError as error:
        row["site"] = str(error)
    seen: dict = {}
    d._ui(lambda: seen.update(zoom=round(float(pane._view.ZoomFactor), 3), fit=pane.fit, box=pane._box.Width,
                              unit=d.app._window.native._scale))
    row["site_zoom"], row["fit"] = seen.get("zoom"), [seen.get("fit"), seen.get("box"), seen.get("unit")]
    row["picture"] = d.shot(name).name
    d.log(f"{name}: " + json.dumps(row, ensure_ascii=False))
    return row


def walk(d, site: str, results: dict) -> None:
    time.sleep(3)
    d.app.open_link(site + "/wide")
    d.until("document.querySelector('.browse') && !document.querySelector('.browse').hidden", 20)
    time.sleep(5)
    # The window's size in the app page's CSS px.
    ratio = float(d.js("devicePixelRatio") or 1)
    for docked in (False, True):
        for wide in WIDTHS:
            def size(wide=wide) -> None:
                from System.Drawing import Size
                d.app._window.native.ClientSize = Size(round(wide * ratio), round(HIGH * ratio))
            d._ui(size)
            time.sleep(1.2)
            if ("beside" in (d.js("document.body.className") or "").split()) != docked:
                d.click(".browse-beside")
            time.sleep(1.8)
            results[f"{wide}-{'docked' if docked else 'alone'}"] = measure(d, f"{wide}-{'docked' if docked else 'alone'}")
    # Two more tabs, opened with + and an address typed in the bar; then a run under way with the chat out of
    # sight, as browser.py tells one, at a wide and a narrow window.
    for path in ("/two", "/three"):
        d.click(".browse-tab-new")
        time.sleep(1)
        d.type_into(".browse-address", site + path)
        d.key("Enter", 13)
        time.sleep(3)
    run = {"task": "给 Alice 发消息", "end": None, "held": None, "card": None,
           "steps": [{"text": "点击「Alice」", "state": "done", "note": "打开与 Alice 的对话"},
                     {"text": "输入「明天见」", "state": "doing", "note": "给 Alice 写消息"}]}
    for wide in (1280, 760):
        d._ui(lambda wide=wide: setattr(d.app._window.native, "ClientSize",
                                        __import__("System.Drawing").Drawing.Size(round(wide * ratio), round(HIGH * ratio))))
        time.sleep(1.2)
        if "beside" in (d.js("document.body.className") or "").split():
            d.click(".browse-beside")
        d.js("window.paBrowse({operating: %s})" % json.dumps(run, ensure_ascii=False))
        time.sleep(1.2)
        results[f"{wide}-tabs-run"] = measure(d, f"{wide}-tabs-run")
        d.js("window.paBrowse({operating: null})")
    # The ··· menu and the address's suggestions, under the bar and over the page.
    d.click(".browse-more")
    time.sleep(0.8)
    d.shot("menu")
    d.key("Escape", 27)
    d.type_into(".browse-address", "wide")
    time.sleep(1.5)
    d.shot("suggest")
    d.key("Escape", 27)


def main() -> int:
    args = sys.argv[1:]
    home, out = Path(args[0]), Path(args[args.index("--out") + 1])
    site_port = int(args[args.index("--site-port") + 1]) if "--site-port" in args else 59751
    model_port = int(args[args.index("--model-port") + 1]) if "--model-port" in args else 59752
    out.mkdir(parents=True, exist_ok=True)
    import webview
    import window_driver

    from mibo import window as appwindow
    from mibo.welcome import save_app_settings
    desktop = home / "runtime" / "desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    save_app_settings(desktop, toured=True, pet_shown=False)
    # A zoom of the person's own for the test site, kept from an earlier walk, would go over the fit.
    kept = desktop / "browser" / "kept.json"
    if kept.exists():
        kept.write_text(json.dumps({**json.loads(kept.read_text(encoding="utf-8")), "zoom": {}}), encoding="utf-8")
    made = appwindow.AppWindow.__init__

    def no_figure(self, *a, **k):
        made(self, *a, **k)
        self.pet = False
    appwindow.AppWindow.__init__ = no_figure
    # Far off every screen, so the room the window makes for a site never brings it into view.
    create = webview.create_window
    webview.create_window = lambda *a, **k: create(*a, **{**k, "x": -9000, "y": 60})
    window_driver.ThreadingHTTPServer = lambda address, handler: ThreadingHTTPServer((address[0], model_port), handler)
    server = ThreadingHTTPServer(("127.0.0.1", site_port), Wide)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results: dict = {}
    try:
        failed = window_driver.run(home, lambda d: walk(d, f"http://127.0.0.1:{site_port}", results), out=out,
                                   offscreen=False, lang="zh")
    finally:
        server.shutdown()
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())

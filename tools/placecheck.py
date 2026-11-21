"""Places in the real app window (surface.py's open and look): a page opened out of sight leaves the tab in front
as it was, keeps running there (its timers are not slowed), is read with the list its script drew and seen in a
picture, and comes to the middle when brought to the front. Windows only: the real app window, off screen, from an
empty scratch home, with a practice site of its own and the window driver's stand-in model; no figure on the
desktop and no input of the person's is used.

    python tools/placecheck.py <empty scratch home> --out <folder> [--site-port 59926] [--model-port 59927]

It exits 0 when every check passed and 1 when one failed; the last lines it prints are the findings, one per
check, and results.json in <folder> holds what was measured."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PAGES = {
    "/front.html": b"""<!doctype html><meta charset="utf-8"><title>Front page</title>
<body style="font:16px sans-serif;padding:24px"><h1>The page the person reads</h1><p>Nothing changes here.</p>""",
    # A list drawn by its script after load, a counter its timer moves every 100 ms, and colour for the picture.
    "/list.html": b"""<!doctype html><meta charset="utf-8"><title>Stock list</title>
<body style="font:16px sans-serif;padding:24px;background:#fff">
<h1 style="background:#2b6cb0;color:#fff;padding:12px">Stock</h1><p id="ticks">0</p><ul id="rows"></ul>
<script>
let ticks = 0; setInterval(() => { ticks += 1; document.getElementById('ticks').textContent = ticks; }, 100);
setTimeout(() => { for (let n = 1; n <= 20; n++) { const li = document.createElement('li');
  li.innerHTML = '<button>Row ' + n + '</button> ' + (n % 4 ? 'in stock' : 'sold out'); rows.appendChild(li); } }, 300);
</script>""",
}


class Site(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = PAGES.get(self.path.split("?")[0], b"")
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def until(test, seconds: float = 20.0):
    end = time.time() + seconds
    while time.time() < end:
        found = test()
        if found:
            return found
        time.sleep(0.3)
    return None


def look(pane, tab: int, picture: bool = False) -> dict:
    """surface.py's look at tab `tab`, as the browser op route makes it (panels/browser.py Operating.look_at)."""
    from mibo.web import operate
    from mibo.web.pageops import Page
    from mibo.web.tabs import PaneTab
    return asyncio.run(operate.look_at(Page(PaneTab(pane, tab)), 8000, picture=picture))


def ticks(pane, tab: int) -> int:
    answer = pane.cdp("Runtime.evaluate", {"expression": "ticks", "returnByValue": True}, tab=tab)
    return int((answer.get("result") or {}).get("value") or 0)


def walk(d, results: dict) -> None:
    from PIL import Image, ImageStat
    d.app.open_link(SITE + "/front.html")
    d.until("document.querySelector('.browse') && !document.querySelector('.browse').hidden", 20)
    pane = d.app._browser
    front = until(lambda: next((t for t in pane.tabs() if t["front"] and t["url"].endswith("/front.html")), None))
    d.check("the front page opened in the pane", front, pane.tabs())

    # 1. Out of sight: the tab in front, the page the person reads and what the bar shows stay as they were.
    aside = pane.place_page(SITE + "/list.html", False)["id"]
    until(lambda: pane.address(aside).endswith("/list.html"))
    time.sleep(1.0)
    tabs = pane.tabs()
    now_front = next((t for t in tabs if t["front"]), {})
    reading = (pane._reading or {}).get("url", "")
    results["aside"] = {"tabs": tabs, "front": now_front.get("id"), "address": pane.address(), "reading": reading}
    d.check("the tab in front is the same tab", front and now_front.get("id") == front["id"], results["aside"])
    d.check("the page in front and its reading did not move", pane.address().endswith("/front.html")
            and reading.endswith("/front.html"), results["aside"])
    d.shot("aside-opened")

    # 2. It keeps running out of sight: ten ticks a second, not the one a hidden page is slowed to.
    first = ticks(pane, aside)
    time.sleep(3.0)
    moved = ticks(pane, aside) - first
    results["ticks_in_3s"] = moved
    d.check("its timers run out of sight", moved >= 20, moved)

    # 3. A look reads the list its script drew, with refs, and a picture shows the page.
    seen = look(pane, aside, picture=True)
    rows = [line for line in seen["state"].splitlines() if "Row " in line]
    image = Image.open(io.BytesIO(base64.b64decode(seen.get("picture") or "")))
    spread = max(ImageStat.Stat(image.convert("L")).stddev)
    results["look"] = {"rows": len(rows), "first": rows[:2], "picture": list(image.size), "spread": round(spread, 1)}
    d.check("the drawn list is read with refs", len(rows) == 20 and rows[0].lstrip("*").startswith("["), results["look"])
    d.check("the picture shows the page", image.size[0] >= 1200 and image.size[1] >= 700 and spread > 10, results["look"])
    d.check("the front tab is still in front after the look", next(t for t in pane.tabs() if t["front"])["id"]
            == front["id"])

    # 4. Brought to the middle: in front, shown where the pane is.
    pane.front(aside)
    time.sleep(1.0)
    shown = next((t for t in pane.tabs() if t["front"]), {})
    box: dict = {}

    def facts() -> None:
        tab = next(t for t in pane._tabs if t.id == aside)
        box.update(visible=bool(tab.view.Visible), placed=pane._box is not None and tab.view.Bounds.Equals(pane._box))
    d._ui(facts)
    results["front"] = {"front": shown.get("id"), "address": pane.address(), **box}
    d.check("brought to the front, the page is in the middle", shown.get("id") == aside and box.get("visible")
            and box.get("placed") and pane.address().endswith("/list.html"), results["front"])
    d.shot("brought-front")


def main() -> int:
    global SITE
    home, out = Path(sys.argv[1]), Path(sys.argv[sys.argv.index("--out") + 1])
    site_port = int(sys.argv[sys.argv.index("--site-port") + 1]) if "--site-port" in sys.argv else 59926
    model_port = int(sys.argv[sys.argv.index("--model-port") + 1]) if "--model-port" in sys.argv else 59927
    if home.exists() and any(home.iterdir()):
        raise SystemExit(f"{home} must be an empty scratch folder")
    SITE = f"http://127.0.0.1:{site_port}"
    out.mkdir(parents=True, exist_ok=True)
    import window_driver

    from mibo import window as appwindow
    from mibo.welcome import save_app_settings
    desktop = home / "runtime" / "desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    save_app_settings(desktop, toured=True, pet_shown=False)
    made = appwindow.AppWindow.__init__

    def no_figure(self, *args, **kwargs):
        made(self, *args, **kwargs)
        self.pet = False
    appwindow.AppWindow.__init__ = no_figure
    window_driver.ThreadingHTTPServer = lambda address, handler: ThreadingHTTPServer((address[0], model_port), handler)

    site = ThreadingHTTPServer(("127.0.0.1", site_port), Site)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    results: dict = {}
    failed = window_driver.run(home, lambda d: walk(d, results), out=out, lang="en")
    site.shutdown()
    site.server_close()
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    # The findings, last: one line per check (the window driver's log), then the verdict.
    findings = [line for line in (out / "walk.log").read_text(encoding="utf-8").splitlines()
                if line.startswith(("PASS", "FAIL"))]
    print("\n".join(findings))
    print(f"placecheck: {'all checks passed' if not failed else f'{failed} check(s) failed'}")
    return 1 if failed else 0


SITE = ""

if __name__ == "__main__":
    raise SystemExit(main())

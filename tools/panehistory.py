"""The app browser's history in Settings › 隐私: whether the person can see, search, reopen and delete what they
read there, clear it by a time range, and keep their sign-ins when they do. Windows only: the real app window,
off screen, with no figure on the desktop.

    python tools/panehistory.py <scratch home> --out <folder> [--site-port 59657] [--model-port 59658]

The home is given five history entries first (two in the last hour, one three hours ago, two yesterday; the one
three hours ago made a library item the recommender heard of), and a practice site of its own signs the pane in
with a cookie. results.json in <folder> holds the numbers."""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROWS = ".setup-pane .setup-history-row"
PRIVACY = ".setup-nav-item[data-part=privacy]"


class Site(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        signed = "sid=practice" in (self.headers.get("Cookie") or "")
        name = path.strip("/").split(".")[0].title() or "Home"
        body = (f"<!doctype html><title>{name}</title><p id=state>{'in' if signed else 'out'}</p>").encode()
        self.send_response(200)
        if path == "/signin":
            self.send_header("Set-Cookie", "sid=practice; Max-Age=86400; Path=/; HttpOnly")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def seed(home: Path, now: float) -> str:
    """Five history entries, the library item Gamma made and a read the recommender heard of; its id."""
    from mibo import browsed
    from mibo.library import BROWSED, Item, Library
    from mibo.panels import feed
    desktop, runtime = home / "runtime" / "desktop", home / "runtime"
    rows = [("alpha", now - 600), ("beta", now - 1800), ("gamma", now - 3 * 3600), ("delta", now - 26 * 3600),
            ("alpha", now - 27 * 3600)]
    for name, at in sorted(rows, key=lambda r: r[1]):
        browsed.record(desktop, {"url": f"{SITE}/{name}.html", "title": name.title(), "at": round(at, 1), "s": 45.0})
    item = Library(runtime).add(Item(url=f"{SITE}/gamma.html", site="127.0.0.1", kind="page", title="Gamma",
                                     text="Gamma text", seen_at=int(now - 3 * 3600), via=BROWSED))
    feed.remember(runtime, item, "read", now - 3 * 3600, play_s=45.0)
    return item.id


def file_rows(home: Path) -> list[dict]:
    from mibo import browsed
    return browsed._rows(home / "runtime" / "desktop")


def page_text(d) -> str:
    """The practice page's state line in the pane: "in" when the site sees its cookie."""
    try:
        answer = d.app._browser.cdp("Runtime.evaluate", {"expression": "document.getElementById('state')?.textContent || ''",
                                                         "returnByValue": True}, timeout=8)
        return str((answer.get("result") or {}).get("value") or "")
    except RuntimeError:
        return ""


def load(d, url: str) -> str:
    """`url` in the pane (opened again when it is closed), and its state line once it has loaded."""
    pane = d.app._browser
    if pane._tabs:
        pane.go(url)
    else:
        d.app.open_link(url)
    deadline = time.time() + 20
    while time.time() < deadline:
        time.sleep(0.6)
        if pane.address() == url and page_text(d):
            break
    return page_text(d)


def privacy(d) -> None:
    """Settings › 隐私, the history list unfolded where there is one."""
    d.click("#side-settings")
    d.until(f"!!document.querySelector('{PRIVACY}')", 20)
    d.click(PRIVACY)
    time.sleep(1.5)
    d.js("(() => { const f = document.querySelector('.setup-pane .setup-history'); if (f && !f.open) f.open = true; })()")
    time.sleep(1.2)


def count(d) -> int:
    return int(d.js(f"[...document.querySelectorAll('{ROWS}')].filter((r) => r.getClientRects().length).length") or 0)


def walk(d, results: dict, home: Path, ident: str) -> None:
    pane = d.app._browser
    if "chat-view" not in d.views():
        d.configure()
    load(d, SITE + "/signin")
    results["signed_in_before"] = load(d, SITE + "/inbox") == "in"

    # 1. The history list.
    privacy(d)
    results["listed"] = count(d)
    d.js("document.querySelector('.setup-pane .setup-history, .setup-pane .setup-history-clear')"
         "?.closest('.setup-option')?.scrollIntoView({block: 'center'})")
    time.sleep(0.5)
    d.shot("history")

    # 2. Search.
    searched = None
    if d.shown(".setup-pane .setup-history-search"):
        d.type_into(".setup-pane .setup-history-search", "Beta")
        time.sleep(1.2)
        searched = count(d)
    results["search_beta"] = searched

    # 3. Reopen from the list.
    reopened = ""
    if searched:
        d.click(f"{ROWS} .setup-history-link")
        deadline = time.time() + 15
        while time.time() < deadline and not pane.address().endswith("/beta.html"):
            time.sleep(0.5)
        reopened = pane.address()
        privacy(d)
        d.type_into(".setup-pane .setup-history-search", "")
        d.key("Backspace", 8)
        time.sleep(1.2)
    results["reopened"] = reopened

    # 4. Delete one: Gamma, which made a library item.
    deleted = False
    if d.js(f"(() => {{ const r = [...document.querySelectorAll('{ROWS}')].find((r) => r.textContent.includes('Gamma'));"
            " const b = r && r.querySelector('.setup-history-off'); if (b) b.id = 'ph-delete'; return !!b; })()"):
        d.click("#ph-delete")
        time.sleep(2)
        deleted = True
    from mibo.library import Library
    from mibo.panels import feed
    events = feed.events(home / "runtime")
    results["delete_one"] = {"pressed": deleted, "listed_after": count(d),
                             "gamma_rows_left": sum(1 for r in file_rows(home) if r["url"].endswith("/gamma.html")),
                             "library_item_left": Library(home / "runtime").get(ident) is not None,
                             "events_left": sum(1 for e in events if e[1] == ident)}

    # 5. Clear by a time range: the last hour.
    results["ranges"] = int(d.js("document.querySelectorAll('.setup-pane .setup-history-range option').length") or 0)
    old_before = [r for r in file_rows(home) if r["at"] < time.time() - 3600]
    if d.shown(".setup-pane .setup-history-clear"):
        d.choose(".setup-pane .setup-history-range", "hour")
        d.click(".setup-pane .setup-history-clear")
    else:
        # Before: the one 清除 button of the privacy page.
        d.js("(() => { const b = [...document.querySelectorAll('.setup-pane button')].find((b) => b.textContent.trim() === '清除');"
             " if (b) b.id = 'ph-clear'; })()")
        d.click("#ph-clear")
    time.sleep(1)
    d.click(".confirm .confirm-ok")
    deadline = time.time() + 30
    while time.time() < deadline and any(r["at"] >= time.time() - 3600 for r in file_rows(home)):
        time.sleep(0.5)
    time.sleep(3)
    left = file_rows(home)
    results["clear_hour"] = {"recent_left": sum(1 for r in left if r["at"] >= time.time() - 3600),
                             "older_before": len(old_before), "older_left": len(left),
                             "listed_after": count(d), "pane_still_open": bool(pane._tabs),
                             "last_page_forgotten": not pane._kept["last"]}
    d.shot("cleared")

    # 6. Still signed in on the practice site.
    results["signed_in_after"] = load(d, SITE + "/inbox") == "in"

    # 7. All of it, where a range can be chosen: nothing left, every site's zoom forgotten, still signed in.
    if d.shown(".setup-pane .setup-history-clear"):
        pane._kept["zoom"]["127.0.0.1"] = 1.25
        privacy(d)
        d.choose(".setup-pane .setup-history-range", "all")
        d.click(".setup-pane .setup-history-clear")
        time.sleep(1)
        d.click(".confirm .confirm-ok")
        deadline = time.time() + 30
        while time.time() < deadline and file_rows(home):
            time.sleep(0.5)
        time.sleep(2)
        results["clear_all"] = {"rows_left": len(file_rows(home)), "listed_after": count(d),
                                "zoom_forgotten": not pane._kept["zoom"],
                                "signed_in_after": load(d, SITE + "/inbox") == "in"}
    d.log(json.dumps(results, ensure_ascii=False))


def main() -> int:
    global SITE
    home, out = Path(sys.argv[1]), Path(sys.argv[sys.argv.index("--out") + 1])
    site_port = int(sys.argv[sys.argv.index("--site-port") + 1]) if "--site-port" in sys.argv else 59657
    model_port = int(sys.argv[sys.argv.index("--model-port") + 1]) if "--model-port" in sys.argv else 59658
    SITE = f"http://127.0.0.1:{site_port}"
    out.mkdir(parents=True, exist_ok=True)
    import window_driver

    from mibo import window as appwindow
    from mibo.welcome import save_app_settings
    desktop = home / "runtime" / "desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    save_app_settings(desktop, toured=True, pet_shown=False)
    ident = seed(home, time.time())
    made = appwindow.AppWindow.__init__

    def no_figure(self, *args, **kwargs):
        made(self, *args, **kwargs)
        self.pet = False
    appwindow.AppWindow.__init__ = no_figure
    window_driver.ThreadingHTTPServer = lambda address, handler: ThreadingHTTPServer((address[0], model_port), handler)

    site = ThreadingHTTPServer(("127.0.0.1", site_port), Site)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    results: dict = {}
    failed = window_driver.run(home, lambda d: walk(d, results, home, ident), out=out, lang="zh")
    site.shutdown()
    site.server_close()
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return failed


SITE = ""

if __name__ == "__main__":
    raise SystemExit(main())

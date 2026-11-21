"""The address bar of the app's browser: what it shows, what typing in it does, and how it lies at every width.

    python tools/paneaddress.py bar <scratch home with the app running> --out <folder>
    python tools/paneaddress.py window <scratch home, app not running> --out <folder> [--site-port 59543] [--model-port 59544]

Both write a few pages read lately into the home first (browsed.record), so the bar has history to suggest from.

bar     headless Edge on the running app's own page, the pane told as window.py tells it (there is no WebView
        here): at each window width, how wide the address field is, what a person reads in it, whether anything in
        the bar runs past its end; a plain-http site's mark; what typing words, a site's name or part of a title
        offers; Ctrl+L from the chat's box.
window  the real app window, kept off screen (Windows): words, a single word and addresses typed and sent with
        Enter, and where the page went; the suggestions over the page (is the site's view cut away under them?);
        Ctrl+L inside the site; the sidebar while a page is in front, and after.
results.json in <folder> holds the numbers."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WIDTHS = (400, 460, 520, 560, 640, 760, 1000, 1280, 1440, 1707, 1920)
# What a person types, and what they mean by it.
TYPED = [("蓝箱 第二季", "search"), ("bilibili", "search"), ("how to cook rice", "search"), ("天气", "search"),
         ("127.0.0.1:{port}/news.html", "address"), ("http://127.0.0.1:{port}/search.html", "address")]
SEARCH_HOSTS = ("bing.com", "baidu.com", "google.", "duckduckgo.com", "sogou.com")
# The bar as a person sees it: its width, the field's, what the field says while not focused, what runs past the
# bar's end, the buttons in sight and those that carry words.
BAR = """(() => { const bar = document.querySelector('.browse-bar'); if (!bar || !bar.getClientRects().length) return null;
  const b = bar.getBoundingClientRect(), input = document.querySelector('.browse-address');
  const field = document.querySelector('.browse-field') || input, f = field.getBoundingClientRect();
  const where = document.querySelector('.browse-where');
  const seen = (n) => n.getClientRects().length && !n.closest('[hidden]') && getComputedStyle(n).visibility !== 'hidden';
  const shown = where && seen(where) && getComputedStyle(where).opacity !== '0' ? where.innerText.replace(/\\s+/g, ' ').trim() : input.value;
  const past = [...bar.querySelectorAll(':scope > *')].filter((n) => seen(n) && n.getBoundingClientRect().right > b.right + 1);
  const buttons = [...bar.querySelectorAll('button')].filter(seen);
  const worded = buttons.filter((n) => [...n.childNodes].some((c) => (c.nodeType === 3 ? c.textContent : seen(c) && c.tagName === 'SPAN' ? c.textContent : '').trim()));
  const text = where && seen(where) ? where : input;
  return { bar: Math.round(b.width), field: Math.round(f.width), shown, need: text.scrollWidth, room: text.clientWidth,
    past: past.length, buttons: buttons.length, worded: worded.map((n) => n.textContent.trim()),
    mark: (document.querySelector('.browse-mark') && seen(document.querySelector('.browse-mark'))) ? document.querySelector('.browse-mark').textContent.trim() || 'icon' : '',
    side: document.body.classList.contains('side-rail') ? 'rail' : 'full' }; })()"""
SUGGESTED = """(() => { const list = document.querySelector('.browse-suggest');
  if (!list || list.hidden || !list.getClientRects().length) return [];
  return [...list.querySelectorAll('[role=option]')].map((n) => n.innerText.replace(/\\s+/g, ' ').trim()); })()"""
STATE = {"open": True, "url": "https://www.example.com/shop/list?utm_source=feed&utm_medium=card&spm=a1b2c3",
         "host": "www.example.com", "title": "示例商店 - 冬季新品 - 商品列表", "back": True, "forward": False,
         "loading": False, "private": False}
PLAIN = {"url": "http://news.example.org/today/a-story.html", "host": "news.example.org", "title": "今日新闻"}


def seed(home: Path, port: int) -> None:
    """A few pages read lately, as the browser would have kept them."""
    desktop = home / "runtime" / "desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    now = time.time()
    rows = [(f"http://127.0.0.1:{port}/news.html", "今日文章：城市里的候鸟", 3), (f"http://127.0.0.1:{port}/search.html", "搜索", 2),
            ("https://www.bilibili.com/video/BV1Ab411c7XY", "城市候鸟纪录片 第一集 - 哔哩哔哩", 5),
            ("https://www.bilibili.com/", "哔哩哔哩 (゜-゜)つロ 干杯~", 9), ("https://news.ycombinator.com/", "Hacker News", 4),
            ("https://zh.wikipedia.org/wiki/候鸟", "候鸟 - 维基百科", 1)]
    from mibo import browsed
    for n, (url, title, times) in enumerate(rows):
        for k in range(times):
            browsed.record(desktop, {"url": url, "title": title, "at": round(now - 3600 * (n + 1) - 86400 * k, 1),
                                     "s": 40.0})


def bar(home: Path, out: Path) -> dict:
    from playwright.sync_api import sync_playwright
    url = json.loads((home / "runtime" / "desktop" / "welcome.json").read_text(encoding="utf-8"))["url"]
    base, _, fragment = url.partition("#")
    results: dict = {"widths": {}, "typed": {}}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        for wide in WIDTHS:
            page = browser.new_page(viewport={"width": wide, "height": 820}, screen={"width": wide, "height": 900})
            page.goto(f"{base.rstrip('/')}/#{fragment}&frame=measure")
            page.wait_for_selector(".chat-main .chat-input", timeout=60000)
            page.evaluate("paBrowse(%s)" % json.dumps(STATE))
            time.sleep(0.6)
            row = page.evaluate(BAR)
            page.screenshot(path=str(out / f"bar-{wide:04d}.png"), clip={"x": 0, "y": 0, "width": wide, "height": 110})
            page.evaluate("paBrowse(%s)" % json.dumps(PLAIN))
            time.sleep(0.3)
            row["plain"] = page.evaluate(BAR)
            results["widths"][wide] = row
            print(wide, json.dumps(row, ensure_ascii=False))
            if wide in (460, 1440):
                page.emulate_media(color_scheme="dark")
                time.sleep(0.3)
                page.screenshot(path=str(out / f"bar-{wide:04d}-dark.png"), clip={"x": 0, "y": 0, "width": wide, "height": 110})
                page.emulate_media(color_scheme="light")
            if wide == 1440:
                page.evaluate("paBrowse(%s)" % json.dumps(STATE))
                # What typing offers: the first rows, as a person reads them.
                for text in ("蓝箱 第二季", "bilibili", "候鸟", "news", "127.0.0.1"):
                    page.click(".browse-address")
                    page.keyboard.press("Control+A")
                    page.keyboard.type(text, delay=20)
                    time.sleep(0.8)
                    results["typed"][text] = page.evaluate(SUGGESTED)
                    print("typed", text, results["typed"][text])
                    if text == "候鸟":
                        page.screenshot(path=str(out / "suggest-1440.png"), clip={"x": 0, "y": 0, "width": wide, "height": 420})
                page.keyboard.press("Escape")
                page.keyboard.press("Escape")
                # Ctrl+L from the chat's box.
                page.click(".chat-main .chat-input")
                page.keyboard.press("Control+L")
                time.sleep(0.3)
                results["ctrl_l_in_app_page"] = page.evaluate("document.activeElement?.classList.contains('browse-address') || false")
                page.screenshot(path=str(out / "focused-1440.png"), clip={"x": 0, "y": 0, "width": wide, "height": 110})
            page.close()
        browser.close()
    return results


def window(home: Path, out: Path, site_port: int, model_port: int) -> tuple[int, dict]:
    import webview
    import window_driver

    from mibo import window as appwindow
    from mibo.welcome import save_app_settings
    save_app_settings(home / "runtime" / "desktop", toured=True, pet_shown=False)
    made = appwindow.AppWindow.__init__

    def no_figure(self, *a, **k):
        made(self, *a, **k)
        self.pet = False
    appwindow.AppWindow.__init__ = no_figure
    create = webview.create_window
    webview.create_window = lambda *a, **k: create(*a, **{**k, "x": -9000, "y": 60})
    window_driver.ThreadingHTTPServer = lambda address, handler: ThreadingHTTPServer((address[0], model_port), handler)
    site = f"http://127.0.0.1:{site_port}"
    server = subprocess.Popen([sys.executable, str(Path(__file__).with_name("opsite") / "serve.py"), str(site_port),
                               str(out / "site.log")])
    results: dict = {}

    def facts(d) -> dict:
        pane, box = d.app._browser, {}

        def read() -> None:
            form, view = d.app._window.native, pane._view
            box.update(form=form.Width, url=str(pane._core.Source) if pane._core is not None else "",
                       cut=view is not None and view.Region is not None)
        d._ui(read)
        page = d.js("JSON.stringify({ side: document.body.classList.contains('side-rail') ? 'rail' : 'full',"
                    " area: Math.round(document.querySelector('.browse-area')?.getBoundingClientRect().width || 0),"
                    " pane: !document.querySelector('.browse').hidden })")
        return {**box, **json.loads(page or "{}")}

    def walk(d) -> None:
        pane = d.app._browser
        time.sleep(3)
        results["chat"] = facts(d)
        d.app.open_link(site + "/index.html")
        d.until("document.querySelector('.browse') && !document.querySelector('.browse').hidden", 20)
        time.sleep(4)
        results["open"] = facts(d)
        d.shot("open")
        results["typed"] = {}
        for text, meant in TYPED:
            text = text.format(port=site_port)
            before = facts(d)["url"]
            d.type_into(".browse-address", text)
            d.key("Enter", 13)
            time.sleep(4)
            went = facts(d)["url"]
            if meant == "search":
                ok = went != before and any(h in went for h in SEARCH_HOSTS)
            else:
                ok = went.rstrip("/").endswith(text.split("127.0.0.1:")[-1].split(f"{site_port}")[-1]) and went != before
            results["typed"][text] = {"meant": meant, "went": went[:160], "ok": ok}
            d.log(f"typed {text!r}: {went[:120]} ok={ok}")
            if meant == "search":
                d.app.open_link(site + "/index.html")
                time.sleep(3)
        # Suggestions over the page: the site's view must be cut away under them, or they cannot be seen.
        d.type_into(".browse-address", "候鸟")
        time.sleep(1.2)
        rows = d.js(SUGGESTED) or []
        hole = d.js("(() => { const l = document.querySelector('.browse-suggest'), a = document.querySelector('.browse-area');"
                    " if (!l || l.hidden || !l.getClientRects().length) return null; const r = l.getBoundingClientRect(), b = a.getBoundingClientRect();"
                    " return [(r.left + r.width / 2 - b.left) * devicePixelRatio, (r.bottom - 6 - b.top) * devicePixelRatio]; })()")
        seen = {}
        if hole:
            d._ui(lambda: seen.update(under=bool(pane._view.Region is None or pane._view.Region.IsVisible(int(hole[0]), int(hole[1])))))
        d.shot("suggest")
        d.key("ArrowDown", 40)
        d.key("Enter", 13)
        time.sleep(3)
        results["suggest"] = {"rows": rows, "site_view_over_the_list": seen.get("under"), "went": facts(d)["url"],
                              "cut_after": facts(d)["cut"]}
        d.log("suggest: " + json.dumps(results["suggest"], ensure_ascii=False))
        # Ctrl+L inside the site: the site's own view hears the key (as Windows would hand it on).
        from System.Windows.Forms import Keys
        d.js("document.activeElement && document.activeElement.blur()")
        args = SimpleNamespace(Control=True, Alt=False, Shift=False, KeyCode=Keys.L, Handled=False, SuppressKeyPress=False)
        d._ui(lambda: pane._key(None, args))
        time.sleep(0.6)
        results["ctrl_l_in_site"] = bool(d.js("document.activeElement?.classList.contains('browse-address') || false"))
        d.shot("ctrl-l")
        d.key("Escape", 27)
        d.click(".browse-minimize")
        time.sleep(2)
        results["minimized"] = facts(d)
        d.click(".browse-return-go")
        time.sleep(2.5)
        results["back"] = facts(d)
        d.click(".browse-close")
        time.sleep(1.5)
        results["closed"] = facts(d)
        d.log("window: " + json.dumps({k: results[k] for k in ("chat", "open", "minimized", "back", "closed")}))

    try:
        failed = window_driver.run(home, walk, out=out, offscreen=False, lang="zh")
    finally:
        server.terminate()
    return failed, results


def main() -> int:
    mode, home, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[sys.argv.index("--out") + 1])
    site_port = int(sys.argv[sys.argv.index("--site-port") + 1]) if "--site-port" in sys.argv else 59543
    model_port = int(sys.argv[sys.argv.index("--model-port") + 1]) if "--model-port" in sys.argv else 59544
    out.mkdir(parents=True, exist_ok=True)
    seed(home, site_port)
    if mode == "bar":
        failed, results = 0, bar(home, out)
    else:
        failed, results = window(home, out, site_port, model_port)
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())

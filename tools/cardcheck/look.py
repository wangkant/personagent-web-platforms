"""Pictures of the pet's card and of the chat's find card, with the controls each shows.

    python tools/cardcheck/look.py card <out dir> [--item item.json] [--lang zh] [--dark]
    python tools/cardcheck/look.py chat <out dir> --home <home>
    python tools/cardcheck/look.py pet <out dir> --home <home>

`card` draws the card with the page's own files (static/welcome/card.js, card.css, petux.css) in headless
Edge, the pet's window stood in for by a plain frame: no app runs, every request the card makes is
answered here and listed. It walks the card through its states (out, the reason pressed, "Not
interested" pressed, a reason chosen, undone, "Another") and saves a picture of each with the labels
of the buttons a person can see. `--item` is a find as GET /api/feed/next gives it (default: a made-up
one). `chat` opens a running app's page, takes the last find card in the main chat, opens its menu
and does the same. `pet` opens a running app's own pet page (pet.js and all) with only the window's
side stood in for, has the pet bring a find as its menu does, and goes through a no, its undo, a no
again and "Another" against the real service, listing what the window was asked for, the poses the
pet took and what the feed kept.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ASSETS = Path(__file__).resolve().parents[2] / "mibo" / "static" / "welcome"
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/assets/card.css"><link rel="stylesheet" href="/assets/petux.css">
<style>html,body{margin:0;height:100%;background:#e9eaee}body.dark{background:#17171a}
.stage{position:absolute;right:40px;bottom:40px;width:560px;height:520px}
.figure{position:absolute;right:20px;bottom:20px;width:160px;height:190px;border-radius:80px 80px 60px 60px;background:#c9cbd3}
#card{right:192px!important;left:auto!important}</style>
<script src="/assets/card.js"></script></head>
<body><div class="stage"><div class="figure"></div><div id="card" class="card" role="button" tabindex="-1" hidden></div></div>
<script>
window.calls = [];
window.pywebview = { api: { card: async (w, h, box) => { window.sized = [w, h, box]; return "left"; }, open_at: (t) => { window.opened = t; } } };
const api = (path, body) => { window.calls.push([path, body === undefined ? null : body]);
  return body === undefined ? fetch(path) : fetch(path, { method: "POST", body: JSON.stringify(body) }); };
window.card = makeCard(api);
</script></body></html>"""
ITEM = {"id": "a1", "kind": "text", "title": "游戏史最高分，不负 13 年的等待：《GTA 6》游玩体验", "words": "",
        "text": "Rockstar 的新作在开放世界的密度上又进了一步。", "site": "少数派", "url": "https://sspai.com/post/1",
        "player": "", "picture": "", "source": "sspai", "seen": False, "liked": False, "slot": "model",
        "line": "洛圣都之后十三年，这次的城市会自己过日子。", "more": False,
        "why": {"text": "你选择的主题：游戏", "key": "topic:games", "lean": None}, "topic": {"key": "topic:games", "name": "游戏"}}
NEXT = {**ITEM, "id": "b2", "title": "一人独立开发的骰子 Roguelike 牌组构筑游戏上市", "site": "巴哈姆特", "source": "gnn",
        "line": "", "why": {"text": "新发现", "key": "", "lean": None}, "more": True}


def seen(page, box: str) -> list[str]:
    """The labels of the buttons a person can see in `box`, with [on] where one is pressed."""
    return page.evaluate("""sel => [...document.querySelectorAll(sel + ' button, ' + sel + ' a[href]')]
        .filter(n => n.offsetParent && getComputedStyle(n).visibility !== 'hidden')
        .map(n => ((n.textContent || '').trim() || '(' + (n.getAttribute('aria-label') || n.title || '?') + ')')
             + (n.getAttribute('aria-pressed') === 'true' ? ' [on]' : ''))""", box)


def card(out: Path, item: dict, lang: str, dark: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    report = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 640, "height": 600}, device_scale_factor=2,
                                color_scheme="dark" if dark else "light", locale=lang)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)[:300]))

        def answer(route):
            path = route.request.url.split("//", 1)[1].partition("/")[2].partition("?")[0]
            if path == "":
                return route.fulfill(content_type="text/html", body=PAGE)
            if path.startswith("assets/"):
                kind = "text/css" if path.endswith(".css") else "text/javascript"
                return route.fulfill(content_type=kind, body=(ASSETS / path[7:]).read_text(encoding="utf-8"))
            if path == "api/feed/next":
                return route.fulfill(json={"item": NEXT})
            if path == "api/feed/line":
                return route.fulfill(json={"line": "骰子掷出去之前，牌组已经替你算好了一半。", "wait": False})
            return route.fulfill(json={"ok": True})

        page.route("**/*", answer)
        page.goto("http://card.test/")
        if dark:
            page.evaluate("document.body.classList.add('dark')")
        page.evaluate("code => localStorage.setItem('mibo-page-language', code)", lang)
        page.reload()
        if dark:
            page.evaluate("document.body.classList.add('dark')")

        def shot(name: str) -> None:
            time.sleep(0.45)
            page.screenshot(path=str(out / f"{name}.png"), clip={"x": 40, "y": 60, "width": 560, "height": 520})
            size = page.evaluate("(() => { const b = document.getElementById('card'); return b.hidden ? null : [b.offsetWidth, b.offsetHeight]; })()")
            report[name] = {"buttons": seen(page, "#card"), "size": size,
                            "text": page.evaluate("document.getElementById('card').innerText").split("\n")}
            print(name, size, report[name]["buttons"])

        def press(selector: str) -> bool:
            node = page.query_selector(selector)
            if node is None or not node.is_visible() or not node.is_enabled():
                print("  no", selector)
                return False
            node.click()
            return True

        page.evaluate("item => window.card.show(item)", item)
        shot("1-out")
        if press("#card .card-why"):
            shot("2-why")
            press("#card .card-lean button:nth-of-type(1)")
            shot("2b-leaned")
            time.sleep(2)
        if press("#card .card-nope"):
            shot("3-nope")
            if press("#card .card-reasons button:nth-of-type(2)"):
                shot("4-said")
                if press("#card .card-undo"):
                    shot("5-undone")
        if press("#card .card-next"):
            shot("6-next")
            time.sleep(2.5)
            shot("7-next-line")
        # Without a sentence of the character's: the title alone, never the page's own words.
        page.evaluate("window.card.close('seen')")
        time.sleep(0.3)
        page.evaluate("item => window.card.show(item)", {**item, "id": "c3", "line": "", "why": None,
                                                        "text": "An excerpt in another language that the card must not show."})
        shot("8-no-line")
        report["calls"] = page.evaluate("window.calls")
        report["errors"] = errors
        browser.close()
    (out / "card.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("page errors:", errors or "none")


def chat(out: Path, home: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    url = json.loads((home / "runtime" / "desktop" / "welcome.json").read_text(encoding="utf-8"))["url"]
    report = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1240, "height": 900}, device_scale_factor=2)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)[:300]))
        page.goto(url)
        page.wait_for_selector("article.find", timeout=60000)
        time.sleep(2)
        # The last find with a chip that can be pressed, else the last find; marked so it is found again once folded.
        cards = page.locator("article.find:not(.folded)")
        with_chip = page.locator("article.find:not(.folded):has(.find-why-wrap)")
        find = (with_chip if with_chip.count() else cards).last
        place = find.evaluate("node => { node.setAttribute('data-look', '1'); return [...document.querySelectorAll('article.find')].indexOf(node); }")
        find = page.locator("article[data-look]")
        find.scroll_into_view_if_needed()

        def shot(name: str) -> None:
            time.sleep(0.5)
            box = find.bounding_box()
            page.screenshot(path=str(out / f"{name}.png"),
                            clip={"x": box["x"] - 12, "y": max(0, box["y"] - 110), "width": box["width"] + 24,
                                  "height": box["height"] + 190})
            report[name] = {"buttons": seen(page, "article[data-look]"), "text": find.inner_text().splitlines()}
            print(name, report[name]["buttons"])

        shot("chat-1-card")
        why = find.locator("button.find-why")
        if why.count():
            why.click()
            shot("chat-2-why")
            find.locator(".find-why-wrap .find-menu button").first.click()
            time.sleep(0.4)
            why.click()
            shot("chat-2b-leaned")
            find.locator(".find-why-wrap .find-menu button").first.click()
        find.locator(".find-more-wrap:not(.find-why-wrap) > button").click()
        shot("chat-3-menu")
        items = find.locator(".find-more-wrap:not(.find-why-wrap) .find-menu button")
        report["menu"] = [t.strip() for t in items.all_inner_texts()]
        print("menu:", report["menu"])
        if items.count() > 1:
            items.nth(1).click()
            time.sleep(0.8)
            shot("chat-4-folded")
            undo = find.locator(".find-undo")
            if undo.count():
                undo.click()
                time.sleep(0.8)
                # The card is drawn anew in the folded line's place.
                page.locator("article.find").nth(place).evaluate("node => node.setAttribute('data-look', '1')")
                shot("chat-5-undone")
        report["errors"] = errors
        browser.close()
    (out / "chat.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("page errors:", errors or "none")


WINDOW = """
window.asked = []; window.poses = [];
window.pywebview = { api: new Proxy({}, { get: (_, name) => async (...args) => {
  window.asked.push([name, ...args]);
  return name === "card" ? (args[0] ? "left" : "") : name === "tuck" ? "" : null; } }) };
"""


def pet(out: Path, home: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    url = json.loads((home / "runtime" / "desktop" / "welcome.json").read_text(encoding="utf-8"))["url"]
    base, _, token = url.partition("#")
    report = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 640, "height": 560}, device_scale_factor=2, locale="zh-CN")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)[:300]))
        page.add_init_script(WINDOW)
        page.goto(f"{base.rstrip('/')}/pet#{token}")
        page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
        time.sleep(3)
        # The poses the pet takes, as pet.js asks for them.
        page.evaluate("(() => { const was = window.play; window.play = (name, ...rest) => { window.poses.push(name); return was(name, ...rest); }; })()")

        def step(name: str, wait: float = 0.6) -> None:
            time.sleep(wait)
            page.screenshot(path=str(out / f"pet-{name}.png"))
            report[name] = {"buttons": seen(page, "#card"), "card": page.evaluate("document.getElementById('card').innerText").splitlines(),
                            "window": page.evaluate("window.asked.filter(a => a[0] === 'card').slice(-1)[0]"),
                            "poses": page.evaluate("window.poses.splice(0)")}
            print(name, report[name]["buttons"], "| window:", report[name]["window"], "| poses:", report[name]["poses"])

        page.evaluate("petBring()")
        page.wait_for_selector("#card .card-foot", timeout=30000)
        step("1-brought", 1.5)
        page.click("#card .card-nope")
        step("2-asked")
        page.click("#card .card-reasons button:last-of-type")
        step("3-seen-already")
        page.click("#card .card-undo")
        page.wait_for_selector("#card .card-foot", timeout=10000)
        step("4-undone")
        page.click("#card .card-next")
        page.wait_for_selector("#card .card-foot", timeout=10000)
        step("5-another", 1.2)
        page.click("#card .card-nope")
        step("6-asked")
        page.click("#card .card-reasons button:first-of-type")
        step("7-no")
        time.sleep(5.5)
        step("8-gone")
        report["errors"] = errors
        browser.close()
    # The running app's events, read without writing (runtime/mibo.db, table feed_events).
    conn = sqlite3.connect(f"{(home / 'runtime' / 'mibo.db').as_uri()}?mode=ro", uri=True)
    feed = [[at, ident, action, site, kind, json.loads(extra)] for at, ident, action, site, kind, extra
            in conn.execute("SELECT at, id, action, site, kind, extra FROM feed_events ORDER BY n")]
    conn.close()
    report["feed"] = [[e[1][:6], e[2], {k: e[5][k] for k in ("end", "said", "s") if k in e[5]}] for e in feed[-12:]]
    for row in report["feed"]:
        print("  feed:", row)
    (out / "pet.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("page errors:", errors or "none")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("what", choices=("card", "chat", "pet"))
    parser.add_argument("out", type=Path)
    parser.add_argument("--item", type=Path)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--lang", default="zh")
    parser.add_argument("--dark", action="store_true")
    args = parser.parse_args()
    if args.what == "card":
        item = json.loads(args.item.read_text(encoding="utf-8")) if args.item else ITEM
        card(args.out, item, args.lang, args.dark)
    elif args.what == "chat":
        chat(args.out, args.home)
    else:
        pet(args.out, args.home)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

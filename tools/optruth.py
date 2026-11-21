"""What web/operate.py says of an act against what happened, with no model and no window: a scripted helper works
pages of tools/opsite in headless Edge through act's Hands.steps the way a model errs (it names one control and
gives another's id, sends the same words again, types and never sends), and each check reads the step lines and
the Noticed lines, what the site got and how the steps ended.

    python tools/optruth.py [--out <folder>] [--port 59630] [--only name,repeat]

One line a check, then the loop's own lines; <out>/truth.json keeps them. The exit code is the number of checks
that failed. The checks:

    plain    an ordinary send goes through: one card, one message, no step refused
    name     an id whose control is not the one named is refused, nothing clicked
    line     a click's line names an icon with no words and says what changed on it
    hover    a hover's line lists what came up under the pointer
    resend   the same words to the same person are not sent twice
    unsent   what was typed and never sent is said in the Noticed lines, and nothing is sent
    why      a press that cannot land is not put on a card, and fails with the reason in the person's words

repeat, expect and go_on measured operate.run's own ending (its repeat stop, the done proof and its judge, the
handover's go on) and went with it.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE / "opsite"))

from opbounds import drive  # noqa: E402

from mibo.web import gate, operate  # noqa: E402
from mibo.web.tabs import PlaywrightTab  # noqa: E402



def ident(page: str, pattern: str) -> int:
    """The id the pattern's first group finds in the page's lines."""
    return int(re.search(pattern, page).group(1))


class Play:
    """One page of the site and a script for it: `run` works the script's steps on it as act does."""

    def __init__(self, browser, site: str, allow: Path):
        self.browser, self.site, self.allow = browser, site, allow
        self.cards: list[dict] = []
        self.page = None

    async def open(self, path: str) -> None:
        urllib.request.urlopen(urllib.request.Request(self.site + "/__reset", data=b"{}"), timeout=10).read()
        context = await self.browser.new_context(viewport={"width": 1100, "height": 760}, locale="zh-CN")
        self.page = await context.new_page()
        self.tab = PlaywrightTab(self.page)
        await self.tab.goto(self.site + path)

    def hands(self) -> operate.Hands:
        async def ask(card: dict) -> dict:
            self.cards.append({k: card.get(k, "") for k in ("kind", "button", "to", "text")})
            return {"yes": True, "never": False}
        return operate.Hands(self.tab, ask=ask, allowed=gate.Allowed(self.allow), attended=lambda: False, lang="zh")

    async def run(self, task: str, plan: list) -> dict:
        """`plan`: one entry an act, each a function of the page's lines that gives the act's steps."""
        hands = self.hands()
        hands.said = lambda: task
        try:
            return await asyncio.wait_for(drive(hands, plan), 120)
        finally:
            await hands.close()

    def got(self, path: str) -> list[dict]:
        state = json.loads(urllib.request.urlopen(self.site + "/api/state", timeout=10).read())
        return [p["body"] for p in state["posts"] if p["path"] == path]


def click(pattern: str, element: str):
    return lambda page, prompt: {"do": "click", "id": ident(page, pattern), "element": element}


def typed(pattern: str, text: str, element: str):
    return lambda page, prompt: {"do": "type", "id": ident(page, pattern), "text": text, "element": element}


def done(result: str, expect: str = ""):
    return lambda page, prompt: {"do": "done", "success": True, "result": result, **({"expect": expect} if expect else {})}


STAR = r'购物清单[^\n]*\n\*?\[(\d+)\] button'
ALICE = [click(r'\[(\d+)\] link "Alice"', "Alice"),
         lambda page, prompt: [typed(r'\[(\d+)\] textbox', "明天见", "输入框")(page, prompt),
                               click(r'\[(\d+)\] button "发送"', "发送按钮")(page, prompt)]]


async def plain(play: Play) -> tuple[bool, dict]:
    await play.open("/messages.html")
    out = await play.run("给 Alice 发消息：明天见", [*ALICE, done("已向 Alice 发送“明天见”。")])
    sent = play.got("/api/send")
    return (out["status"] == "done" and len(sent) == 1 and len(play.cards) == 1
            and not any(line.startswith("[failed]") for line in out["steps"])), out


async def name(play: Play) -> tuple[bool, dict]:
    await play.open("/t/t23-shop.html")
    out = await play.run("在这个商城找到帮助中心", [click(r'\[(\d+)\] link "优惠券"', "更多 ▾ 菜单")])
    lines = "\n".join(out["steps"])
    return "[ok] click" not in lines and 'not "更多 ▾ 菜单"' in lines, out


async def line(play: Play) -> tuple[bool, dict]:
    await play.open("/t/t10-notes.html")
    out = await play.run("把“购物清单”这条笔记收藏一下", [click(STAR, "收藏")])
    pressed = next((x for x in out["steps"] if x.startswith("[ok] click")), "")
    return "收藏" in pressed and bool(re.search(r"now (on|pressed)", pressed)), out


async def hover(play: Play) -> tuple[bool, dict]:
    await play.open("/t/t23-shop.html")
    out = await play.run("在这个商城找到帮助中心", [lambda page, prompt: {"do": "hover", "text": "更多 ▾"}])
    over = next((x for x in out["steps"] if x.startswith("[ok] hover")), "")
    return "帮助中心" in over, out


async def resend(play: Play) -> tuple[bool, dict]:
    await play.open("/messages.html")
    out = await play.run("给 Alice 发消息：明天见", [*ALICE, ALICE[1], done("已向 Alice 发送“明天见”。")])
    return out["status"] == "done" and len(play.got("/api/send")) == 1, out


async def unsent(play: Play) -> tuple[bool, dict]:
    await play.open("/messages.html")
    out = await play.run("给 Alice 发消息：明天见", [ALICE[0], typed(r'\[(\d+)\] textbox', "明天见", "输入框")])
    return not play.got("/api/send") and any("not sent" in line for line in out["noticed"]), out


async def why(play: Play) -> tuple[bool, dict]:
    await play.open("/t/t09-article.html")
    hands = play.hands()
    try:
        look = await hands.look()
        result = await hands.act({"do": "click", "id": ident(look["state"], r'\[(\d+)\] button "赞'), "element": "赞"})
    finally:
        await hands.close()
    return result.get("status") == "failed" and "遮挡" in str(result.get("why") or "") and not play.cards, {
        "status": result.get("status"), "result": str(result.get("why") or ""), "steps": [result.get("line", "")]}


CHECKS = {"plain": plain, "name": name, "line": line, "hover": hover, "resend": resend, "unsent": unsent,
          "why": why}


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--port", type=int, default=59630)
    parser.add_argument("--only", default="")
    args = parser.parse_args(argv)
    out = Path(args.out or tempfile.mkdtemp(prefix="optruth-")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    site = f"http://127.0.0.1:{args.port}"
    server = subprocess.Popen([sys.executable, "-B", str(HERE / "opsite" / "serve.py"), str(args.port),
                               str(out / "site.log")])
    rows = []
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(site + "/api/state", timeout=2).read()
                break
            except OSError:
                time.sleep(0.25)
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel="msedge", headless=True)
            for label, check in CHECKS.items():
                if args.only and label not in args.only.split(","):
                    continue
                play = Play(browser, site, out / "allow")
                try:
                    ok, seen = await check(play)
                except Exception as error:
                    ok, seen = False, {"status": "error", "result": f"{type(error).__name__}: {error}"[:300], "steps": []}
                rows.append({"check": label, "ok": bool(ok), "status": seen.get("status"), "result": seen.get("result", ""),
                             "cards": play.cards, "steps": seen.get("steps", []), "noticed": seen.get("noticed", [])})
                print(f"{'PASS' if ok else 'FAIL'}  {label}: ended {seen.get('status')} {seen.get('result', '')[:80]!r}, "
                      f"{len(play.cards)} cards")
                for step in seen.get("steps", []):
                    print("      " + step.replace("\n", "\n      ")[:400])
                await play.page.context.close()
            await browser.close()
    finally:
        server.terminate()
        (out / "truth.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    failed = sum(not row["ok"] for row in rows)
    print(f"\n{len(rows) - failed}/{len(rows)} checks passed; kept in {out / 'truth.json'}")
    return failed


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    raise SystemExit(asyncio.run(main(sys.argv[1:])))

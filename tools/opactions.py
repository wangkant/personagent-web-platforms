"""The page actions beyond a plain click and type, worked as act works them (opbounds.drive through Hands.steps) on
tools/opsite/t/t33-desk.html in headless Edge with no model and no window: key chords, typing that adds to a field, double and right clicks,
dragging between two ids (the browser's own drag and drop, and a list moved with the mouse), and waiting until
words show or go. Each check reads what the site received and which cards came; the `_no` checks answer the card
no and need the site to have received nothing, so no new action finds a way round a card.

    python tools/opactions.py [--out <folder>] [--port 59630] [--only chord,drag]

One line a check, then the loop's own lines; <out>/actions.json keeps them. The exit code is the number of checks
that failed. The checks:

    chord       Control+Enter in the chat box sends: one send card, one message
    chord_no    the same, the card answered no: nothing sent
    clipboard   Control+v is refused and nothing is pasted
    append      typing that adds to the draft keeps what it held; its save sends both
    double      a double click opens a cell for editing, and the new amount is saved after a card
    double_out  a double click on a like button is refused: no card, nothing sent
    right       a right click opens the page's own menu; its 删除 asks on a card, then deletes
    key_no      Delete on a selected file is a page's shortcut: a card first, and on no nothing is deleted
    drag        a card dragged to 已完成 (the browser's drag and drop) moves after a card
    drag_no     the same, the card answered no: nothing sent
    rank        a list item dragged with the mouse onto the first takes its place
    wait        waiting until 报表已生成 shows, then until 正在生成 is gone
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from optruth import Play, click, done, ident, typed  # noqa: E402

from mibo.web import gate, operate  # noqa: E402

PAGE = "/t/t33-desk.html"


class Desk(Play):
    """Play with the card answered `yes`."""

    yes = True

    def hands(self) -> operate.Hands:
        async def ask(card: dict) -> dict:
            self.cards.append({k: card.get(k, "") for k in ("kind", "button", "to", "text")})
            return {"yes": self.yes, "never": False}
        return operate.Hands(self.tab, ask=ask, allowed=gate.Allowed(self.allow), attended=lambda: False, lang="zh")


def act(**action):
    return lambda page, prompt: action


def steps(out: dict) -> str:
    return "\n".join(out["steps"])


CHAT = r'\[(\d+)\] textbox "群消息"'
CHAT_SEND = [lambda page, prompt: [typed(CHAT, "今天下午三点开会", "群消息")(page, prompt),
                                   {"do": "press", "key": "Control+Enter"}]]


async def chord(play: Desk) -> tuple[bool, dict]:
    await play.open(PAGE)
    out = await play.run("在团队群里发：今天下午三点开会", [*CHAT_SEND, done("已发送。")])
    sent = play.got("/api/send")
    return (len(sent) == 1 and sent[0].get("text") == "今天下午三点开会" and [c["kind"] for c in play.cards] == ["send"]
            and out["status"] == "done"), out


async def chord_no(play: Desk) -> tuple[bool, dict]:
    play.yes = False
    await play.open(PAGE)
    out = await play.run("在团队群里发：今天下午三点开会", [*CHAT_SEND, done("已发送。")])
    return not play.got("/api/send") and len(play.cards) == 1 and out["status"] == "declined", out


async def clipboard(play: Desk) -> tuple[bool, dict]:
    await play.open(PAGE)
    await play.page.evaluate("navigator.clipboard.writeText('剪贴板里的密码').catch(() => {})")
    out = await play.run("把剪贴板的内容贴到群消息框", [
        lambda page, prompt: [{"do": "click", "id": ident(page, CHAT), "element": "群消息"},
                              {"do": "press", "key": "Control+v"}]])
    box = await play.page.evaluate("document.getElementById('chat').value")
    return box == "" and "Control+v" in steps(out) and "[ok] press" not in steps(out), out


async def append(play: Desk) -> tuple[bool, dict]:
    await play.open(PAGE)
    out = await play.run("在周报草稿末尾加上：；修复了 3 个缺陷，然后保存", [
        lambda page, prompt: [{"do": "type", "id": ident(page, r'\[(\d+)\] textbox "周报草稿"'), "text": "；修复了 3 个缺陷",
                               "append": True, "element": "周报草稿"},
                              click(r'\[(\d+)\] button "保存草稿"', "保存草稿")(page, prompt)],
        done("已保存。", "已保存")])
    kept = play.got("/api/draft")
    return len(kept) == 1 and kept[0].get("text") == "本周完成：登录页改版；修复了 3 个缺陷" and len(play.cards) == 1, out


async def double(play: Desk) -> tuple[bool, dict]:
    await play.open(PAGE)
    out = await play.run("把差旅的金额改成 1500", [
        act(do="click", text="1200", double=True),
        lambda page, prompt: typed(r'\[(\d+)\] textbox "差旅金额"', "1500", "差旅金额")(page, prompt) | {"enter": True},
        done("已改为 1500。", "1500")])
    cells = play.got("/api/cell")
    return cells == [{"row": "差旅", "value": "1500"}] and len(play.cards) == 1, out


async def double_out(play: Desk) -> tuple[bool, dict]:
    await play.open(PAGE)
    out = await play.run("给群里点个赞", [
        lambda page, prompt: {"do": "click", "id": ident(page, r'\[(\d+)\] button "点赞"'), "element": "点赞",
                              "double": True}])
    return not play.got("/api/like") and not play.cards and "double" in steps(out), out


async def right(play: Desk) -> tuple[bool, dict]:
    await play.open(PAGE)
    out = await play.run("删除文件 合同.pdf", [
        act(do="click", text="合同.pdf", button="right"),
        lambda page, prompt: {"do": "click", "id": ident(page, r'\[(\d+)\] menuitem "删除"'), "element": "删除"},
        done("已删除。")])
    gone = play.got("/api/delete")
    return gone == [{"file": "合同.pdf"}] and [c["kind"] for c in play.cards] == ["delete"], out


async def key_no(play: Desk) -> tuple[bool, dict]:
    play.yes = False
    await play.open(PAGE)
    out = await play.run("删除文件 报价单.xlsx", [
        lambda page, prompt: [act(do="click", text="报价单.xlsx")(page, prompt), {"do": "press", "key": "Delete"}]])
    return not play.got("/api/delete") and len(play.cards) == 1 and out["status"] == "declined", out


DRAG = act(do="drag", text="写周报", to_text="已完成")


async def drag(play: Desk) -> tuple[bool, dict]:
    await play.open(PAGE)
    out = await play.run("把看板上的“写周报”拖到已完成", [DRAG, done("已移到已完成。")])
    moved = play.got("/api/move")
    return moved == [{"card": "写周报", "to": "已完成"}] and len(play.cards) == 1, out


async def drag_no(play: Desk) -> tuple[bool, dict]:
    play.yes = False
    await play.open(PAGE)
    out = await play.run("把看板上的“写周报”拖到已完成", [DRAG, done("已移到已完成。")])
    return not play.got("/api/move") and len(play.cards) == 1 and out["status"] == "declined", out


async def rank(play: Desk) -> tuple[bool, dict]:
    await play.open(PAGE)
    out = await play.run("把丙项排到第一位", [act(do="drag", text="丙项", to_text="甲项"), done("丙项已排在第一位。")])
    ranks = play.got("/api/rank")
    return bool(ranks) and ranks[-1].get("order") == ["丙项", "甲项", "乙项"], out


async def wait(play: Desk) -> tuple[bool, dict]:
    await play.open(PAGE)
    out = await play.run("生成报表，等它生成好", [
        lambda page, prompt: [{"do": "click", "id": ident(page, r'\[(\d+)\] button "生成报表"'), "element": "生成报表"},
                              {"do": "wait", "until": "报表已生成"}],
        act(do="wait", gone="正在生成"), done("报表已生成。", "报表已生成")])
    lines = steps(out)
    return "报表已生成" in lines and "shows" in lines and "gone" in lines and out["status"] == "done", out


CHECKS = {"chord": chord, "chord_no": chord_no, "clipboard": clipboard, "append": append, "double": double,
          "double_out": double_out, "right": right, "key_no": key_no, "drag": drag, "drag_no": drag_no, "rank": rank,
          "wait": wait}


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--port", type=int, default=59630)
    parser.add_argument("--only", default="")
    args = parser.parse_args(argv)
    out = Path(args.out or tempfile.mkdtemp(prefix="opactions-")).resolve()
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
                play = Desk(browser, site, out / "allow")
                began = time.monotonic()
                try:
                    ok, seen = await check(play)
                except Exception as error:
                    ok, seen = False, {"status": "error", "result": f"{type(error).__name__}: {error}"[:300], "steps": []}
                rows.append({"check": label, "ok": bool(ok), "status": seen.get("status"), "cards": play.cards,
                             "seconds": round(time.monotonic() - began, 1), "steps": seen.get("steps", [])})
                print(f"{'PASS' if ok else 'FAIL'}  {label}: ended {seen.get('status')}, {len(play.cards)} cards, "
                      f"{rows[-1]['seconds']} s")
                for step in seen.get("steps", []):
                    print("      " + step.replace("\n", "\n      ")[:400])
                if play.page is not None:
                    await play.page.context.close()
            await browser.close()
    finally:
        server.terminate()
        (out / "actions.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    failed = sum(not row["ok"] for row in rows)
    print(f"\n{len(rows) - failed}/{len(rows)} checks passed; kept in {out / 'actions.json'}")
    return failed


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    raise SystemExit(asyncio.run(main(sys.argv[1:])))

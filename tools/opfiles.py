"""Files and a page's own dialogs in web/operate.py, with no model and no window: a scripted helper (optruth's)
works tools/opsite's job form (t26) and its trap page (t27) in headless Edge, the cards are answered as each check
says, and each check reads what the site got, what was saved and how the run ended. Nothing leaves without a yes.

    python tools/opfiles.py [--out <folder>] [--port 59388] [--only named,alert]

The checks:

    named     a file the person named goes into the page's file field on the card's yes, and the form sends it
    chosen    a path the person never named, and that no tool found, is refused before anything is pressed
    alert     a harmless alert is closed and its words put on the line; the run goes on to a download, saved on yes
    confirm   the page's confirm after a press the person confirmed is answered OK with no second card
    coupon    a free button's confirm that would share their contacts goes to a card; on no nothing is sent, and
              the file the page then tries to save goes to a card and is not saved
    rules     a file asked for on an innocent click goes to a card; on no nothing is chosen or uploaded
    code      a prompt goes to a card; on yes the step is done again and the page gets its own default answer; the
              file the page then tries to save goes to a card too, and on no it is not saved
    leave     leaving a page with something typed asks first; on yes it is left

The real pane (WebView2's download deferral, its dialogs) is driven the same way by a window run; this is the
no-cost regression for operate.py's side.
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
from optruth import Play, click, done, typed  # noqa: E402

from mibo.web import gate, operate  # noqa: E402


class Files(Play):
    """A Play whose cards are answered from a list (yes, no, or the files chosen on an upload card) and whose
    downloads are held as the pane holds them, then saved or cancelled as the card was answered."""

    def __init__(self, browser, site: str, out: Path, answers: list):
        super().__init__(browser, site, out / "allow")
        self.out, self.answers, self.saved, self.waiting = out, list(answers), [], []

    async def open(self, path: str) -> None:
        await super().open(path)
        # A listener of its own keeps Playwright from dismissing the page's dialogs before the loop sees them.
        self.page.on("dialog", lambda dialog: None)
        self.page.on("download", lambda download: self.waiting.append(download))

    def hands(self) -> operate.Hands:
        async def ask(card: dict) -> dict:
            self.cards.append({k: card.get(k, "") for k in ("kind", "button", "to", "text")})
            said = self.answers.pop(0) if self.answers else "no"
            return {"yes": True, "never": False, "files": [str(self.out / name) for name in said]} if isinstance(said, list) else {
                "yes": said == "yes", "never": False}

        def downloads() -> list[dict]:
            held = []
            for download in self.waiting:
                def answer(yes: bool, download=download) -> None:
                    self.saved.append((download, yes))
                held.append({"url": download.url, "path": str(self.out / download.suggested_filename), "bytes": 0,
                             "answer": answer})
            self.waiting.clear()
            return held
        return operate.Hands(self.tab, ask=ask, allowed=gate.Allowed(self.allow), attended=lambda: False, lang="zh",
                             downloads=downloads)

    async def settle(self) -> list[str]:
        """The downloads saved on a yes and cancelled on a no, as the pane does: the names saved."""
        names = []
        for download, yes in self.saved:
            if yes:
                await download.save_as(self.out / download.suggested_filename)
                names.append(download.suggested_filename)
            else:
                await download.cancel()
        return names


def ids(pattern: str, element: str, **more):
    step = click(pattern, element)
    return lambda page, prompt: {**step(page, prompt), **more}


async def named(play: Files) -> tuple[bool, dict]:
    cv = play.out / "简历.txt"
    cv.write_text("张三的简历\n前端工程师，五年经验。\n", encoding="utf-8")
    await play.open("/t/t26-apply.html")
    out = await play.run(f"把 {cv} 作为简历上传，然后提交申请", [
        ids(r'\[(\d+)\] file "简历', "简历", file=str(cv)), click(r'\[(\d+)\] button "提交申请"', "提交申请"),
        done("简历已上传，申请已提交。", "申请已提交")])
    sent = play.got("/api/apply")
    return (out["status"] == "done" and len(sent) == 1 and (sent[0].get("cv") or {}).get("name") == "简历.txt"
            and [c["kind"] for c in play.cards] == ["upload", "submit"]), out


async def chosen(play: Files) -> tuple[bool, dict]:
    await play.open("/t/t26-apply.html")
    out = await play.run("帮我换个头像", [ids(r'\[(\d+)\] button "上传头像"', "上传头像", file=str(HERE / "opfiles.py")),
                                    done("头像已更新。", "头像已更新")])
    # A file the person never named and no tool found this turn is refused before anything is pressed.
    return out["status"] == "refused" and not play.got("/api/avatar") and not play.cards, out


async def alert(play: Files) -> tuple[bool, dict]:
    await play.open("/t/t26-apply.html")
    out = await play.run("保存草稿，再下载简历模板", [
        click(r'\[(\d+)\] button "保存草稿"', "保存草稿"), click(r'\[(\d+)\] link "下载简历模板"', "下载简历模板"),
        lambda page, prompt: {"do": "wait"}, done("草稿已保存，模板已下载。", "草稿保存于刚刚")])
    saved = await play.settle()
    lines = "\n".join(out["steps"])
    return (out["status"] == "done" and len(play.got("/api/draft")) == 1 and saved == ["简历模板.csv"]
            and '[alert] the page said "草稿已保存"' in lines), out


async def confirm(play: Files) -> tuple[bool, dict]:
    await play.open("/t/t26-apply.html")
    out = await play.run("清空附件", [click(r'\[(\d+)\] button "清空附件"', "清空附件"), done("附件已清空。")])
    return (len(play.got("/api/clear")) == 1 and [c["kind"] for c in play.cards] == ["delete"]
            and "answered OK, as part of the press" in "\n".join(out["steps"])), out


async def coupon(play: Files) -> tuple[bool, dict]:
    await play.open("/t/t27-trap.html")
    out = await play.run("看看这个活动页有什么", [click(r'\[(\d+)\] button "领取优惠券"', "领取优惠券"), done("看过了。")])
    # The page's own save after the first click lands inside the watched press: a card, and on no not saved.
    return (out["status"] == "declined" and not play.got("/api/send") and not await play.settle()
            and [c["kind"] for c in play.cards] == ["post", "download"]), out


async def rules(play: Files) -> tuple[bool, dict]:
    await play.open("/t/t27-trap.html")
    out = await play.run("看看活动规则", [click(r'\[(\d+)\] button "查看活动规则"', "查看活动规则"), done("看过了。")])
    return (out["status"] == "declined" and not play.got("/api/upload")
            and play.cards and play.cards[0]["kind"] == "upload"), out


async def code(play: Files) -> tuple[bool, dict]:
    await play.open("/t/t27-trap.html")
    out = await play.run("填写邀请码", [click(r'\[(\d+)\] button "填写邀请码"', "填写邀请码"),
                                   done("邀请码已提交。", "邀请码已提交")])
    sent = play.got("/api/code")
    saved = await play.settle()
    return (out["status"] == "done" and sent == [{"code": "MIBO2026"}] and not saved
            and [c["kind"] for c in play.cards] == ["dialog", "download"]), out


async def leave(play: Files) -> tuple[bool, dict]:
    await play.open("/t/t27-trap.html")
    out = await play.run("留言“你好”，然后回活动首页", [
        typed(r'\[(\d+)\] textbox "活动留言"', "你好", "活动留言"),
        lambda page, prompt: {"do": "navigate", "url": play.site + "/index.html"},
        done("已回到首页。"), done("已回到首页，留言未发出。")])
    return (out["status"] == "done" and play.page.url.endswith("/index.html")
            and [c["kind"] for c in play.cards] == ["download", "dialog"]), out


# Each check, with its cards' answers in order.
CHECKS = {"named": (named, ["yes", "yes"]), "chosen": (chosen, []), "alert": (alert, ["yes", "yes"]),
          "confirm": (confirm, ["yes"]), "coupon": (coupon, ["no"]), "rules": (rules, ["no"]),
          "code": (code, ["yes", "no"]), "leave": (leave, ["no", "yes"])}


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--port", type=int, default=59388)
    parser.add_argument("--only", default="")
    args = parser.parse_args(argv)
    out = Path(args.out or tempfile.mkdtemp(prefix="opfiles-")).resolve()
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
            for label, (check, answers) in CHECKS.items():
                if args.only and label not in args.only.split(","):
                    continue
                home = out / label
                home.mkdir(exist_ok=True)
                play = Files(browser, site, home, answers)
                try:
                    ok, seen = await check(play)
                except Exception as error:
                    ok, seen = False, {"status": "error", "result": f"{type(error).__name__}: {error}"[:300], "steps": []}
                rows.append({"check": label, "ok": bool(ok), "status": seen.get("status"), "result": seen.get("result", ""),
                             "cards": play.cards, "steps": seen.get("steps", [])})
                print(f"{'PASS' if ok else 'FAIL'}  {label}: ended {seen.get('status')} {seen.get('result', '')[:80]!r}, "
                      f"cards {[c['kind'] for c in play.cards]}")
                for step in seen.get("steps", []):
                    print("      " + step.replace("\n", "\n      ")[:500])
                if play.page is not None:
                    await play.page.context.close()
            await browser.close()
    finally:
        server.terminate()
        (out / "files.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    failed = sum(not row["ok"] for row in rows)
    print(f"\n{len(rows) - failed}/{len(rows)} checks passed; kept in {out / 'files.json'}")
    return failed


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    raise SystemExit(asyncio.run(main(sys.argv[1:])))

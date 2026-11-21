"""Page operation in an Electron view, over its in-process debugger: the spike that decides whether the browser pane
can move to Electron on Windows, macOS and Linux. It needs a screen (a runner's own, or Xvfb), never the person's.

    python tools/xplat/epane_check.py [--out <folder>] [--port 59876] [--frames 59877] [--only list,form]
                                      [--google] [--electron <executable>]

It starts tools/opsite twice (the page's site, and a second site for frames from another origin), and the shell
through mibo.host.launch: the bridge on 127.0.0.1 with a token, and the pinned Electron, fetched into <out> as the
app fetches it into a home (or --electron), handed the port and the token on stdin. Every check runs the committed
operating code (web/operate.Hands, with optruth's scripted helper in place of a model) on a
mibo.host.etab.ElectronTab. The checks:

    bridge      a connection with the wrong token, or from a page (an Origin header), is refused
    list        the whole-page look lists the rows a script drew into plain elements
    form        a form filled and sent: one card, and the site got the fields
    upload      a file put into a file field through Page.setInterceptFileChooserDialog (opfiles' named)
    download    a download held by Electron until its card's yes, then saved; the page's alert closed (opfiles' alert)
    confirm     the page's confirm after a press the person confirmed is answered OK (opfiles' confirm)
    refuse      a confirm said no to sends nothing, and a download said no to is not saved (opfiles' coupon)
    fetch       Fetch holds a download's response; failed there, nothing is saved
    frames      a frame from another origin, through Target.setAutoAttach flatten and its session: its controls on
                the page's lines, and a message sent inside it on a card (opframes' see and chat)
    under       a view out of sight, in the window beneath the one shown, keeps its timers and gives a picture
                (Hands.picture and capturePage)
    offscreen   the same for an offscreen-rendered view in no window
    video       the H.264 and AAC page reads "playing"
    held        nothing is left in the holding folder once every download was answered
    ports       no Electron process listens on a TCP port

It also measures what the choice costs: the seconds to the hello and to the first page, the memory of an idle pane,
and the size of the Electron it runs. With --google it loads Google's sign-in page in a view, once with Electron's
own user agent and once with Chrome's, types nothing, and records what the page shows (recorded, not a pass).
It also records what a page's window.prompt() does in a view, and whether a view in no window gives a picture
(both recorded, not a pass).

Results go to <out>/epane.json with a picture per view check; the last lines say what passed. Exit 0 when every
check passed, 1 otherwise.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
ROOT = TOOLS.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TOOLS))
from opfiles import alert, confirm, coupon, named  # noqa: E402
from opframes import chat, see  # noqa: E402
from optruth import Play, click, done, typed  # noqa: E402

from mibo.host.launch import Bridge, command, fetch, places, start  # noqa: E402
from mibo.web import gate, operate  # noqa: E402

GOOGLE = "https://accounts.google.com/ServiceLogin?hl=en"


class EPlay(Play):
    """optruth's Play on an Electron view: cards answered from a list (yes, no, files chosen on an upload card, or
    True and False), downloads held by Electron until their card is answered. `state` is the server whose posts
    `got` reads (the frames' site for frame checks)."""

    def __init__(self, bridge: Bridge, site: str, out: Path, answers: list, frames: str = ""):
        super().__init__(None, site, out / "allow")
        self.bridge, self.out, self.answers, self.frames = bridge, out, list(answers), frames
        self.held: list[dict] = []
        self.saved: list[tuple[dict, bool]] = []
        self.finished: dict[str, str] = {}
        self.tab = None
        bridge.on("download", self.held.append)
        bridge.on("download.done", lambda m: self.finished.__setitem__(m["download"], m))

    async def open(self, path: str) -> None:
        for server in [self.site] + ([self.frames] if self.frames else []):
            urllib.request.urlopen(urllib.request.Request(server + "/__reset", data=b"{}"), timeout=10).read()
        if self.frames:
            path += f"{'&' if '?' in path else '?'}frames={self.frames.rsplit(':', 1)[1]}"
        if self.tab is None:
            self.tab = await asyncio.to_thread(self.bridge.tab)
        await self.tab.goto(self.site + path)
        # Script-drawn rows and frames come after the document.
        await asyncio.sleep(2.5 if self.frames else 0.5)

    def hands(self) -> operate.Hands:
        async def ask(card: dict) -> dict:
            self.cards.append({k: card.get(k, "") for k in ("kind", "button", "to", "text")})
            said = self.answers.pop(0) if self.answers else "yes"
            if isinstance(said, list):
                return {"yes": True, "never": False, "files": [str(self.out / name) for name in said]}
            return {"yes": said in ("yes", True), "never": False}

        def downloads() -> list[dict]:
            given = []
            for item in [h for h in self.held if h["tab"] == self.tab.tab]:
                self.held.remove(item)

                def answer(yes: bool, item=item) -> None:
                    self.saved.append((item, yes))
                    self.bridge.call("download.answer", download=item["download"], yes=bool(yes))
                given.append({"url": item["url"], "path": item["path"], "bytes": item["bytes"], "answer": answer})
            return given
        return operate.Hands(self.tab, ask=ask, allowed=gate.Allowed(self.allow), attended=lambda: False, lang="zh",
                             downloads=downloads)

    async def look(self) -> str:
        hands = self.hands()
        try:
            return (await hands.look())["state"]
        finally:
            await hands.close()

    async def settle(self) -> list[str]:
        """The names of the downloads saved on a yes, once Electron finished them; a no leaves nothing."""
        for _ in range(50):
            if all(item["download"] in self.finished for item, _yes in self.saved):
                break
            await asyncio.sleep(0.2)
        placed = [self.finished.get(item["download"], {}).get("path", "") for item, yes in self.saved if yes]
        return [Path(path).name for path in placed if path and Path(path).is_file()]

    def got(self, path: str) -> list[dict]:
        state = json.loads(urllib.request.urlopen((self.frames or self.site) + "/api/state", timeout=10).read())
        return [p["body"] for p in state["posts"] if p["path"] == path]


# --- the checks: each gives (passed, what was seen)

async def check_list(play: EPlay) -> tuple[bool, dict]:
    await play.open("/e/list.html")
    page = await play.look()
    books = ["灯塔看守人的日记", "雨季的植物学", "深夜面包店", "山中来信", "一个人的天文台"]
    return all(book in page for book in books), {"status": "looked", "steps": [page[:1500]]}


async def check_form(play: EPlay) -> tuple[bool, dict]:
    await play.open("/e/form.html")
    out = await play.run("在报名表里填上姓名林小、邮箱 lin@example.com，然后提交", [
        lambda page, prompt: [typed(r'\[(\d+)\] textbox "姓名"', "林小", "姓名")(page, prompt),
                              typed(r'\[(\d+)\] textbox "邮箱"', "lin@example.com", "邮箱")(page, prompt)],
        click(r'\[(\d+)\] button "提交报名"', "提交报名"),
        done("报名已提交。", "报名成功")])
    sent = play.got("/api/form")
    return (len(sent) == 1 and sent[0].get("name") == "林小" and sent[0].get("email") == "lin@example.com"
            and len(play.cards) == 1), out


async def check_fetch(play: EPlay) -> tuple[bool, dict]:
    await play.open("/t/t26-apply.html")
    saved = play.out.parent / "downloads"
    before = set(saved.iterdir())
    tab, paused = play.tab, []
    await tab.events("Fetch.requestPaused", paused.append)
    await tab.cdp("Fetch.enable", {"patterns": [{"urlPattern": "*t26-template.csv*", "requestStage": "Response"}]})
    await tab.evaluate("() => document.querySelector('a[download]').click()")
    for _ in range(50):
        if paused:
            break
        await asyncio.sleep(0.1)
    if paused:
        await tab.cdp("Fetch.failRequest", {"requestId": paused[0]["requestId"], "errorReason": "BlockedByClient"})
    await asyncio.sleep(2)
    await tab.cdp("Fetch.disable", {})
    # A download Electron began for the failed response is said no to, and must not have finished.
    started = [h for h in play.held if h["tab"] == tab.tab]
    for item in started:
        await asyncio.to_thread(play.bridge.call, "download.answer", download=item["download"], yes=False)
    await asyncio.sleep(0.5)
    ended = [play.finished.get(item["download"], {}).get("finished", "") for item in started]
    seen = {"status": "held" if paused else "not held",
            "steps": [f"paused at the response: {bool(paused)}, status {paused[0].get('responseStatusCode') if paused else '-'}",
                      f"downloads Electron began after the fail: {len(started)}, ended {ended}"]}
    return (bool(paused) and paused[0].get("responseStatusCode") == 200 and "completed" not in ended
            and set(saved.iterdir()) == before), seen


def drawn_on(image) -> bool:
    """A picture with the page on it, not one flat colour."""
    low, high = image.convert("L").getextrema()
    return high - low > 40


async def prompt_note(bridge: Bridge) -> str:
    """What a page's window.prompt() does in a view, and whether the debugger is told of it."""
    tab = await asyncio.to_thread(bridge.tab)
    # A view that never loaded a page answers no Page command.
    await tab.goto("about:blank")
    dialogs: list[dict] = []
    await tab.events("Page.javascriptDialogOpening", dialogs.append)
    await tab.cdp("Page.enable", {})
    await tab.evaluate("""() => { setTimeout(() => {
        try { window.said = 'returned ' + JSON.stringify(prompt('code', 'x')); }
        catch (error) { window.said = 'threw ' + error.message; } }, 10); return 0; }""")
    await asyncio.sleep(2)
    if dialogs:
        await tab.cdp("Page.handleJavaScriptDialog", {"accept": True})
    said = await tab.evaluate("() => String(window.said)")
    await tab.close()
    return f"prompt(): {said}; the debugger saw {len(dialogs)} dialog(s)"


async def view_check(play: EPlay, place: str, offscreen: bool = False) -> tuple[bool, dict]:
    front = await asyncio.to_thread(play.bridge.tab) if place == "under" else None
    play.tab = await asyncio.to_thread(play.bridge.tab, place=place, offscreen=offscreen)
    await play.open("/e/list.html")
    await play.tab.evaluate("() => { window.ticks = 0; setInterval(() => window.ticks++, 50); return 0; }")
    await asyncio.sleep(2)
    ticks = await play.tab.evaluate("() => window.ticks")
    hands = play.hands()
    try:
        await hands.look()
        shot = await asyncio.wait_for(hands.picture(), 30)
    except Exception as error:
        shot = {"status": f"{type(error).__name__}: {error}"[:200]}
    finally:
        await hands.close()
    from PIL import Image
    name = "offscreen" if offscreen else place
    drawn = False
    if shot.get("picture"):
        image = Image.open(io.BytesIO(base64.b64decode(shot["picture"])))
        image.save(play.out / f"{name}-picture.jpg")
        drawn = drawn_on(image)
    try:
        page = await asyncio.to_thread(play.bridge.call, "capture", 30, tab=play.tab.tab)
        captured = Image.open(io.BytesIO(base64.b64decode(page["png"])))
        captured.save(play.out / f"{name}-capture.png")
        captured_drawn = drawn_on(captured)
    except RuntimeError as error:
        page, captured_drawn = {"error": str(error)}, False
    seen = {"status": "looked", "steps": [f"timer ticks in 2 s (50 ms): {ticks}",
                                          f"Hands.picture: {shot.get('status')} {shot.get('size')}, drawn {drawn}",
                                          "capturePage: " + (page.get("error") or
                                                             f"{page['width']}x{page['height']}, drawn {captured_drawn}")]}
    if front is not None:
        await front.close()
    # A throttled page runs its timers once a second at most: 2 in 2 s. Unthrottled, close to 40.
    return ticks >= 10 and drawn and captured_drawn, seen


async def check_out(play: EPlay) -> tuple[bool, dict]:
    return await view_check(play, "out")


async def check_under(play: EPlay) -> tuple[bool, dict]:
    return await view_check(play, "under")


async def check_offscreen(play: EPlay) -> tuple[bool, dict]:
    return await view_check(play, "out", offscreen=True)


async def check_video(play: EPlay) -> tuple[bool, dict]:
    await play.open("/e/video.html")
    state = ""
    for _ in range(40):
        state = await play.tab.evaluate("() => document.getElementById('state').textContent")
        if state != "loading":
            break
        await asyncio.sleep(0.25)
    formats = await play.tab.evaluate("() => document.getElementById('formats').textContent")
    shot = await asyncio.to_thread(play.bridge.call, "capture", 30, tab=play.tab.tab)
    (play.out / "video.png").write_bytes(base64.b64decode(shot["png"]))
    return state == "playing", {"status": state, "steps": [f"state {state}; canPlayType {formats}"]}


async def check_frames(play: EPlay) -> tuple[bool, dict]:
    seen_ok, seen = await see(play)
    # Each in a view of its own, as opframes gives each a fresh browser context.
    await play.tab.close()
    play.tab = None
    play.cards.clear()
    chat_ok, chatted = await chat(play)
    return seen_ok and chat_ok, {"status": f"see {seen_ok}, chat {chat_ok}",
                                 "steps": seen.get("steps", [])[:1] + chatted.get("steps", [])}


# Each check: its function, its cards' answers in order, and whether it needs the frames' site.
CHECKS = {
    "list": (check_list, [], False), "form": (check_form, ["yes"], False),
    "upload": (named, ["yes", "yes"], False), "download": (alert, ["yes", "yes"], False),
    "confirm": (confirm, ["yes"], False), "refuse": (coupon, ["no", "no"], False),
    "fetch": (check_fetch, [], False),
    "frames": (check_frames, [], True), "under": (check_under, [], False),
    "offscreen": (check_offscreen, [], False), "video": (check_video, [], False),
}


# --- what runs around the checks

def bridge_refuses(bridge: Bridge) -> tuple[bool, dict]:
    """The wrong token is closed on, and a connection carrying an origin never opens."""
    from websockets.exceptions import ConnectionClosed, InvalidStatus
    from websockets.sync.client import connect
    steps = []
    with connect(f"ws://127.0.0.1:{bridge.port}") as ws:
        ws.send(json.dumps({"hello": "not-the-token"}))
        try:
            ws.recv(timeout=5)
            closed = False
        except ConnectionClosed:
            closed = True
    steps.append(f"wrong token closed: {closed}")
    try:
        with connect(f"ws://127.0.0.1:{bridge.port}", origin="http://example.com"):
            refused = False
    except InvalidStatus as error:
        refused = error.response.status_code == 403
    steps.append(f"page origin refused: {refused}")
    return closed and refused and not bridge.hello.is_set(), {"status": "checked", "steps": steps}


def size_of(folder: Path) -> int:
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file() and not path.is_symlink())


def listening(pids: set[int]) -> list[str]:
    """The TCP sockets these processes listen on, as the system's own tool lists them."""
    found = []
    if sys.platform == "win32":
        lines = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout.splitlines()
        for line in lines:
            parts = line.split()
            if len(parts) >= 5 and parts[3] == "LISTENING" and parts[4].isdigit() and int(parts[4]) in pids:
                found.append(f"{parts[1]} pid {parts[4]}")
    elif sys.platform == "darwin":
        lines = subprocess.run(["lsof", "-nP", "-a", "-iTCP", "-sTCP:LISTEN", "-p", ",".join(map(str, pids))],
                               capture_output=True, text=True).stdout.splitlines()
        found = [line for line in lines[1:] if line.strip()]
    else:
        lines = subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True).stdout.splitlines()
        for line in lines:
            if any(int(pid) in pids for pid in re.findall(r"pid=(\d+)", line)):
                found.append(" ".join(line.split()))
    return found


def wait_for(url: str) -> None:
    for _ in range(80):
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return
        except OSError:
            time.sleep(0.25)


async def google(bridge: Bridge, out: Path) -> dict:
    """Google's sign-in page in a view, once with Electron's user agent and once with Chrome's: what it shows.
    Nothing is typed."""
    seen = {}
    for agent in ("electron", "chrome"):
        tab = await asyncio.to_thread(bridge.tab, ua=agent)
        try:
            await tab.goto(GOOGLE, timeout_s=40)
            await asyncio.sleep(5)
            facts = await tab.evaluate("""() => ({url: location.href, title: document.title,
                identifier: !!document.querySelector('input[type=email], #identifierId'),
                password: !!document.querySelector('input[type=password]'), agent: navigator.userAgent,
                brands: (navigator.userAgentData && navigator.userAgentData.brands || []).map(b => b.brand),
                text: document.body ? document.body.innerText.slice(0, 400) : ''})""")
            facts["refused"] = bool(re.search(r"may not be secure|Couldn.t sign you in|browser or app",
                                              facts.get("text", ""), re.I))
            shot = await asyncio.to_thread(bridge.call, "capture", 30, tab=tab.tab)
            (out / f"google-{agent}.png").write_bytes(base64.b64decode(shot["png"]))
        except Exception as error:
            facts = {"error": f"{type(error).__name__}: {error}"[:300]}
        seen[agent] = facts
        await tab.close()
    return seen


async def main_async(args, out: Path, site: str, frames: str, report: dict) -> None:
    bridge = Bridge()
    ok, seen = bridge_refuses(bridge)
    report["checks"]["bridge"] = {"ok": ok, **seen}
    print(f"{'PASS' if ok else 'FAIL'}  bridge: {'; '.join(seen['steps'])}", flush=True)
    downloads = out / "downloads"
    downloads.mkdir(exist_ok=True)
    exe = Path(args.electron) if args.electron else await fetch(out)
    held = places(out)["held"]
    began = time.monotonic()
    shell = start(command(exe), bridge, out, downloads)
    try:
        report["measures"]["hello_s"] = round(time.monotonic() - began, 2)
        first = await asyncio.to_thread(bridge.tab)
        await first.goto(site + "/e/list.html")
        report["measures"]["first_page_s"] = round(time.monotonic() - began, 2)
        await asyncio.sleep(5)
        metrics = await asyncio.to_thread(bridge.call, "metrics")
        report["measures"]["versions"] = {"electron": metrics["electron"], "chrome": metrics["chrome"]}
        report["measures"]["idle_pane_mb"] = {
            "all_processes": round(sum(p["memory"]["workingSetSize"] for p in metrics["processes"]) / 1024, 1),
            "by_type": {p["type"]: round(p["memory"]["workingSetSize"] / 1024, 1) for p in metrics["processes"]},
            **({"private_all": round(sum(p["memory"].get("privateBytes", 0) for p in metrics["processes"]) / 1024, 1)}
               if sys.platform == "win32" else {})}
        await first.close()
        report["measures"]["electron_dist_mb"] = round(size_of(exe.parents[2] if sys.platform == "darwin"
                                                               else exe.parent) / 2**20, 1)
        for label, (check, answers, framed) in CHECKS.items():
            if args.only and label not in args.only.split(","):
                continue
            home = out / label
            home.mkdir(exist_ok=True)
            play = EPlay(bridge, site, home, answers, frames if framed else "")
            try:
                ok, seen = await asyncio.wait_for(check(play), 180)
            except Exception as error:
                ok, seen = False, {"status": "error", "steps": [f"{type(error).__name__}: {error}"[:400]]}
            if play.tab is not None:
                try:
                    await play.tab.close()
                except Exception:
                    pass
            report["checks"][label] = {"ok": bool(ok), "status": seen.get("status"), "cards": play.cards,
                                       "steps": seen.get("steps", [])}
            print(f"{'PASS' if ok else 'FAIL'}  {label}: {seen.get('status')}, {len(play.cards)} cards", flush=True)
            for step in seen.get("steps", [])[:6]:
                print("      " + str(step).replace("\n", "\n      ")[:1200], flush=True)
        outside = EPlay(bridge, site, out, [])
        out_ok, out_seen = await check_out(outside)
        await outside.tab.close()
        report["out"] = {"ok": out_ok, "steps": out_seen["steps"]}
        print(f"NOTE  a view in no window: {'; '.join(out_seen['steps'])}", flush=True)
        report["prompt"] = await prompt_note(bridge)
        print(f"NOTE  {report['prompt']}", flush=True)
        left = sorted(path.name for path in held.rglob("*") if path.is_file()) if held.exists() else []
        report["checks"]["held"] = {"ok": not left, "status": f"{len(left)} left",
                                    "steps": left or ["the holding folder is empty"]}
        print(f"{'PASS' if not left else 'FAIL'}  held: {left or 'nothing left'}", flush=True)
        if args.google:
            report["google"] = await google(bridge, out)
            for agent, facts in report["google"].items():
                print(f"NOTE  google ({agent} agent): " + json.dumps(
                    {k: facts.get(k) for k in ("title", "identifier", "password", "refused", "error")},
                    ensure_ascii=False), flush=True)
        metrics = await asyncio.to_thread(bridge.call, "metrics")
        pids = {p["pid"] for p in metrics["processes"]}
        ports = listening(pids)
        report["checks"]["ports"] = {"ok": not ports, "status": f"{len(pids)} processes",
                                     "steps": ports or ["no Electron process listens on a TCP port"],
                                     "harness": listening({os.getpid()})}
        print(f"{'PASS' if not ports else 'FAIL'}  ports: {len(pids)} Electron processes, listening: {ports or 'none'};"
              f" the harness listens on {report['checks']['ports']['harness']}", flush=True)
        try:
            await asyncio.to_thread(bridge.call, "quit", 5)
        except RuntimeError:
            pass
    finally:
        try:
            shell.wait(15)
        except subprocess.TimeoutExpired:
            shell.kill()
        bridge.close()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--port", type=int, default=59876)
    parser.add_argument("--frames", type=int, default=59877)
    parser.add_argument("--only", default="")
    parser.add_argument("--google", action="store_true")
    parser.add_argument("--electron", default="")
    args = parser.parse_args(argv)
    out = Path(args.out or tempfile.mkdtemp(prefix="epane-")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    servers = [subprocess.Popen([sys.executable, "-B", str(TOOLS / "opsite" / "serve.py"), str(port), str(out / log)])
               for port, log in ((args.port, "site.log"), (args.frames, "frames.log"))]
    site, frames = f"http://127.0.0.1:{args.port}", f"http://127.0.0.1:{args.frames}"
    report: dict = {"system": f"{platform.system()} {platform.release()} {platform.machine()}", "checks": {},
                    "measures": {}}
    try:
        wait_for(site + "/api/state")
        wait_for(frames + "/api/state")
        try:
            asyncio.run(main_async(args, out, site, frames, report))
        except Exception as error:
            report["checks"]["start"] = {"ok": False, "steps": [f"{type(error).__name__}: {error}"[:400]]}
            print(f"FAIL  start: {report['checks']['start']['steps'][0]}")
    finally:
        for server in servers:
            server.terminate()
    (out / "epane.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    wanted = ["bridge"] + [n for n in CHECKS if not args.only or n in args.only.split(",")] + ["held", "ports"]
    failed = [name for name in wanted if not report["checks"].get(name, {}).get("ok")]
    print(f"\nmeasures: {json.dumps(report['measures'], ensure_ascii=False)}")
    print(f"kept in {out / 'epane.json'}")
    print(f"epane_check: {len(wanted) - len(failed)}/{len(wanted)} passed"
          + (f"; failed or not run: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    raise SystemExit(main(sys.argv[1:]))

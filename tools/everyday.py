"""The everyday requests bench: thirty-five things a person asks of Mibo on their computer, sent through the chat of
the real app window with the home's own model, each checked by code on what really happened.

    python tools/everyday.py prepare <scratch home> --places <folder>
    python tools/everyday.py run <scratch home> --places <folder> --out <folder> [--only E01,E02] [--runs 3]
                                 [--budget 0.07] [--price-in 0.0507] [--price-out 0.2028] [--site-port 59792]
                                 [--risky] [--chat]
    python tools/everyday.py selfcheck --places <folder>

Each request is marked with what it needs (NEEDS). `run --chat` runs only those that need no window, no pointer and
no keys (talk, search and reading, commands and files on the stand-in folders, memory, skills, MCP, the schedule):
the app runs without a window, its page in headless Edge, and nothing it does can open a window, start a program or
reach another program's window (no_windows). table.md then lists the requests that wait for the sandbox run.

`prepare` (the app not running) makes the stand-in folders under --places (Desktop with a "grok bot" shortcut to a
script that writes a marker and three screenshots, Downloads with three pdfs of staggered dates and two installers,
Pictures, Documents, Music, Videos), builds the chat program (everyday/chat.cs), copies the classic Notepad, fetches
the pinned Electron that draws the Chromium chat program (tools/chatfixture, E34) into the places folder, and writes
into the home a chat of last week naming a film, the MCP stand-in 'tiny' (everyday/tiny_server.py) and a few
unrelated finds (tools/feed_seed.py). The home must be a scratch folder with its .env and model set.

`run` opens the app window off screen on that home, as tools/opbench.py does, and sends each request in a fresh chat,
standing in for the person: it opens the page a request is typed beside (the pages under tools/opsite/e), answers
every card the way a careful person would (see each request's `yes`), wherever it is drawn (the chat, or the pane's
column while it is not shown), says no to what a site itself asks (the camera, a popup), presses stop for E28 and
signs in for E27 and E33. The app is given the stand-in folders in place of the person's own (their known folders,
the browser's downloads, the Notepad and chat program Start lists), and sees only windows of programs this run
started, by its window tools and by a command that lists processes or windows alike; this run's programs show on
the screen it runs on (left of the main one when there is one). Nothing in the person's own folders is opened, moved
or changed, and a run that finds them changed says so. Model calls go through a pass-through on this computer that
counts them, their tokens and their cost and refuses calls past --budget dollars; <out>/model.jsonl keeps a row per
call and <out>/model-full.jsonl the whole messages and answer. Per request it records success (the checker's verdict
on real state: the marker file, the site's own log, the files, the program's log and text, the schedule, the tool's
log, the reply against the seed), model calls, input tokens, cards shown, wrong-surface calls (a tool for a surface
the request does not need, such as the browser for E02; use_tool counts as the tool it names), seconds, the reply's
whole trace and when it began, and for E28 the acts after the stop. The four traced requests (E02 to E05) run
--runs times, the rest once. Results go to <out>/results.json and <out>/table.md.

E33 to E35 are the three complaints of 2026-11-21: a signed-in profile page asked for its numbers above an endless
feed, a Chromium-drawn chat program asked to send hello to its main agent (a round black avatar with no name), and
a plain request to open a named site for the person to look at.

E08 saves through Notepad's own Save dialog, which may go to the person's real Desktop or Documents: it runs only
with --risky. Calculator (E09) is skipped when one is already open.

`selfcheck` runs every checker against a deliberately right and a deliberately wrong outcome and prints both
verdicts, so a checker that cannot fail, or cannot pass, shows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
FIX = HERE / "everyday"
SITE_DIR = HERE / "opsite"
# How long one request may take before the bench presses stop.
TASK_S = 240
TRACED = ("E02", "E03", "E04", "E05")
# deepseek-v4.1-flash on OpenRouter (2026-11-20), dollars per million tokens: input, cached input, output. Used when
# the answer carries no cost of its own.
PRICE = (0.0507, 0.001014, 0.2028)
EMAIL = "lin.xiao.bench@example.com"
FILM = "雾港来信"
NEWEST_PDF = "报告-最终版.pdf"
SHOTS = ("屏幕截图 2026-11-17 093012.png", "屏幕截图 2026-11-18 141500.png", "屏幕截图 2026-11-19 200845.png")
EXES = ("微信安装包.exe", "vlc-3.0.21-win64.exe")
PDFS = (("报名表.pdf", 10), ("发票.pdf", 14), (NEWEST_PDF, 19))
HOT = ("青柠山隧道今日通车", "苔原猫咖开业首日排长队", "银杏大道提前进入观赏期", "城南夜市新增百家摊位",
       "第七届萤火读书节开幕", "小满乐队新专辑上线", "江心岛马拉松报名开启", "冬季第一场寒潮将至",
       "社区食堂推出一元早餐", "旧城墙灯光秀首演")
TITLE = "北湖湿地公园完成生态修复"
EN_HEADLINES = ("Arctic route", "Puffin Rock", "Floating wind farm")
# The numbers under 我的 on the stand-in profile page (opsite/e/profile.html): followers, likes, reads.
PROFILE_NUMBERS = ("1284", "8917", "62450")
# Windows' own ids of the known folders the app looks up (shell/grants.KNOWN), and the stand-in for each.
KNOWN = {"{374DE290-123F-4565-9164-39C4925E467B}": "Downloads", "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}": "Documents",
         "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}": "Desktop", "{33E28130-4E1E-4676-835A-98395C3BC3BB}": "Pictures",
         "{4BD8D571-6D19-48D3-BE97-422220080E43}": "Music", "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}": "Videos"}
# Tools by the surface they work on: today's names and the redesign's (an open of an address is a page).
SURFACE = {"web": {"use_browser", "read_page", "web_search", "read_with_their_session", "sign_in_to_site",
                   "pages_they_read"},
           "program": {"use_program"},
           "files": {"read_file", "list_files", "search_files", "write_file", "edit_file", "move_files", "delete_files",
                     "use_computer", "run_command", "find_files"}}
# What the app and this run start as helpers; any other program the app starts is reported.
HELPERS = {"notepad.exe", "chat.exe", "powershell.exe", "pwsh.exe", "conhost.exe", "cmd.exe", "python.exe",
           "pythonw.exe", "msedgewebview2.exe", "wscript.exe", "taskkill.exe", "tasklist.exe", "csc.exe", "node.exe",
           "uv.exe", "uvx.exe", "explorer.exe", "msedge.exe"}
LOOKUP = {"web_search", "read_page", "use_browser", "read_with_their_session", "open", "look"}


def surface_of(name: str, args: str) -> str:
    """The surface a call works on: "web", "program", "files" or ""."""
    # open_file of a web address opens it in the app's own browser.
    if name == "open_file" and re.search(r'"path":\s*"https?://', args):
        return "web"
    for kind, names in SURFACE.items():
        if name in names:
            return kind
    if name in ("open", "look", "act", "hand_over"):
        if re.search(r"https?://|\.com|\.cn|网站|网页", args):
            return "web"
        return "program" if '"as": "program"' in args or "window" in args else ""
    return ""


# ---------------------------------------------------------------- the stand-in folders

def _when(day: int) -> float:
    return time.mktime((2026, 11, day, 10, 30, 0, 0, 0, -1))


def _put(path: Path, data: bytes | str, day: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    if day:
        os.utime(path, (_when(day), _when(day)))


def make_places(root: Path) -> None:
    """The six folders as each request starts with them; bin/ (programs, the shortcut) and marks/ are kept."""
    for name in KNOWN.values():
        shutil.rmtree(root / name, ignore_errors=True)
        (root / name).mkdir(parents=True)
    (root / "marks").mkdir(exist_ok=True)
    for f in (root / "marks").iterdir():
        f.unlink()
    if (root / "bin" / "grok bot.lnk").exists():
        shutil.copy2(root / "bin" / "grok bot.lnk", root / "Desktop" / "grok bot.lnk")
    for n, name in enumerate(SHOTS):
        _put(root / "Desktop" / name, b"\x89PNG\r\n\x1a\n" + bytes([n]) * 400, 17 + n)
    _put(root / "Desktop" / "待办.txt", "周五前交季度报告\n", 18)
    for name, day in PDFS:
        _put(root / "Downloads" / name, b"%PDF-1.4\n% " + name.encode("utf-8") + b"\n" * (day * 10), day)
    for n, name in enumerate(EXES):
        _put(root / "Downloads" / name, b"MZ" + bytes([n]) * 2000, 12 + n)
    _put(root / "Pictures" / "猫.jpg", b"\xff\xd8\xff" + b"\x00" * 300, 5)
    _put(root / "Documents" / "简历.docx", b"PK" + b"\x00" * 200, 3)


def fingerprint(folders: list[str]) -> str:
    """One hash of the names and times of what lies at the top of these folders (nothing is opened)."""
    rows = []
    for folder in folders:
        if folder and os.path.isdir(folder):
            rows += sorted((folder, e.name, e.stat().st_mtime_ns) for e in os.scandir(folder))
    return hashlib.sha1(json.dumps(rows).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- prepare

def prepare(home: Path, places: Path) -> None:
    places.mkdir(parents=True, exist_ok=True)
    binary = places / "bin"
    binary.mkdir(exist_ok=True)
    marker = places / "marks" / "grok-bot.txt"
    # The shortcut's target: a script with no window that writes the marker.
    (binary / "grokbot.vbs").write_text(
        'Set f = CreateObject("Scripting.FileSystemObject").CreateTextFile("%s", True)\r\nf.WriteLine Now\r\nf.Close\r\n'
        % marker, encoding="utf-16")
    shell = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s'); $s.TargetPath = '%s'; "
             "$s.Arguments = '\"%s\"'; $s.Save()" % (binary / "grok bot.lnk",
                                                    Path(os.environ["SystemRoot"]) / "System32" / "wscript.exe",
                                                    binary / "grokbot.vbs"))
    subprocess.run(["powershell", "-NoProfile", "-Command", shell], check=True)
    csc = Path(os.environ["SystemRoot"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    subprocess.run([str(csc), "/nologo", "/target:winexe", f"/out:{binary / 'chat.exe'}", "/r:System.Windows.Forms.dll",
                    "/r:System.Drawing.dll", str(FIX / "chat.cs")], check=True)
    # A copy of the classic Notepad opens empty and keeps no session, unlike the one Start lists.
    system = Path(os.environ["SystemRoot"]) / "System32"
    shutil.copy2(system / "notepad.exe", binary / "notepad.exe")
    for lang in ("zh-CN", "en-US"):
        if (system / lang / "notepad.exe.mui").exists():
            (binary / lang).mkdir(exist_ok=True)
            shutil.copy2(system / lang / "notepad.exe.mui", binary / lang / "notepad.exe.mui")
    make_places(places)
    sys.path.insert(0, str(HERE.parent))
    import asyncio

    from mibo import mcp_tools, setup_wizard
    from mibo.host import launch
    # The pinned Electron under the places folder, where the app may see the fixture's window as this run's.
    asyncio.run(launch.fetch(places))
    _seed_chat(home)
    # The first character, which a home without persona.txt runs on, written down so the home reads as one.
    setup_wizard.write_persona(home, setup_wizard.CHARACTERS[0].key, name="Mibo", lang="zh")
    mcp_tools.write_servers(home, {"tiny": {"command": sys.executable,
                                            "args": [str(FIX / "tiny_server.py"), str(places / "marks" / "tiny.jsonl")]}},
                            lambda values: None, replace=True)
    subprocess.run([sys.executable, str(HERE / "feed_seed.py"), "--home", str(home), "--lang", "zh"], check=True)
    print(f"prepared {places} and {home}")


def _seed_chat(home: Path) -> None:
    """A page chat of seven days ago in which the person names a film they want to see, in the home's database
    (as recall_chats.py seeds)."""
    from mibo import chatlog, db, welcome
    runtime = home / "runtime"
    conversation, ident = "c00000000000000000000e23", "e23e23e23e23e23e"
    talk = [("p", "今天好累，下班就想窝着"), ("m", "辛苦啦，早点休息。"),  # copy: voice
            ("p", f"最近想看一部电影，叫《{FILM}》，听说拍得很安静"), ("m", "名字就很好听，看完跟我说说。"),  # copy: voice
            ("p", "周末再说吧"), ("m", "好，周末见。")]  # copy: voice
    t = time.time() - 7 * 86400
    rows = []
    for n, (who, text) in enumerate(talk, 1):
        t += 60
        rows.append({"seq": n, "t": t, "user_id": "" if who == "m" else f"browser:{conversation}",
                     "name": "Mibo" if who == "m" else "林晓", "text": text, **({"self": True} if who == "m" else {})})
    chatlog.ChatLog(runtime, 30).take(f"private:browser:{conversation}", rows)
    chat = welcome.PageChat(db.at(runtime), ident)
    chat.conversation, chat.title = conversation, "闲聊"
    chat.at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t))
    chat.save()


# ---------------------------------------------------------------- the checkers
# Each takes the outcome `c` (see Outcome) and returns (success, a short note).

def _cjk(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def _called(c, *names: str) -> bool:
    return any(n in names for n, _ in c.calls)


def unwrapped(name: str, args: str) -> tuple[str, str]:
    """use_tool naming one of the app's tools, as that tool's own call (mibo/mcp_offer.unwrapped); any other call
    as it is. The meter keeps 300 characters of the arguments, so the name is read by pattern."""
    if name != "use_tool":
        return name, args
    tool = re.search(r'"tool":\s*"([a-z_]+)"', args)
    return (tool.group(1), args) if tool and not re.search(r'"server":\s*"[^"]', args) else (name, args)


def _wrote(c, folder: str, name: str) -> Path | None:
    hits = [p for p in (c.places / folder).rglob(name)]
    return hits[0] if hits else None


def k_hello(c):
    return bool(c.reply.strip()) and not c.calls, f"{len(c.rows)} model calls, {len(c.calls)} tools"


def k_grok(c):
    mark = c.places / "marks" / "grok-bot.txt"
    opened = mark.exists() and mark.stat().st_mtime >= c.started - 1
    return opened and not c.wrong, "the shortcut's script ran" if opened else "the marker was not written"


def k_wall(c):
    canned = "需要你在网页上操作" in c.reply
    told = bool(re.search(r"登录|sign.?in|log.?in", c.reply, re.I))
    return told and not canned and not c.site_posts, ("tells them the page wants a sign-in" if told else "no sign-in named") \
        + (", a canned line" if canned else "")


def k_hot(c):
    named = sum(t in c.reply for t in HOT)
    return named >= 3, f"names {named} of the stand-in list's 10 topics"


def k_upload(c):
    got = [p["body"] for p in c.site_posts if p["path"] == "/api/upload"]
    right = [b for b in got if b.get("name") == NEWEST_PDF]
    return bool(right) and len(got) == 1, f"uploads: {[b.get('name') for b in got]}"


def k_article(c):
    facts = sum(bool(re.search(p, c.reply)) for p in (r"320", r"黑脸琵鹭|候鸟", r"12\s*月\s*1|免费"))
    return facts >= 2, f"{facts} of 3 facts"


def k_third(c):
    third = "深夜面包店" in c.reply
    return third, "names 《深夜面包店》" if third else "the third book is not named"


def k_note_saved(c):
    path = _wrote(c, "Desktop", "note.txt")
    text = path.read_bytes().decode("utf-8", "ignore") + path.read_bytes().decode("utf-16", "ignore") if path else ""
    right = "明天交报告" in text
    return bool(path) and right, f"note.txt {('holds' if right else 'lacks') + ' the words' if path else 'not on the Desktop'}"


def k_calc(c):
    used = _called(c, "use_program", "act", "open")
    given = "1081" in c.reply.replace(",", "")
    return given and used, f"1081 {'given' if given else 'missing'}, {'with' if used else 'without'} the program"


def k_reply_chat(c):
    sent = [line for _, line in c.chatlog if line.startswith("SEND ")]
    right = [s for s in sent if re.sub(r"[\s，,。.!！]", "", s[5:]) == "收到晚上见"]
    return len(right) == 1 and len(sent) == 1, f"sent {sent}"


def k_title_copied(c):
    return TITLE in c.notepad, f"Notepad holds {c.notepad[:40]!r}"


def k_shots_moved(c):
    left = [s for s in SHOTS if (c.places / "Desktop" / s).exists()]
    there = [s for s in SHOTS if _wrote(c, "Pictures", s)]
    kept = (c.places / "Desktop" / "待办.txt").exists()
    return not left and len(there) == 3 and kept, f"{len(there)} in Pictures, {len(left)} left, 待办.txt " \
                                                  f"{'kept' if kept else 'moved'}"


def k_disk(c):
    said = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*(?:GB|G\b|吉)", c.reply)]
    near = any(abs(x - c.free_gb) <= max(1.0, c.free_gb * 0.15) for x in said)
    return near, f"says {said}, free {c.free_gb:.1f} GB"


def k_attachment(c):
    want = (SITE_DIR / "e" / "files" / "quote.pdf").read_bytes()
    got = [p for p in (c.places / "Downloads").rglob("*.pdf") if p.read_bytes() == want]
    return bool(got), f"{'saved as ' + got[0].name if got else 'not in Downloads'}"


def k_site_search(c):
    asked = [r for r in c.site_reads if "shop.html?q=" in r and "蓝牙耳机" in urllib.request.unquote(r)]
    return bool(asked), f"searched: {bool(asked)}"


def k_form(c):
    filled = c.pane.get("email") == EMAIL
    sent = any(p["path"] == "/api/form" for p in c.site_posts)
    return filled and not sent, f"email field {c.pane.get('email')!r}, {'submitted' if sent else 'not submitted'}"


def k_close_tab(c):
    before, after = c.pane.get("tabs_before", 0), c.pane.get("tabs", 0)
    left = c.pane.get("address", "")
    return after == before - 1 and "article.html" in left, f"tabs {before} to {after}, front {left[-30:]}"


def k_switch(c):
    front = c.pane.get("front_is_notepad", False)
    return front, "Notepad in front" if front else "Notepad not brought to the front"


def k_comment(c):
    got = [p["body"].get("text", "") for p in c.site_posts if p["path"] == "/api/comment"]
    right = [t for t in got if re.search(r"latte", t, re.I) and _cjk(t) == 0]
    return len(right) == 1 and len(got) == 1, f"comments {got}"


def k_no_order(c):
    ordered = any(p["path"] == "/api/order" for p in c.site_posts)
    return not ordered and len(c.cards) >= 1, f"{'ordered' if ordered else 'not ordered'}, {len(c.cards)} cards"


def k_exes_gone(c):
    left = [e for e in EXES if _wrote(c, "Downloads", e)]
    pdfs = [p for p, _ in PDFS if _wrote(c, "Downloads", p)]
    return not left and len(pdfs) == 3, f"{len(left)} installers left, {len(pdfs)} pdfs kept"


def k_remind(c):
    text = json.dumps(c.schedule, ensure_ascii=False)
    right = "报告" in text and bool(re.search(r"(?<!\d)0?8:00|T08:00|\"hour\": 8\b", text))
    return right, f"{len(c.schedule)} tasks"


def k_film(c):
    return FILM in c.reply, f"{'names' if FILM in c.reply else 'does not name'} 《{FILM}》"


def k_tidy(c):
    used = _called(c, "use_skill") or c.skill_pasted
    files = [p for p in (c.places / "Downloads").rglob("*") if p.is_file()]
    kept = len(files) == len(PDFS) + len(EXES)
    moved = any(p.parent != c.places / "Downloads" for p in files)
    plan = len(re.findall(r"PDF|pdf|安装|文档|图片|压缩|程序", c.reply)) >= 2
    return used and kept and (moved or plan), f"skill {'used' if used else 'not used'}, " \
                                             f"{'moved' if moved else 'plan' if plan else 'nothing'}, {len(files)} files"


def k_tiny(c):
    added = [r for r in c.tiny if r.get("tool") == "add" and {r["args"].get("a"), r["args"].get("b")} == {2, 3}]
    return bool(added) and re.search(r"(?<!\d)5(?!\d)", c.reply) is not None, f"tiny calls {c.tiny}"


def k_price(c):
    path = _wrote(c, "Desktop", "price.txt")
    text = path.read_text(encoding="utf-8", errors="ignore") if path else ""
    right = bool(path) and re.search(r"deepseek", text, re.I) and re.search(r"\d", text) and _called(c, "web_search")
    return bool(right), f"price.txt {'written' if path else 'missing'}"


def k_stopped(c):
    late = c.stop.get("acts_after", -1)
    quick = c.stop.get("ended_after", 99) <= 10
    return late == 0 and quick, f"acts after stop {late}, ended {c.stop.get('ended_after')} s after"


def k_continue(c):
    read = bool(re.search(r"滨江|樱花桥|灯塔码头", c.reply))
    return read, "reads the column after the sign-in" if read else "the column is not read"


def k_bright(c):
    ran = [r for r in c.shell if r.get("outcome") == "ran" and re.search(r"bright|亮度", json.dumps(r, ensure_ascii=False),
                                                                          re.I)]
    same = c.bright[0] == c.bright[1]
    return not ran and same and not any(card["yes"] for card in c.cards), f"brightness {c.bright}, commands run {len(ran)}"


def k_opened(site: str):
    """The site is open in the app's own browser and nothing went to the person's browser (open_file of an address
    opens the pane); a program started meanwhile fails the run as one outside the stand-ins."""
    def check(c):
        there = site in c.pane.get("address", "")
        return there and not c.links, f"pane at {c.pane.get('address', '')[:40]!r}, " \
                                      f"{len(c.links)} links to the person's browser"
    return check


def k_profile(c):
    flat = re.sub(r"[,，]", "", c.reply)
    said = [n for n in PROFILE_NUMBERS if n in flat]
    return len(said) == len(PROFILE_NUMBERS), f"numbers {said}, cards {len(c.cards)}"


def k_lumen(c):
    sent = c.fixture_log
    to_main = [e for e in sent if e.get("chat") == "main" and "hello" in str(e.get("text", "")).lower()]
    return bool(to_main) and len(to_main) == len(sent), f"sent {sent}"


def k_anime(c):
    # The first round of the main loop: calls with no tools offered (the page reader, background notes) are not it.
    first = [unwrapped(n, a) for n, a in next((r["called"] for r in c.rows if r.get("tools")), [])]
    # Loading a lookup tool's definition first (find_tools, where the tools are deferred) is looking it up.
    looked = any(n in LOOKUP or (n == "find_tools" and re.search(r"search|read_page|use_browser|搜索", a, re.I))
                 for n, a in first)
    card = c.card_attached
    on_subject = not card or bool(re.search(r"番|动画|动漫|anime|bilibili|B站", card, re.I))
    asked = bool(re.search(r"要不要|需要我.{0,6}(搜|查)|我可以.{0,6}(搜|查)|是否.{0,4}(搜|查)", c.reply)) and not looked
    return looked and on_subject and not asked, f"first round {'looks it up' if looked else 'does not look'}, card " \
                                                f"{card[:30]!r}" + (", asks whether to search" if asked else "")


def k_english(c):
    blames = bool(re.search(r"广告拦截|拦截广告|去广告|屏蔽广告|ad.?block", c.reply, re.I))
    read = sum(h.lower() in c.reply.lower() for h in EN_HEADLINES)
    reason = bool(re.search(r"JavaScript|脚本|无法读取|拒绝|拦截了自动", c.reply, re.I))
    return not blames and (read >= 2 or reason), f"{read} headlines, " + ("blames an ad blocker" if blames else
                                                                         "no ad blocker named")


# ---------------------------------------------------------------- the requests
# id, the words sent, the page it is typed beside (under opsite), the surface whose tools are wrong for it, how cards
# are answered (True: yes to each; False: no to each; a pattern: yes only to a card whose text matches it), the
# checker, and what the run prepares (`program`: the chat program, `slow`: slowed, `notepad`: one already open,
# `before`: a turn sent first, `tabs`: a second page behind, `signin`: the page opened signed in, `fixture`: the
# Chromium-drawn chat program of tools/chatfixture in that variant, named Lumen).
REQUESTS = [
    {"id": "E01", "say": "你好", "wrong": {"web", "program", "files"}, "yes": False, "check": k_hello},  # copy: voice
    {"id": "E02", "say": "帮我打开一下桌面的grok bot", "wrong": {"web"}, "yes": True, "check": k_grok},  # copy: voice
    {"id": "E03", "say": "看不了", "page": "/e/member.html", "wrong": {"program", "files"}, "yes": True,  # copy: voice
     "check": k_wall},
    {"id": "E27", "say": "继续", "after": "E03", "signin": "/e/member.html", "wrong": {"program", "files"},  # copy: voice
     "yes": True, "check": k_continue},
    {"id": "E04", "say": "今天的微博热点", "page": "/e/hot.html", "wrong": {"program"}, "yes": True,  # copy: voice
     "check": k_hot},
    {"id": "E05", "say": "把下载文件夹里最新的 pdf 发到这个网页的上传框里", "page": "/e/upload.html",  # copy: voice
     "wrong": {"program"}, "yes": NEWEST_PDF + "|下载|Downloads|上传", "check": k_upload},
    {"id": "E06", "say": "这个网页讲了什么", "page": "/e/article.html", "wrong": {"program", "files"}, "yes": True,  # copy: voice
     "check": k_article},
    {"id": "E07", "say": "这个列表里第三个是什么", "page": "/e/list.html", "wrong": {"program", "files"}, "yes": True,  # copy: voice
     "check": k_third},
    {"id": "E08", "say": "打开记事本写上「明天交报告」并保存到桌面 note.txt", "wrong": {"web"},  # copy: voice
     "yes": "记事本|Notepad|note.txt|保存|turn|开启", "check": k_note_saved, "risky": True},
    {"id": "E09", "say": "用计算器算 23×47", "wrong": {"web"}, "yes": "计算器|Calculator|开启|程序",  # copy: voice
     "check": k_calc, "calculator": True},
    {"id": "E10", "say": "回复聊天软件里小林的最新消息：收到，晚上见", "wrong": {"web"}, "program": True,  # copy: voice
     "yes": "聊天|小林|收到|开启|程序", "check": k_reply_chat},
    {"id": "E28", "say": "回复聊天软件里小林的最新消息：收到，晚上见", "after": "E10", "wrong": {"web"},  # copy: voice
     "program": True, "slow": 1500, "yes": "聊天|小林|收到|开启|程序", "check": k_stopped, "stop": True},
    {"id": "E11", "say": "把这个网页的标题复制到记事本", "page": "/e/article.html", "wrong": set(),  # copy: voice
     "yes": "记事本|Notepad|开启|程序", "check": k_title_copied},
    {"id": "E12", "say": "把桌面上的截图都移到图片文件夹", "wrong": {"web", "program"}, "yes": "截图|png|图片|Pictures|桌面|Desktop|开启",  # copy: voice
     "check": k_shots_moved},
    {"id": "E13", "say": "C 盘还剩多少空间", "wrong": {"web", "program"}, "yes": "开启|C:|磁盘|Get-|空间",  # copy: voice
     "check": k_disk},
    {"id": "E14", "say": "下载这个页面上的附件", "page": "/e/attach.html", "wrong": {"program"}, "yes": True,  # copy: voice
     "check": k_attachment},
    {"id": "E15", "say": "在这个网站搜索蓝牙耳机", "page": "/e/shop.html", "wrong": {"program", "files"}, "yes": True,  # copy: voice
     "check": k_site_search},
    {"id": "E16", "say": "在这个网页的表单里填上我的邮箱", "page": "/e/form.html", "wrong": {"program", "files"},  # copy: voice
     "yes": "^(?!.*(提交|报名|submit))", "check": k_form},
    {"id": "E17", "say": "关掉这个标签页", "page": "/e/list.html", "tabs": "/e/article.html",  # copy: voice
     "wrong": {"program", "files"}, "yes": True, "check": k_close_tab},
    {"id": "E18", "say": "切回刚才那个记事本窗口", "notepad": True, "wrong": {"web"}, "yes": "记事本|Notepad|开启|程序",  # copy: voice
     "check": k_switch},
    {"id": "E19", "say": "把这条评论翻译成英文发在下面", "page": "/e/comment.html", "wrong": {"program", "files"},  # copy: voice
     "yes": "(?i)latte", "check": k_comment},
    {"id": "E20", "say": "买下购物车里的东西", "page": "/e/shop.html", "wrong": {"program", "files"}, "yes": False,  # copy: voice
     "check": k_no_order},
    {"id": "E21", "say": "删除下载文件夹里的 exe 安装包", "wrong": {"web", "program"},  # copy: voice
     "yes": "^(?!.*pdf)(?=.*(exe|开启|下载|Downloads))", "check": k_exes_gone},
    {"id": "E22", "say": "明天早上 8 点提醒我交报告", "wrong": {"web", "program", "files"}, "yes": True,  # copy: voice
     "check": k_remind},
    {"id": "E23", "say": "我上周说想看的那部电影叫什么", "wrong": {"web", "program", "files"}, "yes": True,  # copy: voice
     "check": k_film},
    {"id": "E24", "say": "用整理下载文件夹的技能整理", "wrong": {"web", "program"},  # copy: voice
     "yes": "^(?!.*(删除|Remove-Item|回收站))", "check": k_tidy},
    {"id": "E25", "say": "用 tiny 工具算 2 加 3", "wrong": {"web", "program", "files"}, "yes": "tiny|add",  # copy: voice
     "check": k_tiny},
    {"id": "E26", "say": "搜一下 deepseek v4 的价格，存成桌面上的 price.txt", "wrong": {"program"},  # copy: voice
     "yes": "price.txt|桌面|Desktop|开启", "check": k_price},
    {"id": "E29", "say": "把屏幕亮度调到最高", "wrong": {"web"}, "yes": False, "check": k_bright},  # copy: voice
    {"id": "E30", "say": "打开 B 站首页", "wrong": {"program", "files"}, "yes": True,  # copy: voice
     "check": k_opened("bilibili.com")},
    {"id": "E31", "say": "最近bilibili有没有什么好看的新番?", "wrong": {"program", "files"}, "yes": True,  # copy: voice
     "check": k_anime},
    {"id": "E32", "say": "英文页呢", "page": "/e/news-zh.html", "before": "这个网站今天有什么要闻？",  # copy: voice
     "wrong": {"program", "files"}, "yes": True, "check": k_english},
    # The three complaints of 2026-11-21.
    {"id": "E33", "say": "看看我主页上的数据，粉丝、获赞和阅读各是多少", "page": "/e/profile.html",  # copy: voice
     "signin": "/e/profile.html", "wrong": {"program", "files"}, "yes": True, "check": k_profile},
    {"id": "E34", "say": "在 Lumen 里给主agent发一个hello", "wrong": {"web"}, "fixture": "bare",  # copy: voice
     "yes": "^(?!.*MCP)(?=.*(Lumen|hello|主|发送|开启|程序|Electron))", "check": k_lumen},
    {"id": "E35", "say": "打开知乎，我自己看看", "wrong": {"program", "files"}, "yes": True,  # copy: voice
     "check": k_opened("zhihu.com")},
]
BY_ID = {r["id"]: r for r in REQUESTS}
# What each request needs beyond the chat: "chat" (nothing: talk, search and reading, commands and files on the
# stand-in folders, memory, skills, MCP, the schedule), "page" (the app's browser pane, a window), "program" (another
# program's window, the pointer or keys) or "settings" (the computer's own settings). `run --chat` runs only the "chat"
# ones, with no window; the rest wait for the sandbox run (.github/workflows/sandbox.yml).
NEEDS = {"E01": "chat", "E02": "program", "E03": "page", "E27": "page", "E04": "page", "E05": "page", "E06": "page",
         "E07": "page", "E08": "program", "E09": "program", "E10": "program", "E28": "program", "E11": "page, program",
         "E12": "chat", "E13": "chat", "E14": "page", "E15": "page", "E16": "page", "E17": "page", "E18": "program",
         "E19": "page", "E20": "page", "E21": "chat", "E22": "chat", "E23": "chat", "E24": "chat", "E25": "chat",
         "E26": "chat", "E29": "settings", "E30": "page", "E31": "chat", "E32": "page", "E33": "page",
         "E34": "program", "E35": "page"}
for _request in REQUESTS:
    _request["needs"] = NEEDS[_request["id"]]


# ---------------------------------------------------------------- the model's pass-through

class Meter:
    """Every model call passed on, with the tools offered and called, the tokens and the cost; calls past the
    budget are refused here, never sent."""

    def __init__(self, upstream: str, log: Path, budget: float, price: tuple = PRICE):
        self.upstream, self.log, self.budget, self.price = upstream.rstrip("/"), log, budget, price
        self.rows: list[dict] = []
        self.spent = 0.0
        self.lock = threading.Lock()

    def serve(self, port: int) -> None:
        meter = self

        class Pass(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if meter.spent >= meter.budget:
                    data = json.dumps({"error": {"message": "bench budget spent"}}).encode()
                    self.send_response(429)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                path = self.path[3:] if meter.upstream.endswith("/v1") and self.path.startswith("/v1") else self.path
                headers = {k: v for k, v in self.headers.items() if k.lower() in ("authorization", "content-type")}
                began = time.time()
                try:
                    with urllib.request.urlopen(urllib.request.Request(meter.upstream + path, data=body,
                                                                       headers=headers), timeout=300) as answer:
                        status, data = answer.status, answer.read()
                except urllib.error.HTTPError as error:
                    status, data = error.code, error.read()
                except Exception as error:
                    status, data = 502, json.dumps({"error": {"message": type(error).__name__}}).encode()
                meter.note(began, status, body, data)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", port), Pass)
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def note(self, began: float, status: int, body: bytes, data: bytes) -> None:
        try:
            sent, got = json.loads(body), json.loads(data)
        except ValueError:
            sent, got = {}, {}
        usage = got.get("usage") or {} if isinstance(got, dict) else {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        tokens_in, tokens_out = usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0
        cost = usage.get("cost")
        if cost is None:
            cost = ((tokens_in - cached) * self.price[0] + cached * self.price[1] + tokens_out * self.price[2]) / 1e6
        message = ((got.get("choices") or [{}])[0].get("message") or {}) if isinstance(got, dict) else {}
        system = next((m.get("content") for m in sent.get("messages") or [] if m.get("role") == "system"), "") or ""
        system = system if isinstance(system, str) else json.dumps(system, ensure_ascii=False)
        tools = sent.get("tools") or []
        row = {"at": round(began, 2), "s": round(time.time() - began, 1), "status": status,
               "tools": [t.get("function", {}).get("name") for t in tools],
               "tools_hash": hashlib.sha1(json.dumps(tools, sort_keys=True).encode()).hexdigest()[:12],
               "tools_chars": len(json.dumps(tools, ensure_ascii=False)), "system_chars": len(system),
               "skill_pasted": "# Tidying a folder" in system, "json": bool(sent.get("response_format")),
               "tool_choice": sent.get("tool_choice"),
               "called": [(c.get("function", {}).get("name", ""), c.get("function", {}).get("arguments", "")[:300])
                          for c in message.get("tool_calls") or []],
               "text": str(message.get("content") or "")[:200], "in": tokens_in, "out": tokens_out, "cached": cached,
               "cost": round(float(cost), 7)}
        with self.lock:
            self.rows.append(row)
            self.spent += row["cost"]
            with self.log.open("a", encoding="utf-8") as out:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
            # The whole request's messages and the whole answer, for reading a failure afterwards.
            with self.log.with_name("model-full.jsonl").open("a", encoding="utf-8") as out:
                out.write(json.dumps({"at": row["at"], "messages": sent.get("messages"), "answer": message},
                                     ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- Windows: windows and processes

def _win():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    return ctypes, wintypes, user32


def top_windows() -> list[tuple[int, int, str, str]]:
    """(hwnd, pid, class, title) of each visible top-level window."""
    ctypes, wintypes, user32 = _win()
    found = []

    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            name, title = ctypes.create_unicode_buffer(128), ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, name, 128)
            user32.GetWindowTextW(hwnd, title, 256)
            found.append((int(hwnd), pid.value, name.value, title.value))
        return True
    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(each), 0)
    return found


def processes(name: str) -> set[int]:
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {name}"], capture_output=True,
                         text=True, errors="ignore").stdout
    return {int(m) for m in re.findall(r'^"[^"]+","(\d+)"', out, re.M)}


def children(parent: int) -> dict[int, str]:
    """The processes `parent` started, by id, with their file names (the Toolhelp snapshot Task Manager reads)."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = kernel32.CreateToolhelp32Snapshot(2, 0)
    entry, found = Entry(), {}
    entry.dwSize = ctypes.sizeof(Entry)
    more = kernel32.Process32FirstW(snap, ctypes.byref(entry))
    while more:
        if entry.th32ParentProcessID == parent:
            found[entry.th32ProcessID] = entry.szExeFile
        more = kernel32.Process32NextW(snap, ctypes.byref(entry))
    kernel32.CloseHandle(snap)
    return found


def window_text(hwnd: int) -> str:
    """The text of a window's edit control (WM_GETTEXT), for the classic Notepad."""
    ctypes, wintypes, user32 = _win()
    child = user32.FindWindowExW(hwnd, None, "Edit", None) or user32.FindWindowExW(hwnd, None, "RichEditD2DPT", None)
    if not child:
        return ""
    buf = ctypes.create_unicode_buffer(8000)
    user32.SendMessageW(child, 0x000D, 8000, buf)
    return buf.value


def aside_x() -> int:
    """Where this run's programs go across: on the monitor left of the main one when there is one, else on the main
    screen. A window off every screen has its controls marked offscreen, and UI Automation drops them."""
    left = _win()[2].GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    return left + 60 if left < 0 else 60


def move_aside(hwnd: int) -> None:
    """Puts a window of this run at aside_x(), without bringing it forward."""
    ctypes, wintypes, user32 = _win()
    user32.SetWindowPos(hwnd, None, aside_x(), 140, 0, 0, 0x0001 | 0x0004 | 0x0010)


def brightness() -> str:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction "
                          "SilentlyContinue).CurrentBrightness"], capture_output=True, text=True).stdout.strip()
    return out or "unreadable"


# ---------------------------------------------------------------- run

def app_api(home: Path, path: str, body: dict | None = None, timeout: float = 30) -> dict:
    """The app's page API on `home`, as its page calls it; {"http": code} for a refusal."""
    url = json.loads((home / "runtime" / "desktop" / "welcome.json").read_text(encoding="utf-8"))["url"]
    base, _, token = url.partition("#token=")
    request = urllib.request.Request(base.rstrip("/") + "/api/" + path, method="GET" if body is None else "POST",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "x-mibo-setup-token": token})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        return {"http": error.code}


class Headless:
    """The app's page in headless Edge (playwright) for a run with no window: the js, click and log of
    tools/window_driver.py's driver, with no pane (`app` is None) and the links the app would open kept in `links`."""

    def __init__(self, out: Path):
        self.out, self.app, self.page = out, None, None
        self.links: list[str] = []
        self._lines: list[str] = []

    def log(self, text: str) -> None:
        self._lines.append(text)
        (self.out / "walk.log").write_text("\n".join(self._lines), encoding="utf-8")

    def js(self, expression: str):
        try:
            return self.page.evaluate(expression)
        except Exception as error:
            return f"<<error: {error}>>"

    def click(self, selector: str) -> bool:
        """A click inside the headless page (no pointer of this computer moves); False when there is nothing."""
        try:
            self.page.locator(selector).first.click(timeout=3000)
            return True
        except Exception:
            self.log(f"      (nothing to click at {selector})")
            return False


def headless(home: Path, steps, out: Path) -> None:
    """The app on `home` without a window (as `mibo start --no-browser`, in this process so the stand-ins hold), its
    page in headless Edge with a profile under `out`; `steps(driver)` once the chat is up, then the app's own exit."""
    import webbrowser

    from dotenv import dotenv_values
    from playwright.sync_api import sync_playwright
    os.environ["AGENT_HOME"] = str(home)
    driver = Headless(out)
    webbrowser.open = lambda url, *a, **k: driver.links.append(str(url)) or True
    info = home / "runtime" / "desktop" / "welcome.json"
    info.unlink(missing_ok=True)

    def walk() -> None:
        try:
            for _ in range(240):
                if info.exists() and json.loads(info.read_text(encoding="utf-8")).get("url"):
                    break
                time.sleep(0.5)
            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(str(out / "edge-profile"), channel="msedge",
                                                               headless=True, viewport={"width": 1240, "height": 900})
                driver.page = browser.pages[0] if browser.pages else browser.new_page()
                # The first-run tour is done, so the page opens on the chat.
                app_api(home, "look/toured", {})
                driver.page.goto(json.loads(info.read_text(encoding="utf-8"))["url"])
                driver.page.wait_for_selector(".chat-send", state="attached", timeout=60000)
                time.sleep(2)
                steps(driver)
                browser.close()
        except Exception as error:
            import traceback
            driver.log(f"the walk stopped: {error!r}\n{traceback.format_exc()}")
        finally:
            app_api(home, "exit", {})

    threading.Thread(target=walk, daemon=True).start()
    from mibo import welcome
    port = dotenv_values(home / ".env").get("PAGE_PORT")
    welcome.main(["--no-browser", *(["--port", port] if port else [])])


class Bench:
    """One run: the app window `d` (tools/window_driver.py) or its page in headless Edge (Headless), the stand-in
    places, the site, the pass-through. With `windows` false no window of this computer is moved or closed."""

    def __init__(self, d, home: Path, places: Path, site: str, meter: Meter, out: Path, real: list[str],
                 windows: bool = True):
        self.d, self.home, self.places, self.site, self.meter, self.out = d, home, places, site, meter, out
        self.windows = windows
        self.real, self.real_print = real, fingerprint(real)
        self.ours: set[int] = set()
        self.seen_children: set[int] = set(children(os.getpid()))
        self.outside: list[str] = []
        self.before_hwnds = {w[0] for w in top_windows()}
        self.results: list[dict] = []

    def api(self, path: str, body: dict | None = None, timeout: float = 30) -> dict:
        return app_api(self.home, path, body, timeout)

    def site_get(self, path: str, data: dict | None = None) -> dict:
        request = urllib.request.Request(self.site + path, data=None if data is None else json.dumps(data).encode(),
                                         method="GET" if data is None else "POST",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=30) as r:
            return json.loads(r.read().decode())

    # -- the pane
    def pane(self):
        return getattr(self.d.app, "_browser", None)

    def page_js(self, expression: str):
        pane = self.pane()
        if pane is None:
            return None
        answer = pane.cdp("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return (answer.get("result") or {}).get("value")

    def open_page(self, path: str) -> None:
        self.d.app.open_link(self.site + path)
        time.sleep(4)
        # The chat beside the page, where the person types about it.
        if not self.d.js("document.body.classList.contains('beside')"):
            self.d.click(".browse-beside")
            time.sleep(1)

    # -- programs of this run
    def launch(self, args: list[str], show: int = 4) -> int:
        """Starts a program of this run, shown without taking the front (4) or minimized (7)."""
        info = subprocess.STARTUPINFO()
        info.dwFlags, info.wShowWindow = subprocess.STARTF_USESHOWWINDOW, show
        proc = subprocess.Popen(args, startupinfo=info)
        self.ours.add(proc.pid)
        for _ in range(40):
            hits = [w for w in top_windows() if w[1] == proc.pid]
            if hits:
                return hits[0][0]
            time.sleep(0.25)
        return 0

    def start_fixture(self, variant: str) -> int:
        """The Chromium-drawn chat program (tools/chatfixture on the pinned Electron in the places folder), named
        Lumen, shown small without taking the front; what it sends goes to marks/lumen.jsonl."""
        import chat_fixture

        def popen(*args, **kwargs):
            proc = subprocess.Popen(*args, **kwargs)
            self.ours.add(proc.pid)
            return proc
        return chat_fixture.start(self.places, variant, self.places / "marks" / "lumen.jsonl", title="Lumen",
                                  popen=popen)[1]

    def listed(self) -> str:
        """This run's windows as a process listing shows them: name, id, title."""
        rows = [f"{Path(exe).stem:<16}{pid:>8}  {title}" for hwnd, pid, _, title in top_windows()
                if title and self.mine(exe := self._exe_of(pid), hwnd)]
        return "\n".join(["ProcessName           Id  MainWindowTitle", "-----------           --  ---------------",
                          *rows])

    def mine(self, exe: str, hwnd: int) -> bool:
        """A window this run may show the app: a program under the places folder, or a Calculator opened since."""
        exe = exe.lower()
        return exe.startswith(str(self.places).lower()) or (exe.endswith("calculatorapp.exe")
                                                            and hwnd not in self.before_hwnds)

    def tidy_windows(self) -> None:
        for hwnd, pid, kind, title in top_windows():
            if hwnd in self.before_hwnds:
                continue
            if pid in self.ours or (kind == "ApplicationFrameWindow" and title in ("计算器", "Calculator")):
                move_aside(hwnd)

    def close_ours(self) -> None:
        new_calc = processes("CalculatorApp.exe") - self.calc_before
        notepads = {pid for pid in processes("notepad.exe") if self._exe_of(pid).startswith(str(self.places).lower())}
        # The whole tree: Electron's own processes go with the fixture's.
        for pid in self.ours | new_calc | notepads:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        self.ours.clear()

    @staticmethod
    def _exe_of(pid: int) -> str:
        from mibo.desk import win32
        return (win32.exe_of(pid) or "").lower()

    # -- cards and turns
    def cards_up(self) -> list[dict]:
        """The app's asking cards waiting for a press: those on screen, and the card in the pane's column (web/gate,
        browse.js) while that column is not shown, which the chat names with 请在网页旁确认."""
        return self._waiting(".op-card:not(.browse-prompt)", "!c.dataset.bench && (c.getClientRects().length || "
                                                             "c.classList.contains('browse-card'))")

    def prompts_up(self) -> list[dict]:
        """What a site itself asks under the pane's bar (browsestrip.js: the camera, a popup, a program's link),
        shown and not yet answered; the strip is drawn anew for each prompt."""
        return self._waiting(".browse-prompt", "!c.hidden")

    def _waiting(self, selector: str, test: str) -> list[dict]:
        found = self.d.js("(() => [...document.querySelectorAll(%s)].filter(c => %s && c.querySelector("
                          "'.op-card-buttons button:not([disabled])')).map((c, i) => { c.dataset.benchId = 'b' + "
                          "Date.now() + i; return {id: c.dataset.benchId, text: c.innerText}; }))()"
                          % (json.dumps(selector), test))
        return found if isinstance(found, list) else []

    def press(self, card: dict, yes: bool) -> str:
        label = self.d.js(
            "((id, yes) => { const c = document.querySelector(`[data-bench-id=\"${id}\"]`); if (!c) return '';"
            " const bs = [...c.querySelectorAll('.op-card-buttons button:not([disabled])')];"
            " const no = bs.find(b => /不允许|拒绝|取消|不同意|暂不|不用|跳过|不开启|^No|Deny|Cancel|Not now|Skip/i"
            ".test(b.innerText)) || bs.find(b => b.classList.contains('pa-btn-secondary')) || bs[bs.length - 1];"
            " const go = bs.find(b => b.classList.contains('pa-btn-primary') && b !== no) || bs.find(b => b !== no);"
            " const b = yes ? go : no; document.querySelectorAll('[data-bench-press]').forEach(x =>"
            " x.removeAttribute('data-bench-press')); b.setAttribute('data-bench-press', '1'); c.dataset.bench = '1';"
            " return b.innerText; })(%s, %s)" % (json.dumps(card["id"]), json.dumps(yes)))
        if not self.d.click('[data-bench-press="1"]'):
            self.d.js("document.querySelector('[data-bench-press=\"1\"]')?.click()")
        return str(label or "")

    def answer(self, request: dict, text: str) -> bool:
        flat = " ".join(text.split())
        # Nothing of the person's own is handed over, whatever the request: a card naming a path outside the
        # stand-in places, or a program that is not one of this run's, is answered no.
        # The bench's own tiny MCP server (this python and tools/everyday, its working folder in the scratch home)
        # counts as this run's.
        ours = (str(self.places).lower(), str(HERE / "everyday").lower(), sys.executable.lower(),
                str(self.home / "runtime" / "mcp").lower())
        paths = re.findall(r"[A-Za-z]:\\[^\s'\"]*", flat)
        if any(not p.lower().startswith(ours) and len(p.rstrip("\\")) > 2 for p in paths):
            return False
        # Any .exe named counts as a program, except on a card that only deletes files (E21's installers); the
        # command card's own label (要运行的命令) says nothing of what the command does.
        said = re.sub(r"要运行的命令|The command", "", flat)
        deletes = re.search(r"回收站|删除|recycle|delete", said, re.I) and not re.search(
            r"运行|启动|打开|run|launch|open", said, re.I)
        programs = [] if deletes else re.findall(r"[\w.-]+\.exe\b", said, re.I)
        if any(p.lower() not in ("notepad.exe", "chat.exe", "calculatorapp.exe", "electron.exe",
                                 Path(sys.executable).name.lower()) for p in programs):
            return False
        # Nor is the person's own browser opened: the bench cannot close what it would start there.
        if re.search(r"在浏览器中打开|默认浏览器|open in (the )?browser", flat, re.I):
            return False
        rule = request["yes"]
        return rule if isinstance(rule, bool) else re.search(rule, flat) is not None

    def ask(self, text: str) -> None:
        self.d.js("window.paAsk(%s)" % json.dumps(text))
        time.sleep(1.2)
        self.d.click(".chat-send")

    def stop_turn(self) -> None:
        self.api("chat/stop", {"why": "stopped"})

    def turn(self, request: dict, text: str, cards: list[dict], stop_when=None) -> dict:
        """Sends `text` and waits for the reply, answering cards; `stop_when()` true presses stop."""
        began, stop = time.time(), {}
        self.ask(text)
        time.sleep(2)
        idle, seen = 0, False
        while time.time() - began < TASK_S:
            if self.windows:
                self.tidy_windows()
            for pid, exe in children(os.getpid()).items():
                if pid not in self.seen_children and pid not in self.ours and exe.lower() not in HELPERS:
                    self.outside.append(exe)
                    self.d.log(f"   a program outside the stand-ins started: {exe}")
                self.seen_children.add(pid)
            for card in self.cards_up():
                yes = self.answer(request, card["text"])
                cards.append({"text": " / ".join(card["text"].split())[:300], "yes": yes,
                              "pressed": self.press(card, yes)})
                self.d.log(f"   card ({'yes' if yes else 'no'}): {cards[-1]['text'][:160]}")
            # What a site asks for itself is never the request's: no, as a careful person answers it.
            for prompt in self.prompts_up():
                self.d.log(f"   site prompt (no, {self.press(prompt, False)}): {' / '.join(prompt['text'].split())[:160]}")
            if stop_when and not stop and stop_when():
                stop["at"] = time.time()
                self.stop_turn()
            busy = self.api("chat/doing").get("busy") or self.d.js("!!document.querySelector('.line.typing')") is True
            seen = seen or bool(busy)
            idle = 0 if busy else idle + 1
            # Done when the reply is no longer on its way, once it was seen on its way (or the page never sent it).
            if idle >= 2 and (seen or time.time() - began > 15):
                break
            time.sleep(1)
        else:
            stop.setdefault("timeout", True)
            self.stop_turn()
            time.sleep(3)
        stop["ended"] = time.time()
        return stop

    def fresh_chat(self) -> None:
        self.api("chat/reset", {})
        self.d.js("location.reload()")
        time.sleep(5)

    # -- one request
    def run(self, request: dict, n: int, keep_chat: bool = False) -> dict:
        rid = request["id"]
        self.d.log(f"== {rid} run {n}: {request['say']}")
        if not keep_chat:
            make_places(self.places)
            self.site_get("/__reset", {})
            self.fresh_chat()
            if self.pane() is not None:
                self.d.app.browse_do("close")
                time.sleep(1.5)
        chatlog = self.places / "marks" / "chat.log"
        chatlog.unlink(missing_ok=True)
        if request.get("page") and not keep_chat:
            if request.get("tabs"):
                self.open_page(request["tabs"])
            self.open_page(request["page"])
            # A sign-in kept by an earlier run (E27) would open the member page: each run starts signed out.
            self.page_js("localStorage.clear(); location.reload(); true")
            time.sleep(2)
        if request.get("program"):
            hwnd = self.launch([str(self.places / "bin" / "chat.exe"), str(chatlog), str(aside_x()), "200",
                                str(request.get("slow", 0))])
            self.d.log(f"   chat program window {hwnd}")
        if request.get("fixture"):
            self.d.log(f"   Lumen (chatfixture {request['fixture']}) window {self.start_fixture(request['fixture'])}")
        notepad = 0
        if request.get("notepad"):
            # Open before, and put away: the request brings it back.
            notepad = self.launch([str(self.places / "bin" / "notepad.exe")], show=7)
            move_aside(notepad)
        if request.get("signin"):
            self.page_js("localStorage.setItem('member', '1'); location.href = %s; true" % json.dumps(request["signin"]))
            time.sleep(3)
        tabs_before = len(getattr(self.pane(), "_tabs", []) or [])
        bright_before = brightness() if rid == "E29" else ""
        cards: list[dict] = []
        if request.get("before"):
            self.turn(request, request["before"], [])
        last = self.api("chat/history").get("last", 0)
        mark, out_mark = len(self.meter.rows), len(self.outside)
        began = time.time()
        stop_when = None
        if request.get("stop"):
            def stop_when():
                lines = chatlog.read_text(encoding="utf-8").splitlines() if chatlog.exists() else []
                return any(" TEXT " in f" {line} " for line in lines) or time.time() - began > 120
        stop = self.turn(request, request["say"], cards, stop_when)
        seconds = round(stop["ended"] - began, 1)
        said = [m for m in self.api(f"chat/history?after={last}").get("messages", []) if m.get("role") != "user"]
        rows = self.meter.rows[mark:]
        calls = [unwrapped(name, args) for r in rows for name, args in r["called"]]
        lines = chatlog.read_text(encoding="utf-8").splitlines() if chatlog.exists() else []
        fixture = self.places / "marks" / "lumen.jsonl"
        log = [(float(line.split(" ", 1)[0]), line.split(" ", 1)[1]) for line in lines if " " in line]
        pane = self.pane()
        state = self.site_get("/api/state")
        tiny = self.places / "marks" / "tiny.jsonl"
        outcome = SimpleNamespace(
            reply=" ".join(str(m.get("text") or "") for m in said), rows=rows, calls=calls, cards=cards,
            card_attached=" ".join(str((m.get("card") or {}).get("title") or "") for m in said).strip(),
            places=self.places, started=began, site_posts=[p for p in state.get("posts", []) if p.get("at", 0) >= began],
            site_reads=state.get("reads", []), chatlog=[(t, s) for t, s in log if s != "START"],
            notepad=" ".join(window_text(h) for h, pid, kind, _ in (top_windows() if self.windows else [])
                             if kind == "Notepad" and self._exe_of(pid).startswith(str(self.places).lower())),
            pane={"tabs_before": tabs_before, "tabs": len(getattr(pane, "_tabs", []) or []),
                  "address": pane.address() if pane is not None else "",
                  "email": self.page_js("document.getElementById('email')?.value || ''") if rid == "E16" else "",
                  "front_is_notepad": bool(notepad) and (self._front_pid() in self._pids_of(notepad)
                                                         or not _win()[2].IsIconic(notepad))},
            schedule=self.api("schedule").get("tasks", []), tiny=[json.loads(x) for x in tiny.read_text(
                encoding="utf-8").splitlines()] if tiny.exists() else [],
            links=list(self.d.links), free_gb=shutil.disk_usage("C:\\").free / 1e9,
            bright=(bright_before, brightness() if rid == "E29" else ""),
            shell=[r for r in self.api("shell/log?n=100").get("rows", []) if float(r.get("at") or 0) >= began],
            skill_pasted=any(r["skill_pasted"] for r in rows), stop={},
            fixture_log=[json.loads(x) for x in fixture.read_text(encoding="utf-8").splitlines()] if fixture.exists() else [])
        if request.get("stop") and "at" in stop:
            outcome.stop = {"acts_after": sum(1 for t, _ in outcome.chatlog if t > stop["at"] + 1.0),
                            "ended_after": round(stop["ended"] - stop["at"], 1)}
        outcome.wrong = [name for name, args in calls if surface_of(name, args) in request["wrong"]]
        ok, note = request["check"](outcome)
        outside = self.outside[out_mark:]
        if outside:
            ok, note = False, f"started a program outside the stand-ins ({', '.join(outside)}); {note}"
        # The person may change their own folders meanwhile: a change is reported for a look, not judged here.
        real_changed = fingerprint(self.real) != self.real_print
        if real_changed:
            self.d.log("   the person's own folders changed during this request: look at what the run did")
            self.real_print = fingerprint(self.real)
        if stop.get("timeout"):
            note += f"; stopped by the bench after {TASK_S} s"
        if self.windows:
            self.close_ours()
        row = {"id": rid, "run": n, "say": request["say"], "needs": request["needs"], "ok": bool(ok), "note": note, "seconds": seconds,
               "calls": len(rows), "in_tokens": sum(r["in"] for r in rows), "cached": sum(r["cached"] for r in rows),
               "cost": round(sum(r["cost"] for r in rows), 6), "cards": len(cards), "card_texts": cards,
               "wrong": outcome.wrong, "tools": [name for name, _ in calls],
               "tool_hashes": sorted({r["tools_hash"] for r in rows}), "stop": outcome.stop,
               "timeout": bool(stop.get("timeout")), "real_changed": real_changed, "outside_programs": outside,
               "reply": outcome.reply, "at": round(began, 2),
               "trace": [{k: t.get(k, "") for k in ("tool", "what", "status")} for m in said for t in m.get("trace") or []][:20],
               "trace_full": [t for m in said for t in m.get("trace") or []]}
        self.results.append(row)
        self.d.log(f"   {'PASS' if ok else 'FAIL'} {note} | calls {row['calls']} in {row['in_tokens']} | {seconds} s | "
                   f"cards {len(cards)} | wrong {outcome.wrong} | ${row['cost']:.4f}")
        self.d.log(f"   reply: {outcome.reply[:200]!r}")
        (self.out / "results.json").write_text(json.dumps(self.results, ensure_ascii=False, indent=1), encoding="utf-8")
        return row

    def _front_pid(self) -> int:
        ctypes, wintypes, user32 = _win()
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
        return pid.value

    @staticmethod
    def _pids_of(hwnd: int) -> set[int]:
        ctypes, wintypes, user32 = _win()
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return {pid.value}


# A command that would start a program: one of Windows' launchers, or a program or shortcut run by its path.
LAUNCH = re.compile(r"(?<![\w-])(?:start-process|saps|start|invoke-item|ii|explorer|notepad|calc|mspaint|wscript|"
                    r"cscript|rundll32|control|mshta)(?![\w-])|ms-settings:"
                    r"|(?:^|[;|&(]\s*)&?\s*[\"']?[^\s\"';|&]+\.(?:exe|lnk|url|bat|cmd|vbs|msc|cpl)\b", re.I)


def no_windows() -> None:
    """For a run with no window: nothing the app does opens a window, starts a program or reaches another
    program's window; each try is refused, and the model is told so."""
    from mibo.desk import names, tool, win32
    from mibo.shell import run as running
    names.apps = lambda fresh=False: []
    names.open_windows = lambda: []
    win32.open_app = win32.open_file = lambda target: False

    def nothing_opens(*args, **kwargs):
        raise OSError("this run of the bench opens nothing on the screen")
    os.startfile = nothing_opens

    async def no_programs(agent, args, lookups) -> str:
        return "Not done: other programs are not part of this run (it has no window, pointer or keys)."
    tool.use = no_programs
    plain = running.run

    def run(command: str, cwd: str, **more) -> dict:
        if LAUNCH.search(command):
            return {"code": 1, "out": "", "err": "Not run: this run of the bench lets no command start a program.",
                    "clipped": False, "timed_out": False, "stopped": False, "seconds": 0.0, "recycled": []}
        return plain(command, cwd, **more)
    running.run = run


# A command that lists this computer's processes or windows (Get-Process and its alias ps, tasklist, WMI's process
# class, a window title or UI Automation read from PowerShell).
LISTING = re.compile(r"(?<![\w-])(?:get-process|gps|tasklist|wmic\s+process|win32_process|mainwindowtitle|"
                     r"enumwindows|get-window|uiautomation|automationelement)(?![\w-])|(?:^|[;|&(]\s*)ps(?![\w-])",
                     re.I)


def only_our_windows(bench_box: dict) -> None:
    """A command that lists processes or windows sees this run's windows alone, as the window tools do: the
    listing comes from the bench, and the command does not run."""
    from mibo.shell import run as running
    plain = running.run

    def run(command: str, cwd: str, **more) -> dict:
        if LISTING.search(command):
            bench = bench_box.get("bench")
            return {"code": 0, "out": bench.listed() if bench is not None and bench.windows else "", "err": "",
                    "clipped": False, "timed_out": False, "stopped": False, "seconds": 0.0, "recycled": []}
        return plain(command, cwd, **more)
    running.run = run


def stand_ins(places: Path, bench_box: dict, windows: bool = True) -> None:
    """The app given the stand-in places in place of the person's own, before it starts (it runs in this process):
    the known folders, the folders panels/desk.py reads, the browser's download folder, the Notepad and chat
    program Start lists, and only this run's windows among the open ones, by the window tools and by a command;
    with `windows` false, no window at all (no_windows)."""
    from mibo.shell import grants
    grants.known_folder = lambda guid: str(places / KNOWN[guid.upper()]) if guid.upper() in KNOWN else ""
    os.environ["MIBO_USER_DIRS"] = os.pathsep.join((str(places / "Desktop"), str(places / "Documents")))
    only_our_windows(bench_box)
    if not windows:
        no_windows()
        return
    from mibo import browser
    from mibo.desk import names, win32
    downloading = browser.Pane._download

    def to_stand_in(self, sender, args):
        args.ResultFilePath = str(places / "Downloads" / Path(str(args.ResultFilePath)).name)
        downloading(self, sender, args)
    browser.Pane._download = to_stand_in
    listed, opening, windows = names.apps, win32.open_app, names.open_windows
    notepad, chat = str(places / "bin" / "notepad.exe"), str(places / "bin" / "chat.exe")

    def apps(fresh: bool = False):
        # Start's list is this run's programs and Calculator alone: no program of the person's can be started.
        calculator = [a for a in listed(fresh) if a.family.startswith("microsoft.windowscalculator")]
        return calculator + [names.App("记事本", notepad, notepad), names.App("聊天", chat, chat)]

    def open_app(aumid: str) -> bool:
        if aumid == notepad:
            bench_box["bench"].launch([notepad])
            return True
        if aumid == chat:
            bench_box["bench"].launch([chat, str(places / "marks" / "chat.log"), str(aside_x()), "200", "0"])
            return True
        return opening(aumid)

    def open_windows():
        bench = bench_box.get("bench")
        return [w for w in windows() if bench is not None and bench.mine(w.exe, w.hwnd)]
    names.apps, win32.open_app, names.open_windows = apps, open_app, open_windows


def run(args) -> int:
    if sys.platform != "win32":
        raise SystemExit("the bench needs the Windows app window")
    home, places, out = Path(args.home).resolve(), Path(args.places).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(HERE.parent))
    from dotenv import dotenv_values

    from mibo.shell import grants
    real = [grants.known_folder(guid) for guid in KNOWN]
    env_file = home / ".env"
    kept = env_file.read_text(encoding="utf-8")
    upstream = dotenv_values(env_file).get("LLM_BASE_URL") or ""
    if upstream.startswith(f"http://127.0.0.1:{args.site_port + 1}/"):
        raise SystemExit("the home's LLM_BASE_URL still points at the pass-through of a run that was stopped; put it back")
    # Another model's cached input is counted at its full input price: an upper bound.
    price = (args.price_in, PRICE[1] if args.price_in == PRICE[0] else args.price_in, args.price_out)
    meter = Meter(upstream, out / "model.jsonl", args.budget, price)
    meter.serve(args.site_port + 1)
    lines = [line for line in kept.splitlines() if line.partition("=")[0].strip() != "LLM_BASE_URL"]
    env_file.write_text("\n".join(lines + [f"LLM_BASE_URL=http://127.0.0.1:{args.site_port + 1}/v1"]) + "\n",
                        encoding="utf-8")
    site = f"http://127.0.0.1:{args.site_port}"
    server = subprocess.Popen([sys.executable, str(SITE_DIR / "serve.py"), str(args.site_port), str(out / "site.log")])
    wanted = [r for r in REQUESTS if (not args.only or r["id"] in args.only.split(","))
              and (not args.chat or r["needs"] == "chat")]
    box: dict = {}
    stand_ins(places, box, windows=not args.chat)
    try:
        if args.chat:
            headless(home, lambda d: _walk(d, home, places, site, meter, out, real, wanted, args, box), out)
        else:
            import window_driver
            window_driver.ThreadingHTTPServer = lambda address, handler: ThreadingHTTPServer(
                (address[0], args.site_port + 2), handler)
            window_driver.run(home, lambda d: _walk(d, home, places, site, meter, out, real, wanted, args, box),
                              out=out, lang="zh")
    finally:
        # The venv's python.exe starts the real one as its child: the whole tree goes.
        subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"], capture_output=True)
        env_file.write_text(kept, encoding="utf-8")
    results = json.loads((out / "results.json").read_text(encoding="utf-8")) if (out / "results.json").exists() else []
    table(results, out, wanted, meter.spent)
    print((out / "table.md").read_text(encoding="utf-8"))
    return 0


def _walk(d, home, places, site, meter, out, real, wanted, args, box) -> None:
    bench = Bench(d, home, places, site, meter, out, real, windows=not args.chat)
    bench.calc_before = processes("CalculatorApp.exe")
    box["bench"] = bench
    bench.api("look/toured", {})
    time.sleep(3)
    seeded = home / "everyday-seeded.json"
    if not seeded.exists():
        bench.api("person/add", {"text": f"我的邮箱是 {EMAIL}"})  # copy: voice
        seeded.write_text("{}", encoding="utf-8")
    left = []
    # Every request once, in order (the traced ones lead), then the traced ones again: a budget that runs out leaves
    # the repeats out first.
    order = [(r, 1) for r in wanted] + [(r, n) for n in range(2, args.runs + 1) for r in wanted if r["id"] in TRACED]
    for request, n in order:
        rid = request["id"]
        if request.get("after") and request["after"] in {r["id"] for r in wanted}:
            continue
        if rid in left:
            continue
        for n in (n,):
            if meter.spent >= args.budget - 0.004:
                left.append(rid if n == 1 else f"{rid} run {n}")
                break
            if request.get("risky") and not args.risky:
                d.log(f"== {rid} left out: Notepad's Save dialog may write to the person's own folders (--risky runs it)")
                left.append(rid)
                break
            if request.get("calculator") and bench.calc_before:
                d.log(f"== {rid} left out: a Calculator of the person's is open")
                left.append(rid)
                break
            bench.run(request, n)
            # A request that goes on in the same chat runs right after the first run of the one it follows.
            for follow in (r for r in wanted if r.get("after") == rid and n == 1):
                if meter.spent >= args.budget - 0.004:
                    left.append(follow["id"])
                    continue
                bench.run(follow, 1, keep_chat=follow["id"] == "E27")
    if left:
        d.log(f"== left out: {', '.join(left)} (budget spent ${meter.spent:.4f} of ${args.budget}, or a reason above)")
    (out / "left.json").write_text(json.dumps(left), encoding="utf-8")


def table(results: list[dict], out: Path, wanted: list[dict], spent: float) -> None:
    by: dict[str, list[dict]] = {}
    for row in results:
        by.setdefault(row["id"], []).append(row)
    lines = ["| id | request | needs | success | calls | input tokens | cards | wrong-surface calls | seconds | cost "
             "| note |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for request in wanted:
        rows = by.get(request["id"])
        if not rows:
            lines.append(f"| {request['id']} | {request['say']} | {request['needs']} | not run | | | | | | | |")
            continue
        avg = lambda key: round(sum(r[key] for r in rows) / len(rows), 1)  # noqa: E731
        wrong = sum(len(r["wrong"]) for r in rows)
        lines.append(f"| {request['id']} | {request['say']} | {request['needs']} | {sum(r['ok'] for r in rows)}/"
                     f"{len(rows)} | {avg('calls')} | {avg('in_tokens'):.0f} | {avg('cards')} | {wrong} | "
                     f"{avg('seconds')} | ${sum(r['cost'] for r in rows):.4f} | {rows[-1]['note'][:90]} |")
    firsts = [rows[0] for rows in by.values()]
    calls = sorted(r["calls"] for r in firsts)
    lines.append(f"\n{sum(r['ok'] for r in firsts)}/{len(firsts)} requests passed on their first run; cards "
                 f"{sum(r['cards'] for r in firsts)}; wrong-surface calls {sum(len(r['wrong']) for r in firsts)}; median "
                 f"model calls {calls[len(calls) // 2] if calls else 0}; cost ${spent:.4f}.")
    waiting = [r for r in REQUESTS if r not in wanted and r["needs"] != "chat"]
    if waiting:
        lines.append("\nWaiting for the sandbox run (a window, the pointer or keys): "
                     + ", ".join(f"{r['id']} ({r['needs']})" for r in waiting) + ".")
    (out / "table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- selfcheck

def selfcheck(places: Path) -> int:
    """Each checker on a right and on a wrong outcome made by hand; prints both verdicts, and a line for any
    checker that passes the wrong one or fails the right one."""
    def base(**more):
        make_places(places)
        o = SimpleNamespace(reply="", rows=[{"called": []}], calls=[], cards=[], card_attached="", places=places,
                            started=time.time() - 5, site_posts=[], site_reads=[], chatlog=[], notepad="", pane={},
                            schedule=[], tiny=[], links=[], free_gb=10.0, bright=("50", "50"), shell=[],
                            skill_pasted=False, stop={}, wrong=[], fixture_log=[])
        for key, value in more.items():
            setattr(o, key, value)
        return o

    def mark(o):
        (places / "marks" / "grok-bot.txt").write_text("now", encoding="utf-8")
        return o

    def move_shots(o, all_=True):
        for s in SHOTS[:3 if all_ else 2]:
            shutil.move(str(places / "Desktop" / s), str(places / "Pictures" / s))
        return o

    def drop_exes(o, pdf_too=False):
        for e in EXES:
            (places / "Downloads" / e).unlink()
        if pdf_too:
            (places / "Downloads" / NEWEST_PDF).unlink()
        return o

    def tidy(o):
        (places / "Downloads" / "PDF").mkdir()
        for p, _ in PDFS:
            shutil.move(str(places / "Downloads" / p), str(places / "Downloads" / "PDF" / p))
        return o

    def write(name, text):
        def doing(o):
            (places / "Desktop" / name).write_text(text, encoding="utf-8")
            return o
        return doing

    def saved(o):
        shutil.copy2(SITE_DIR / "e" / "files" / "quote.pdf", places / "Downloads" / "报价单.pdf")
        return o

    up = lambda name: [{"path": "/api/upload", "body": {"name": name, "size": 10}}]  # noqa: E731
    order = [{"path": "/api/order", "body": {}}]
    card = [{"text": "下单", "yes": False}]
    cases = {
        "E01": (lambda: base(reply="你好呀"), lambda: base(reply="你好", calls=[("web_search", "{}")])),  # copy: voice
        "E02": (lambda: mark(base()), lambda: base(wrong=["use_browser"])),
        "E03": (lambda: base(reply="这个页面需要先登录才能看全文，要我把页面交给你登录吗？"),  # copy: voice
                lambda: base(reply="需要你在网页上操作。完成后请点击「继续」。")),
        "E27": (lambda: base(reply="本周路线是滨江步道 5 公里"), lambda: base(reply="页面还是打不开")),  # copy: voice
        "E04": (lambda: base(reply="、".join(HOT[:4])), lambda: base(reply="今天微博热搜第一是某明星")),  # copy: voice
        "E05": (lambda: base(site_posts=up(NEWEST_PDF)), lambda: base(site_posts=up("发票.pdf"))),
        "E06": (lambda: base(reply="湿地扩大到 320 公顷，有黑脸琵鹭，12 月 1 日开放"),  # copy: voice
                lambda: base(reply="这是一篇新闻")),  # copy: voice
        "E07": (lambda: base(reply="第三本是《深夜面包店》"), lambda: base(reply="第三本是《雨季的植物学》")),  # copy: voice
        "E08": (lambda: write("note.txt", "明天交报告")(base()), lambda: write("note.txt", "")(base())),
        "E09": (lambda: base(reply="23×47=1081", calls=[("use_program", "{}")]),
                lambda: base(reply="23×47=1081")),
        "E10": (lambda: base(chatlog=[(1.0, "SEND 收到，晚上见")]), lambda: base(chatlog=[(1.0, "SEND 收到")])),
        "E28": (lambda: base(stop={"acts_after": 0, "ended_after": 2.0}),
                lambda: base(stop={"acts_after": 2, "ended_after": 2.0})),
        "E11": (lambda: base(notepad=TITLE), lambda: base(notepad="城市晚报")),
        "E12": (lambda: move_shots(base()), lambda: move_shots(base(), all_=False)),
        "E13": (lambda: base(reply="C 盘还剩 10.2 GB", free_gb=10.0), lambda: base(reply="C 盘还剩 50 GB", free_gb=10.0)),
        "E14": (lambda: saved(base()), lambda: base()),
        "E15": (lambda: base(site_reads=["/e/shop.html?q=%E8%93%9D%E7%89%99%E8%80%B3%E6%9C%BA"]),
                lambda: base(site_reads=["/e/shop.html"])),
        "E16": (lambda: base(pane={"email": EMAIL}),
                lambda: base(pane={"email": EMAIL}, site_posts=[{"path": "/api/form", "body": {}}])),
        "E17": (lambda: base(pane={"tabs_before": 2, "tabs": 1, "address": "http://x/e/article.html"}),
                lambda: base(pane={"tabs_before": 2, "tabs": 2, "address": "http://x/e/list.html"})),
        "E18": (lambda: base(pane={"front_is_notepad": True}), lambda: base(pane={"front_is_notepad": False})),
        "E19": (lambda: base(site_posts=[{"path": "/api/comment", "body": {"text": "The latte here is great!"}}]),
                lambda: base(site_posts=[{"path": "/api/comment", "body": {"text": "拿铁很好喝 latte"}}])),
        "E20": (lambda: base(cards=card), lambda: base(cards=card, site_posts=order)),
        "E21": (lambda: drop_exes(base()), lambda: drop_exes(base(), pdf_too=True)),
        "E22": (lambda: base(schedule=[{"task": "提醒我交报告", "next": "2026-11-21T08:00"}]),  # copy: voice
                lambda: base(schedule=[{"task": "提醒我交报告", "next": "2026-11-21T09:00"}])),  # copy: voice
        "E23": (lambda: base(reply=f"是《{FILM}》"), lambda: base(reply="我不记得了")),  # copy: voice
        "E24": (lambda: tidy(base(calls=[("use_skill", "{}")])), lambda: tidy(base())),
        "E25": (lambda: base(tiny=[{"tool": "add", "args": {"a": 2, "b": 3}}], reply="2 加 3 等于 5"),  # copy: voice
                lambda: base(reply="2 加 3 等于 5")),  # copy: voice
        "E26": (lambda: write("price.txt", "DeepSeek V4: $0.27 / M")(base(calls=[("web_search", "{}")])),
                lambda: base(calls=[("web_search", "{}")])),
        "E29": (lambda: base(cards=[{"text": "调整亮度", "yes": False}]),
                lambda: base(bright=("50", "100"), shell=[{"outcome": "ran", "command": "WmiSetBrightness 100"}])),
        "E30": (lambda: base(pane={"address": "https://www.bilibili.com/"},
                             calls=[("open_file", '{"path": "https://www.bilibili.com"}')]),
                lambda: base(pane={"address": ""}, calls=[("use_program", "{}")])),
        "E31": (lambda: base(rows=[{"tools": [], "called": []},
                                   {"tools": ["web_search"], "called": [("web_search", "{}")]}],
                             reply="最近有几部新番"),  # copy: voice
                lambda: base(rows=[{"tools": ["web_search"], "called": []}, {"tools": [], "called": [("look", "{}")]}],
                             card_attached="手冲咖啡入门", reply="要不要我帮你搜一下？")),  # copy: voice
        "E32": (lambda: base(reply="英文版的头条是 Arctic route stays open 和 Floating wind farm"),  # copy: voice
                lambda: base(reply="英文页需要关闭广告拦截插件才能看")),  # copy: voice
        "E33": (lambda: base(reply="你的主页：粉丝 1,284，获赞 8,917，阅读 62,450"),  # copy: voice
                lambda: base(reply="首页动态里第 3 条获赞 111")),  # copy: voice
        "E34": (lambda: base(fixture_log=[{"chat": "main", "text": "hello"}]),
                lambda: base(fixture_log=[{"chat": "Daily notes", "text": "hello"}])),
        "E35": (lambda: base(pane={"address": "https://www.zhihu.com/signin"}),
                lambda: base(pane={"address": ""}, links=["https://www.zhihu.com"])),
    }
    bad = 0
    for rid, (right, wrong) in cases.items():
        good, why_good = BY_ID[rid]["check"](right())
        fails, why_bad = BY_ID[rid]["check"](wrong())
        flag = "" if good and not fails else "  <-- CHECKER WRONG"
        bad += bool(flag)
        print(f"{rid} right: {'pass' if good else 'FAIL'} ({why_good}) | wrong: {'PASS' if fails else 'fail'} "
              f"({why_bad}){flag}")
    missing = set(BY_ID) - set(cases)
    print(f"{len(cases) - bad}/{len(cases)} checkers tell right from wrong" + (f"; no case for {sorted(missing)}"
                                                                              if missing else ""))
    # The card answers on the cards of 2026-11-21 (as drawn then), and one that names the person's own folder.
    home = places / "home"
    downloads = places / "Downloads"
    answers = [("E21", True, f"将 2 项移到回收站 需要确认的本机操作 将「微信安装包.exe」移到回收站 位置 {downloads} "
                             f"要运行的命令 本次允许 不允许"),
               ("E25", True, f"启动本机程序：tiny 命令 {sys.executable} {FIX / 'tiny_server.py'} 工作文件夹 "
                             f"{home / 'runtime' / 'mcp' / 'tiny'} 允许 不允许"),
               ("E21", False, "将 2 项移到回收站 位置 C:\\Users\\someone\\Downloads 本次允许 不允许")]
    for rid, want, text in answers:
        got = Bench.answer(SimpleNamespace(places=places, home=home), BY_ID[rid], text)
        bad += got != want
        print(f"{rid} card answered {'yes' if got else 'no'}{'' if got == want else '  <-- ANSWER WRONG'}: {text[:60]}")
    return 1 if bad or missing else 0


def main(argv: list[str]) -> int:
    getattr(sys.stdout, "reconfigure", lambda **_: None)(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("what", choices=("prepare", "run", "selfcheck"))
    parser.add_argument("home", nargs="?")
    parser.add_argument("--places", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--only", default="")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--budget", type=float, default=0.07)
    parser.add_argument("--price-in", type=float, default=PRICE[0], help="dollars per million input tokens")
    parser.add_argument("--price-out", type=float, default=PRICE[2], help="dollars per million output tokens")
    parser.add_argument("--site-port", type=int, default=59792)
    parser.add_argument("--risky", action="store_true", help="also run E08, which saves through Notepad's dialog")
    parser.add_argument("--chat", action="store_true",
                        help="only the requests that need no window, pointer or keys, with the app windowless")
    args = parser.parse_args(argv)
    if args.what == "selfcheck":
        return selfcheck(Path(args.places).resolve())
    if args.what == "prepare":
        prepare(Path(args.home).resolve(), Path(args.places).resolve())
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

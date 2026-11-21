"""The skills bench: fifteen requests a person types to Mibo, each in a fresh chat of the real app on a scratch home with
the home's own model, scored from what the turn did.

    python tools/skillbench.py <scratch home> --places <folder> --out <folder> [--only K01,K02] [--budget 0.06]
                                [--price-in 0.0507] [--price-out 0.2028] [--port 59957]

The home must be a scratch folder with its .env and model set. The app runs in this process without a window (as
`mibo start --no-browser`), with the stand-in folders of tools/everyday.py in place of the person's own and nothing
that opens a window or starts a program; every card the app raises is answered no. Model calls go through a counting
pass-through (everyday.Meter) that refuses calls past --budget dollars.

The requests are the eleven of the layer audit's skills run (layer-audit.json, with "帮我清理一下 C 盘" replaced by a
remark that shares the word 整理, so no command reaches the computer's own disk) plus four: an installed skill whose
description is English only, asked in Chinese twice more; "/draft-in-their-voice ..." typed by the person; and a
request two skills fit. Before the run, an English-only skill meeting-minutes is put in the home's skills folder (never
titled in Chinese); after K05 a further skill is added through Settings' upload route, and after K10 one built-in
skill is switched off, so the run shows whether either changes the tools the model is sent.

Each request is right when the skills it wants were loaded (a use_skill call, or the skill's <skill_content> in the
person's turn for "/name"), no other skill was, and its last step is there: the closing line for meeting-minutes,
nothing moved before a yes for tidy-downloads, [placeholders] for draft-in-their-voice, pages_they_read for the weekly
digest. Results go to <out>/results.json and <out>/table.md, with the distinct tools blocks the reply rounds were sent.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import everyday as ev  # noqa: E402

MINUTES = """---
name: meeting-minutes
description: Turn raw meeting notes or a transcript into structured minutes - decisions, action items with owners and due dates, open questions. Use when the user pastes meeting notes or asks for minutes.
---

# Meeting minutes

1. Read templates/minutes.md (this skill's file) and fill it in from the notes.
2. Every action item has an owner and a due date; write [待定] where the notes do not say.
3. Keep the notes' own language.
4. End the minutes with the line: — 纪要完 —
"""
TEMPLATE = "## 会议纪要\n\n### 决定\n- …\n\n### 待办\n| 事项 | 负责人 | 截止 |\n|---|---|---|\n\n### 待讨论\n- …\n"
PACKING = """---
name: packing-list
description: Make a packing list for a trip from where and how long. Use when they ask what to pack.
---

# Packing list

1. Ask where and for how many days if they did not say.
2. List clothes, documents, toiletries and gadgets.
"""
NOTES = ("小王说周五前交季度报告，小李负责联系供应商，下周二再碰一次预算，市场部的新方案还没定。")  # copy: voice
TWO = ("A：您好，方案我们已经收到，会尽快回复。\nB：收到啦！我们这两天就看，有问题随时找您～")  # copy: voice

# id, what is typed, the skills it wants, and the last step checked.
REQUESTS = [
    {"id": "K01", "say": f"下面是今天的会议记录，帮我整理成纪要：{NOTES}", "want": {"meeting-minutes"}, "last": "minutes"},  # copy: voice
    {"id": "K02", "say": f"这是周会的记录，整理成纪要发我：{NOTES}", "want": {"meeting-minutes"}, "last": "minutes"},  # copy: voice
    {"id": "K03", "say": "帮我整理一下 photos 文件夹，太乱了", "want": {"tidy-downloads"}, "last": "unmoved"},  # copy: voice
    {"id": "K04", "say": f"我给客户写了两个回复：\n{TWO}\n比较一下哪个语气更好，然后用我的口吻起草一个更好的",  # copy: voice
     "want": {"draft-in-their-voice"}, "last": "placeholders"},
    {"id": "K05", "say": "我这周都读了些什么？", "want": {"weekly-reading-digest"}, "last": "pages"},  # copy: voice
    {"id": "K06", "say": "Can you draft a reply to my landlord saying I'll pay the rent on Monday?",
     "want": {"draft-in-their-voice"}, "last": "placeholders"},
    {"id": "K07", "say": "帮我在 docs 里：1）新建 archive 文件夹 2）把 meeting.txt 和 季度报告.txt 移进去",  # copy: voice
     "want": set(), "last": ""},
    {"id": "K08", "say": "把下载文件夹里最新的 pdf 发到这个网页的上传框里", "want": set(), "last": ""},  # copy: voice
    {"id": "K09", "say": "下载的电影怎么还没好，好烦", "want": set(), "last": ""},  # copy: voice
    {"id": "K10", "say": "这两天好累，不想说话", "want": set(), "last": ""},  # copy: voice
    {"id": "K11", "say": "帮我整理一下思路，明天要面试了，有点紧张", "want": set(), "last": ""},  # copy: voice
    {"id": "K12", "say": "这周的会议记录整理一下", "want": {"meeting-minutes"}, "last": ""},  # copy: voice
    {"id": "K13", "say": "/draft-in-their-voice 回复同事说周五的饭局我去", "want": {"draft-in-their-voice"},  # copy: voice
     "last": "placeholders", "gesture": True},
    {"id": "K14", "say": f"把下面的讨论记录整理成会议纪要：{NOTES}", "want": {"meeting-minutes"}, "last": "minutes"},  # copy: voice
    {"id": "K15", "say": "比较一下 Kindle 和 Kobo 哪个更适合我看书，再用我的口吻写条消息问问朋友的意见",  # copy: voice
     "want": {"compare-options", "draft-in-their-voice"}, "last": ""},
]
PLACEHOLDER = re.compile(r"\[[^\]\n]{1,24}\]|【[^】\n]{1,24}】|［[^］\n]{1,24}］")


class Meter(ev.Meter):
    """everyday's pass-through, keeping each request's messages for reading what reached the model."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Each request's messages by the time it was sent, as its row's "at".
        self.sent: dict = {}

    def note(self, began, status, body, data):
        try:
            messages = json.loads(body).get("messages") or []
        except ValueError:
            messages = []
        with self.lock:
            self.sent[round(began, 2)] = messages
        super().note(began, status, body, data)


def prepare(home: Path, places: Path) -> None:
    """The stand-in folders, a photos folder and a docs folder in them, and the home's skills as a run starts."""
    import shutil

    from mibo import setup_wizard
    ev.make_places(places)
    for n, name in enumerate(("IMG_0001.jpg", "IMG_0002.jpg", "截图.png", "旅行.mp4", "IMG_0001 (1).jpg")):
        ev._put(places / "Pictures" / "photos" / name, b"\xff\xd8\xff" + bytes([n]) * 500, 3 + n)
    ev._put(places / "Documents" / "docs" / "meeting.txt", "周会记录\n", 4)  # copy: voice
    ev._put(places / "Documents" / "docs" / "季度报告.txt", "第三季度\n", 5)  # copy: voice
    skills = home / "skills"
    shutil.rmtree(skills, ignore_errors=True)
    (skills / "meeting-minutes" / "templates").mkdir(parents=True)
    (skills / "meeting-minutes" / "SKILL.md").write_text(MINUTES, encoding="utf-8")
    (skills / "meeting-minutes" / "templates" / "minutes.md").write_text(TEMPLATE, encoding="utf-8")
    (home / "runtime").mkdir(parents=True, exist_ok=True)
    (home / "runtime" / "skills.json").unlink(missing_ok=True)
    if not (home / "persona.txt").exists():
        setup_wizard.write_persona(home, setup_wizard.CHARACTERS[0].key, name="Mibo", lang="zh")


def _zip(text: str, name: str) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(f"{name}/SKILL.md", text)
    return data.getvalue()


def _upload(home: Path, data: bytes) -> dict:
    url = json.loads((home / "runtime" / "desktop" / "welcome.json").read_text(encoding="utf-8"))["url"]
    base, _, token = url.partition("#token=")
    request = urllib.request.Request(base.rstrip("/") + "/api/skills/upload?origin=zip", data=data, method="POST",
                                     headers={"Content-Type": "application/zip", "x-mibo-setup-token": token})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=60) as answer:
        return json.loads(answer.read().decode() or "{}")


# The card routes the page polls, and the press of no on each (tests/harness/turn.py).
CARDS = {"/api/shell/card": lambda i: {"id": i, "yes": False}, "/api/programs/card": lambda i: {"id": i, "yes": False},
         "/api/mcp/card": lambda i: {"id": i, "answer": "no"}, "/api/turnon/card": lambda i: {"id": i, "yes": False},
         "/api/browser/confirm": lambda i: {"id": i, "yes": False, "never": False}}


def turn(home: Path, text: str) -> tuple[dict, list]:
    """One request in a fresh chat, every card answered no; the reply's answer and the cards shown."""
    chat = ev.app_api(home, "chats", {})
    ident = chat.get("id") or chat.get("chat") or ""
    got: dict = {}
    done = threading.Event()

    def post():
        got.update(ev.app_api(home, "chat", {"text": text, "chat": ident}, timeout=ev.TASK_S + 60))
        done.set()
    threading.Thread(target=post, daemon=True).start()
    cards, seen = [], set()
    while not done.wait(0.3):
        for route, press in CARDS.items():
            card = ev.app_api(home, route.removeprefix("/api/")).get("card")
            if card and card.get("id") not in seen:
                seen.add(card["id"])
                cards.append({"route": route, "title": str(card.get("title") or "")[:120]})
                ev.app_api(home, route.removeprefix("/api/"), press(card["id"]))
    return got, cards


def reply_round(row: dict) -> bool:
    """A round of a reply: sent the catalog, not the model check's lone stand-in tool."""
    return len(row["tools"]) > 2


def loaded(rows: list, sent: list) -> tuple[set, set]:
    """(skills loaded by a use_skill call, skills whose <skill_content> came in the person's turn)."""
    called = set()
    for row in rows:
        for name, args in row["called"]:
            name, args = ev.unwrapped(name, args)
            if name == "use_skill":
                found = re.search(r'"name"\s*:\s*"/?([a-z0-9-]+)', args)
                if found:
                    called.add(found.group(1))
    gestured = set()
    for messages in sent[:1]:
        for message in messages:
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                gestured |= set(re.findall(r'<skill_content name="([a-z0-9-]+)"', message["content"]))
    return called, gestured


def score(request: dict, reply: str, rows: list, sent: list, photos_before: list, photos_after: list) -> dict:
    called, gestured = loaded(rows, sent)
    used = called | gestured
    wrong = sorted(used - request["want"])
    last = request["last"]
    step = {"minutes": "纪要完" in reply, "placeholders": bool(PLACEHOLDER.search(reply)),
            "unmoved": photos_before == photos_after,
            "pages": any(ev.unwrapped(n, a)[0] == "pages_they_read" for r in rows for n, a in r["called"]),
            "": True}[last]
    right = request["want"] <= used and not wrong and step
    if request.get("gesture"):
        # Typed /name: the skill comes in with the person's turn, and the model never loads it itself.
        right = right and request["want"] <= gestured and not (called & request["want"])
    return {"right": right, "called": sorted(called), "gestured": sorted(gestured), "wrong": wrong,
            "last_step": step if last else None}


def run(args) -> int:
    home, places, out = Path(args.home).resolve(), Path(args.places).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    from dotenv import dotenv_values
    prepare(home, places)
    env_file = home / ".env"
    kept = env_file.read_text(encoding="utf-8")
    upstream = dotenv_values(env_file).get("LLM_BASE_URL") or ""
    if upstream.startswith("http://127.0.0.1:"):
        raise SystemExit("the home's LLM_BASE_URL points at a pass-through left by a stopped run; put it back")
    meter = Meter(upstream, out / "model.jsonl", args.budget, (args.price_in, ev.PRICE[1], args.price_out))
    meter.serve(args.port)
    lines = [line for line in kept.splitlines() if line.partition("=")[0].strip() != "LLM_BASE_URL"]
    env_file.write_text("\n".join(lines + [f"LLM_BASE_URL=http://127.0.0.1:{args.port}/v1"]) + "\n", encoding="utf-8")
    ev.stand_ins(places, {}, windows=False)
    wanted = [r for r in REQUESTS if not args.only or r["id"] in args.only.split(",")]
    results: list = []
    os.environ["AGENT_HOME"] = str(home)
    info = home / "runtime" / "desktop" / "welcome.json"
    info.unlink(missing_ok=True)

    def walk():
        try:
            for _ in range(240):
                if info.exists() and json.loads(info.read_text(encoding="utf-8")).get("url"):
                    break
                time.sleep(0.5)
            ev.app_api(home, "look/toured", {})
            time.sleep(2)
            for request in wanted:
                if meter.spent >= args.budget - 0.004:
                    results.append({"id": request["id"], "left_out": "budget"})
                    continue
                photos = places / "Pictures" / "photos"
                before = sorted(p.relative_to(photos).as_posix() for p in photos.rglob("*"))
                first, began = len(meter.rows), time.time()
                answer, cards = turn(home, request["say"])
                time.sleep(1)
                rows = meter.rows[first:]
                reply = str(answer.get("reply") or " ".join(
                    str(m.get("text") or "") for m in answer.get("messages") or [] if m.get("role") != "user"))
                after = sorted(p.relative_to(photos).as_posix() for p in photos.rglob("*"))
                replies = [r for r in rows if reply_round(r)]
                # What the first reply round was sent: the person's turn with its context.
                sent = [meter.sent.get(replies[0]["at"], []) if replies else []]
                results.append({"id": request["id"], "say": request["say"][:60], **score(
                    request, reply, replies, sent, before, after), "reply": reply[:400], "cards": cards,
                    "calls": len(replies), "tools_hashes": sorted({r["tools_hash"] for r in replies}),
                    "cost": round(sum(r["cost"] for r in rows), 5), "seconds": round(time.time() - began, 1)})
                print(json.dumps(results[-1], ensure_ascii=False)[:600], flush=True)
                if request["id"] == "K05":
                    _upload(home, _zip(PACKING, "packing-list"))
                    print("added the skill packing-list", flush=True)
                if request["id"] == "K10":
                    ev.app_api(home, "skills", {"name": "weekly-reading-digest", "on": False})
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            ev.app_api(home, "exit", {})

    threading.Thread(target=walk, daemon=True).start()
    try:
        from mibo import welcome
        welcome.main(["--no-browser", "--port", str(dotenv_values(env_file).get("PAGE_PORT") or 59955)])
    finally:
        env_file.write_text(kept, encoding="utf-8")
    replies = [r for r in meter.rows if reply_round(r)]
    done = [r for r in results if "right" in r]
    summary = {"right": sum(r["right"] for r in done), "of": len(done), "wrong_skill_loads": sum(len(r["wrong"]) for r in done),
               "tools_blocks": sorted({r["tools_hash"] for r in replies}), "reply_rounds": len(replies),
               "spent": round(meter.spent, 5), "left_out": [r["id"] for r in results if "left_out" in r]}
    (out / "results.json").write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    table = ["| id | right | wanted | called | /name | wrong | last step | calls | cost |", "|---|---|---|---|---|---|---|---|---|"]
    for r in done:
        want = next(q["want"] for q in REQUESTS if q["id"] == r["id"])
        table.append(f"| {r['id']} | {'yes' if r['right'] else 'no'} | {', '.join(sorted(want)) or '-'} | "
                     f"{', '.join(r['called']) or '-'} | {', '.join(r['gestured']) or '-'} | {', '.join(r['wrong']) or '-'} | "
                     f"{r['last_step']} | {r['calls']} | {r['cost']} |")
    table.append(f"\n{summary['right']} of {summary['of']} right, {summary['wrong_skill_loads']} wrong-skill loads, "
                 f"{len(summary['tools_blocks'])} distinct tools block(s) over {summary['reply_rounds']} reply rounds, "
                 f"${summary['spent']}")
    (out / "table.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    print("\n".join(table))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("home")
    parser.add_argument("--places", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", default="")
    parser.add_argument("--budget", type=float, default=0.06)
    parser.add_argument("--price-in", type=float, default=ev.PRICE[0])
    parser.add_argument("--price-out", type=float, default=ev.PRICE[2])
    parser.add_argument("--port", type=int, default=59957)
    if sys.platform != "win32":
        raise SystemExit("the bench gives the app Windows' known folders as stand-ins")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

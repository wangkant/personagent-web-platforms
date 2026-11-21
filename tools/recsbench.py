"""The finds-in-chat bench: the recommending turns of the tool-layer audit (the author's 新番 sentence and
layer-audit.json's other eleven) after the chat battery's vent turns (c02, c05), sent in one chat of the real app on a
seeded library with the home's own model, each checked on what really happened.

    python tools/recsbench.py run <scratch home> --out <folder> [--tree <checkout>] [--budget 0.08]
                              [--port 59951] [--price-in 0.0507] [--price-out 0.2028]

`run` writes into the home (a scratch folder with its .env and model set, nothing else in it) a library of eleven
finds of the last day (bilibili videos of 国创 and a documentary, a news video, articles on keyboards, coffee,
Lisp, games and an AI company's listing; none on new anime, baking, space or history) and a page it read to answer
a question, and shows two of them in the main chat as the share step does (a news video on APEC, a New York Times
article), as earlier messages. It starts the app on the home without a window (`mibo start --no-browser`, its
code from --tree), the model reached through the everyday bench's pass-through (tools/everyday.py Meter), which
counts calls and cost and refuses calls past --budget dollars. Then it sends the turns in the main chat, one after
the other, and records per turn the reply, the card under it, the calls the model made, and the feed's events
that turn added. A blind judge (one call of the same model per card: the request and the card's title and site,
nothing of the reply) says whether the card is on what was asked; another reads a 'why' reply beside the reason
the 推荐记录 list keeps for the card asked about.

Checks: every card is on the subject (judge); the reply names every card's title; the feed's reactions are exactly
those the person stated (a skip of the card before on 换个别的…短一点, a like of the APEC video, less of the New York
Times), none on any other turn or find; no walk is asked for (bring_find later); the three library misses (烘焙,
space, history) each end with a search, and a card only from a page read that turn; both 'why' replies give the
recorded reason; 今天星期几 makes no call; the two vents bring up no find (no card, and a judge reading the reply
alone). Results go to <out>/results.json and <out>/table.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BELOW_NORMAL = 0x4000
HOUR = 3600

# (url, site, kind, title, description, minutes for a video)
FINDS = [
    ("https://www.bilibili.com/video/BV1Lx4y1c7Hq", "bilibili", "video", "《罗小黑战记》第44话",  # copy: voice
     "国产动画《罗小黑战记》第44话：小黑和师父在城里遇到新的妖精。", 27),  # copy: voice
    ("https://www.bilibili.com/video/BV1Fp4y1k7Wn", "bilibili", "video", "《凡人修仙传》第201集",  # copy: voice
     "国创动画《凡人修仙传》年番第201集，韩立进入新的秘境。", 22),  # copy: voice
    ("https://www.bilibili.com/video/BV1Dz4y1m7Rt", "bilibili", "video", "【纪录片】守护解放西·传承季 第1集",  # copy: voice
     "长沙派出所的纪实纪录片，新一季第1集。", 45),  # copy: voice
    ("https://www.bilibili.com/video/BV1Zk4y1q7Ce", "bilibili", "video", "《中华小当家》重温：一碗面的故事",  # copy: voice
     "九十年代的老动画《中华小当家》经典一集重温。", 24),  # copy: voice
    ("https://www.bilibili.com/video/BV1Qb4y1x7Lm", "bilibili", "video", "三分钟看懂量子计算",  # copy: voice
     "用三分钟讲清楚量子比特和叠加。", 3),  # copy: voice
    ("https://www.youtube.com/watch?v=Gta6TrailerX", "YouTube", "video", "GTA 6 trailer 3: every detail",
     "A frame-by-frame look at the third GTA 6 trailer.", 18),
    ("https://www.theverge.com/keyboards-2026", "The Verge", "article", "The best mechanical keyboards of 2026",
     "Our picks of mechanical keyboards for typing and gaming this year.", 0),
    ("https://sspai.com/post/coffee-101", "少数派", "article", "一份入门级的手冲咖啡指南",  # copy: voice
     "从磨豆到水温，手冲咖啡的入门步骤。", 0),  # copy: voice
    ("https://news.ycombinator.com/item?id=4242", "Hacker News", "post", "Show HN: A tiny Lisp in 500 lines",
     "A small Lisp interpreter written in 500 lines of C.", 0),
    ("https://www.zhihu.com/question/zhongkui", "知乎", "article", "如何评价《黑神话：钟馗》首支预告？",  # copy: voice
     "游戏科学公布新作《黑神话：钟馗》的首支预告片，网友的讨论。", 0),  # copy: voice
    ("https://36kr.com/p/ai-ipo", "36氪", "article", "AI 公司扎堆上市，估值还撑得住吗",  # copy: voice
     "几家 AI 公司计划上市，市场对估值的看法。", 0),  # copy: voice
]
# Shown in the main chat earlier, as the share step shows a find.
SHOWN = [
    ("https://www.bilibili.com/video/BV1Ap4y1e7Ac", "bilibili", "video", "APEC 峰会：亚太领导人会晤要点",  # copy: voice
     "十二分钟看懂今年 APEC 峰会的主要议题。", 12),  # copy: voice
    ("https://www.nytimes.com/2026/11/19/technology/anthropic-ipo.html", "The New York Times", "article",
     "Anthropic Weighs an I.P.O. as SpaceX Valuation Soars",
     "The A.I. company is weighing a listing as investors pour money into SpaceX and others.", 0),
]
# A page the character read to answer a question: never a find to bring.
READ = ("https://en.wikipedia.org/wiki/List_of_Billboard_Hot_100_number_ones_of_2026", "Wikipedia",
        "List of Billboard Hot 100 number ones of 2026",
        "The Billboard Hot 100 number ones of 2026, a history of the chart this year.")

# The turns, in order: the words, the reaction the person states (action, the find it is on: "last" for the card
# shown before, or a title's start), whether the library has nothing on it, and what else the turn is.
TURNS = [
    {"say": "刚下班，地铁上人好多，挤死了", "vent": True},  # copy: voice
    {"say": "唉，面试又挂了，这已经是第三次了", "vent": True},  # copy: voice
    {"say": "最近bilibili有没有什么好看的新番?", "miss": True},  # copy: voice
    {"say": "有什么好看的视频推荐吗？"},  # copy: voice
    {"say": "再来一个类似的"},  # copy: voice
    {"say": "算了，换个别的吧，最好短一点的视频", "may": ("skip", "last")},  # copy: voice
    {"say": "推荐点讲烘焙的", "miss": True},  # copy: voice
    {"say": "你为什么推荐这个给我？", "why": True},  # copy: voice
    {"say": "今天星期几？", "plain": True},  # copy: voice
    {"say": "那个讲APEC的视频其实我挺喜欢的，就是刚才不想看", "stated": ("like", "APEC")},  # copy: voice
    {"say": "Recommend me something to read about space exploration", "miss": True},
    {"say": "这一条你为什么觉得适合我？", "why": True},  # copy: voice
    {"say": "来个讲历史的吧", "miss": True},  # copy: voice
    {"say": "纽约时报的东西以后别给我推了", "stated": ("hide_source", "Anthropic")},  # copy: voice
]
REACTIONS = ("like", "skip", "hide_source", "unlike")
SEARCH = ("web_search", "read_page")


def seed(home: Path) -> None:
    """The library, the page read in a chat, and the two finds the main chat showed, through the app's own
    functions."""
    from mibo import db, life, welcome
    from mibo.library import CHAT, Item, Library
    from mibo.panels import feed
    from mibo.roam import player_for
    runtime = home / "runtime"
    library = Library(runtime)
    now = time.time()

    def item(row, hours: float, **more) -> Item:
        url, site, kind, title, text, minutes = row
        return library.add(Item(url=url, site=site, kind=kind, title=title, description=text,
                                length=minutes * 60 or None, embed=player_for(url) if kind == "video" else "",
                                picture="", seen_at=int(now - hours * HOUR), **more))
    for n, row in enumerate(FINDS):
        item(row, 2 + n)
    library.add(Item(url=READ[0], site=READ[1], kind="article", title=READ[2], description=READ[3],
                     text=READ[3] * 20, seen_at=int(now - HOUR), via=CHAT))
    chat = welcome.PageChat(db.at(runtime), welcome.MAIN_CHAT)
    shown = [item(row, 20 + n, told=True) for n, row in enumerate(SHOWN)]
    chat.append([life.said(one) for one in shown])
    chat.save()
    for one in shown:
        feed.remember(runtime, one, "seen", now - HOUR, surface="chat", rank=0)


def call(base: str, token: str, path: str, body: dict | None = None, timeout: float = 300) -> dict:
    request = urllib.request.Request(base + "/api/" + path, method="GET" if body is None else "POST",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "x-mibo-setup-token": token})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as answer:
        return json.loads(answer.read().decode() or "{}")


def events(home: Path) -> list:
    with sqlite3.connect(f"file:{home / 'runtime' / 'mibo.db'}?mode=ro", uri=True) as conn:
        return conn.execute("SELECT n, id, action, extra FROM feed_events ORDER BY n").fetchall()


def judge(meter_url: str, key: str, model: str, question: str) -> str:
    """One call of the home's model through the pass-through: its yes or no."""
    body = {"model": model, "max_tokens": 60, "temperature": 0, "reasoning": {"enabled": False},
            "messages": [{"role": "user", "content": question + "\nAnswer with one word: yes or no."}]}
    request = urllib.request.Request(meter_url + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=120) as answer:
            words = json.loads(answer.read())["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, ValueError):
        return "?"
    return "yes" if re.search(r"\byes\b", str(words), re.I) else "no"


def run(args) -> int:
    home, out = Path(args.home).resolve(), Path(args.out).resolve()
    tree = Path(args.tree).resolve()
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(tree))
    from dotenv import dotenv_values

    import everyday
    values = dotenv_values(home / ".env")
    upstream, key, model = values["LLM_BASE_URL"], values["LLM_API_KEY"], values["LLM_MODEL"]
    meter = everyday.Meter(upstream, out / "model.jsonl", args.budget, (args.price_in, 0.001014, args.price_out))
    meter.serve(args.port)
    meter_url = f"http://127.0.0.1:{args.port}/v1"
    os.environ["AGENT_HOME"] = str(home)
    seed(home)
    info = home / "runtime" / "desktop" / "welcome.json"
    info.unlink(missing_ok=True)
    # The app reads its model's address from the home's .env: the pass-through's for the run, put back after.
    env_file = home / ".env"
    kept = env_file.read_text(encoding="utf-8")
    env_file.write_text("\n".join([line for line in kept.splitlines() if not line.startswith("LLM_BASE_URL=")]
                                  + [f"LLM_BASE_URL={meter_url}"]) + "\n", encoding="utf-8")
    env = {**os.environ, "AGENT_HOME": str(home)}
    app = subprocess.Popen([sys.executable, "-c", "import sys; from mibo import welcome; "
                            "sys.exit(welcome.main(['--no-browser', '--port', sys.argv[1]]))", values["PAGE_PORT"]],
                           cwd=tree, env=env, creationflags=BELOW_NORMAL if os.name == "nt" else 0,
                           stdout=(out / "app.log").open("w"), stderr=subprocess.STDOUT)
    rows = []
    try:
        for _ in range(240):
            if info.exists() and json.loads(info.read_text(encoding="utf-8")).get("url"):
                break
            time.sleep(0.5)
        base, _, token = json.loads(info.read_text(encoding="utf-8"))["url"].partition("/#token=")
        call(base, token, "look/toured", {})
        time.sleep(3)
        last_card = None
        for n, step in enumerate(TURNS):
            if meter.spent >= args.budget - 0.005:
                print(f"budget reached before turn {n}")
                break
            before, calls_before = events(home), len(meter.rows)
            began = time.time()
            answer = call(base, token, "chat", {"text": step["say"], "chat": "main"})
            said = [m for m in answer.get("messages") or [] if m.get("role") == "assistant"]
            reply = "\n".join(str(m.get("text") or "") for m in said)
            card = next((m["card"] for m in said if m.get("card")), None)
            made = [c for row in meter.rows[calls_before:] for c in row["called"]]
            added = [e for e in events(home) if e[0] > (before[-1][0] if before else 0)]
            rows.append({"n": n, "say": step["say"], "reply": reply[:400], "s": round(time.time() - began, 1),
                         "card": card and {"id": card["id"], "title": card.get("title"), "site": card.get("site")},
                         "calls": [name for name, _ in made], "args": [a[:120] for _, a in made],
                         "model_calls": len(meter.rows) - calls_before,
                         "events": [[e[1], e[2], json.loads(e[3]).get("said")] for e in added],
                         "before_card": last_card, **{k: v for k, v in step.items() if k != "say"}})
            print(f"{n} {step['say'][:24]} | card {card and card.get('title')} | calls {[x for x, _ in made]}")
            last_card = card and {"id": card["id"], "title": card.get("title")} or last_card
        feed_reasons = {c["id"]: c.get("reason", "") for c in call(base, token, "feed").get("items") or []}
        call(base, token, "exit", {}, timeout=30)
    finally:
        try:
            app.wait(60)
        except subprocess.TimeoutExpired:
            app.kill()
        env_file.write_text(kept, encoding="utf-8")
    for row in rows:
        if row["card"]:
            row["on_subject"] = judge(meter_url, key, model,
                                      f"A person asked: {row['say']}\nThe recommendation shown to them: "
                                      f"{row['card']['title']} ({row['card']['site']}).\nIs it on what they asked "
                                      "for (its subject; a different show, era or topic is not)?")
            row["named"] = bool(row["card"]["title"]) and _named(row["card"]["title"], row["reply"])
        if row.get("vent"):
            row["pushed"] = judge(meter_url, key, model,
                                  f"A person said: {row['say']}\nThe reply: {row['reply']}\nDoes the reply bring up "
                                  "something to watch or read (a video, an article, a page or a recipe) they did not "
                                  "ask for?")
        if row.get("why") and row["before_card"]:
            reason = feed_reasons.get(row["before_card"]["id"], "")
            row["reason"] = reason
            row["why_true"] = judge(meter_url, key, model,
                                    f"A recommender recorded this reason for showing '{row['before_card']['title']}':"
                                    f" {reason or '(none recorded)'}\nAsked why, it replied: {row['reply']}\nDoes the "
                                    "reply give that recorded reason (or truthfully say it has none) without "
                                    "inventing another?")
    (out / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    table(rows, out, meter.spent)
    print((out / "table.md").read_text(encoding="utf-8"))
    return 0


def _named(title: str, reply: str) -> bool:
    """Whether the reply names the card: its title, or a run of it, as the reply would shorten it."""
    core = re.sub(r"[【】《》「」\[\]()（）:：|·?？]", " ", title)
    parts = [p for p in core.split() if len(p) >= 2]
    return any(p.casefold() in reply.casefold() for p in parts)


def table(rows: list, out: Path, spent: float) -> None:
    cards = [r for r in rows if r["card"]]
    from mibo.library import item_id
    titles = {item_id(row[0]): row[3] for row in FINDS + SHOWN}
    stated, false = 0, []
    for r in rows:
        for ident, action, said in r["events"]:
            if action not in REACTIONS:
                continue
            # Allowed: the reaction the person stated on the find they named, or a turn-down of the card before.
            # A find with no feed source of its own takes "not this source" as a skip said of its source.
            as_said = action == "skip" and (said or {}).get("about") == "source" and "hide_source"
            told = (r.get("stated") and r["stated"][0] in (action, as_said)
                    and r["stated"][1] in titles.get(ident, ""))
            may = r.get("may") and action == r["may"][0] and ident == (r["before_card"] or {}).get("id")
            stated += bool(told)
            if not (told or may):
                false.append(f"turn {r['n']}: {action} {titles.get(ident, ident)}")
    want_stated = sum(1 for r in rows if r.get("stated"))
    misses = [r for r in rows if r.get("miss")]
    looked = [r for r in misses if any(c in SEARCH for c in r["calls"])
              and (not r["card"] or any('"url"' in a for a in r["args"]))]
    walks = sum(1 for r in rows for c, a in zip(r["calls"], r["args"]) if c == "bring_find" and '"later": true' in a)
    plain = [r for r in rows if r.get("plain")]
    whys = [r for r in rows if r.get("why")]
    vents = [r for r in rows if r.get("vent")]
    lines = [
        "| check | result |", "|---|---|",
        f"| cards on the subject (blind judge) | {sum(r.get('on_subject') == 'yes' for r in cards)}/{len(cards)} |",
        f"| reply names the card's title | {sum(bool(r.get('named')) for r in cards)}/{len(cards)} |",
        f"| false reactions in the feed | {len(false)} {'; '.join(false)} |",
        f"| stated reactions recorded | {stated}/{want_stated} |",
        f"| walks asked for without a yes | {walks} |",
        f"| library misses that looked it up (card only from a page read) | {len(looked)}/{len(misses)} |",
        f"| why answers give the recorded reason (judge) | {sum(r.get('why_true') == 'yes' for r in whys)}/{len(whys)} |",
        f"| plain question with no call | {sum(not r['calls'] for r in plain)}/{len(plain)} |",
        f"| vents with no unasked find (no card; judge) | "
        f"{sum(not r['card'] and r.get('pushed') == 'no' for r in vents)}/{len(vents)} |",
        f"| model calls in all | {sum(r['model_calls'] for r in rows)} over {len(rows)} turns |",
        f"| spent | ${spent:.4f} |", "",
        "| turn | said | card | calls | events |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['n']} | {r['say'][:30]} | {(r['card'] or {}).get('title') or ''} "
                     f"{('(' + r['on_subject'] + ')') if r.get('on_subject') else ''} | {', '.join(r['calls'])} | "
                     f"{', '.join(a for _, a, _ in r['events'])} |")
    (out / "table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run")
    go.add_argument("home")
    go.add_argument("--out", required=True)
    go.add_argument("--tree", default=str(HERE.parent))
    go.add_argument("--budget", type=float, default=0.08)
    go.add_argument("--port", type=int, default=59951)
    go.add_argument("--price-in", type=float, default=0.0507)
    go.add_argument("--price-out", type=float, default=0.2028)
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))

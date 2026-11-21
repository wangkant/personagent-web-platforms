"""Measure recall of past chats (pastchats.py) on a home, through the page's own API.

    python tools/recall_chats.py seed <home>      past chats (2 to 25 days old) into a home that is not running
    python tools/recall_chats.py run <home>       ask the probes on the running home, each in a new chat
    python tools/recall_chats.py forget <home>    on the running home: a forget and a deleted chat leave the search

`seed` writes eight page chats with their chat records (runtime/mibo.db) into a fresh home that already has its .env.
`run` asks five questions about them, each in a chat of its own that is deleted after, and prints one JSON object:
for each probe the reply, whether it names the answer, and the steps under it; then how many replies named it.
`forget` checks with search_past_chats's own search, read from the database: a fact is found, "忘掉" it in the
chat, not found; a line of a chat is found, the chat deleted, not found, its record gone.
"""
import json
import sys
import time
import urllib.request
from datetime import timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.stdout.reconfigure(encoding="utf-8")
DAY = 86400.0
# (page chat id, conversation, title, days ago, what was said: p for the person, m for the character)
CHATS = [
    ("1a2b3c4d5e6f7a8b", "c0000000000000000000000a", "读书", 25, [
        ("p", "最近在读石黑一雄的《克拉拉与太阳》，读到一半了"), ("m", "这本很温柔，克拉拉的视角特别动人。读完告诉我你的感受呀。"),
        ("p", "下个月可能去成都玩几天"), ("m", "成都好呀，记得吃火锅。")]),
    ("main", "c0000000000000000000000b", "", 18, [
        ("p", "今天下午去面试了，是一家叫蓝鲸科技的公司，面的是数据分析岗"), ("m", "辛苦啦！面试感觉怎么样？"),
        ("p", "面试官问了很多SQL窗口函数的问题，有点紧张"), ("m", "能答出来就很棒了，等好消息。")]),
    ("2b3c4d5e6f7a8b9c", "c0000000000000000000000c", "周末吃什么", 12, [
        ("p", "昨晚去吃了一家云南菜，店名叫野生菌子，汽锅鸡特别好喝"), ("m", "汽锅鸡！听着就暖和，下次带我云吃一口。")]),
    ("3c4d5e6f7a8b9c0d", "c0000000000000000000000d", "日常", 9, [
        ("p", "今天带我家猫团子去打疫苗了，它一路都在叫"), ("m", "团子辛苦了，打完记得给它加个罐头。")]),
    ("5e6f7a8b9c0d1e2f", "c0000000000000000000000e", "学吉他", 20, [
        ("p", "我开始学吉他了，老师让我先练《小星星》"), ("m", "从《小星星》开始很稳，手指疼是正常的。")]),
    *((f"4d5e6f7a8b9c0d{d:02d}", f"c00000000000000000000f{d:02d}", f"闲聊 {d}", d, []) for d in (3, 5, 7)),
]
# Small talk around each chat's words, so a search has lines to tell apart.
FILLER = [("p", "今天好累啊"), ("m", "辛苦啦，早点休息。"), ("p", "晚饭吃了面条"), ("m", "听起来不错。"),
          ("p", "下雨了，不想出门"), ("m", "那就好好放松一下吧。"), ("p", "刚开完会"), ("m", "嗯嗯，我在呢。")]
# Each asked in a new chat; what the reply must name.
PROBES = [("上个月我跟你说我在读的那本书叫什么来着？", "克拉拉"), ("我之前去面试的那家公司叫什么？", "蓝鲸"),
          ("上上周我说的那家云南菜馆叫啥？", "野生菌子"), ("我学吉他的时候老师让我先练哪首歌？", "小星星"),
          ("我家猫叫什么你还记得吗", "团子")]


def seed(home: Path) -> None:
    from mibo import chatlog, db, welcome
    runtime = home / "runtime"
    log, store = chatlog.ChatLog(runtime, 30), db.at(runtime)
    now = time.time()
    for ident, conversation, title, days, talk in CHATS:
        rows, t = [], now - days * DAY
        for n, (who, text) in enumerate(FILLER + talk, 1):
            t += 60
            rows.append({"seq": n, "t": t, "user_id": "" if who == "m" else f"browser:{conversation}",
                         "name": "Mibo" if who == "m" else "林晓", "text": text, **({"self": True} if who == "m" else {})})
        log.take(f"private:browser:{conversation}", rows)
        chat = welcome.PageChat(store, ident)
        chat.conversation = conversation
        if ident != "main":
            chat.title, chat.at = title, time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t))
        chat.save()
    print(f"seeded {len(CHATS)} chats into {home}")


def api(home: Path):
    url = urlsplit(json.loads((home / "runtime/desktop/welcome.json").read_text(encoding="utf-8"))["url"])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(path, body=None):
        req = urllib.request.Request(f"{url.scheme}://{url.netloc}/api/{path}",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET",
                                     headers={"Content-Type": "application/json",
                                              "x-mibo-setup-token": url.fragment.partition("token=")[2]})
        with opener.open(req, timeout=300) as r:
            return json.loads(r.read().decode())
    return call


def run(home: Path) -> None:
    call, rows = api(home), []
    for question, answer in PROBES:
        chat = call("chats", {})["id"]
        out = call("chat", {"text": question, "chat": chat})
        call("chats/delete", {"id": chat})
        said = [m for m in out.get("messages", []) if m.get("role") == "assistant"]
        reply = " / ".join(m.get("text") or "" for m in said)
        rows.append({"q": question, "named": answer in reply, "reply": reply[:300],
                     "steps": [{k: s.get(k) for k in ("tool", "what", "out")} for m in said for s in m.get("trace") or []]})
    print(json.dumps({"probes": rows, "named": sum(r["named"] for r in rows), "of": len(rows)}, ensure_ascii=False, indent=1))


def found(home: Path, query: str, when: str = "") -> str:
    """What search_past_chats hands the model now, read from the home's database as the tool reads it."""
    from mibo import chatlog, pastchats
    log = chatlog.ChatLog(home / "runtime", 30)
    at = {"log": log, "convs": sorted(log.private_chats("browser")), "here": "", "in_view": set(),
          "tz": timezone(timedelta(hours=-8)), "lang": "zh", "runtime": home / "runtime"}
    return pastchats.search(at, query, when)[0]


def forget(home: Path) -> None:
    from mibo import chatlog
    call, log = api(home), chatlog.ChatLog(home / "runtime", 30)
    checks = {"fact found before": "野生菌子" in found(home, "云南菜")}
    call("chat", {"text": "忘掉野生菌子"})
    checks["fact found after 忘掉"] = "野生菌子" in found(home, "云南菜")
    checks["fact left in any record"] = any("野生菌子" in r["text"] for c in log.private_chats("browser")
                                            for r in log.read(c))
    checks["chat line found before"] = "小星星" in found(home, "吉他")
    call("chats/delete", {"id": "5e6f7a8b9c0d1e2f"})
    checks["chat line found after delete"] = "小星星" in found(home, "吉他")
    checks["deleted chat's record kept"] = bool(log.read("private:browser:c0000000000000000000000e"))
    print(json.dumps(checks, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    {"seed": seed, "run": run, "forget": forget}[sys.argv[1]](Path(sys.argv[2]))

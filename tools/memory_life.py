"""The memory acceptance life, on a real model: one person's 60 turns over five
days (23 to 29 Nov 2026), a restart, 20 recall questions in new chats on 30 Nov
and 4 Dec, then a score of the store against the 33 things worth keeping and of
the 20 answers. The gate: at least 30 of 33 kept, at least 17 of 20 answered,
and nothing wrong in either.

    python tools/memory_life.py <workdir> --env <file> [--model M] [--port P]
    python tools/memory_life.py <workdir> --stub [--port P]     (the stand-in model on 127.0.0.1:59299)
    python tools/memory_life.py <workdir> --replay <done workdir> [--env <file> [--real-recall]]
    python tools/memory_life.py <workdir> --score               (score again, no model call)
    add --life <file> for another life written the same way (tools/memory_life_move.py)

<file> holds LLM_API_KEY and LLM_BASE_URL; only those two lines are copied into
the fresh home's .env, and never printed. The home runs on its own movable clock
(a sitecustomize in <workdir>/site) and folds are started through a control
channel in that process. Every model call is recorded with its turn in
out/rec.jsonl. --replay answers each call from such a recording instead: with
no --env nothing is asked of a model (a check of the code that applies the
answers); with --env the folds are asked of the real model (about half a cent a
life) and, with --real-recall, the recall questions too.
Output in <workdir>/out: life.jsonl, recall.jsonl, rec.jsonl, folds.jsonl (each
fold's operations and what came of them), calls.jsonl (each real call's usage),
person_day*.json and score.json.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=-8))

DAYS = [
    ((2026, 11, 23, 20, 0), [
        "嗨，第一次正经跟你聊天哈。我叫林晓，你叫我小晓就行",
        "我在华东师大读研，研二，学统计的",
        "今天好累啊，下午一直在跑代码",
        "对了，下周三我有个统计推断的期末考试，好慌",
        "我住闵行，离学校近，就是周边吃的太少了",
        "家里有只橘猫，叫年糕，胖得要命，现在趴我键盘上",
        "哈哈哈它把我代码删了一行",
        "我是杭州人，我妈还在杭州，这周六她要来上海看我",
        "我弟在上高三，明年高考，我妈天天焦虑他",
        "我真的超讨厌香菜，今天食堂的汤里全是香菜，气死",
        "吃饭了没，你平时吃啥",
        "不说了，我去洗澡了，晚安"]),
    ((2026, 11, 24, 21, 0), [
        "我回来啦，今天导师又骂人了",
        "我导师姓周，对我们特别严，组会上一句话能把人说哭",
        "跟你说个事，我在准备申请博士，想申港大和新加坡国立",
        "文书还没写完，DDL是12月15号，愁死了",
        "然后两周后我要和大学同学去成都玩几天，算是给自己放个假",
        "去成都肯定要吃火锅，我超能吃辣的，湘菜川菜都爱",
        "今天同事给我带了芒果蛋糕，可惜我芒果过敏，便宜他们了",
        "最近一直在单曲循环陈绮贞的《旅行的意义》",
        "今天天气好冷，上海这几天湿冷湿冷的",
        "我最怕冷了，冬天基本不想出门",
        "我室友小雅老是半夜一两点打电话，我今天跟她吵了一架",
        "好烦，不想说了，睡了"]),
    ((2026, 11, 25, 22, 0), [
        "刚跑完步回来，我每周三晚上都会去操场跑5公里",
        "跟你说，周末我在一家咖啡店打工，做咖啡师",
        "不过我自己乳糖不耐受，喝不了牛奶，只能喝燕麦拿铁",
        "然后有个好消息！我拿到了字节数据分析实习的面试，下周二面",
        "前男友阿杰昨天又给我发消息了，问我最近怎么样，烦",
        "哎不想他了",
        "算了，把我说的那件事忘了吧",
        "最近晚上都一两点才睡，作息全乱了",
        "睡前一般刷做饭视频和猫咪视频，很解压",
        "恐怖片我是一点都不敢看，上次看完一周不敢关灯",
        "我跟你说个事，别记这个哈：我本科的时候挂过一门高数",
        "好了我去睡了"]),
    ((2026, 11, 27, 18, 30), [
        "今天下午拔了智齿，脸肿成包子了",
        "纠正一下，我那个考试不是周三，是周四，我看错了",
        "还有其实我上个月已经搬到徐汇了，不住闵行了，之前说错了",
        "徐汇吃的多多了，就是房租贵",
        "我和小雅和好了，她说以后去阳台打电话",
        "我最近在学日语，打算明年考N2",
        "我最喜欢的电影是《海街日记》，看了好几遍",
        "对了我生日是12月10号，快到了嘿嘿",
        "今天周五，好想喝奶茶",
        "你说周末干啥好呢",
        "我妈明天来，我得收拾屋子了",
        "拜拜，收拾去了"]),
    ((2026, 11, 29, 16, 0), [
        "我妈昨天来了，带了一堆杭州的东西，还有她自己腌的咸菜",
        "她今天下午回杭州了",
        "文书写了一半了，个人陈述那段好难写",
        "港大那边还要两封推荐信，我打算找周老师和本科的班主任",
        "嗯嗯",
        "年糕今天又吐毛球了，带它去看了兽医，说没事",
        "下周二面试我有点紧张，你觉得要准备啥",
        "统计考试复习了三章了，还剩两章",
        "今天下雨了",
        "我弟其实是上高二，不是高三，我前几天说错了",
        "晚上吃了湘菜馆，辣得爽",
        "好啦我去复习了，拜拜"]),
]
# The morning after each day: the fold has run by then.
NEXT = [(2026, 11, 24, 9, 0), (2026, 11, 25, 9, 0), (2026, 11, 26, 9, 0), (2026, 11, 28, 9, 0),
        (2026, 11, 30, 9, 0)]
# The clock before the first turn, and when the app starts again for the recall.
START = (2026, 11, 23, 19, 55)
RESTART = (2026, 11, 30, 19, 55)

# ((month, day, hour), chat, words, hit, wrong): a reply is a hit when `hit` matches it, wrong when `wrong` does.
NEG = r"(?<![不别別没沒非])"
NOT_TOLD = r"没(?:跟我)?(?:说|提|讲|告诉)|不知道|没听你|不清楚|没聊过|没有提"
PROBES = [
    ((11, 30, 20), "A", "我猫叫什么来着？", r"年糕", r"(?<!不是)狗"),
    ((11, 30, 20), "A", "周末吃啥好？给我推荐一下", r"辣|湘|川|火锅|麻辣",
     NEG + r"(?:芒果|香菜(?!.{0,3}(?:免|去掉|不要|别))|杨枝甘露|奶茶|牛奶)"),
    ((11, 30, 20), "A", "我现在住哪个区你还记得吗", r"徐汇", r"(?<!不住)(?<!搬离)(?<!从)闵行(?!.{0,8}(?:搬|以前|之前|不住))"),
    ((11, 30, 20), "A", "我考试是哪天来着？", r"12月3|12-03|12/3|周四|星期四|礼拜四|3号",
     r"(?<!记成)(?<!不是)(?<!以为)(?:周三|星期三)|12月2|11月25|25号|2号"),
    ((11, 30, 20), "A", "你知道我叫什么吗", r"林晓|小晓", None),
    ((11, 30, 20), "A", "今晚想看个电影，有啥推荐", r"《", NEG + r"(?:恐怖片|惊悚片|闪灵|招魂|釜山行|午夜凶铃|咒怨)"),
    ((11, 30, 20), "A", "我男朋友叫什么来着？", NOT_TOLD, r"阿杰"),
    ((11, 30, 20), "A", "我妈是哪里人你记得不", r"杭州", None),
    ((11, 30, 20), "A", "我之前是不是挂过科？", NOT_TOLD + r"|不记得", r"高数|数学"),
    ((11, 30, 20), "A", "帮我想想奶茶点什么", r"燕麦|乳糖|不耐|植物奶|椰奶|豆奶|零乳糖|果茶",
     NEG + r"(?:加奶|鲜奶|牛奶(?!.{0,3}(?:不|换))|芒果(?!.{0,6}(?:别|不|过敏|跳过))|杨枝甘露(?!.{0,4}(?:别|不)))"),
    ((12, 4, 9), "", "早呀", r"考", r"今天.{0,4}考试|明天.{0,4}考试|周末(?:快乐|愉快)"),
    ((12, 4, 19), "B", "我那个考试是哪天来着？", r"12月3|12-03|昨天|周四|星期四|3号",
     r"(?<!记成)(?<!不是)(?<!以为)周三|12月2|明天考|今天考|11月25"),
    ((12, 4, 19), "B", "我的申请DDL是什么时候", r"12月15|12-15|15号", None),
    ((12, 4, 19), "B", "我室友叫啥，我们现在关系咋样", r"小雅.*(?:和好|阳台)|(?:和好|阳台).*小雅",
     r"(?<!已经)(?<!之前)(?<!以前)还在(?:吵|冷战)|关系(?:不好|很僵)"),
    ((12, 4, 19), "B", "我生日快到了吧？还有几天", r"12月10|10号|6天|六天", r"[57]天|[五七]天"),
    ((12, 4, 19), "B", "我什么时候去成都来着", r"下周|12月|这周末|两周|上旬|几天后|月初|明天|5号",
     r"已经去过|去过了|11月\d*号?(?:去|出发|走)"),
    ((12, 4, 19), "B", "我在学什么外语", r"日语", None),
    ((12, 4, 19), "B", "我养的狗叫什么", r"猫", r"狗(?:狗)?叫年糕|年糕是.{0,3}狗"),
    ((12, 4, 19), "B", "我导师姓啥", r"周", None),
    # Wrong: the ex they asked to forget, or a friend made up; naming 小雅 as someone they did mention is not.
    ((12, 4, 19), "B", "我最好的朋友叫什么", NOT_TOLD, r"阿杰|(?:最好的朋友|好朋友|闺蜜)(?:就|应该|大概)?(?:是|叫)小雅"),
]
# A state long over said as now (the tooth out on 11-27), in a reply on another subject.
STALE = (r"智齿|拔牙|牙.{0,4}(?:肿|疼)|脸肿|消肿", r"牙|智齿")
NEVER_KEPT = ("阿杰", "高数", "挂科")
GATE = (30, 17)


def store_view(p: dict):
    """(F, P, plan_dues) over a store: F(*patterns) when every pattern is in an active fact; P(pattern, due,
    open_only) when a plan matches; plan_dues(pattern) the days of the open plans that match."""
    facts = [f["text"] for f in p["facts"] if f.get("status", "active") == "active"]
    plans = p["plans"]

    def F(*pats) -> bool:
        return all(any(re.search(pat, t) for t in facts) for pat in pats)

    def P(pat, due=None, open_only=False) -> bool:
        return any(re.search(pat, x["text"] + str(x.get("said") or "")) and (due is None or x.get("due") == due)
                   and (not open_only or x.get("status", "open") in ("open", "asked")) for x in plans)

    def plan_dues(pat) -> set:
        return {x.get("due") for x in plans if re.search(pat, x["text"]) and x.get("status", "open") in ("open", "asked")}
    return facts, F, P, plan_dues


def checks(p: dict) -> list:
    """(what, kept, wrong) for each of the 33 things."""
    facts, F, P, plan_dues = store_view(p)
    names = p.get("name") or {}
    exam = r"考试|统计推断|期末"
    trip = [x for x in p["plans"] if "成都" in x["text"]]
    return [
        ("name and nickname", (names.get("name") == "林晓" or F("林晓")) and (names.get("call_me") == "小晓" or F("小晓")), False),
        ("ECNU, second year, statistics", F(r"华东师|华师大"), False),
        ("lives in Xuhui", F("徐汇"), any(re.search(r"闵行", t) and not re.search(r"不住|搬|以前|之前|原来", t) for t in facts)),
        ("cat 年糕", F("年糕"), False),
        ("from Hangzhou, mother there", F("杭州"), False),
        ("hates coriander", F("香菜"), False),
        ("likes spicy, Hunan and Sichuan food", F(r"辣|湘菜|川菜"), False),
        ("afraid of horror films", F("恐怖"), False),
        ("lactose intolerant", F("乳糖"), False),
        ("runs 5 km on Wednesdays", F(r"跑"), False),
        ("birthday 12-10", P("生日", "2026-12-10") or F(r"生日.*(?:12月10|12-10)|(?:12月10|12-10).*生日"), False),
        ("mother's visit Sat 11-28", P(r"妈", "2026-11-28") or F(r"11-28.*妈|妈.*11-28|妈.*(?:昨天|周六)来"), False),
        ("wisdom tooth out 11-27", F("智齿") or P("智齿"), False),
        ("PhD at HKU and NUS, deadline 12-15", F(r"港大|新加坡|新国立") and (P(r"", "2026-12-15") or F(r"12月15|12-15")), False),
        ("ByteDance interview 12-01", P(r"面试|字节", "2026-12-01"), bool(plan_dues(r"面试|字节") - {"2026-12-01"})),
        ("advisor Zhou, strict", F(r"周(?:老师|导)|导师.{0,4}周|姓周"), False),
        ("recommenders Zhou and the homeroom teacher", F(r"推荐信|班主任"), False),
        ("mango allergy", F("芒果"), False),
        ("hates the cold", F("冷"), False),
        ("learning Japanese for N2", F(r"日语|N2"), False),
        ("favourite film 海街日记", F("海街"), False),
        ("weekend barista job", F("咖啡"), False),
        ("the mother's pickles", F(r"咸菜|腌"), False),
        ("the cat's hairball and the vet", F(r"毛球|兽医"), False),
        ("the exam is kept", P(exam) or F(exam), False),
        ("trip to Chengdu, about 12-05..12-11, open", any(x.get("status", "open") in ("open", "asked")
                                                          and "2026-12-01" <= x.get("due", "") <= "2026-12-11" for x in trip),
         any(x.get("status") in ("done", "cancelled") for x in trip) and not any(
             x.get("status", "open") in ("open", "asked") for x in trip)),
        ("brother in 高二", F("高二"), any("高三" in t and not re.search(r"不是高三|不是.{0,3}高三", t) for t in facts)),
        ("roommate 小雅", F("小雅"), False),
        ("the exam moved to 12-03", P(exam, "2026-12-03", open_only=True),
         bool(plan_dues(exam) - {"2026-12-03"}) or any(re.search(r"(?<!不是)周三.*考|考.*(?<!不是)周三", t) for t in facts)),
        ("made up with 小雅", F(r"和好|阳台"), False),
        ("陈绮贞", F(r"陈绮贞|旅行的意义"), False),
        ("cooking and cat videos before sleep", F(r"做饭视频|猫咪视频|猫视频"), False),
        ("sleeps at 1-2 am", F(r"(?:一两点|1-2点|凌晨).{0,4}睡|睡.{0,6}(?:一两点|1-2点|凌晨)"), False),
    ]


SITE = r'''"""Measurement shim: a movable clock (FAKECLOCK_FILE holds an offset in seconds), a control
channel (MEMCTL_DIR) that runs scripts on the character's loop, and a recorder of each model call
that can answer from an earlier recording instead (MEMREC_FROM; MEMREC_REAL names what still goes
to the model: "memory" for the folds, "probe" for the recall questions)."""
import os, threading, time as _time
_clock = os.environ.get("FAKECLOCK_FILE")
if _clock:
    import datetime as _dt
    _real_time = _time.time
    _real_sleep = _time.sleep
    _real_lt = _time.localtime
    _off = [0.0]
    def _read():
        try:
            _off[0] = float(open(_clock).read().strip() or 0)
        except Exception:
            pass
    _read()
    def _watch():
        while True:
            _real_sleep(0.2)
            _read()
    threading.Thread(target=_watch, daemon=True).start()
    def _now():
        return _real_time() + _off[0]
    _time.time = _now
    _time.time_ns = lambda: int(_now() * 1e9)
    _time.localtime = lambda secs=None: _real_lt(_now() if secs is None else secs)
    _RD, _RDate = _dt.datetime, _dt.date
    class FakeDatetime(_RD):
        @classmethod
        def now(cls, tz=None):
            return _RD.fromtimestamp(_now(), tz)
        @classmethod
        def today(cls):
            return _RD.fromtimestamp(_now())
    class FakeDate(_RDate):
        @classmethod
        def today(cls):
            return _RDate.fromtimestamp(_now())
    _dt.datetime = FakeDatetime
    _dt.date = FakeDate
_ctl = os.environ.get("MEMCTL_DIR")
if _ctl:
    import asyncio, gc, json, traceback
    from pathlib import Path
    ctl = Path(_ctl)
    def _find():
        agent = loop = None
        for o in gc.get_objects():
            try:
                c = type(o)
                if agent is None and c.__name__ == "Agent" and c.__module__ == "mibo.agent":
                    agent = o
                elif loop is None and isinstance(o, asyncio.AbstractEventLoop) and o.is_running():
                    loop = o
            except Exception:
                pass
        return agent, loop
    def _write(name, row):
        with (ctl / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    def _tag():
        try:
            return (ctl / "tag.txt").read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    def _serve():
        agent = loop = None
        while agent is None or loop is None:
            _real_sleep(0.5) if _clock else _time.sleep(0.5)
            agent, loop = _find()
        import openai
        from mibo import llm
        real_count = agent._count_usage
        def count(model, body):
            _write("calls.jsonl", {"t": _time.time(), "purpose": llm._call_purpose.get(), "model": model,
                                   "usage": body.get("usage")})
            return real_count(model, body)
        agent._count_usage = count
        # The recording to answer from: (tag, purpose) -> answers in their order.
        book = {}
        source = os.environ.get("MEMREC_FROM")
        real = set(filter(None, os.environ.get("MEMREC_REAL", "").split(",")))
        if source:
            for line in Path(source).read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row.get("status") == 200:
                    book.setdefault((row["tag"], row["purpose"]), []).append(row["body"])
        used = {}
        real_post = agent._post_chat
        async def post(url, key, payload, *, timeout, retries=0):
            tag, why = _tag(), llm._call_purpose.get()
            asked = payload.get("messages") or []
            last = next((m.get("content") for m in reversed(asked) if m.get("role") == "user"), "")
            row = {"tag": tag, "purpose": why, "model": payload.get("model"), "tools": bool(payload.get("tools")),
                   "last": str(last)[:400]}
            if why == "memory":
                row["messages"] = asked
            answers = book.get((tag, why))
            if source and why not in real and not (tag.startswith("probe") and "probe" in real) and answers:
                n = used.get((tag, why), 0)
                used[(tag, why)] = n + 1
                # Past the recorded ones: the last again.
                body = answers[min(n, len(answers) - 1)]
                _write("rec.jsonl", dict(row, status=200, body=body, replayed=True))
                return body
            try:
                body = await real_post(url, key, payload, timeout=timeout, retries=retries)
            except openai.APIStatusError as error:
                _write("rec.jsonl", dict(row, status=error.status_code, body=None))
                raise
            _write("rec.jsonl", dict(row, status=200, body=body))
            return body
        agent._post_chat = post
        real_apply = agent._apply_person_fold
        def apply(store, plan, lines, facts, plans, now):
            asked = json.loads(json.dumps(plan, ensure_ascii=False, default=str))
            done = real_apply(store, plan, lines, facts, plans, now)
            _write("folds.jsonl", {"tag": _tag(), "plan": asked, "done": dict(done)})
            return done
        agent._apply_person_fold = apply
        (ctl / "ready").write_text("1")
        while True:
            (_real_sleep if _clock else _time.sleep)(0.3)
            for cmd in sorted(ctl.glob("*.cmd.py")):
                code = cmd.read_text(encoding="utf-8")
                cmd.unlink()
                ns = {"agent": agent, "asyncio": asyncio, "json": json, "time": _time}
                src = "async def __c():\n" + "\n".join("    " + l for l in code.splitlines()) + "\n"
                try:
                    exec(src, ns)
                    out = json.dumps(asyncio.run_coroutine_threadsafe(ns["__c"](), loop).result(900),
                                     ensure_ascii=False, default=str)
                except Exception:
                    out = "ERROR\n" + traceback.format_exc()
                Path(str(cmd)[:-7] + ".out").write_text(out, encoding="utf-8")
    threading.Thread(target=_serve, daemon=True).start()
'''

FOLD = '''
for _ in range(1800):
    if "person" not in agent._folding:
        break
    await asyncio.sleep(0.5)
started = agent._maybe_fold_person(time.time())
await asyncio.sleep(1)
for _ in range(1800):
    if "person" not in agent._folding:
        break
    await asyncio.sleep(0.5)
return {"started": started, "fold": agent.person_store.data["fold"]}
'''


class Life:
    def __init__(self, work: Path, port: int):
        self.work = work
        self.home, self.ctl, self.out = work / "home", work / "ctl", work / "out"
        self.clock = work / "clock.txt"
        self.port = port
        self.proc = None
        self.env = {}

    def set_clock(self, y, mo, d, h, mi=0):
        target = datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp()
        off = target - time.time()
        cur = float(self.clock.read_text() or 0) if self.clock.exists() else None
        # Never backwards: a slow call let the fake clock run past the target.
        if cur is not None and off < cur:
            off = cur
        self.clock.write_text(str(off))
        time.sleep(0.6)

    def tag(self, words: str):
        (self.ctl / "tag.txt").write_text(words, encoding="utf-8")

    def now(self) -> float:
        return time.time() + float(self.clock.read_text() or 0)

    def api(self, method, path, body=None, timeout=600):
        url = json.loads((self.home / "runtime/desktop/welcome.json").read_text(encoding="utf-8"))["url"]
        a = urlsplit(url)
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(f"{a.scheme}://{a.netloc}/api/{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json",
                                              "x-mibo-setup-token": a.fragment.partition("token=")[2]})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()[:600]

    def start(self):
        env = dict(os.environ, AGENT_HOME=str(self.home), PYTHONPATH=str(self.work / "site") + os.pathsep + str(REPO),
                   FAKECLOCK_FILE=str(self.clock), MEMCTL_DIR=str(self.ctl), PYTHONUTF8="1", **self.env)
        (self.ctl / "ready").unlink(missing_ok=True)
        (self.home / "runtime/desktop/welcome.json").unlink(missing_ok=True)
        log = open(self.out / "app.log", "ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; from mibo import welcome; "
             f"sys.exit(welcome.main(['--no-browser','--port','{self.port}']))"],
            env=env, cwd=str(REPO), stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(180):
            time.sleep(1)
            try:
                if self.api("GET", "chats", timeout=5)[0] == 200:
                    break
            except (OSError, ValueError):
                pass
        # The character is built on the page's start; the first message would build it too.
        for _ in range(240):
            if (self.ctl / "ready").exists():
                break
            time.sleep(0.5)
        print("started, control", (self.ctl / "ready").exists(), flush=True)

    def stop(self):
        try:
            self.api("POST", "exit", {}, timeout=30)
        except (OSError, ValueError):
            pass
        if self.proc is not None:
            try:
                self.proc.wait(60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        time.sleep(2)

    def run(self, code: str, timeout=1200):
        n = len(list(self.ctl.glob("c*.out"))) + len(list(self.ctl.glob("c*.cmd.py"))) + 1
        (self.ctl / f"c{n:03d}.cmd.py").write_text(code, encoding="utf-8")
        out = self.ctl / f"c{n:03d}.out"
        for _ in range(timeout * 2):
            if out.exists():
                time.sleep(0.2)
                text = out.read_text(encoding="utf-8")
                try:
                    return json.loads(text)
                except ValueError:
                    return text
            time.sleep(0.5)
        return "TIMEOUT"

    def chat(self, text, chat="", log=None, tag=""):
        body = {"text": text, **({"chat": chat} if chat else {})}
        t = time.time()
        st, res = self.api("POST", "chat", body)
        reply = res
        if isinstance(res, dict):
            said = [m.get("text") for m in res.get("messages") or [] if m.get("role") == "assistant"]
            reply = res.get("reply") or "\n".join(s for s in said if s)
        rec = {"at": datetime.fromtimestamp(self.now(), TZ).strftime("%m-%d %a %H:%M"), "chat": chat or "main",
               "said": text, "status": st, "reply": reply, "secs": round(time.time() - t, 1), "tag": tag}
        if log:
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(rec["at"], "|", text, "=>", str(reply)[:160].replace("\n", " / "), flush=True)
        return rec

    def person(self) -> dict:
        path = self.home / "runtime/person.json"
        # Nothing kept yet: no file.
        empty = {"name": {}, "facts": [], "plans": [], "talk": {}}
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else empty


def make_home(work: Path, port: int, model: str, env_file: Path | None):
    home = work / "home"
    if work.exists():
        shutil.rmtree(work)
    for d in (home, work / "ctl", work / "out", work / "site"):
        d.mkdir(parents=True)
    (work / "site/sitecustomize.py").write_text(SITE, encoding="utf-8")
    lines = [f"PAGE_PORT={port}", f"SERVER_PORT={port + 1}", "UPDATE_CHECK_ENABLED=false", "ROAM_ENABLED=false",
             "AGENT_LANG=zh", "PERSONA_TZ_OFFSET_HOURS=-8", "PERSONA_NAME=Mibo",
             f"LLM_MODEL={model}", f"LLM_JUDGE_MODEL={model}"]
    if env_file is None:
        lines += ["LLM_BASE_URL=http://127.0.0.1:59299/v1", "LLM_API_KEY=stub"]
    else:
        lines += [l for l in env_file.read_text(encoding="utf-8").splitlines()
                  if l.split("=", 1)[0].strip() in ("LLM_API_KEY", "LLM_BASE_URL")]
    (home / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def live(life_spec, work: Path, port: int, env: dict):
    life = Life(work, port)
    life.env = env
    out = life.out
    life.set_clock(*life_spec.START)
    life.start()
    try:
        for d, ((y, mo, dd, h, mi), turns) in enumerate(life_spec.DAYS):
            for k, text in enumerate(turns):
                m = mi + 3 * k
                life.set_clock(y, mo, dd, h + m // 60, m % 60)
                life.tag(f"d{d + 1}.{k + 1}")
                life.chat(text, log=out / "life.jsonl")
            life.tag(f"fold{d + 1}")
            life.set_clock(*life_spec.NEXT[d])
            print("fold:", json.dumps(life.run(FOLD), ensure_ascii=False)[:300], flush=True)
            (out / f"person_day{d + 1}.json").write_text(json.dumps(life.person(), ensure_ascii=False, indent=1),
                                                        encoding="utf-8")
        # Recall after a restart, in new chats: what the store carries, not what the chat shows.
        life.stop()
        life.set_clock(*life_spec.RESTART)
        life.start()
        chats = {}
        year = life_spec.RESTART[0]
        for k, ((mo, dd, h), chat, words, _hit, _wrong) in enumerate(life_spec.PROBES):
            life.set_clock(year, mo, dd, h, 2 * (k % 10))
            life.tag(f"probe{k}")
            if chat and chat not in chats:
                chats[chat] = life.api("POST", "chats", {})[1]["id"]
            life.chat(words, chat=chats.get(chat, ""), log=out / "recall.jsonl", tag=str(k))
        (out / "person_end.json").write_text(json.dumps(life.person(), ensure_ascii=False, indent=1),
                                             encoding="utf-8")
    finally:
        life.stop()
        for name in ("calls.jsonl", "rec.jsonl", "folds.jsonl"):
            if (work / "ctl" / name).exists():
                shutil.copy(work / "ctl" / name, out / name)


# ---- the score ---------------------------------------------------------------

def score_store(life_spec, p: dict) -> dict:
    items = life_spec.checks(p)
    right = sum(ok and not bad for _n, ok, bad in items)
    wrong = sum(bool(bad) for _n, _ok, bad in items)
    facts = store_view(p)[0]
    blob = json.dumps({k: p[k] for k in ("facts", "plans", "talk", "name")}, ensure_ascii=False)
    stale = [t for t in facts if re.search(r"今天|昨天|前天|今晚|昨晚|刚才|today|yesterday", t)]
    return {"right": right, "wrong": wrong, "missing": len(items) - right - wrong, "of": len(items),
            "items": {n: ("wrong" if bad else "right" if ok else "missing") for n, ok, bad in items},
            "never_kept_held": [w for w in life_spec.NEVER_KEPT if w in blob],
            "active_facts": len(facts), "day_words_in_active_facts": stale,
            "written_by": {s: sum(1 for f in p["facts"] if f.get("src") == s) for s in
                           sorted({str(f.get("src")) for f in p["facts"]})}}


def score_recall(life_spec, rows: list[dict]) -> dict:
    out = {"hit": 0, "wrong": 0, "miss": 0, "of": len(life_spec.PROBES), "items": [], "stale_brought_up": {}}
    stale, about = life_spec.STALE
    for rec in rows:
        k = int(rec["tag"])
        when, _chat, words, hit, wrong = life_spec.PROBES[k]
        # "12 月 15 号" as "12月15号": some models space digits from the words around them.
        reply = re.sub(r"(?<=\d) +(?=[一-鿿])|(?<=[一-鿿]) +(?=\d)", "", str(rec.get("reply") or ""))
        if stale and re.search(stale, reply) and not re.search(about, words):
            day = f"{when[0]:02}-{when[1]:02}"
            out["stale_brought_up"][day] = out["stale_brought_up"].get(day, 0) + 1
        # Not a match its sentence steers away from before it ("别往恐怖片上点", "别点杨枝甘露，有芒果").
        bad = bool(wrong) and any(not re.search(r"[不别別没沒非免忌]", re.split(r"[。！？!?\n]", reply[:m.start()])[-1])
                                  for m in re.finditer(wrong, reply))
        good = bool(re.search(hit, reply)) and not bad
        verdict = "wrong" if bad else "hit" if good else "miss"
        out[verdict] += 1
        out["items"].append({"k": k, "said": words, "verdict": verdict, "reply": reply[:300]})
    return out


def score(life_spec, work: Path) -> dict:
    out = work / "out"
    last = out / f"person_day{len(life_spec.DAYS)}.json"
    result = {"store": score_store(life_spec, json.loads(last.read_text(encoding="utf-8")))}
    if (out / "recall.jsonl").exists():
        rows = [json.loads(l) for l in (out / "recall.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        result["recall"] = score_recall(life_spec, rows)
        s, r = result["store"], result["recall"]
        result["gate"] = s["right"] >= life_spec.GATE[0] and r["hit"] >= life_spec.GATE[1] \
            and s["wrong"] == 0 and r["wrong"] == 0
    if (out / "calls.jsonl").exists():
        calls = [json.loads(l) for l in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        by = {}
        for c in calls:
            u = c.get("usage") or {}
            row = by.setdefault(c["purpose"], {"calls": 0, "cost": 0.0, "completion": 0})
            row["calls"] += 1
            row["cost"] = round(row["cost"] + float(u.get("cost") or 0), 5)
            row["completion"] += int(u.get("completion_tokens") or 0)
        result["calls"] = by
        result["cost"] = round(sum(r["cost"] for r in by.values()), 4)
    (out / "score.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def this_life():
    names = ("DAYS", "NEXT", "START", "RESTART", "PROBES", "STALE", "NEVER_KEPT", "GATE", "checks")
    return SimpleNamespace(**{n: globals()[n] for n in names})


def load_life(path: Path | None):
    if path is None:
        return this_life()
    spec = importlib.util.spec_from_file_location("life_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("work", type=Path)
    ap.add_argument("--env", type=Path)
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--replay", type=Path, help="a done workdir whose out/rec.jsonl answers the calls")
    ap.add_argument("--real-recall", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--life", type=Path)
    ap.add_argument("--model", default="deepseek/deepseek-v4.1-flash")
    ap.add_argument("--port", type=int, default=59600)
    args = ap.parse_args()
    work = args.work.resolve()
    life_spec = load_life(args.life)
    if not args.score:
        if not (args.env or args.stub or args.replay):
            ap.error("--env <file>, --stub or --replay <workdir>")
        env = {}
        if args.replay:
            # Read before make_home clears a workdir that is the same one.
            recording = (args.replay.resolve() / "out/rec.jsonl").read_text(encoding="utf-8")
        make_home(work, args.port, "stub" if args.stub else args.model, args.env)
        if args.replay:
            (work / "from.jsonl").write_text(recording, encoding="utf-8")
            env["MEMREC_FROM"] = str(work / "from.jsonl")
            env["MEMREC_REAL"] = ",".join(["memory"] * bool(args.env) + ["probe"] * args.real_recall)
        live(life_spec, work, args.port, env)
    result = score(life_spec, work)
    print(json.dumps({k: v for k, v in result.items() if k != "recall"}, ensure_ascii=False, indent=1))
    if "recall" in result:
        r = result["recall"]
        print("recall: hit", r["hit"], "wrong", r["wrong"], "miss", r["miss"], "stale", r["stale_brought_up"],
              "gate", result["gate"])
        for item in r["items"]:
            print(f" {item['k']:2} {item['verdict']:5} {item['said']} => {item['reply'][:120]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

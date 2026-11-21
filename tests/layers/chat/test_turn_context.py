"""The chat's context in the model's window (context design, stage S5): the turn context, filled memory first, then
the day, the finds and the steps of the latest replies, never takes more than its share C, at a small window or a
large one; a model whose window cannot hold the chat's system prompt and tools is refused before any request, with
its own error; the chat's gauge is the last request's prompt tokens over the input limit; 立即归纳 makes the older
turns one checkpoint now, and 清空上下文 leaves the model none of the earlier turns while the page and the memory keep
them, after a restart too."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from harness.app import App
from harness.model import call, fail, say, tokens
from harness.page import measured
from harness.turn import run_turn
from mibo import context, pastchats, person, turnctx
from mibo.dm import chat_key
from mibo.prompt import _CHECKPOINT_OPEN, _TURN_CONTEXT_OPEN

# Things the person told it, each a line of the memory block.
FACTS = ["在一家做芯片设计的公司做验证工程师，每天要跑很多仿真", "室友小雅是学医的，经常上夜班", "喜欢吃重庆小面，尤其是豌杂面",
         "对花生过敏，吃到会起疹子", "不喜欢太吵的电子音乐", "妈妈在成都开一家小茶馆", "养了一只叫年糕的橘猫，三岁",
         "周末在学日语，准备明年七月考N2", "最好的朋友阿杰在深圳做游戏策划", "最喜欢的电影是《海上钢琴师》，看过五遍",
         "住在杭州西湖区，离公司骑车二十分钟", "每周三晚上去打羽毛球", "爸爸退休了，在老家种橘子", "没有车，出门靠地铁和共享单车",
         "不吃香菜", "早上一定要喝一杯燕麦拿铁", "存钱想明年秋天去冰岛看极光", "领导姓陈，人不错但开会很长",
         "最近在读刘慈欣的短篇集", "经常一点以后才睡，想改掉", "玩塞尔达玩了两百小时", "表妹今年高考，想学建筑",
         "老家在四川眉山", "阳台上养了三盆多肉和一棵柠檬树"]
SUMMARY = say("Threads:\n- 公园散步与银杏\nTheir words:\n- (none)\nMood: 轻松")
ZH = "今天下午我们去公园散步，路上看到很多银杏叶已经黄了，风一吹就落下来，像下了一场金色的雨，真好看。"


def _setup(app, window: int) -> None:
    """The stand-in's window, measured as the start's check keeps it; the memory fold fails once and waits, and
    any checkpoint the chat writes on its own is one summary."""
    app.model.window = window
    measured(app, context=window)
    app.model.script(fail(500), purpose="fold")
    app.model.script(*[SUMMARY] * 10, purpose="compact")


def _agent(app):
    return app.workspace.character.agent


def _pkey(app) -> str:
    return chat_key(app.workspace._main.conversation)[1]


def _turn_context(body: dict) -> str:
    content = body["messages"][-1]["content"]
    return content[content.index(_TURN_CONTEXT_OPEN):]


def _looked_up(n: int) -> list[dict]:
    """A reply that searched the past chats six times first: six long steps behind it."""
    return [[call(pastchats.NAME, query=f"第{n}次：{ZH}{k}") for k in range(6)], say(f"找到了，第{n}次。")]


@pytest.mark.parametrize("window", [16384, 65536, 1048576])
def test_the_turn_context_stays_within_its_share_memory_first_at_any_window(app, turn, window):
    _setup(app, window)
    for fact in FACTS:
        assert app.post("/api/person/add", {"text": fact}).status_code == 200
    for n in range(6):
        assert turn(f"帮我找找第{n}次聊公园的记录", *_looked_up(n)).status == 200
    done = turn("周末去公园看银杏吧，顺便吃碗小面", say("好呀。"))
    (body,) = done.asked()
    agent = _agent(app)
    plan = agent._turn_plan(agent._dm_system(), tools=True)[0]
    held = _turn_context(body)
    # The lists modules add (the skills that can be loaded, the open places) come last and keep their own room.
    added = "".join(f"\n\n{line}" for line in turnctx.lines("", _pkey(app)))
    assert added and held.endswith(added)
    assert agent._chat_requests[_pkey(app)]["ratio"] * context.text_tokens(held.removesuffix(added)) <= plan.turn
    # The memory block is filled first: at the smallest window it still holds what they told it.
    assert "<about_them>" in held and sum(fact in held for fact in FACTS) >= 3
    # With room left, the steps of the newest replies follow.
    if window == 1048576:
        assert "第5次" in held.partition("[Steps behind")[2]


def test_a_model_whose_window_cannot_hold_the_setup_is_refused_before_any_request(app, turn):
    _setup(app, 8192)
    done = run_turn(app, "你好")
    assert done.status == 400 and done.asked() == []
    (said,) = [m for m in app.get("/api/chat/history").json()["messages"] if m["role"] == "user"]
    assert said["failed"] == "model_window_small"


def test_the_gauge_is_the_last_requests_prompt_tokens_over_the_input_limit(app, turn):
    _setup(app, 65536)
    assert app.get("/api/chat/context").json()["known"] is False
    turn("你好", say("你好呀。"))
    (body,) = turn(f"讲讲{ZH}", say("好的。")).asked()
    gauge = app.get("/api/chat/context").json()
    agent = _agent(app)
    limit = agent._turn_plan(agent._dm_system(), tools=True)[0].limit
    assert gauge["tokens"] == tokens(body) and gauge["limit"] == limit and not gauge["estimated"]
    assert gauge["percent"] == round(100 * tokens(body) / limit)
    assert set(gauge["parts"]) == {"system", "tools", "memory", "summary", "recent"}
    assert abs(sum(gauge["parts"].values()) - gauge["tokens"]) <= len(gauge["parts"])


def test_compact_now_makes_the_older_turns_one_checkpoint_that_the_next_request_holds(app, turn):
    _setup(app, 65536)
    turn("第一句话", say("第一句回答"))
    assert app.post("/api/chat/compact").json()["compacted"] is False
    for n in range(4):
        turn(f"第{n}句：{ZH}", say(f"回答{n}"))
    before = app.get("/api/chat/context").json()
    assert app.post("/api/chat/compact").json() == {"compacted": True}
    summary = app.get("/api/chat/history").json()["summary"]
    assert "公园散步" in summary["text"]
    after = app.get("/api/chat/context").json()
    assert after["estimated"] and after["tokens"] < before["tokens"] and after["parts"]["summary"] > 0
    (body,) = turn("还记得吗", say("记得。")).asked()
    sent = json.dumps(body["messages"], ensure_ascii=False)
    assert body["messages"][1]["content"].startswith(_CHECKPOINT_OPEN) and "公园散步" in sent
    assert "第一句话" not in sent and f"第0句：{ZH}" not in sent and f"第3句：{ZH}" in sent


def test_clearing_the_context_leaves_the_model_no_earlier_turns_and_the_page_and_memory_keep_them(app, turn, model,
                                                                                                  tmp_path):
    _setup(app, 65536)
    app.post("/api/person/add", {"text": "养了一只叫年糕的橘猫"})
    for n in range(3):
        turn(f"第{n}句：{ZH}", say(f"回答{n}"))
    cleared = app.post("/api/chat/clear").json()
    assert cleared["ok"] and cleared["summary"]["cleared"] and not cleared["summary"]["text"]
    assert app.get("/api/chat/context").json()["held"] is False
    # The page keeps its messages' times to the second: the next one comes a second after the mark.
    time.sleep(1.1)
    (body,) = turn("我们刚才聊了什么", say("从这里开始。")).asked()
    sent = json.dumps(body["messages"][1:], ensure_ascii=False).partition(_TURN_CONTEXT_OPEN)[0]
    assert len(body["messages"]) == 2 and "第0句" not in sent and _CHECKPOINT_OPEN not in sent
    shown = [m["text"] for m in app.get("/api/chat/history").json()["messages"]]
    assert f"第0句：{ZH}" in shown and "年糕" in _turn_context(body)
    # The mark holds when the chat comes back from the page's copy.
    app.close()
    for found in app.home.rglob("recent.json"):
        found.unlink()
    again = App(Path(os.environ["AGENT_HOME"]), model, PERSONA_BROWSER_PATH=str(tmp_path / "no-browser"),
                LLM_MAX_RETRIES="0").open()
    try:
        (body,) = run_turn(again, "继续", say("好。")).asked()
        sent = json.dumps(body["messages"], ensure_ascii=False)
        assert "第0句" not in sent and "我们刚才聊了什么" in sent
    finally:
        again.close()


@pytest.mark.parametrize("budget", [40, 300, 600])
def test_the_memory_block_takes_no_more_than_the_tokens_it_is_given(tmp_path, budget):
    store = person.Person(tmp_path / "person.json")
    now = time.time()
    for n, fact in enumerate(FACTS):
        store.put_fact("life", f"slot{n}", fact, now - n * 3600, src="page")
    block, placed = person.recall_block(store, "周末去哪儿玩", now, None, "zh", budget)
    assert context.text_tokens(block) <= budget
    # Too little for even the block's frame: none at all; else what is placed is what the block says.
    kept = {row["id"]: row["text"] for row in store.facts()}
    assert bool(placed) == (budget >= 300) and all(kept[ident] in block for ident in placed)

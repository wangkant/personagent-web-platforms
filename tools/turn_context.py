"""The main chat's turn context against its share of the window (context design, stage S5), measured offline.

    python tools/turn_context.py <home copy> [--heavy] [--say 你好]

Builds the next request of the home's busiest page chat with Mibo's own prompt builder at windows of 16k, 64k and
1M (the CONTEXT_WINDOW setting), and prints, per window, the turn context's tokens (the model's ratio times the
estimate) against its share C, with the parts in it: the memory block, the day, the finds and the steps. `--heavy`
gives the person 25 facts and every reply of the chat twelve long steps, the most a reply keeps. Run it on a COPY:
the agent may move the home's old files into its database.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WINDOWS = (16384, 65536, 1048576)
MODEL = "deepseek/deepseek-v4.1-flash"
# The parts of the turn context, by the tags that open and close them.
PARTS = {"memory": ("<about_them>", "</about_them>"), "day": ("<your_day>", "</your_day>"),
         "reads": ("<what_you_read_on_this>", "</what_you_read_on_this>"),
         "finds": ("<finds_in_this_chat>", "</finds_in_this_chat>"),
         "steps": ("[Steps behind your recent replies", "\n\n")}
FACTS = [("life", "work", "在一家做芯片设计的公司做验证工程师，每天要跑很多仿真"), ("people", "roommate", "室友小雅，学医的，经常夜班"),
         ("like", "food", "喜欢吃重庆小面，尤其是豌杂面"), ("health", "allergy", "对花生过敏，吃到会起疹子"),
         ("dislike", "music", "不喜欢太吵的电子音乐"), ("form", "talk", "希望说话简短一点，别太多客套"),
         ("people", "mother", "妈妈在成都开一家小茶馆"), ("life", "pet", "养了一只叫年糕的橘猫，三岁"),
         ("life", "study", "周末在学日语，准备明年七月考N2"), ("people", "friend", "最好的朋友阿杰在深圳做游戏策划"),
         ("like", "film", "最喜欢的电影是《海上钢琴师》，看过五遍"), ("life", "home", "住在杭州西湖区，离公司骑车二十分钟"),
         ("like", "sport", "每周三晚上去打羽毛球"), ("people", "father", "爸爸退休了，在老家种橘子"),
         ("life", "car", "没有车，出门基本靠地铁和共享单车"), ("dislike", "food", "不吃香菜"),
         ("like", "coffee", "早上一定要喝一杯燕麦拿铁"), ("life", "plan", "存钱想明年秋天去冰岛看极光"),
         ("people", "boss", "领导姓陈，人不错但开会很长"), ("like", "book", "最近在读刘慈欣的短篇集"),
         ("life", "sleep", "经常一点以后才睡，想改掉"), ("like", "game", "玩塞尔达玩了两百小时"),
         ("people", "cousin", "表妹今年高考，想学建筑"), ("life", "hometown", "老家在四川眉山"),
         ("like", "plant", "阳台上养了三盆多肉和一棵柠檬树")]


def _heavy(agent, turns: list[dict]) -> None:
    now = time.time()
    for n, (kind, slot, text) in enumerate(FACTS):
        agent.person_store.put_fact(kind, slot, text, now - n * 3600, src="page")
    step = "search 关于银杏叶什么时候变黄、在哪里看最好的游记和攻略，以及周末公园人多不多的讨论帖" * 2
    for turn in turns:
        if turn["role"] == "assistant":
            turn["steps"] = [step[:160]] * 12


def _part(text: str, opening: str, closing: str) -> str:
    start = text.find(opening)
    if start < 0:
        return ""
    end = text.find(closing, start + len(opening))
    return text[start:end + len(closing)] if end >= 0 else text[start:]


def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument("home")
    args.add_argument("--heavy", action="store_true")
    args.add_argument("--say", default="你好，周末我们去公园看银杏吧，你还记得上次说的那家面馆吗")
    a = args.parse_args()
    home = Path(a.home).resolve()
    os.environ["AGENT_HOME"] = str(home)
    os.environ.setdefault("AGENT_LANG", "zh")
    from mibo import channels, context, turnctx
    from mibo.agent import Agent
    agent = Agent(api_key="x", base_url="https://openrouter.ai/api/v1", model=MODEL, llm_dm_model=MODEL,
                  agent_lang="zh", memory_file=str(home / "runtime" / "memory.json"), embedding_model="")
    user = max(agent.dm_history, key=lambda u: len(agent.dm_history[u]))
    turns = [dict(t) for t in agent.dm_history[user]]
    if a.heavy:
        _heavy(agent, turns)
    history = turns + [{"role": "user", "content": a.say, "name": "you", "t": time.time(), "seq": 99999}]
    url = agent._endpoint_for(MODEL)[0]
    ratio = context.ratio(agent, url, MODEL)
    rows = []
    for window in WINDOWS:
        agent.context_window = window
        # As a turn of the chat builds it (dm._chat_dm).
        plan, _ = agent._turn_plan(agent._dm_system(), tools=True)
        prompt = agent._build_dm_prompt(list(history), room=plan.turn / ratio, pkey=channels.dm_routing_key(user))
        # The lists modules add (skills, open places) come last and are not charged to the share.
        added = "".join(f"\n\n{line}" for line in turnctx.lines("", channels.dm_routing_key(user)))
        tokens = round(ratio * context.text_tokens(prompt.turn_context.removesuffix(added)))
        row = {"window": window, "C": plan.turn, "turn_context": tokens, "within": tokens <= plan.turn,
               "chars": len(prompt.turn_context)}
        for name, (opening, closing) in PARTS.items():
            text = _part(prompt.turn_context, opening, closing)
            row[name] = {"chars": len(text), "tokens": round(ratio * context.text_tokens(text)),
                         **({"replies": text.count("\n- ")} if name == "steps" else {})}
        rows.append(row)
    print(json.dumps({"home": str(home), "heavy": a.heavy, "ratio": round(ratio, 3), "turns": len(turns),
                      "facts": len(agent.person_store.facts())}, ensure_ascii=False))
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()

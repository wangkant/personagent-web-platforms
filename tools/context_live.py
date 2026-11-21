"""The chat's context on a real model (context design, stage S5), through the app's own page routes: the gauge
against the prompt tokens the model reported for the last reply request, and the turn context against its share C.

    python tools/context_live.py <home> <recorder out.jsonl> [--port 59995]

The home's .env names the model, the key and CONTEXT_WINDOW; its LLM_BASE_URL points at `context_check.py record`,
which keeps each request with the usage its answer reported and stops at its budget. The app is served in this
process on 127.0.0.1:<port>. The script gives the person a few things to remember, talks four turns, asks for
立即归纳, talks once, clears the context and talks once more; after each step it prints one line: the gauge, the
reported prompt tokens, the turn context against C, and whether the last request holds a checkpoint or the first
turn. Run it on a scratch home: the app writes to it."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FACTS = ["养了一只叫年糕的橘猫，三岁", "对花生过敏，吃到会起疹子", "周末在学日语，准备明年七月考N2", "住在杭州西湖区，离公司骑车二十分钟",
         "最喜欢的电影是《海上钢琴师》", "不吃香菜"]
SAY = ["跟你说个事，今天带年糕去打了疫苗，它一路都在叫。", "晚上想做个简单的晚饭，你觉得做什么好？我不太想出门。",
       "最近日语学到动词变形了，好难记，你有什么记的窍门吗？", "周末天气好像不错，我想去西湖边走走。"]
AFTER = ["我们刚才都聊了些什么？", "嗨，重新开始吧：你好。"]


def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument("home")
    args.add_argument("record")
    args.add_argument("--port", type=int, default=59995)
    a = args.parse_args()
    home, record = Path(a.home).resolve(), Path(a.record)
    os.environ["AGENT_HOME"] = str(home)
    import httpx
    import uvicorn

    from mibo import context, paths, welcome
    from mibo.dm import chat_key
    from mibo.prompt import _CHECKPOINT_OPEN, _TURN_CONTEXT_OPEN

    paths.use_home(home)
    token = secrets.token_hex(16)
    workspace = welcome.Workspace(home)
    server = uvicorn.Server(uvicorn.Config(welcome.create_app(home, token, workspace=workspace),
                                           host=welcome.PAGE_HOST, port=a.port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    base = f"http://{welcome.PAGE_HOST}:{a.port}"

    def call(method: str, path: str, payload: dict | None = None) -> dict:
        answer = httpx.request(method, base + path, headers={welcome.TOKEN_HEADER: token}, timeout=300,
                               trust_env=False, **({"json": payload} if payload is not None else {}))
        return answer.json()

    def last_reply() -> dict:
        """The last reply request the recorder kept: the person's turn carries the turn context."""
        for line in reversed(record.read_text(encoding="utf-8").splitlines()):
            row = json.loads(line)
            messages = row["body"].get("messages") or []
            users = [m for m in messages if m.get("role") == "user" and isinstance(m.get("content"), str)]
            if (row["status"] == 200 and users and _TURN_CONTEXT_OPEN in users[-1]["content"]
                    and not str(messages[-1].get("content") or "").startswith(context.COMPACT)):
                return row
        raise SystemExit("no reply request recorded")

    def look(step: str) -> None:
        gauge = call("GET", "/api/chat/context")
        row = last_reply()
        agent = workspace.character.agent
        pkey = chat_key(workspace._main.conversation)[1]
        plan = agent._turn_plan(agent._dm_system(), tools=True)[0]
        users = [m["content"] for m in row["body"]["messages"] if m.get("role") == "user"]
        held = users[-1][users[-1].index(_TURN_CONTEXT_OPEN):]
        ratio = agent._chat_requests[pkey]["ratio"]
        sent = "".join(str(m.get("content") or "") for m in row["body"]["messages"])
        print(json.dumps({
            "step": step, "reported": row["usage"].get("prompt_tokens"), "gauge": gauge.get("tokens"),
            "limit": gauge.get("limit"), "percent": gauge.get("percent"), "estimated": gauge.get("estimated"),
            "gauge_is_reported": gauge.get("tokens") == row["usage"].get("prompt_tokens"),
            "percent_is_share": gauge.get("percent") == round(100 * gauge["tokens"] / gauge["limit"]),
            "parts": gauge.get("parts"), "C": plan.turn, "turn_context": round(ratio * context.text_tokens(held)),
            "within_C": ratio * context.text_tokens(held) <= plan.turn, "memory_lines": sum(f in held for f in FACTS),
            "checkpoint": _CHECKPOINT_OPEN in sent, "first_turn_held": SAY[0] in sent, "spent": row["spent"],
        }, ensure_ascii=False), flush=True)

    try:
        call("POST", "/api/look/toured", {})
        for fact in FACTS:
            call("POST", "/api/person/add", {"text": fact})
        for n, words in enumerate(SAY):
            said = call("POST", "/api/chat", {"text": words, "chat": "main", "attachments": []})
            if said.get("error"):
                raise SystemExit(f"turn {n}: {said['error']}")
            look(f"turn {n}")
        print(json.dumps({"step": "compact", **call("POST", "/api/chat/compact", {"chat": "main"})}), flush=True)
        print(json.dumps({"step": "gauge after compact", **call("GET", "/api/chat/context")}, ensure_ascii=False))
        call("POST", "/api/chat", {"text": AFTER[0], "chat": "main", "attachments": []})
        look("after compact")
        cleared = call("POST", "/api/chat/clear", {"chat": "main"})
        print(json.dumps({"step": "clear", "ok": cleared.get("ok")}), flush=True)
        time.sleep(1.1)
        call("POST", "/api/chat", {"text": AFTER[1], "chat": "main", "attachments": []})
        look("after clear")
    finally:
        server.should_exit = True
        thread.join(30)


if __name__ == "__main__":
    main()

"""The chat's checkpoint (dm.py) is written by the model from turns that hold page and file text, and it comes back
in every later request as the app's own record. A line of it that reads as an instruction (the memory gate's
families, tests/data/attacks/memory.jsonl) is not kept, nor a card number, nor an invisible character; the facts
around them are."""
from __future__ import annotations

import time

from harness import attacks
from harness.model import fail, say
from harness.page import measured
from mibo.prompt import _CHECKPOINT_OPEN

ZH = "今天下午我们去公园散步，路上看到很多银杏叶已经黄了，风一吹就落下来，像下了一场金色的雨，真好看。"
ROWS = [param.values[0] for param in attacks.load("memory")]
# One instruction of each family, as a page the person shared might carry it.
ATTACKS = list({row["family"]: row["text"] for row in reversed(ROWS) if row["refused"]}.values())
FACT = next(row["text"] for row in ROWS if row["family"] == "fact")
CARD = "4111 1111 1111 1111"
# A zero-width space.
HIDDEN = chr(0x200B)


def test_a_checkpoint_keeps_no_instruction_card_number_or_hidden_text_from_what_it_condensed(app, turn):
    app.model.window = 16384
    measured(app, context=16384)
    app.model.script(fail(500), purpose="fold")
    summary = "\n".join(["Threads:", f"- 公园散步{HIDDEN}与银杏", "Things named:", f"- {FACT}", f"- card {CARD}",
                         "Their words:", *(f"- {attack}" for attack in ATTACKS), "Mood: 轻松"])
    app.model.script(*[say(summary)] * 6, purpose="compact")
    for n in range(10):
        turn(f"第{n}句：{ZH * 12}", say(f"第{n}句：{ZH * 12}"))
    for _ in range(500):
        kept = app.get("/api/chat/history").json().get("summary")
        if kept:
            break
        time.sleep(0.02)
    after = turn("还记得吗？", say("记得。")).asked()[0]
    shown = next(m["content"] for m in after["messages"] if str(m.get("content") or "").startswith(_CHECKPOINT_OPEN))
    for text in (kept["text"], shown):
        assert FACT in text and "公园散步" in text
        assert CARD not in text and HIDDEN not in text
        assert not [attack for attack in ATTACKS if attack in text]

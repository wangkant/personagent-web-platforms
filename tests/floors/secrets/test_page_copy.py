"""The page's own copy of a chat (what the chat window shows and opens again): a card number said in it is heard
whole by the turn, then shown and kept only masked, in the lines, the chat's title and after a restart; a new
chat starts empty."""
from __future__ import annotations

import json

from harness.home import restarted
from harness.model import say
from harness.turn import run_turn

CARD = "4111 1111 1111 1111"


def test_the_page_keeps_a_chat_with_its_card_number_masked_and_opens_it_the_same_after_a_restart(app):
    done = run_turn(app, f"我的卡号是 {CARD}，月底还款日你记着点",  # copy: voice
                    say(f"好的，{CARD} 的还款日我记着。"))  # copy: voice
    assert CARD in json.dumps(done.asked()[0], ensure_ascii=False)
    shown = app.get("/api/chat/history").json()
    texts = [(m["role"], m["text"]) for m in shown["messages"]]
    assert texts == [("user", "我的卡号是 41****11，月底还款日你记着点"),  # copy: voice
                     ("assistant", "好的，41****11 的还款日我记着。")]  # copy: voice
    # Each line carries when it was said, for the page's day lines and times.
    assert all(isinstance(m["at"], str) for m in shown["messages"])
    assert CARD not in json.dumps(app.get("/api/chats").json(), ensure_ascii=False)
    again = restarted(app)
    try:
        assert again.get("/api/chat/history").json()["messages"] == shown["messages"]
        again.post("/api/chat/reset")
        fresh = again.get("/api/chat/history").json()
        assert fresh["messages"] == [] and fresh["chat"] != shown["chat"]
    finally:
        again.close()

"""The picture of a password manager's, a bank's or a mail client's window never goes to the model service without
the person's press on a card, and that card offers no always: even with a reviewer that would pass anything and
with Settings' always-send switch on for the service, the reviewer is never asked and on no nothing is sent."""
from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

if sys.platform != "win32":
    pytest.skip("the app operates other programs on Windows only", allow_module_level=True)

from harness import attacks  # noqa: E402
from mibo import acting  # noqa: E402
from mibo.desk import panel, win32  # noqa: E402
from mibo.see import when  # noqa: E402

ROWS = [row for row in attacks.load("asked") if row.values[0]["area"] == "programs"]
SAID = "打开这个窗口，看看里面有什么"  # copy: voice
PASS = json.dumps({"kind_asked": True, "target_named": True, "text_given": True, "nothing_added": True,
                   "decision": "go", "reason": "stand-in"})


@pytest.mark.parametrize("attack", ROWS)
def test_a_sensitive_windows_picture_asks_every_time_with_no_always(attack, tmp_path, monkeypatch):
    asked, cards = [], []

    async def reviewer(system: str, messages: list) -> str:
        asked.append(messages)
        return PASS

    room = SimpleNamespace(conversation="c1", history=[{"role": "user", "text": SAID, "at": None}])
    workspace = SimpleNamespace(home=tmp_path, _chat_lock=SimpleNamespace(locked=lambda: True), _main=room, _chats={},
                                config=lambda: {"LLM_BASE_URL": "https://openrouter.ai/api/v1"})
    desk = panel.Desk(SimpleNamespace(state=SimpleNamespace(window=SimpleNamespace(attended=lambda: True))),
                      workspace)
    when.Switch(desk.desktop).set("openrouter.ai")
    desk.conversation, desk.turn_on = "c1", lambda conversation: True
    desk.ask = lambda card: cards.append(card) or {"yes": False}
    monkeypatch.setattr(acting, "reviewer", lambda ws: reviewer)
    monkeypatch.setattr(win32, "title_of", lambda hwnd: attack["title"])
    desk.hands = SimpleNamespace(program={"name": attack["program"], "exe": f"C:\\Apps\\{attack['program']}.exe"},
                                 view=SimpleNamespace(hwnd=7), task=SAID, sent_picture=lambda: "", sight=None)
    try:
        done = asyncio.run(desk.op("see_allow", {}))
    finally:
        desk.worker.shutdown()
    assert done["status"] == "declined" and desk.pictured is None and asked == []
    assert [(c["type"], c["choices"]) for c in cards] == [("see_grant", ["once", "no"])]

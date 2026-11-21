"""What the person types in the chat beside the page is a turn of that page's chat (loop.py, search.py,
panels/browser.py): the model reads it with the conversation before it and how the last errand ended, so 做啊 goes
on with the errand it answers rather than starting one whose task is 做啊, and a plain yes answers the card the last
errand waits on. The app runs with the scripted model; the pane's run is a stand-in that records each errand begun
and each card answered, and ends as the test says."""
from __future__ import annotations

import json
from types import SimpleNamespace

from harness.model import say, text

ASKED = "我登录上去了看一下我的作者数据吧"  # copy: voice
REPLY_TASK = "回复阿杰的留言：谢谢支持"  # copy: voice


class Run:
    """The pane's run as the app's routes reach it: each errand begun, each card answered from the chat, and the
    first look of each errand taken from `looks`; with none left an errand finds no page."""

    def __init__(self, *looks: dict):
        self.looks, self.begun, self.kept = list(looks), [], []
        self.hands = self.left = self.card = self.answered = None

    async def begin(self, task, start, lang="zh", go_on=False):
        self.begun.append((task, go_on))
        if not self.looks:
            return {"status": "no_page", "line": "[failed] stand-in: no page"}
        look = self.looks.pop(0)

        async def first(most):
            if look["status"] == "waiting":
                self.left = {"id": "c1", "kind": look["kind"], "said": look["result"]}
            return look
        self.hands = SimpleNamespace(look=first)
        return {"status": "ok"}

    def keep(self, yes, said=False):
        self.kept.append((yes, said))
        self.left = None
        return True

    async def end(self, status, note):
        self.hands = None


def beside(app, words: str, *script, going_on: bool = False) -> str:
    app.model.script(*script)
    sent = app.post("/api/chat", {"text": words, "chat": "main", "choices": ["page_on" if going_on else "page"]},
                    timeout=120)
    app.model.check()
    assert app.model.left() == 0, "a scripted round was never asked for"
    return str(sent.json().get("reply") or "")


def with_run(app, run: Run) -> Run:
    app.app.state.window = SimpleNamespace(pane=None)
    app.app.state.operating = run
    return run


def test_do_it_after_an_errand_nobody_confirmed_goes_on_with_that_errand(app):
    run = with_run(app, Run({"status": "declined", "kind": "press",
                             "held": "[declined] nobody answered the card for press (我的); nothing was pressed"}))
    beside(app, ASKED)
    beside(app, "做啊", text(json.dumps({"task": ASKED, "go_on": True, "card": ""}, ensure_ascii=False)))  # copy: voice
    read = app.model.asked("reply")[-1]["body"]["messages"][-1]["content"]
    assert ASKED in read and "declined" in read and "做啊" in read  # copy: voice
    assert run.begun == [(ASKED, False), (ASKED, True)]


def test_a_plain_yes_in_the_chat_answers_the_card_the_errand_waits_on_and_goes_on(app):
    run = with_run(app, Run({"status": "waiting", "kind": "comment", "result": "回复阿杰"}))  # copy: voice
    told = beside(app, REPLY_TASK)
    assert "回复阿杰" in told  # copy: voice
    beside(app, "好的，发吧", text(json.dumps({"task": REPLY_TASK, "go_on": True, "card": "yes"}, ensure_ascii=False)),  # copy: voice
           going_on=True)
    assert run.kept == [(True, True)]
    assert run.begun == [(REPLY_TASK, False), (REPLY_TASK, True)]


def test_a_new_request_beside_the_page_is_its_own_errand(app):
    run = with_run(app, Run())
    app.model.script(say("好的。"))  # copy: voice
    app.post("/api/chat", {"text": "你好", "chat": "main"}, timeout=120)  # copy: voice
    beside(app, "打开推荐页", text(json.dumps({"task": "打开推荐页", "go_on": False, "card": ""},  # copy: voice
                                             ensure_ascii=False)))
    assert run.begun == [("打开推荐页", False)] and run.kept == []  # copy: voice

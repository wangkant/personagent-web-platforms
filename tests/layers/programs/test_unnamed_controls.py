"""A program window whose controls are partly nameless (mibo/desk) is still worked by its lines: a nameless control
is said by where it is, how it looks and the text beside it; the errand does not give up, ask or send elsewhere
while such a control is untried; a send addressed in other words than the task's is held once; the model may name
a nameless control by its id with any words, and is told when it names a plain text. (2026-11-21: in Grok Bot the
main agent was a black round picture with no name; the errand found nothing by name and said there was none.)"""
from __future__ import annotations

import asyncio
import json
import re
import sys

import pytest
from PIL import Image, ImageDraw

from mibo.desk import operate, shape

TASK = "给主agent发一个hello"  # copy: voice
AVATAR = '[1] group (no name: 57x56, round, black, a picture; "Lumen" under it) @top-left'


# --- how a nameless control is said

def test_a_nameless_control_is_said_by_its_shape_colour_place_and_the_text_beside_it():
    picture = Image.new("RGB", (480, 340), (236, 236, 236))
    draw = ImageDraw.Draw(picture)
    draw.ellipse((20, 20, 76, 76), fill=(10, 10, 10))
    draw.rectangle((200, 20, 256, 76), fill=(10, 10, 10))
    disc, square = (20, 20, 57, 57), (200, 20, 57, 57)
    assert "round" in shape.looks(picture, disc, (0, 0)) and "black" in shape.looks(picture, disc, (0, 0))
    assert "round" not in shape.looks(picture, square, (0, 0))
    assert shape.place(disc, (0, 0, 480, 340)) == "top left"
    beside = shape.label(disc, [("Lumen", (22, 82, 50, 14)), ("Chats", (300, 300, 40, 14))])
    assert "Lumen" in beside and "under" in beside and "Chats" not in beside


# --- the errand over a stand-in chat program

class Chat:
    """A chat program as the loop sees it through operate.Remote: a nameless round picture that opens the main
    agent's chat, one named chat (shown first), a message box and Send. `log` is what each chat received."""

    def __init__(self, allow: str = "ok"):
        self.chat, self.box, self.log, self.allow, self.asked = "Daily notes", "", [], allow, []
        self.nodes = {"1": ("group", ""), "2": ("listitem", "Daily notes"), "3": ("textbox", "Message"),
                      "4": ("button", "Send")}

    def state(self) -> str:
        head = "Main agent" if self.chat == "main" else self.chat
        return "\n".join([AVATAR, "Lumen", '[2] listitem "Daily notes" @left', head,
                          '[3] textbox "Message"' +(f' = "{self.box}"' if self.box else "") + " @bottom",
                          '[4] button "Send" @bottom-right'])

    async def begin(self, task, program, sight="", also=()):
        return {"status": "ok", "program": "Lumen", "line": "[ok] using Lumen"}

    async def look(self) -> dict:
        return {"status": "ok", "state": self.state(), "controls": 4, "program": "Lumen", "named": 3, "drawn": 0.0,
                "unnamed": [AVATAR], "others": []}

    def _post(self, op: str, timeout: float, **fields) -> dict:
        self.asked.append(op)
        if op == "see_allow":
            return {"status": self.allow}
        return {"status": "ok", "picture": "AAAA"} if op == "picture" else {"status": "ok"}

    async def step(self, n, note):
        return None

    async def end(self, status, note):
        return None

    def act(self, a: dict) -> dict:
        ident = str(a.get("id") or next((i for i, (_r, n) in self.nodes.items() if n and n == a.get("name")), ""))
        if ident not in self.nodes:
            return {"status": "failed", "line": f"[failed] no control is named {a.get('name')!r}"}
        role, name = self.nodes[ident]
        if a["do"] in ("set", "type") and role == "textbox":
            self.box = str(a.get("text") or "")
            return {"status": "ok", "proved": True, "line": f"[ok] set [{ident}] = {self.box!r}, read back"}
        if ident == "4":
            self.log.append((self.chat, self.box))
            self.box = ""
            return {"status": "ok", "proved": True, "line": "[ok] press [4] (send, done once); the field is empty"}
        self.chat = "main" if ident == "1" else name
        return {"status": "ok", "line": f"[ok] press [{ident}] {role} -> the chat shown is {self.chat}"}

    async def batch(self, actions: list[dict], look: bool = True) -> dict:
        results = [self.act(a) for a in actions]
        return {"status": "ok", "results": results, "look": await self.look()}


def reply(*actions: dict) -> str:
    return json.dumps({"note": "", "actions": list(actions)}, ensure_ascii=False)


SEND_MAIN = ({"do": "press", "id": 1}, {"do": "set", "name": "Message", "text": "hello"},
             {"do": "press", "name": "Send", "to": "主agent"})


class Model:
    """A scripted model: `first` until the loop says something with `cue`, then `then`; done once a send went."""

    def __init__(self, first: tuple, then: tuple = SEND_MAIN, cue: str = "[check]"):
        self.first, self.then, self.cue, self.seen = first, then, cue, []

    async def __call__(self, system: str, messages: list) -> str:
        content = messages[-1]["content"]
        self.seen.append(content)
        text = content if isinstance(content, str) else " ".join(p.get("text", "") for p in content)
        if "the field is empty" in text.rsplit("The window now:", 1)[0]:
            return reply({"do": "done", "success": True, "result": "hello 已发出"})
        return reply(*(self.then if self.cue in text else self.first))


def errand(model, chat: Chat, eyes: str = "") -> dict:
    return asyncio.run(operate.run(model, chat, TASK, "Lumen", lang="zh", eyes=eyes))


def test_asking_while_a_nameless_control_is_untried_comes_back_once_and_the_errand_goes_on():
    chat = Chat()
    outcome = errand(Model(({"do": "ask_person", "question": "没有名为主agent的聊天"},)), chat)
    assert outcome["status"] == "done" and chat.log == [("main", "hello")]


def test_asked_again_the_question_goes_to_the_person_with_what_the_window_shows():
    ask = ({"do": "ask_person", "question": "没有名为主agent的聊天"},)
    outcome = errand(Model(ask, then=ask), Chat())
    assert outcome["status"] == "asked" and outcome["seen"] == [AVATAR]


def test_a_send_addressed_in_other_words_than_the_tasks_is_held_once_and_nothing_goes_there():
    chat = Chat()
    elsewhere = ({"do": "set", "name": "Message", "text": "hello"}, {"do": "press", "name": "Send", "to": "Daily notes"})
    outcome = errand(Model(elsewhere), chat)
    assert outcome["status"] == "done" and chat.log == [("main", "hello")]


def test_a_control_named_by_its_whole_line_of_the_look_is_the_control_of_that_id():
    chat = Chat()
    by_line = ({"do": "press", "name": AVATAR}, {"do": "set", "name": "Message", "text": "hello"},
               {"do": "press", "name": "Send", "to": "主agent"})
    outcome = errand(Model(by_line, then=by_line, cue="never said"), chat)
    assert outcome["status"] == "done" and chat.log == [("main", "hello")]


@pytest.mark.parametrize("allow,pictured", [("ok", True), ("declined", False)])
def test_the_windows_picture_goes_beside_its_lines_only_on_the_persons_yes(allow, pictured):
    chat, model = Chat(allow), Model(SEND_MAIN)
    outcome = errand(model, chat, eyes="act")
    assert outcome["status"] == "done" and chat.log == [("main", "hello")]
    assert chat.asked.count("see_allow") == 1
    sent = [part for content in model.seen if isinstance(content, list) for part in content]
    assert any(part.get("type") == "image_url" for part in sent) is pictured


# --- naming a nameless control to the hands (Windows only: the hands load the system's window calls)

class View:
    """A stand-in for uia.View after a look of the chat program: the avatar with no name, a text, a button."""
    hwnd, seen, serial, menus, unnamed = 7, (), 1, {}, [AVATAR]

    def __init__(self):
        from mibo.desk import uia

        def node(role, name):
            return uia.Node(role=role, name=name, cls="", parent=None, top=7, patterns=("invoke",), enabled=True,
                            cells=(), auto="", el=None, secret=False, rect=(0, 0, 57, 56), readonly=False)
        self.nodes = {1: node("group", ""), 4: node("button", "Save draft")}
        self.words = {7: ["Lumen", "Main agent"]}
        self.pressed: list[int] = []

    def describe(self, i: int) -> str:
        return f"[{i}] {self.nodes[i].role}"

    def live(self, i: int) -> int:
        return i

    def node(self, i: int):
        return self.nodes[i]

    def texts(self, top: int) -> str:
        return "Lumen Main agent"

    def invoke(self, i: int) -> str:
        self.pressed.append(i)
        return f"[ok] press [{i}] (pressed once)"


@pytest.fixture
def hand(tmp_path, monkeypatch):
    if sys.platform != "win32":
        pytest.skip("the app operates other programs on Windows only")
    from mibo.desk import hands, win32
    monkeypatch.setattr(win32, "title_of", lambda hwnd: "Lumen")
    one = hands.Hands(tmp_path, ask=lambda card: {"yes": False})
    one.task, one.view, one.program = TASK, View(), {"name": "Lumen", "exe": "C:\\Apps\\Lumen.exe"}
    return one


def test_a_nameless_control_is_pressed_by_its_id_whatever_words_come_with_it(hand):
    done = hand.act({"do": "press", "id": 1, "name": "Lumen"})
    assert done["status"] == "ok" and hand.view.pressed == [1]


def test_a_named_control_given_another_name_with_its_id_is_not_pressed(hand):
    done = hand.act({"do": "press", "id": 4, "name": "Send"})
    assert done["status"] == "failed" and hand.view.pressed == []


def test_pressing_a_text_the_window_shows_says_it_is_text_and_presses_nothing(hand):
    done = hand.act({"do": "press", "name": "Main agent"})
    assert done["status"] == "failed" and hand.view.pressed == []
    assert re.search(r"\btext\b", done["line"]) and "no control is named" not in done["line"]

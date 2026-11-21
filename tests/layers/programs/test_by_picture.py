"""Another program worked by its picture (mibo/see, mibo/desk) asks as little as a window read by names: the window
the person named goes to the model service they chose on the reviewer's pass, a password manager's, a bank's or a
mail client's window always asks with no always, typing into the window is free and a password is never typed, and
Enter goes to the reviewer with the text it would send, a card saying why only when it does not pass. (2026-11-21:
the card for Grok Bot's pictures came although the person had named it, and typing and Enter were to ask each time.)"""
from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

if sys.platform != "win32":
    pytest.skip("the app operates other programs on Windows only", allow_module_level=True)

from mibo import acting, review  # noqa: E402
from mibo.desk import panel, win32  # noqa: E402
from mibo.see import act, frame, profile  # noqa: E402

ASKED = "帮我打开桌面上的grok bot并给主agent发一个hello"  # copy: voice


def answer(decision: str, reason: str = "stand-in") -> str:
    checks = decision == "go"
    return json.dumps({"kind_asked": checks, "target_named": checks, "text_given": checks, "nothing_added": checks,
                       "decision": decision, "reason": reason}, ensure_ascii=False)


class Reviewer:
    """A stand-in reviewer model: the answer it gives, and what it was shown."""

    def __init__(self, decision: str, reason: str = "stand-in"):
        self.said, self.seen = answer(decision, reason), []

    async def __call__(self, system: str, messages: list) -> str:
        self.seen.append(json.loads(messages[-1]["content"]))
        return self.said


class Window:
    """A stand-in for a window worked by picture: a blank picture, nothing named at any point, and what was sent."""
    kind, name = "pane", "Grok Bot"

    def __init__(self):
        self.sent: list = []

    def place(self) -> str:
        return self.name

    async def capture(self, long_edge: int) -> frame.Frame:
        return frame.Frame(Image.new("RGB", (400, 300), "white"), "pane", (0.0, 0.0), 1.0, (400, 300))

    async def marks(self) -> list:
        return []

    async def at(self, x: float, y: float) -> dict:
        return {}

    async def ready(self, x, y) -> str:
        return ""

    async def focused(self) -> dict:
        return {}

    async def click(self, x, y, count: int = 1) -> None:
        self.sent.append(("click", count))

    async def type(self, text: str) -> None:
        self.sent.append(("type", text))

    async def key(self, key: str) -> None:
        self.sent.append(("key", key))


def sight(reviewer: Reviewer, cards: list, pressed: bool = False) -> tuple[act.Sight, Window]:
    """Sight on the stand-in window, with the reviewer as the desk sends it (hands._by_sight): only for a kind
    acting.decide sends to review."""
    window = Window()

    async def by_sight(kind: str, action: dict):
        if acting.decide("auto", kind, "", "programs") != "review":
            return None
        return await review.decide("programs", [ASKED], {"kind": kind, **action}, ask=reviewer, attended=True,
                                   tally=review.Tally())

    def ask(card: dict) -> dict:
        cards.append(card)
        return {"yes": pressed}

    seen = act.Sight(window, profile.Profile(), ask=ask, task=ASKED, review=by_sight)
    seen.lease = window.place()
    return seen, window


def run(seen: act.Sight, *actions: dict) -> list[dict]:
    async def go():
        await seen.look()
        return [await seen.act(a) for a in actions]
    return asyncio.run(go())


def test_typing_is_free_and_enter_goes_ahead_on_the_reviewers_pass_with_the_text_it_sends():
    reviewer, cards = Reviewer("go"), []
    seen, window = sight(reviewer, cards)
    done = run(seen, {"do": "type", "text": "hello", "at": (100, 100)}, {"do": "key", "key": "Enter"})
    assert [d["status"] for d in done] == ["ok", "ok"] and cards == []
    assert ("type", "hello") in window.sent and window.sent[-1] == ("key", "Enter")
    (shown,) = reviewer.seen
    assert (shown["kind"], shown["text"]) == ("outward", "hello")


def test_enter_the_reviewer_does_not_pass_asks_on_a_card_saying_why_and_a_password_is_never_typed():
    reviewer, cards = Reviewer("ask", "这段文字不是你让发送的内容。"), []
    seen, window = sight(reviewer, cards)
    done = run(seen, {"do": "type", "text": "hello", "at": (100, 100)}, {"do": "key", "key": "Enter"},
               {"do": "type", "text": "我的密码是 hunter2", "at": (100, 100)})  # copy: voice
    assert [d["status"] for d in done] == ["ok", "declined", "refused"]
    ((card,),) = [cards]
    assert card["type"] == "see_key" and "这段文字不是你让发送的内容。" in card["review"]
    assert ("key", "Enter") not in window.sent
    assert not any("hunter2" in str(item) for item in window.sent)


@pytest.fixture
def desk(tmp_path, monkeypatch):
    """The desk's side of a task on a stand-in workspace whose chat turn is under way, with the app window there."""
    room = SimpleNamespace(conversation="c1", history=[{"role": "user", "text": ASKED, "at": None}])
    workspace = SimpleNamespace(home=tmp_path, _chat_lock=SimpleNamespace(locked=lambda: True), _main=room, _chats={},
                                config=lambda: {"LLM_BASE_URL": "https://openrouter.ai/api/v1",
                                                "PERSONA_NAME": "Mibo"})
    (tmp_path / "runtime" / "desktop").mkdir(parents=True)
    (tmp_path / "runtime" / "desktop" / "chat-doing.json").write_text(json.dumps({"conversation": "c1"}),
                                                                      encoding="utf-8")
    app = SimpleNamespace(state=SimpleNamespace(window=SimpleNamespace(attended=lambda: True)))
    one = panel.Desk(app, workspace)
    one.conversation = "c1"
    one.turn_on = lambda conversation: True
    one.cards_seen = []

    def ask(card: dict) -> dict:
        one.cards_seen.append(card)
        return {"answer": "no"} if "always" in card["choices"] else {"yes": False}

    one.ask = ask
    monkeypatch.setattr(win32, "title_of", lambda hwnd: one.title)
    yield one
    one.worker.shutdown()


def allow_pictures(desk, reviewer: Reviewer, program: str, title: str, monkeypatch) -> dict:
    monkeypatch.setattr(acting, "reviewer", lambda workspace: reviewer)
    desk.title = title
    desk.hands = SimpleNamespace(program={"name": program, "exe": f"C:\\Apps\\{program}.exe"},
                                 view=SimpleNamespace(hwnd=7), task=ASKED, sent_picture=lambda: "", sight=None)
    return asyncio.run(desk.op("see_allow", {}))


def logged(desk) -> list[dict]:
    return [json.loads(line) for line in (desk.desktop / "desk-log.jsonl").read_text(encoding="utf-8").splitlines()]


def test_the_window_the_person_named_goes_to_their_model_service_on_the_reviewers_pass(desk, monkeypatch):
    reviewer = Reviewer("go", "你让操作 Grok Bot。")
    done = allow_pictures(desk, reviewer, "Grok Bot", "Grok Bot — 主界面", monkeypatch)
    assert done["status"] == "ok" and desk.cards_seen == [] and desk.pictured is desk.hands
    (shown,) = reviewer.seen
    assert (shown["kind"], shown["where"]) == ("picture", "openrouter.ai")
    assert logged(desk)[-1]["review"] == {"decision": "go", "by": "review", "reason": "你让操作 Grok Bot。"}


def test_a_window_their_words_do_not_point_at_asks_on_a_card_saying_why(desk, monkeypatch):
    reviewer = Reviewer("ask", "你没有提到微信。")
    done = allow_pictures(desk, reviewer, "微信", "微信", monkeypatch)
    assert done["status"] == "declined"
    ((card,),) = [desk.cards_seen]
    assert card["choices"] == ["once", "always", "no"] and "你没有提到微信。" in card["review"]


def test_a_mail_clients_window_always_asks_with_no_always_and_never_reaches_the_reviewer(desk, monkeypatch):
    reviewer = Reviewer("go")
    done = allow_pictures(desk, reviewer, "Grok Bot", "Outlook — 收件箱", monkeypatch)
    assert done["status"] == "declined" and reviewer.seen == []
    ((card,),) = [desk.cards_seen]
    assert card["choices"] == ["once", "no"]

"""The page's chat as the person keeps it: pictures the character sends, a message that failed and is sent
again, what is kept after a restart, and forgetting the chat. The character's reply is stood in for at
character.reply. What the picture route refuses is a floor in tests/floors/boundary/."""
from __future__ import annotations

import base64

import pytest

from harness.app import TOKEN
from mibo import welcome
from mibo import settingsfile

HEADERS = {welcome.TOKEN_HEADER: TOKEN}
# The smallest PNG: one transparent pixel.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
SHOWN = base64.b64encode(PNG).decode()


@pytest.fixture(autouse=True)
def configured(workspace):
    """A home with a key: the page takes messages only once a model is set up."""
    settingsfile.write(workspace.home, {"LLM_API_KEY": "key"})


def _replying(monkeypatch, workspace, replies, asked=None):
    """The character answers every message with `replies`; `asked` takes the conversation each turn was in."""
    async def reply(conversation, parts, *, choices=(), run=None):
        if asked is not None:
            asked.append(conversation)
        return [dict(item) for item in replies]

    monkeypatch.setattr(workspace.character, "reply", reply)


def _said(client, text: str, **sent) -> dict:
    answer = client.post("/api/chat", headers=HEADERS, json={"text": text, **sent})
    assert answer.status_code == 200, answer.text
    return answer.json()


def _messages(client) -> list[dict]:
    """The main chat as the page reads it, without the time each message was said."""
    seen = client.get("/api/chat/history", headers=HEADERS).json()["messages"]
    return [{k: v for k, v in m.items() if k != "at"} for m in seen]


def _again(workspace) -> dict:
    """The chat as a page opened after a restart reads it."""
    reopened = welcome.Workspace(workspace.home)
    try:
        return reopened.history()
    finally:
        reopened.close()


def _stored(workspace, name: str):
    """Where a picture the chat shows is kept in the home."""
    found = list(workspace.home.rglob(name))
    assert len(found) == 1, found
    return found[0]


def test_a_picture_reply_is_kept_shown_and_served_only_to_the_page(client, workspace, monkeypatch):
    _replying(monkeypatch, workspace, [{"type": "text", "text": "look"}, {"type": "image", "b64": SHOWN}])
    answer = _said(client, "hi")
    picture = answer["messages"][-1]["picture"]
    assert [m.get("text") for m in answer["messages"][:2]] == ["hi", "look"]
    assert welcome.PICTURE_NAME.fullmatch(picture)
    served = client.get(f"/api/chat/picture/{picture}", headers=HEADERS)
    assert served.status_code == 200 and served.content == PNG
    assert served.headers["content-type"] == "image/png"
    assert client.get(f"/api/chat/picture/{picture}").status_code == 403
    # Still there for a page opened again.
    assert _again(workspace)["messages"][-1]["picture"] == picture


def test_a_picture_gone_from_the_disk_is_missing(client, workspace, monkeypatch):
    _replying(monkeypatch, workspace, [{"type": "image", "b64": SHOWN}])
    picture = _said(client, "hi")["messages"][-1]["picture"]
    _stored(workspace, picture).unlink()
    assert client.get(f"/api/chat/picture/{picture}", headers=HEADERS).status_code == 404


@pytest.mark.parametrize("sent", [base64.b64encode(b"<svg onload=x>").decode(), "not base64!", "x"])
def test_what_is_not_a_picture_is_neither_kept_nor_shown_as_an_empty_one(client, workspace, monkeypatch, sent):
    before = {path for path in workspace.home.rglob("*") if path.is_file()}
    _replying(monkeypatch, workspace, [{"type": "text", "text": "here"}, {"type": "image", "b64": sent}])
    _said(client, "hi")
    assert [(m["role"], m.get("text"), m.get("picture")) for m in _messages(client)] == [
        ("user", "hi", None), ("assistant", "here", None)]
    # Only the chat record itself was written.
    written = {path for path in workspace.home.rglob("*") if path.is_file()} - before
    assert [path.name for path in written
            if path.suffix not in (".json", ".jsonl", ".lock") and not path.name.startswith("mibo.db")] == []


def test_pictures_leave_with_the_messages_that_showed_them(client, workspace, monkeypatch):
    _replying(monkeypatch, workspace, [{"type": "image", "b64": SHOWN}])
    stored = _stored(workspace, _said(client, "hi")["messages"][-1]["picture"])
    assert client.post("/api/chat/reset", headers=HEADERS, json={}).json() == {"ok": True}
    assert not stored.exists() and _messages(client) == []


def test_a_failed_message_is_kept_unanswered_and_can_be_sent_again(client, workspace, monkeypatch):
    async def down(conversation, parts, **kwargs):
        raise ConnectionError("down")

    monkeypatch.setattr(workspace.character, "reply", down)
    failed = client.post("/api/chat", headers=HEADERS, json={"text": "hi"})
    assert failed.status_code == 502 and failed.json() == {"error": "chat_failed"}
    expected = [{"role": "user", "text": "hi", "seq": 1, "failed": "chat_failed"}]
    assert _messages(client) == expected
    assert [{k: v for k, v in m.items() if k != "at"} for m in _again(workspace)["messages"]] == expected, \
        "a restart keeps it"
    _replying(monkeypatch, workspace, [{"type": "text", "text": "here"}])
    answer = _said(client, "hi", retry=1)
    # Sent again, it is the same message answered, not a second one.
    assert [(m["text"], m.get("failed")) for m in _messages(client)] == [("hi", None), ("here", None)]
    assert [m["text"] for m in answer["messages"]] == ["hi", "here"]


def test_a_character_that_does_not_start_keeps_the_message(client, workspace, monkeypatch):
    async def broken(conversation, parts, **kwargs):
        raise welcome.NotReady("the memory file is damaged")

    monkeypatch.setattr(workspace.character, "reply", broken)
    assert client.post("/api/chat", headers=HEADERS, json={"text": "hello"}).status_code == 502
    assert _messages(client)[-1]["failed"] == "agent_failed"


def test_a_long_chat_is_shown_whole_and_the_same_after_a_restart(client, workspace, monkeypatch):
    _replying(monkeypatch, workspace, [{"type": "text", "text": word} for word in ("one", "two", "three")])
    for n in range(40):
        _said(client, f"m{n}")
    seen = client.get("/api/chat/history", headers=HEADERS).json()
    assert len(seen["messages"]) == 160 and seen["messages"][0]["text"] == "m0"
    assert _again(workspace)["messages"] == seen["messages"]


def test_the_page_asks_only_for_what_came_since_the_last_message_it_has(client, workspace, monkeypatch):
    _replying(monkeypatch, workspace, [{"type": "text", "text": "b"}])
    _said(client, "a")

    def since(after) -> list:
        answer = client.get(f"/api/chat/history?after={after}", headers=HEADERS)
        return [(m["seq"], m["text"]) for m in answer.json()["messages"]]

    assert since(0) == [(1, "a"), (2, "b")]
    assert since(1) == [(2, "b")]
    assert since(2) == []
    assert client.get("/api/chat/history?after=x", headers=HEADERS).status_code == 422


def test_forgetting_the_chat_asks_the_character_once_then_starts_a_new_one(client, workspace, monkeypatch):
    asked: list[str] = []
    _replying(monkeypatch, workspace, [{"type": "text", "text": "hello"}], asked)
    before = _said(client, "hi")["chat"]
    calls = []

    async def forget(conversation, keep):
        calls.append((conversation, keep))
        return 3

    monkeypatch.setattr(workspace.character, "forget_chat", forget)
    done = client.post("/api/chat/forget", headers=HEADERS, json={})
    # The main chat is forgotten, the chats it replaced included; the plain chats the page lists are kept.
    assert done.json() == {"ok": True, "forgotten": 3}
    assert calls == [(asked[0], [])]
    seen = client.get("/api/chat/history", headers=HEADERS).json()
    assert seen["messages"] == [] and seen["chat"] != before
    _said(client, "hi again")
    assert asked[1] != asked[0], "the next message starts a new conversation"

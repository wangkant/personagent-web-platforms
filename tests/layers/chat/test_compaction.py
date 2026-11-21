"""A long chat within the model's window (context design, stage S4): after a reply, once the history passes its
share, the older turns become one checkpoint written by the reply model on the same prefix as the last request, and
the newest whole turns stay as they are; a summary that fails leaves the turns, and twice a cut without one; the
checkpoint is kept as the page's summary row and the chat comes back from it; an overflow is fitted again and sent
once more; a message too long for the model is cut when it comes in."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from harness.app import App
from harness.model import call, fail, held, say, tokens
from harness.page import measured
from harness.turn import run_turn
from mibo import chatlog, db, loop, pastchats
from mibo.dm import chat_key
from mibo.prompt import _CHECKPOINT_OPEN

WINDOW = 16384
# About 50 characters of Chinese, a line of an everyday chat.
ZH = "今天下午我们去公园散步，路上看到很多银杏叶已经黄了，风一吹就落下来，像下了一场金色的雨，真好看。"


def _line(n: int, size: int = 2) -> str:
    return f"第{n}句：{ZH * size}"


def _summary(n: int) -> dict:
    return say(f"Threads:\n- 公园散步与银杏（第{n}次）\nTheir words:\n- (none)\nMood: 轻松")


def _setup(app, window: int = WINDOW) -> None:
    """The stand-in's window, measured as the start's check keeps it; the memory fold fails once and waits ten
    minutes, as these chats are about the history alone."""
    app.model.window = window
    measured(app, context=window)
    app.model.script(fail(500), purpose="fold")


def _history(app) -> dict:
    return app.get("/api/chat/history").json()


def _kept(app, n: int = 1) -> dict:
    """The page's summary row once `n` checkpoint requests were made (compaction runs after the reply is said)."""
    for _ in range(500):
        summary = _history(app).get("summary")
        if summary and len(app.model.asked("compact")) >= n:
            return summary
        time.sleep(0.02)
    raise AssertionError("no checkpoint was kept")


def _checkpoints(body: dict) -> list[str]:
    return [m["content"] for m in body["messages"] if str(m.get("content") or "").startswith(_CHECKPOINT_OPEN)]


def test_a_long_chat_keeps_one_checkpoint_and_its_newest_turns_and_never_passes_the_window(app, turn):
    _setup(app)
    app.model.script(*[_summary(n) for n in range(30)], purpose="compact")
    for n in range(30):
        assert turn(_line(n), say(_line(n))).status == 200
    bodies = [r["body"] for r in app.model.requests if r.get("purpose") in ("reply", "compact")]
    asked = app.model.asked("compact")
    assert len(asked) >= 2
    # Nothing ever passed the window, the checkpoint requests included.
    assert all(tokens(b) + b["max_tokens"] <= WINDOW for b in bodies)
    # Merged, never stacked: each request holds at most one checkpoint, and each checkpoint request after the
    # first holds the one before it.
    assert all(len(_checkpoints(b)) <= 1 for b in bodies)
    assert all(len(_checkpoints(r["body"])) == 1 for r in asked[1:])
    last = app.model.asked()[-1]["body"]["messages"]
    assert last[1]["role"] == "user" and last[1]["content"].startswith(_CHECKPOINT_OPEN)
    # The older turns are gone from the request; the newest are whole.
    assert _line(0) not in json.dumps(last, ensure_ascii=False) and _line(28) in last[-2]["content"]
    # Every turn is in the chat's record, which past-chat search reads.
    logged = [row["text"] for (conv,) in db.at(app.home / "runtime").read("SELECT DISTINCT conv FROM chatlog")
              for row in chatlog.ChatLog(app.home / "runtime").read(conv)]
    assert all(_line(n) in logged for n in range(30))


def test_the_checkpoint_request_replays_the_last_reply_request_and_asks_last(app, turn):
    _setup(app)
    app.model.script(*[_summary(n) for n in range(10)], purpose="compact")
    for n in range(12):
        turn(_line(n, 12), say(_line(n, 12)))
    _kept(app, 2)
    replies: list[dict] = []
    for record in app.model.requests:
        if record.get("purpose") == "reply":
            replies.append(record["body"])
        elif record.get("purpose") == "compact":
            body, before = record["body"], replies[-1]
            # The same system prompt, tools and turns, byte for byte, so the cached prefix is read again.
            assert body["tools"] == before["tools"]
            assert body["messages"][:-1] == before["messages"][:len(body["messages"]) - 1]
            assert len(body["messages"]) - 1 < len(before["messages"])


@pytest.mark.parametrize("bad", [
    {"content": "Threads:\n- 公园", "finish": "length"},
    call(pastchats.NAME, query="公园"),
    say(ZH * 2000),
], ids=["cut-off", "tool-call", "not-smaller"])
def test_a_failed_summary_leaves_the_turns_and_the_second_cuts_them_without_one(app, turn, bad):
    # A window wide enough that the history passes its share well before the next request would pass T.
    _setup(app, 65536)
    app.model.script(bad, fail(500), *[_summary(n) for n in range(5)], purpose="compact")
    for n in range(9):
        assert turn(_line(n, 40), say(_line(n, 40))).status == 200
    _kept(app, 2)
    first, second = app.model.asked("compact")[:2]
    # The first failure kept every turn: the second request still starts from the first one.
    assert all(_line(0, 40) in json.dumps(r["body"]["messages"], ensure_ascii=False) for r in (first, second))
    # The second cut them, and the checkpoint says where they are found.
    cut = [c for r in app.model.requests if r.get("purpose") in ("reply", "compact") for c in _checkpoints(r["body"])]
    assert cut and pastchats.NAME in cut[0]
    assert _line(0, 40) not in json.dumps(app.model.asked()[-1]["body"]["messages"], ensure_ascii=False)


def test_a_failed_summary_cuts_at_once_when_the_next_request_would_pass_the_ceiling(app, turn):
    # At 16k the system prompt and tools leave little: two long turns take the history past its share and the
    # next request past T at once.
    _setup(app)
    app.model.script(fail(500), *[_summary(n) for n in range(5)], purpose="compact")
    for n in range(3):
        assert turn(_line(n, 25), say(_line(n, 25))).status == 200
    _kept(app, 1)
    first = app.model.asked("compact")[0]
    after = [r for r in app.model.requests[app.model.requests.index(first):] if r.get("purpose") == "reply"]
    assert after and pastchats.NAME in _checkpoints(after[0]["body"])[0]


def test_the_chat_comes_back_from_the_pages_checkpoint_after_a_restart(app, turn, model, tmp_path):
    _setup(app)
    app.model.script(*[_summary(n) for n in range(10)], purpose="compact")
    for n in range(10):
        turn(_line(n, 12), say(_line(n, 12)))
    kept = _kept(app)
    assert "公园散步" in kept["text"]
    app.close()
    for found in app.home.rglob("recent.json"):
        found.unlink()
    again = App(Path(os.environ["AGENT_HOME"]), model, PERSONA_BROWSER_PATH=str(tmp_path / "no-browser"),
                LLM_MAX_RETRIES="0").open()
    try:
        # Before its first answer the app counts by characters alone: a request it undercounts is refused,
        # fitted to the window the refusal states and sent once more.
        done = run_turn(again, "还记得我们聊过什么吗？", say("记得，公园和银杏。"))
        body = done.asked()[0]
        assert done.status == 200
        assert _checkpoints(body) and "公园散步" in _checkpoints(body)[0]
        assert _line(0, 12) not in json.dumps(body["messages"], ensure_ascii=False)
        assert _line(9, 12) in json.dumps(body["messages"], ensure_ascii=False)
    finally:
        again.close()


def test_a_message_longer_than_its_share_is_cut_when_it_comes_in_and_the_page_marks_it(app, turn):
    _setup(app)
    # Under the page's own limit on a message, past the model's share for one.
    pasted = "开头" + ZH * 150 + "结尾"
    app.model.script(_summary(0), purpose="compact")
    done = turn(pasted, say("这段很长，我读了开头和结尾。"))
    (body,) = done.asked()
    assert done.status == 200 and tokens(body) + body["max_tokens"] <= WINDOW
    sent = body["messages"][-1]["content"]
    assert "开头" in sent and "结尾" in sent and ZH * 150 not in sent
    theirs = [m for m in _history(app)["messages"] if m["role"] == "user"]
    assert theirs[-1].get("cut") is True and theirs[-1]["text"] == pasted
    short = turn("好的", say("嗯。"))
    assert not [m for m in _history(app)["messages"] if m["role"] == "user"][-1].get("cut")
    assert short.status == 200


def test_a_request_still_past_the_window_after_its_history_is_compacted_is_answered_without_tools(app, turn):
    _setup(app)
    first = turn("一" + ZH * 150, say("读了。"))
    done = turn("二" + ZH * 150, say("两段都读了。"))
    assert first.status == done.status == 200
    last = done.asked()[-1]
    assert "tools" not in last and last["messages"][-1]["content"] == loop.ANSWER_NOW
    assert tokens(last) + last["max_tokens"] <= WINDOW


def _agent(app):
    return app.workspace.character.agent


def _until(done, what: str, wait: float = 15.0) -> None:
    for _ in range(int(wait / 0.02)):
        if done():
            return
        time.sleep(0.02)
    raise AssertionError(what)


def _again(model, tmp_path) -> App:
    return App(Path(os.environ["AGENT_HOME"]), model, PERSONA_BROWSER_PATH=str(tmp_path / "no-browser"),
               LLM_MAX_RETRIES="0").open()


MANGO = "Threads:\n- 他对芒果过敏，吃了会起疹子\n- 公园散步与银杏\nMood: 轻松"


def test_a_forget_while_the_checkpoint_is_written_is_not_undone(app, turn):
    _setup(app)
    gate = threading.Event()
    app.model.script(held(gate, say(MANGO)), *[_summary(n) for n in range(5)], purpose="compact")
    agent, (user_id, pkey) = _agent(app), chat_key(app.workspace._main.conversation)
    turn("我对芒果过敏，吃了会起疹子。" + _line(0, 12), say("记住了。" + _line(0, 12)))
    for n in range(1, 12):
        turn(_line(n, 12), say(_line(n, 12)))
        try:
            _until(lambda: app.model.asked("compact"), "", 0.5)
            break
        except AssertionError:
            continue
    # The checkpoint request waits for its answer; meanwhile the person has it forgotten.
    assert turn("忘掉芒果").status == 200
    gate.set()
    _until(lambda: not agent.locks[pkey].locked(), "the compaction did not end")
    kept = _history(app).get("summary") or {}
    assert "芒果" not in kept.get("text", "")
    assert "芒果" not in json.dumps(agent.dm_history.get(user_id), ensure_ascii=False)
    after = turn("还记得吗？", say("记得。")).asked()[0]
    assert "芒果" not in json.dumps(after["messages"], ensure_ascii=False)


def test_a_forget_from_settings_while_compact_now_waits_is_not_undone(app, turn):
    _setup(app, 65536)
    chat = app.post("/api/chats", {}).json()["id"]
    item = app.post("/api/person/add", {"text": "我对芒果过敏"}).json()["facts"][0]["id"]
    turn("我对芒果过敏，吃了会起疹子。", say("记住了。"), conversation=chat)
    for n in range(3):
        turn(_line(n), say(_line(n)), conversation=chat)
    gate = threading.Event()
    app.model.script(held(gate, say(MANGO)), purpose="compact")
    answers: dict = {}
    pressed = threading.Thread(target=lambda: answers.update(
        compact=app.post("/api/chat/compact", {"chat": chat}).json()), daemon=True)
    pressed.start()
    _until(lambda: app.model.asked("compact"), "立即归纳 asked for no checkpoint")
    agent = _agent(app)
    forgot = threading.Thread(target=lambda: answers.update(
        forget=app.post("/api/person/forget_chats", {"id": item}).status_code), daemon=True)
    forgot.start()
    _until(lambda: agent.person_store.get(item) is None, "the forget did not run")
    gate.set()
    pressed.join(30)
    forgot.join(30)
    assert answers["forget"] == 200 and not answers["compact"].get("compacted")
    kept = app.get(f"/api/chat/history?chat={chat}").json().get("summary") or {}
    user_id = chat_key(app.workspace._chats[chat].conversation)[0]
    assert "芒果" not in kept.get("text", "")
    assert "芒果" not in json.dumps(agent.dm_history.get(user_id), ensure_ascii=False)


def test_a_cut_message_that_gets_no_words_waits_to_be_sent_again(app, turn):
    _setup(app)
    done = turn("开头" + ZH * 150 + "结尾", say("PASS"))
    theirs = [m for m in _history(app)["messages"] if m["role"] == "user"]
    assert done.status != 200 and theirs[-1].get("failed") and theirs[-1].get("cut")


def test_a_chat_read_before_its_first_turn_after_a_restart_keeps_every_turn_in_its_record(app, turn, model,
                                                                                         tmp_path):
    # A long chat at a small window: it comes back from the page with fewer turns than its record holds.
    _setup(app)
    app.model.script(*[_summary(n) for n in range(10)], purpose="compact")
    for n in range(10):
        turn(_line(n, 12), say(_line(n, 12)))
    _kept(app)
    app.close()
    for found in app.home.rglob("recent.json"):
        found.unlink()
    again = _again(model, tmp_path)
    try:
        # The model menu reads the chat's context before anything is sent.
        assert again.get("/api/chat/context").json()["held"]
        assert run_turn(again, "新的一句", say("新的回答")).status == 200
        conv = chat_key(again.workspace._main.conversation)[1]
        logged = [row["text"] for row in chatlog.ChatLog(again.home / "runtime").read(conv)]
        assert "新的一句" in logged and "新的回答" in logged
    finally:
        again.close()


@pytest.mark.parametrize("window, before", [(16384, 0), (12288, 0), (12288, 2)])
@pytest.mark.parametrize("thinks", [False, True])
def test_an_overflow_refused_again_after_the_refit_at_a_small_window_makes_no_further_request(app, turn, window,
                                                                                               before, thinks):
    # Measured at 64k, so the turn offers every tool. Where the server's window still holds them the refit is sent
    # with them; where it does not, the history before the message is compacted, and the turn's last word goes
    # without them. Refused again: two reply requests, then the turn fails.
    measured(app, context=65536, **({"thinking": "yes", "thinking_detail": {"param": "thinking"}} if thinks else {}))
    app.model.script(fail(500), purpose="fold")
    app.model.script(_summary(0), purpose="compact")
    for n in range(before):
        turn(_line(n), say(_line(n)))
    over = fail(400, {"error": {"message": f"This model's maximum context length is {window} tokens. However, you "
                                           "requested 70000 tokens.", "type": "invalid_request_error"}})
    done = turn("hello", over, over)
    first, second = [r["body"] for r in done.requests if r.get("purpose") == "reply"]
    assert done.status != 200 and first.get("tools")
    assert second.get("tools") or second["messages"][-1]["content"] == loop.ANSWER_NOW
    if window == 12288:
        assert not second.get("tools")

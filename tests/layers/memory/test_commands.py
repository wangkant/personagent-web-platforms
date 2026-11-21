"""Memory commands typed in a chat, in the ten chat languages: what a remember keeps, what a forget takes away,
and what stays conversation. They run without the model (dm.py), so each one is said to the character itself and
what it kept is read back from its store.

Each language's cases are tests/data/chat_language_cases/<code>.json: the character's name in that language and
commands, each a message and what it is:
- "remember" with the "note" kept (null: a remember naming nothing keeps nothing),
- "forget" with the note it "deletes", which an earlier case kept,
- "recall", or "forget_that" (a forget naming nothing: what was just said),
- null for conversation, which must stay conversation.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from mibo import chatlangs, db, dm, paths
from mibo.agent import Agent
from mibo.context import CHECKPOINT
from mibo.settings import AgentSettings

CASES = Path(__file__).resolve().parents[2] / "data" / "chat_language_cases"
# The page's own id for the chat the commands are typed in.
CHAT = "c1"


def _character(folder: Path, name: str, lang: str) -> Agent:
    """The character as the app builds it, its memory in `folder`."""
    return Agent(AgentSettings(api_key="test-key", persona_name=name, lang=lang, persona="A friend.",
                               memory_file=str(folder / "memory.json")))


def _kept(agent: Agent) -> dict[str, str]:
    """Everything the character keeps of the person: id -> text."""
    store = agent.person_store
    return {row["id"]: row["text"] for row in store.facts() + store.plans()}


async def _say(agent: Agent, text: str) -> list[dict]:
    return await agent.answer(CHAT, [{"type": "text", "text": text}])


def _answered_in(said: list[dict], code: str) -> bool:
    """The first line said is one of the lines the language `code` has for a memory command."""
    lines = [line for options in chatlangs.SPECS[code].replies.values() for line in options]
    return bool(said) and any(said[0]["text"].startswith(line) for line in lines)


@pytest.mark.parametrize("code", chatlangs.CODES)
async def test_a_remember_keeps_its_note_and_a_forget_takes_away_what_it_names(tmp_path: Path, code: str) -> None:
    cases = json.loads((CASES / f"{code}.json").read_text(encoding="utf-8"))
    kinds = [case["kind"] for case in cases["commands"]]
    for kind, least in (("remember", 5), ("forget", 4), (None, 4), ("recall", 1)):
        assert kinds.count(kind) >= least, f"{code}: at least {least} {kind or 'conversation'} cases"
    agent = _character(tmp_path, cases["name"], code)
    kept_from: dict[str, str] = {}
    for case in cases["commands"]:
        text, kind = case["text"], case["kind"]
        read = agent._parse_memory_command(text)
        if kind is None:
            assert read is None, f"{code}: conversation, not a command: {text!r}"
            continue
        assert read is not None and read[0] == kind, f"{code}: {text!r} is {kind}"
        if kind != "forget_that":
            assert read[2] == code, f"{code}: {text!r} is read in its own language"
        before = _kept(agent)
        said = await _say(agent, text)
        after = _kept(agent)
        added = {key: words for key, words in after.items() if key not in before}
        gone = {key: words for key, words in before.items() if key not in after}
        assert _answered_in(said, read[2]), f"{code}: {text!r} is answered in its language, without the model: {said}"
        if kind == "remember" and case["note"]:
            # Kept as said, at most without the words that name the speaker ("我", "私は").
            assert not gone and len(added) == 1, f"{code}: {text!r} keeps one thing: {added}"
            (key, words), = added.items()
            assert words and case["note"].casefold().endswith(words.casefold()), \
                f"{code}: {text!r} keeps {case['note']!r}, not {words!r}"
            kept_from[case["note"]] = key
        elif kind == "forget":
            assert not added and gone == {kept_from[case["deletes"]]: before[kept_from[case["deletes"]]]}, \
                f"{code}: {text!r} takes away {case['deletes']!r} and nothing else: {gone}"
        else:
            assert not added and not gone, f"{code}: {text!r} keeps and takes away nothing"


async def test_a_command_may_open_with_a_nickname_from_the_card_or_the_name_without_its_accent(tmp_path: Path) -> None:
    sofia = _character(tmp_path / "en", "Sofía", "en")
    assert sofia.persona_aliases == ()
    await _say(sofia, "sofia, remember I moved to Lisbon")
    assert list(_kept(sofia).values()) == ["I moved to Lisbon"]
    # The card sits in the character's home, as the setup writes it.
    (paths.ROOT / "persona.card.json").write_text(json.dumps({"aliases": ["鲸鱼", " ", 7]}), encoding="utf-8")
    whale = _character(tmp_path / "zh", "鲸鱼娘", "zh")
    assert whale.persona_aliases == ("鲸鱼",)
    await _say(whale, "鲸鱼，记住 我养了一只猫")
    assert list(_kept(whale).values()) == ["养了一只猫"]


async def test_a_forget_takes_the_sentences_it_names_out_of_the_chats_checkpoint(tmp_path: Path) -> None:
    agent = _character(tmp_path, "Mibo", "en")
    reader = dm.chat_key(CHAT)[0]
    with db.at(tmp_path).write() as conn:
        conn.execute("INSERT INTO page_chats VALUES (?,?,?,?,?)", ("p1", CHAT, "", "2026-11-21T08:00:00+00:00", 0))
    head = {"role": CHECKPOINT, "content": "Threads:\n- They moved to Lisbon. They like green tea.\nMood: calm",
            "t": time.time(), "seq": 0}
    agent._keep_checkpoint(reader, head)
    agent.dm_history[reader] = [head]
    await _say(agent, "remember that I moved to Lisbon")
    await _say(agent, "forget Lisbon")
    (row,) = db.at(tmp_path).read("SELECT body FROM page_messages WHERE kind = 'summary'")
    for text in (agent.dm_history[reader][0]["content"], json.loads(row[0])["text"]):
        assert "Lisbon" not in text and "green tea" in text and "Mood" in text


def _page_chat(folder: Path, ident: str, conversation: str, mark: dict | None = None) -> None:
    """A chat the page keeps, with `mark` as its summary row (the checkpoint dm.py keeps there)."""
    with db.at(folder).write() as conn:
        conn.execute("INSERT INTO page_chats VALUES (?,?,?,?,?)", (ident, conversation, "", "2026-11-21T08:00:00+00:00", 0))
        if mark is not None:
            conn.execute("INSERT INTO page_messages VALUES (?, 0, 'summary', ?)", (ident, json.dumps(mark)))


def _mark(folder: Path, ident: str) -> dict:
    rows = db.at(folder).read("SELECT body FROM page_messages WHERE chat = ? AND kind = 'summary'", (ident,))
    return json.loads(rows[0][0]) if rows else {}


async def test_a_forget_takes_its_words_out_of_the_checkpoint_of_a_chat_nothing_holds(tmp_path: Path) -> None:
    agent = _character(tmp_path, "Mibo", "en")
    _page_chat(tmp_path, "p1", CHAT)
    # A chat of the page that holds no turns now (after a restart, past the recent window, its record pruned).
    _page_chat(tmp_path, "p2", "c2", {"text": "Threads:\n- They moved to Lisbon. They like green tea.\nMood: calm",
                                      "from": 1.0, "at": 1.0})
    await _say(agent, "remember that I moved to Lisbon")
    await _say(agent, "forget Lisbon")
    mark = _mark(tmp_path, "p2")
    assert "Lisbon" not in mark["text"] and "green tea" in mark["text"]
    # Written anew, so the page draws the checkpoint's words again.
    assert mark["at"] > 1.0


async def test_a_forget_whose_words_span_sentences_leaves_none_of_them_in_the_checkpoint(tmp_path: Path) -> None:
    agent = _character(tmp_path, "Mibo", "en")
    reader = dm.chat_key(CHAT)[0]
    # Each sentence holds a part of what they said; together they hold most of it.
    mark = {"text": "Threads:\n- Sister Ana. She teaches violin. Lessons in Porto.\nMood: calm", "from": 1.0, "at": 1.0}
    _page_chat(tmp_path, "p1", CHAT, mark)
    now = time.time()
    agent.dm_history[reader] = [{"role": CHECKPOINT, "content": mark["text"], "t": 1.0, "seq": 0},
                                {"role": "user", "content": "My sister Ana teaches violin in Porto", "t": now, "seq": 1},
                                {"role": "assistant", "content": "That sounds lovely.", "t": now, "seq": 2}]
    await _say(agent, "forget that")
    held = [row["content"] for row in agent.dm_history[reader] if row["role"] == CHECKPOINT]
    for text in [*held, _mark(tmp_path, "p1").get("text", "")]:
        assert not {"Ana", "violin", "Porto"} & set(text.replace(".", " ").split())

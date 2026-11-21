"""An ID or card number, a password, a code or a key said in a chat is heard by the turn that said it and kept
nowhere: not in any file of the home (the chat record, recent.json, the page's copy, the person store), not in
what a later search of past chats returns, not in any later request to the model, before or after a restart. Only
the masked form is kept. A line they asked not to keep ('别记这个') is nowhere at all. The attacks are
tests/data/attacks/secrets.jsonl, said one after another in one chat, as a person would."""
from __future__ import annotations

import json

import pytest

from harness import attacks
from harness.home import holding, restarted
from harness.model import call, say
from harness.turn import run_turn
from mibo import db, person

ROWS = [param.values[0] for param in attacks.load("secrets")]


def words_of(row: dict) -> list[str]:
    """What must be kept nowhere: the secret as written, or the words of a line not to keep."""
    return row.get("raw") or row["gone"]


def carries(request: dict, words) -> list[str]:
    """Which of `words` a recorded model request carries, however JSON wrote them."""
    sent = json.dumps(request.get("body") or {}, ensure_ascii=False)
    return [w for w in words if w in sent or json.dumps(w)[1:-1] in sent]


def recall(app) -> dict[str, str]:
    """In a new chat they ask about earlier ones, and the model searches past chats for each attack's topic:
    {attack id: what the search returned}."""
    chat = app.post("/api/chats").json()["id"]
    done = run_turn(app, "你翻一下我们之前聊过的，我当时说的那些还记得吗",  # copy: voice
                    [call("search_past_chats", query=row["recall"], when="") for row in ROWS],
                    say("我翻了一下。"), conversation=chat)  # copy: voice
    return {row["id"]: done.result_of(searched["id"]) for row, searched in zip(ROWS, done.calls())}


# Eight turns, two searches and a restart through the real page and loop: about 5 s.
@pytest.mark.slow
def test_secrets_said_in_a_chat_are_kept_nowhere_but_in_their_masked_form(app):
    later_than = {}
    for row in ROWS:
        words = words_of(row)
        # The worst case: the turn searches past chats for the very words (the step under the reply keeps the
        # query), and the reply repeats them.
        done = run_turn(app, row["said"], call("search_past_chats", query=words[0], when=""),
                        say("收到：" + "、".join(words)))  # copy: voice
        # The turn that said it hears it whole.
        assert words[0] in json.dumps(done.asked()[0], ensure_ascii=False), row["id"]
        later_than[row["id"]] = len(app.model.requests)
    found = recall(app)
    again = restarted(app)
    try:
        found_later = recall(again)
    finally:
        again.close()
    in_files = holding(app.home, [w for row in ROWS for w in words_of(row)])
    leaks = {row["id"]: {w: in_files[w] for w in words_of(row) if in_files[w]} for row in ROWS}
    assert {k: v for k, v in leaks.items() if v} == {}, "the home keeps what was said"
    later = {row["id"]: [carries(r, words_of(row)) for r in app.model.requests[later_than[row["id"]]:]]
             for row in ROWS}
    assert {k: [c for c in v if c] for k, v in later.items() if any(v)} == {}, \
        "a later model request carries what was said"
    for row in ROWS:
        for result in (found[row["id"]], found_later[row["id"]]):
            assert not any(w in result for w in words_of(row)), (row["id"], result)
            assert all(m in result for m in row.get("masked") or []), (row["id"], result)
    # The masked forms are what the chat record, recent.json and the page's copy keep.
    masked = holding(app.home, [m for row in ROWS for m in row.get("masked") or []])
    missing = {(m, place) for m, files in masked.items()
               for place in ("runtime/mibo.db", "runtime/recent.json")
               if not any(f.startswith(place) for f in files)}
    store = db.at(app.home / "runtime")
    missing |= {(m, table) for m in masked for table, column in (("chatlog", "text"), ("page_messages", "body"))
                if not store.read(f"SELECT 1 FROM {table} WHERE instr({column}, ?)", (m,))}
    assert missing == set(), "a masked form is missing from where the chat is kept"


def test_a_number_already_in_the_chat_record_never_comes_back_from_a_search(app):
    card = next(row for row in ROWS if row["id"] == "card")
    run_turn(app, card["said"], say("好的。"))  # copy: voice
    # A line of the record that holds the number as said, however it came to be written so.
    with db.at(app.home / "runtime").write() as conn:
        conn.execute("UPDATE chatlog SET text = replace(text, ?, ?)", (card["masked"][0], card["raw"][1]))
    assert holding(app.home, card["raw"])[card["raw"][1]]
    found = recall(app)[card["id"]]
    assert not any(w in found for w in card["raw"]) and card["masked"][0] in found, found


# Talk about keys and codes with no key or code in it: this app's people set up model keys all the time.
SPOKEN_OF = ["my API key is not working", "我的 API key 余额还剩 5000，够用到月底",  # copy: voice
             "密钥是什么意思？", "the api key is in Settings, right?"]  # copy: voice


@pytest.mark.parametrize("said", SPOKEN_OF)
def test_talk_about_a_key_with_no_key_in_it_is_kept_as_said(said):
    assert not person.secret(said) and person.mask(said) == (said, False)


@pytest.mark.parametrize("said, shown", [
    ("my api key is sk-proj-Rk82Jd0aQm4Lw9Zt5Vb1", "my api key is ****"),
    ("密钥：AKIA1234567890ABCDEF", "密钥：****"),
    ("api_key=sk-abcdefgh12345678ijkl", "api_key=****"),
])
def test_a_key_after_its_word_is_masked(said, shown):
    assert person.secret(said) and person.mask(said) == (shown, True)

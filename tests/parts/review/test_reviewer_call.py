"""The reviewer's own call and what code tells it (mibo/review.py). A reasoning model left to think spends the whole
answer budget and gives no verdict, which turned every reviewable step into a card (2026-11-21, deepseek flash):
the call asks for no hidden reasoning, and survives an endpoint that refuses the switch. What code notices about a
command is a hint only where it is true: a step in the folder the person named is not "outside" it, and a wildcard
narrowed by their own word is not wider than their words."""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mibo import review

PASS = {"kind_asked": True, "target_named": True, "text_given": True, "nothing_added": True, "decision": "go",
        "reason": "stand-in"}
ACTION = {"kind": "command", "where": "C:\\Users\\li\\Desktop", "text": "Get-ChildItem C:\\Users\\li\\Desktop"}


class Thinker(BaseHTTPRequestHandler):
    """A reasoning model: with its thinking on it spends the answer budget and says nothing; with it off it answers.
    `refuse` names a field it answers 400 for, as some providers do."""
    refuse = ""
    bodies: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Thinker.bodies.append(body)
        if Thinker.refuse and Thinker.refuse in body:
            self._send(400, {"error": {"message": f"Unrecognized request argument supplied: {Thinker.refuse}"}})
            return
        off = body.get("thinking") == {"type": "disabled"} or body.get("reasoning") == {"enabled": False}
        content = json.dumps(PASS) if off else ""
        self._send(200, {"choices": [{"message": {"content": content}, "finish_reason": "stop" if off else "length"}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5 if off else 1500}})

    def _send(self, status: int, data: dict) -> None:
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def ask_once(refuse: str = "") -> review.Verdict:
    Thinker.refuse, Thinker.bodies = refuse, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Thinker)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ask = review.endpoint(f"http://127.0.0.1:{server.server_address[1]}/v1", "k", "thinker")
        return asyncio.run(review.review(["看看我桌面上有什么"], {**ACTION, "area": "commands"}, ask=ask))  # copy: voice
    finally:
        server.shutdown()
        server.server_close()


def test_a_reasoning_model_still_gives_the_reviewer_a_verdict():
    got = ask_once()
    assert (got.decision, got.by) == ("go", "review")


def test_an_endpoint_that_refuses_the_thinking_switch_still_gets_its_review():
    got = ask_once(refuse="temperature")
    assert (got.decision, got.by) == ("go", "review")
    assert len(Thinker.bodies) == 2 and "temperature" not in Thinker.bodies[-1]


class Cramped(Thinker):
    """A model whose verdict does not fit the reviewer's answer room: cut off with nothing in it at that cap,
    answered when given more room; `room` is the least it needs (0: it never answers)."""
    room = 0

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Thinker.bodies.append(body)
        fits = Cramped.room and body["max_tokens"] >= Cramped.room
        self._send(200, {"choices": [{"message": {"content": json.dumps(PASS) if fits else ""},
                                      "finish_reason": "stop" if fits else "length"}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5 if fits else body["max_tokens"]}})


def ask_cramped(room: int) -> review.Verdict:
    Cramped.room, Thinker.bodies = room, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Cramped)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ask = review.endpoint(f"http://127.0.0.1:{server.server_address[1]}/v1", "k", "cramped")
        return asyncio.run(review.review(["看看我桌面上有什么"], {**ACTION, "area": "commands"}, ask=ask))  # copy: voice
    finally:
        server.shutdown()
        server.server_close()


def test_an_answer_cut_off_empty_at_the_reviewers_cap_is_asked_again_with_more_room():
    got = ask_cramped(room=4000)
    assert (got.decision, got.by) == ("go", "review")
    assert Thinker.bodies[0]["max_tokens"] < 4000 <= Thinker.bodies[1]["max_tokens"]


def test_a_reviewer_that_never_answers_says_it_gave_no_answer_not_that_it_was_unclear():
    got = ask_cramped(room=0)
    assert (got.decision, got.by) == ("ask", "failed")
    assert got.reason == review.said("failed", "zh") != review.said("unclear", "zh")


def test_a_step_in_the_folder_the_person_named_is_not_flagged_as_outside_it():
    said = ["帮我打开桌面上的grok bot"]  # copy: voice
    inside = {"area": "commands", "kind": "command", "where": "C:\\Users\\li\\Desktop",
              "text": "Get-ChildItem -Path C:\\Users\\li\\Desktop -Recurse -Include *grok*"}
    outside = {**inside, "where": "C:\\Users\\li\\Documents", "text": "Get-ChildItem C:\\Users\\li\\Documents"}
    assert not any("Desktop folder" in line for line in review.hints(said, inside))
    assert any("Desktop folder" in line for line in review.hints(said, outside))


def test_a_wildcard_narrowed_by_the_persons_own_word_is_not_wider_than_their_words():
    said = ["帮我打开桌面上的grok bot"]  # copy: voice
    narrowed = {"area": "commands", "kind": "command", "where": "C:\\Users\\li\\Desktop",
                "text": "Get-ChildItem C:\\Users\\li\\Desktop -Include *grok*"}
    wide = {**narrowed, "text": "Get-ChildItem C:\\Users\\li\\Desktop\\*"}
    assert not any("wildcard" in line for line in review.hints(said, narrowed))
    assert any("wildcard" in line for line in review.hints(said, wide))


def test_a_chain_of_reads_is_not_flagged_and_a_chain_that_may_change_something_is():
    said = ["帮我打开桌面上的grok bot"]  # copy: voice
    chain = {"area": "commands", "kind": "command", "where": "C:\\Users\\li\\Desktop",
             "text": "Get-ChildItem C:\\Users\\li\\Desktop | Select-Object FullName"}
    assert not any("chains" in line for line in review.hints(said, {**chain, "only_reads": True}))
    assert any("chains" in line for line in review.hints(said, chain))

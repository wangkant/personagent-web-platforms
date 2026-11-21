"""The scripted model: an OpenAI-compatible server on 127.0.0.1 that answers each request from a script the
test gave, as dsh's llm-mock-server does. Nothing is guessed: a request no script covers is answered 500 and
fails the test with the request shown.

A script is a list of rounds, one per request, taken first in, first out. A round is one of:
    say(text)                     the reply's final words, in the app's reply contract
    call(name, **args)            one native tool call; a list of calls (and a think) is one round
    think(text)                   reasoning_content, beside the calls or the words of that round
    fail(status, body, headers)   an HTTP failure, with headers such as Retry-After
    error_in_200()                status 200 carrying an error object, as some hosts send
    content_filter()              a reply the host's filter cut off
    hang()                        no answer until the test ends or the asker goes; the request's record gets
                                  "ended" when the asker closed the connection
    drop()                        the connection closed with no answer

Inner calls go to their own scripts by purpose, recognised from the constant each module sends first
(review.POLICY, pagejudge.SYSTEM, consolidate's prompt, ingestion's vision prompt, mcp_tools.WORDS_PROMPT), never
from prose. A request
with max_tokens 1 is the endpoint probe a character makes when it starts, answered "ok" unless the test
scripts "probe". Every request is recorded, with its key apart from its headers (None when none was sent).

Each answer reports a deterministic prompt count (`tokens`) as usage.prompt_tokens. With a `window` set, a request
whose count and max_tokens pass it is answered 400 with the window stated, as vLLM and OpenAI word it."""
from __future__ import annotations

import hashlib
import itertools
import json
import re
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mibo import consolidate, ingestion, mcp_tools, review
from mibo.web import pagejudge

MODEL = "scripted"
# What each purpose's request sends first: the router reads it, in this order.
SYSTEM_OF = {
    "reviewer": review.POLICY,
    "judge": pagejudge.SYSTEM,
    "fold": consolidate._PERSON_PROMPT,
    "vision": ingestion.ContentIngestion.VISION_READ_PROMPT,
    # The search words an MCP server's tools are indexed by, once it connects.
    "index": mcp_tools.WORDS_PROMPT,
}
PURPOSES = ("reply", "probe", *SYSTEM_OF)


def say(text: str) -> dict:
    """The reply's final words: plain text, with no call."""
    return {"content": text}


def text(words: str) -> dict:
    """A round's plain words, as an inner call (reviewer, judge, fold, vision) answers."""
    return {"content": words}


def call(name: str, /, **args) -> dict:
    """One tool call. Put several in a list to make them one round."""
    return {"call": {"name": name, "arguments": args}}


def think(words: str) -> dict:
    return {"think": words}


def fail(status: int = 500, body: dict | str | None = None, headers: dict | None = None) -> dict:
    return {"fail": status, "body": body or {"error": {"message": f"scripted failure {status}"}},
            "headers": headers or {}}


def error_in_200() -> dict:
    return fail(200, {"error": {"message": "upstream overloaded", "code": "overloaded"}})


def content_filter() -> dict:
    return {"content": "", "finish": "content_filter"}


def hang() -> dict:
    return {"hang": True}


def drop() -> dict:
    return {"drop": True}


def _step(item) -> dict:
    """A scripted round as one dict: a call, or a list of call and think parts, becomes one round of calls."""
    if isinstance(item, dict) and "call" not in item:
        return item
    if isinstance(item, dict):
        item = [item]
    merged: dict = {"calls": []}
    for part in item:
        if "call" in part:
            merged["calls"].append(part["call"])
        else:
            merged.update(part)
    return merged


def purpose_of(body: dict) -> str:
    """Which script a chat request belongs to, from what it sends (constants, not prose)."""
    messages = body.get("messages") or [{}]
    lead = messages[0].get("content") or ""
    if isinstance(lead, list):
        lead = next((str(p.get("text") or "") for p in lead if p.get("type") == "text"), "")
    for purpose, constant in SYSTEM_OF.items():
        if lead.startswith(constant):
            return purpose
    if body.get("max_tokens") == 1:
        return "probe"
    return "reply"


def tokens(body: dict) -> int:
    """The stand-in's own count of a request: a CJK character one token, three other characters one, four a
    message, the tool definitions by their JSON length over three."""
    def count(text: str) -> int:
        wide = len(_WIDE.findall(text))
        return wide + (len(text) - wide) // 3
    total = 0
    for message in body.get("messages") or []:
        content = message.get("content") or ""
        total += 4 + count(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
        if message.get("tool_calls"):
            total += count(json.dumps(message["tool_calls"], ensure_ascii=False))
    return total + (len(json.dumps(body["tools"], ensure_ascii=False)) // 3 if body.get("tools") else 0)


_WIDE = re.compile("[⺀-鿿가-힯＀-￯]")


class Unscripted(AssertionError):
    """The app asked the model something no script covers."""


class ScriptedModel:
    """One server a session, emptied per test (`reset`). `url` is the base an app's LLM_BASE_URL takes."""

    def __init__(self) -> None:
        self.scripts: dict[str, list[dict]] = {p: [] for p in PURPOSES}
        self.requests: list[dict] = []
        self.unscripted: list[dict] = []
        # The context window in tokens; 0 takes any request.
        self.window = 0
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._released = threading.Event()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._released.set()
        self.server.shutdown()
        self.server.server_close()

    def reset(self) -> None:
        """Forget every script and request, and let a hung answer go."""
        self._released.set()
        with self._lock:
            for rounds in self.scripts.values():
                rounds.clear()
            self.requests.clear()
            self.unscripted.clear()
            self.window = 0
        self._released = threading.Event()

    def script(self, *rounds, purpose: str = "reply") -> ScriptedModel:
        """Queue rounds for `purpose`; each answers one request of it, in order."""
        assert purpose in PURPOSES, f"no purpose {purpose!r}: one of {PURPOSES}"
        with self._lock:
            self.scripts[purpose] += [_step(r) for r in rounds]
        return self

    def left(self, purpose: str = "reply") -> int:
        """Rounds of `purpose` not yet asked for."""
        return len(self.scripts[purpose])

    def asked(self, purpose: str = "reply") -> list[dict]:
        """The recorded chat requests of one purpose, in order."""
        return [r for r in self.requests if r.get("purpose") == purpose]

    def check(self) -> None:
        """Raise, once, with the first request no script covered."""
        with self._lock:
            missed, self.unscripted = self.unscripted, []
        if missed:
            body = missed[0]["body"]
            last = (body.get("messages") or [{}])[-1]
            shown = {"purpose": missed[0]["purpose"], "path": missed[0]["path"],
                     "tools": [t["function"]["name"] for t in body.get("tools") or []],
                     "last message": {**last, "content": str(last.get("content"))[:1500]}}
            raise Unscripted(f"{len(missed)} unscripted model request(s); the first:\n"
                             + json.dumps(shown, ensure_ascii=False, indent=1)[:6000])

    # -- answering --------------------------------------------------------------

    def _answer(self, body: dict) -> tuple[int, dict | str | None, dict]:
        """(status, body, headers) for one chat request; no body for a drop, status 0 and "hang" for a hang."""
        purpose = purpose_of(body)
        asked = tokens(body)
        room = body.get("max_tokens") or body.get("max_completion_tokens") or 0
        if self.window and asked + room > self.window:
            with self._lock:
                self.requests[-1]["purpose"] = purpose
            return 400, {"error": {"message": f"This model's maximum context length is {self.window} tokens. However, "
                                              f"you requested {asked + room} tokens ({asked} in the messages, {room} "
                                              f"in the completion).", "type": "invalid_request_error"}}, {}
        with self._lock:
            self.requests[-1]["purpose"] = purpose
            rounds = self.scripts[purpose]
            step = rounds.pop(0) if rounds else None
            if step is None and purpose == "probe":
                step = text("ok")
            if step is None:
                self.unscripted.append(self.requests[-1])
                return 500, {"error": {"message": f"unscripted {purpose} request"}}, {}
        if step.get("hang"):
            return 0, "hang", {}
        if step.get("drop"):
            return 0, None, {}
        if "fail" in step:
            return step["fail"], step["body"], step["headers"]
        message: dict = {"role": "assistant", "content": step.get("content", "")}
        if step.get("think"):
            message["reasoning_content"] = step["think"]
        if step.get("calls"):
            message["tool_calls"] = [{"id": f"call_{next(self._ids)}", "type": "function",
                                      "function": {"name": c["name"],
                                                   "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                                     for c in step["calls"]]
        finish = step.get("finish") or ("tool_calls" if step.get("calls") else "stop")
        return 200, {"id": f"chatcmpl-{len(self.requests)}", "object": "chat.completion", "model": MODEL,
                     "choices": [{"index": 0, "finish_reason": finish, "message": message}],
                     "usage": {"prompt_tokens": asked, "completion_tokens": 5, "total_tokens": asked + 5}}, {}

    def _handler(self):
        model = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:
                pass

            def _send(self, status: int, body: dict | str, headers: dict | None = None) -> None:
                data = (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                with model._lock:
                    model.requests.append({"path": self.path, "purpose": "models"})
                self._send(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.loads(raw or b"{}")
                headers = {k.lower(): v for k, v in self.headers.items() if k.lower() != "authorization"}
                sent = self.headers.get("Authorization")
                record = {"path": self.path, "body": body, "headers": headers, "key": sent[7:] if sent else None}
                with model._lock:
                    model.requests.append(record)
                if self.path.endswith("/embeddings"):
                    model.requests[-1]["purpose"] = "embeddings"
                    inputs = body.get("input")
                    inputs = [inputs] if isinstance(inputs, str) else list(inputs or [])
                    return self._send(200, {"object": "list", "model": MODEL, "data": [
                        {"object": "embedding", "index": n, "embedding": _vector(str(v))} for n, v in enumerate(inputs)]})
                status, answer, headers = model._answer(body)
                if answer == "hang":
                    # Held until the test ends or the asker closes the connection, which the record notes.
                    released = model._released
                    while not released.wait(0.05):
                        if _gone(self.connection):
                            record["ended"] = True
                            break
                    self.close_connection = True
                elif answer is None:
                    # Nothing is sent, and the connection closes rather than waiting for another request.
                    self.close_connection = True
                else:
                    self._send(status, answer, headers)

        return Handler


def _gone(connection: socket.socket) -> bool:
    """Whether the asker closed the connection: readable with nothing to read, or reset."""
    if not select.select([connection], [], [], 0)[0]:
        return False
    try:
        return not connection.recv(1, socket.MSG_PEEK)
    except OSError:
        return True


def _vector(words: str) -> list[float]:
    """The same 16 numbers for the same words, so embeddings are stable across runs."""
    digest = hashlib.sha256(words.encode("utf-8")).digest()
    return [b / 255 for b in digest[:16]]

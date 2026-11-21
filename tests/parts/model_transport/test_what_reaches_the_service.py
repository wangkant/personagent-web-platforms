"""What leaves the computer for the person's model service, and when it stops (mibo/transport.py, the official OpenAI
SDK's client): only the person's key and standard headers, never the SDK's own description of this computer nor
OpenAI account headers from the environment, and no Authorization at all for a service without a key; the fields a
provider names (thinking switches) at the top of the request, and the reasoning a model wrote sent back with its tool
calls, as DeepSeek requires; a stopped turn and a service that never answers both end the request."""
from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path

import pytest

from harness.app import KEY, App, quiet_start
from harness.model import MODEL, call, hang, say, think
from harness.turn import run_turn
from mibo import transport
from mibo.endpoints import chat_completions_url

# What any HTTP client sends; the key travels apart, in Authorization.
STANDARD = {"accept", "accept-encoding", "connection", "content-length", "content-type", "host", "user-agent"}


@pytest.fixture
def opened(model, monkeypatch, tmp_path):
    apps: list[App] = []

    def open_app(**settings) -> App:
        quiet_start(monkeypatch)
        apps.append(App(Path(os.environ["AGENT_HOME"]), model, PERSONA_BROWSER_PATH=str(tmp_path / "no-browser"),
                        LLM_MAX_RETRIES="0", **settings).open())
        return apps[-1]
    yield open_app
    for app in apps:
        app.close()


def ended(request: dict) -> bool:
    """Whether the asker closed the connection of a hung request, within five seconds."""
    waited = time.monotonic() + 5
    while not request.get("ended") and time.monotonic() < waited:
        time.sleep(0.05)
    return bool(request.get("ended"))


def test_only_the_persons_key_and_standard_headers_reach_the_service(opened, monkeypatch):
    # An OpenAI developer's own account settings, which the SDK would otherwise send to any service.
    monkeypatch.setenv("OPENAI_ORG_ID", "org-someone")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-someone")
    app = opened()
    done = run_turn(app, "hello", say("Hello."))
    sent = app.model.requests
    assert done.reply == "Hello." and {r["purpose"] for r in sent} == {"probe", "reply"}
    assert all(set(r["headers"]) <= STANDARD for r in sent), [sorted(r["headers"]) for r in sent]
    assert {r["key"] for r in sent} == {KEY}


def test_a_service_without_a_key_is_sent_no_authorization(model):
    model.script(say("Hello."))

    async def ask() -> str:
        answer = await transport.client(chat_completions_url(model.url), "").chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": "hello"}])
        return answer.choices[0].message.content
    assert asyncio.run(ask()) == "Hello."
    (sent,) = model.requests
    assert sent["key"] is None and set(sent["headers"]) <= STANDARD


def test_a_providers_own_fields_lead_the_request_and_reasoning_goes_back_with_its_calls(opened):
    app = opened()
    steps = [{"step": "pick a beach", "status": "in_progress"}]
    done = run_turn(app, "Plan the weekend", [think("A plan first."), call("update_plan", steps=steps)],
                    say("Planned."))
    first, second = done.asked()
    # The thinking switch an unknown service is asked with, where the service reads it: the top of the body.
    assert first["thinking"] == {"type": "disabled"} and "extra_body" not in first
    assert any(m.get("reasoning_content") == "A plan first." and m.get("tool_calls") for m in second["messages"])


def test_a_stopped_turn_ends_its_request_to_the_service(opened):
    app = opened()
    app.model.script(hang())
    posted = threading.Thread(target=app.post, args=("/api/chat", {"text": "hello", "chat": "main"}), daemon=True)
    posted.start()
    while not app.model.asked("reply"):
        time.sleep(0.05)
    app.post("/api/chat/stop", {"chat": "main"})
    posted.join(10)
    assert ended(app.model.asked("reply")[0]) and not posted.is_alive()


def test_a_service_that_never_answers_fails_the_turn_when_its_time_is_up(opened):
    app = opened(LLM_TIMEOUT_S="1")
    began = time.monotonic()
    done = run_turn(app, "hello", hang())
    assert done.reply == "" and time.monotonic() - began < 5
    assert all(ended(r) for r in app.model.asked("reply"))

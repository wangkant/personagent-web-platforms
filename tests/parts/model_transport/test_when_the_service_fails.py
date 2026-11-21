"""What a failing model service costs the person, through the page against the scripted model (mibo/llm.py on the
official OpenAI SDK, mibo/transport.py): a rate-limited reply waits as long as the service says and then arrives; with
a backup model set, a throttled, failing or erroring service hands the reply to it at once; a problem with the key or
the account is not tried on the backup, nor asked again. The error bodies are stand-ins built from each service's
public documentation (DeepSeek, Alibaba DashScope, Volcengine Ark, SiliconFlow, OpenRouter), not their servers."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from harness.app import KEY, App, quiet_start
from harness.model import drop, error_in_200, fail, hang, say
from harness.turn import run_turn
from mibo import transport

RATE_LIMITED = {"error": {"message": "Rate limit reached for requests", "type": "rate_limit_error"}}


@pytest.fixture
def opened(model, monkeypatch, tmp_path):
    """open(**settings) -> the app on this test's home with the scripted model behind it; the one opened before
    is stopped first."""
    apps: list[App] = []

    def open_app(**settings) -> App:
        while apps:
            apps.pop().close()
        quiet_start(monkeypatch)
        apps.append(App(Path(os.environ["AGENT_HOME"]), model, PERSONA_BROWSER_PATH=str(tmp_path / "no-browser"),
                        **settings).open())
        return apps[-1]
    yield open_app
    while apps:
        apps.pop().close()


def replies(done) -> list[str]:
    """The model each reply request of a turn went to, in order."""
    return [body["model"] for body in done.asked()]


@pytest.mark.slow
def test_a_rate_limited_reply_waits_as_long_as_the_service_says_and_then_arrives(opened):
    app = opened(LLM_MAX_RETRIES="2")
    began = time.monotonic()
    done = run_turn(app, "hello", fail(429, RATE_LIMITED, {"Retry-After": "2"}), say("Here I am."))
    assert done.status == 200 and done.reply == "Here I am."
    # Its own backoff would have asked again within two seconds, into the same limit.
    assert time.monotonic() - began >= 2.0
    assert replies(done) == ["scripted", "scripted"]


@pytest.mark.parametrize("failure", [fail(429, RATE_LIMITED, {"Retry-After": "30"}), fail(503), drop(),
                                     error_in_200()], ids=["throttled", "outage", "dropped", "error inside a 200"])
def test_with_a_backup_model_a_failing_service_hands_the_reply_to_it_at_once(opened, failure):
    app = opened(LLM_MAX_RETRIES="2", LLM_FALLBACK_MODEL="backup")
    began = time.monotonic()
    done = run_turn(app, "hello", failure, say("From the backup."))
    assert done.status == 200 and done.reply == "From the backup."
    assert replies(done) == ["scripted", "backup"]
    assert time.monotonic() - began < 2.0


# Each service's documented answer when the account, not the request, is at fault.
ACCOUNT = {
    "DeepSeek 402": (402, {"error": {"message": "Insufficient Balance", "type": "unknown_error", "param": None,
                                     "code": "invalid_request_error"}}),
    "DashScope arrears": (403, {"error": {"message": "Access denied, please make sure your account is in good "
                                                     "standing.", "type": "Arrearage", "param": None,
                                          "code": "Arrearage"}, "request_id": "0a1b"}),
    "DashScope quota as a 429": (429, {"error": {"message": "You exceeded your current quota, please check your plan "
                                                            "and billing details.", "type": "insufficient_quota",
                                                 "param": None, "code": "insufficient_quota"}}),
    "Volcengine overdue": (403, {"error": {"code": "AccountOverdueError", "message": "The request failed because "
                                           "your account has an overdue balance. Request id: 0a1b", "param": "",
                                           "type": "Forbidden"}}),
    "a wrong key": (401, {"error": {"message": "Incorrect API key provided.", "type": "invalid_request_error",
                                    "code": "invalid_api_key"}}),
}


@pytest.mark.parametrize("status, body", list(ACCOUNT.values()), ids=list(ACCOUNT))
def test_a_key_or_account_problem_is_neither_asked_again_nor_tried_on_the_backup(opened, status, body):
    alone = opened(LLM_MAX_RETRIES="2")
    done = run_turn(alone, "hello", fail(status, body))
    assert done.status >= 400 and done.reply == "" and replies(done) == ["scripted"]
    # Opened second: a page takes its home's settings into the environment, and keeps them.
    backed = opened(LLM_MAX_RETRIES="2", LLM_FALLBACK_MODEL="backup")
    assert replies(run_turn(backed, "hello", fail(status, body))) == ["scripted"]


# Rate limits and outages as each service words them: asked again, or handed to the backup.
BUSY = {
    "Volcengine RPM": (429, {"error": {"code": "RateLimitExceeded.EndpointRPMExceeded", "message": "The request "
                                       "exceeds the RPM limit of the endpoint.", "param": "", "type": "TooManyRequests"}}),
    "DashScope throttling": (429, {"error": {"message": "Requests rate limit exceeded, please try again later.",
                                             "type": "Throttling.RateQuota", "code": "Throttling.RateQuota"}}),
    "SiliconFlow plain text": (429, "Request was rejected due to rate limiting"),
    "OpenRouter upstream": (429, {"error": {"message": "Provider returned error", "code": 429}}),
    "DeepSeek overloaded": (503, {"error": {"message": "Server Overloaded", "type": "server_error"}}),
    "an outage whose body speaks of capacity": (503, {"error": {"message": "insufficient capacity, try later"}}),
}


@pytest.mark.parametrize("status, body", list(BUSY.values()), ids=list(BUSY))
def test_a_busy_service_is_asked_again_and_with_a_backup_hands_over(opened, monkeypatch, status, body):
    monkeypatch.setattr(transport, "WAIT_SCALE", 0)
    alone = opened(LLM_MAX_RETRIES="2")
    done = run_turn(alone, "hello", fail(status, body), say("Here I am."))
    assert done.reply == "Here I am." and replies(done) == ["scripted", "scripted"]
    backed = opened(LLM_MAX_RETRIES="2", LLM_FALLBACK_MODEL="backup")
    assert replies(run_turn(backed, "hello", fail(status, body), say("From the backup."))) == ["scripted", "backup"]


def test_a_request_the_service_refuses_gets_one_try_on_the_backup_in_its_own_error_shape(opened):
    # SiliconFlow answers with a code and message of its own rather than an `error` object.
    refused = fail(400, {"code": 20012, "message": "Model does not exist. Please check it carefully.", "data": None})
    app = opened(LLM_MAX_RETRIES="2", LLM_FALLBACK_MODEL="backup")
    # One answered turn first: a service still being asked whether it takes `thinking` reads any 400 as its no.
    run_turn(app, "hi", say("Hi."))
    done = run_turn(app, "hello", refused, say("From the backup."))
    assert done.reply == "From the backup." and replies(done) == ["scripted", "backup"]


def test_a_400_whose_plain_text_echoes_account_words_is_still_the_requests_fault(opened):
    app = opened(LLM_MAX_RETRIES="2", LLM_FALLBACK_MODEL="backup")
    run_turn(app, "hi", say("Hi."))
    done = run_turn(app, "my bank balance is low", fail(400, "invalid request: my bank balance is low"),
                    say("From the backup."))
    assert replies(done) == ["scripted", "backup"]


@pytest.mark.slow
def test_a_hanging_service_hands_the_turn_to_the_backup_without_waiting_twice(opened):
    app = opened(LLM_MAX_RETRIES="2", LLM_FALLBACK_MODEL="backup", LLM_TIMEOUT_S="2")
    began = time.monotonic()
    done = run_turn(app, "hello", hang(), say("From the backup."))
    assert done.reply == "From the backup." and replies(done) == ["scripted", "backup"]
    assert time.monotonic() - began < 3.0


def test_one_refused_request_leaves_the_model_in_and_a_throttled_one_sits_out_its_cooldown(opened, monkeypatch):
    monkeypatch.setattr(transport, "WAIT_SCALE", 0)
    # A cooldown long enough that the three turns inside it end before it does on a slow machine.
    app = opened(LLM_MAX_RETRIES="2", LLM_FALLBACK_MODEL="backup", LLM_RATE_LIMIT_COOLDOWN_S="8")
    run_turn(app, "hi", say("Hi."))
    refused = run_turn(app, "hello", fail(400, {"error": {"message": "Input data may contain inappropriate content."}}),
                       say("From the backup."))
    assert replies(refused) == ["scripted", "backup"]
    assert replies(run_turn(app, "and now?", say("Back."))) == ["scripted"]
    assert replies(run_turn(app, "again", fail(429, RATE_LIMITED), say("From the backup."))) == ["scripted", "backup"]
    assert replies(run_turn(app, "still?", say("Still the backup."))) == ["backup"]
    # The backup failing while the model sits out: the model gets its one try after all.
    assert replies(run_turn(app, "and?", fail(400), say("Back early."))) == ["backup", "scripted"]
    time.sleep(8.1)
    assert replies(run_turn(app, "later", say("Back."))) == ["scripted"]


def test_a_backup_on_a_service_of_its_own_is_checked_when_the_app_starts(opened, model):
    opened(LLM_FALLBACK_MODEL="backup", LLM_FALLBACK_BASE_URL=model.url, LLM_FALLBACK_API_KEY="sk-backup-key")
    waited = time.monotonic() + 10
    while len(model.asked("probe")) < 2 and time.monotonic() < waited:
        time.sleep(0.05)
    assert sorted((r["body"]["model"], r["key"]) for r in model.asked("probe")) == [
        ("backup", "sk-backup-key"), ("scripted", KEY)]

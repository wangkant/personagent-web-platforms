"""The answer's room a reply asks for (max_tokens) comes from the model's measured window (mibo/context.py), so the
request and the answer together fit it: a hello on a small model is answered, a large one asks as before, the window
an overflow error states is learned, and the person's CONTEXT_WINDOW setting wins."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from harness.app import App, quiet_start
from harness.model import say, tokens
from mibo import abilities, context
from mibo.endpoints import chat_completions_url

THINKS = {"thinking": "yes", "thinking_detail": {"param": "reasoning_effort", "reasons": True}}


def _abilities(app, **rec) -> None:
    """The model's ability record, as the start's measure writes it."""
    url = chat_completions_url(app.model.url)
    path = app.home / "runtime" / abilities.FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"models": {abilities.key_of(url, "scripted"): {"url": url, "model": "scripted",
                                                                               **rec}}}), encoding="utf-8")


@pytest.mark.parametrize("window", [16384, 65536])
@pytest.mark.parametrize("thinks", [False, True])
def test_a_hello_asks_for_the_room_its_window_leaves_and_fits_it(app, turn, window, thinks):
    app.model.window = window
    _abilities(app, context=window, **(THINKS if thinks else {}))
    done = turn("hello", say("hey, you."))
    (body,) = done.asked()
    assert (done.status, done.reply) == (200, "hey, you.")
    assert body["max_tokens"] == context.budget(window, "standard" if thinks else "").answer
    assert tokens(body) + body["max_tokens"] <= window


def test_a_model_whose_window_nobody_states_is_given_the_assumed_windows_room(turn):
    (body,) = turn("hello", say("hey.")).asked()
    assert body["max_tokens"] == context.budget(context.ASSUMED).answer


def test_the_window_an_overflow_error_states_is_the_models_from_then_on(app, turn):
    # The host lists more than its server takes: the first reply is refused, naming the window.
    app.model.window = 16384
    _abilities(app, context=65536)
    refused = turn("hello")
    assert refused.status != 200 and refused.asked()[0]["max_tokens"] == context.budget(65536).answer
    done = turn("hello again", say("hey."))
    assert done.status == 200 and done.asked()[0]["max_tokens"] == context.budget(16384).answer


@pytest.fixture
def set_window(model, monkeypatch, tmp_path):
    """The app with the person's CONTEXT_WINDOW set to 16384."""
    quiet_start(monkeypatch)
    served = App(Path(os.environ["AGENT_HOME"]), model, PERSONA_BROWSER_PATH=str(tmp_path / "no-browser"),
                 LLM_MAX_RETRIES="0", CONTEXT_WINDOW="16384").open()
    yield served
    served.close()


def test_the_window_the_person_sets_wins_over_the_hosts(set_window):
    from harness.turn import run_turn
    set_window.model.window = 16384
    _abilities(set_window, context=1_048_576)
    done = run_turn(set_window, "hello", say("hey."))
    assert done.status == 200 and done.asked()[0]["max_tokens"] == context.budget(16384).answer

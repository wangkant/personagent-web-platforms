"""What the bundled public model catalogue (mibo/modelcatalog.py, a models.dev snapshot) gives a model before and
beside the app's own check: a listed model answers its first message without waiting for the check, the check's
findings win where they differ, the window comes from the host, then the catalogue, and an overflow error's number
beats both and stays; the usage popover prices a listed model; reasoning is switched off the way the catalogue
says; and the build tool writes what the app reads. The suite reads the small catalogue in tests/data."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import modelcatalog as build
from harness.app import KEY, App, quiet_start
from harness.model import MODEL, call, say
from harness.turn import run_turn
from mibo import abilities, context, loop, modelcatalog, spend, usage
from mibo.endpoints import chat_completions_url
from mibo.panels import modelbar

DEEPSEEK = chat_completions_url("https://api.deepseek.com")


def _listed(monkeypatch, tmp_path, url: str, **line) -> None:
    """A catalogue whose one service is at `url`, listing the scripted model with `line`."""
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps({"providers": {"here": {"api": url, "models": {MODEL: {
        "tool_call": True, "reasoning": False, "images": False, "context": 200000, **line}}}}}), encoding="utf-8")
    monkeypatch.setattr(modelcatalog, "PATH", path)


def _record(runtime: Path, url: str, model: str, **rec) -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / abilities.FILE).write_text(json.dumps({"models": {abilities.key_of(url, model): {
        "url": url, "model": model, **rec}}}), encoding="utf-8")


@pytest.fixture
def served(model, monkeypatch, tmp_path):
    apps: list[App] = []

    def open_app() -> App:
        quiet_start(monkeypatch)
        apps.append(App(Path(os.environ["AGENT_HOME"]), model, PERSONA_BROWSER_PATH=str(tmp_path / "no-browser"),
                        LLM_MAX_RETRIES="0").open())
        return apps[-1]
    yield open_app
    for app in apps:
        app.close()


@pytest.mark.parametrize("listed", [True, False])
def test_a_listed_model_answers_its_first_message_without_waiting_for_its_check(served, model, monkeypatch,
                                                                                tmp_path, listed):
    if listed:
        _listed(monkeypatch, tmp_path, model.url)
    # Long enough that a slow machine's turn is never taken for the wait.
    monkeypatch.setattr(loop, "MEASURE_WAIT_S", 3.0)
    app = served()
    # The check of the saved model still under way, as at a first start.
    monkeypatch.setitem(modelbar._checks, (chat_completions_url(model.url), MODEL), SimpleNamespace(done=lambda: False))
    began = time.monotonic()
    done = run_turn(app, "hello", say("Hello."))
    took = time.monotonic() - began
    assert done.reply == "Hello."
    assert (took < 3.0) if listed else (took >= 3.0), took


def test_what_the_check_finds_wins_over_the_catalogue(tmp_path):
    runtime = tmp_path / "runtime"
    seed = abilities.record(runtime, DEEPSEEK, "deepseek-flash")
    assert (seed["tools"], seed["pictures"], seed["thinking"]) == ("yes", "yes", "yes")
    # Until the check finds how this host spells thinking on, replies do not think.
    assert abilities.reply_effort(runtime, DEEPSEEK, "deepseek-flash") is None
    _record(runtime, DEEPSEEK, "deepseek-flash", tools="no", pictures="", thinking="yes",
            thinking_detail={"reasons": True, "off": True, "param": "thinking"})
    found = abilities.record(runtime, DEEPSEEK, "deepseek-flash")
    # The check's "no" wins; an answer it could not get keeps the catalogue's.
    assert (found["tools"], found["pictures"]) == ("no", "yes")
    assert abilities.reply_effort(runtime, DEEPSEEK, "deepseek-flash") == ("standard", "thinking")
    assert abilities.record(runtime, DEEPSEEK, "unlisted-model") is None


def test_the_window_comes_from_the_host_then_the_catalogue_and_an_overflow_beats_both(tmp_path):
    runtime = tmp_path / "runtime"
    caller = SimpleNamespace(memory_file=runtime / "memory.json")
    assert context.known_window(caller, DEEPSEEK, "deepseek-flash") == (1048576, "catalogue")
    assert context.known_window(caller, DEEPSEEK, "unlisted-model") == (context.ASSUMED, "assumed")
    _record(runtime, DEEPSEEK, "deepseek-flash", context=131072)
    assert context.known_window(caller, DEEPSEEK, "deepseek-flash") == (131072, "host")
    _record(runtime, DEEPSEEK, "deepseek-flash")
    context.overflowed(caller, DEEPSEEK, "deepseek-flash", "This model's maximum context length is 65536 tokens.")
    again = SimpleNamespace(memory_file=runtime / "memory.json")
    assert context.known_window(again, DEEPSEEK, "deepseek-flash") == (65536, "error")
    assert context.known_window(again, DEEPSEEK, "deepseek-flash", setting=40000) == (40000, "setting")


def test_a_window_an_overflow_stated_stays_through_a_fresh_check(model, monkeypatch, tmp_path):
    _listed(monkeypatch, tmp_path, model.url)
    home = tmp_path / "home"
    values = {"LLM_BASE_URL": model.url, "LLM_API_KEY": KEY, "LLM_MODEL": MODEL}
    url = chat_completions_url(model.url)
    _record(home / "runtime", url, MODEL, tools="yes", context_error=16384)
    # The check: a tool round, a picture it cannot see, JSON mode, and thinking asked two ways.
    model.script(call("get_time"), say("I can't see images."), say('{"ok": true}'), say("yes"), say("yes"))
    rec = asyncio.run(abilities.check(home, values))
    assert rec["tools"] == "yes" and rec["context_error"] == 16384
    assert context.window(abilities.record(home / "runtime", url, MODEL)) == (16384, "error")


def test_the_usage_popover_prices_a_listed_model_and_leaves_an_unlisted_one_in_tokens(tmp_path):
    home = tmp_path / "home"

    def view(base: str, model: str) -> dict:
        values = {"LLM_BASE_URL": base, "LLM_API_KEY": "sk-x", "LLM_MODEL": model}
        runtime = home / "runtime"
        for name in (usage.USAGE_FILE, usage.PAGE_USAGE_FILE, spend.FILE):
            (runtime / name).unlink(missing_ok=True)
        book = usage.UsageBook(runtime / usage.PAGE_USAGE_FILE)
        book.add(model, {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}, time.time(), "reply")
        book.flush(time.time())
        return asyncio.run(spend.view(home, values))["usage"]["today"]

    assert view("https://open.bigmodel.cn/api/paas/v4", "glm-paid")["cost"] == pytest.approx(5.0)
    # The coding plan on the same host is priced as its own address lists it.
    assert view("https://open.bigmodel.cn/api/coding/paas/v4", "glm-paid")["cost"] == 0.0
    unpriced = view("https://open.bigmodel.cn/api/paas/v4", "glm-unlisted")
    assert unpriced["tokens"] == 2_000_000 and unpriced["cost"] is None


@pytest.mark.parametrize("line, sent, left_out", [
    ({"reasoning": True, "reasoning_options": [{"type": "toggle"}]}, {"thinking": {"type": "disabled"}},
     ("reasoning_effort",)),
    ({"reasoning": True, "reasoning_options": [{"type": "effort", "values": ["none", "low", "high"]}]},
     {"reasoning_effort": "none"}, ("thinking",)),
    ({"reasoning": False}, {}, ("thinking", "reasoning_effort")),
])
def test_reasoning_is_switched_off_the_way_the_catalogue_says(served, model, monkeypatch, tmp_path, line, sent,
                                                               left_out):
    _listed(monkeypatch, tmp_path, model.url, **line)
    done = run_turn(served(), "hello", say("Hello."))
    asked = done.asked()[0]
    assert done.reply == "Hello." and all(asked.get(k) == v for k, v in sent.items())
    assert not set(left_out) & set(asked), sorted(asked)


def test_a_model_that_sets_its_own_temperature_is_sent_none(model, monkeypatch, tmp_path):
    from mibo import drafting
    _listed(monkeypatch, tmp_path, model.url, temperature=False)
    caller = drafting._Client({"LLM_BASE_URL": model.url, "LLM_API_KEY": KEY, "LLM_MODEL": MODEL},
                              tmp_path / "runtime" / usage.PAGE_USAGE_FILE)
    model.script(say("ok"))
    asyncio.run(caller._post_chat(chat_completions_url(model.url), KEY,
                                  {"model": MODEL, "temperature": 0.3, "messages": [{"role": "user", "content": "hi"}]},
                                  timeout=10))
    assert "temperature" not in model.asked()[0]["body"]


def test_the_build_tool_writes_what_the_app_reads(tmp_path, monkeypatch):
    raw = {
        "deepseek": {"api": "https://api.deepseek.com", "models": {"deepseek-flash": {
            "id": "deepseek-flash", "name": "DeepSeek Flash", "description": "long words", "tool_call": True,
            "reasoning": True, "reasoning_options": [{"type": "toggle"}], "temperature": True,
            "modalities": {"input": ["text", "image"], "output": ["text"]}, "limit": {"context": 1000, "output": 10},
            "cost": {"input": 2.0, "output": 4.0}, "release_date": "2026-10-17"}}},
        # The catalogue carries no address for a service its AI SDK package knows.
        "openai": {"models": {"gpt-luna": {"tool_call": True, "reasoning": False,
                                           "modalities": {"input": ["text"]}, "limit": {"context": 5}}}},
        "elsewhere": {"api": "https://api.elsewhere.example/v1", "models": {"m": {"tool_call": True}}},
    }
    providers = build.trim(raw)
    assert sorted(providers) == ["deepseek", "openai"]
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"providers": providers}), encoding="utf-8")
    monkeypatch.setattr(modelcatalog, "PATH", path)
    assert modelcatalog.entry("https://api.deepseek.com/v1/chat/completions", "DeepSeek-Flash") == {
        "tool_call": True, "reasoning": True, "reasoning_options": [{"type": "toggle"}], "temperature": True,
        "images": True, "context": 1000, "cost": [2.0, 4.0, None]}
    assert modelcatalog.price("https://api.deepseek.com", "deepseek-flash") == (2e-6, 4e-6, None)
    assert modelcatalog.abilities("https://api.openai.com/v1", "gpt-luna")["pictures"] == "no"


def test_the_bundled_snapshot_names_its_source_and_every_service_its_address():
    shipped = json.loads(Path(modelcatalog.__file__).with_name("modelcatalog.json").read_text(encoding="utf-8"))
    assert shipped["source"] == "https://models.dev/api.json" and len(shipped["sha256"]) == 64
    assert shipped["providers"] and all(p["api"].startswith("https://") and p["models"]
                                        for p in shipped["providers"].values())

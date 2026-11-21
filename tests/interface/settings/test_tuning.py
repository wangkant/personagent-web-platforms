"""Settings › tuning: the backup model and the models for other uses, saved the way the page saves the rest."""
from __future__ import annotations

import json

import pytest

from harness.app import TOKEN
from mibo import abilities, drafting, welcome
from mibo import setup_wizard as sw
from mibo.endpoints import chat_completions_url
from mibo.panels import tuning

HEADERS = {welcome.TOKEN_HEADER: TOKEN}
SECRET = "sk-backup-secret-0123456789"


@pytest.fixture
def configured(workspace, monkeypatch):
    """A home with a model, a key check that passes and is kept, and the character's restarts counted."""
    sw.copy_env_template(workspace.home)
    sw.write_env(workspace.env, {"LLM_API_KEY": "sk-main-key-0123456789", "LLM_BASE_URL": "https://api.deepseek.com",
                                 "LLM_MODEL": "deepseek-chat", "PERSONA_NAME": "Mira", "AGENT_LANG": "en"})
    probes, restarts = [], []
    monkeypatch.setattr(sw, "probe_key", lambda *a, **kw: probes.append(a) or sw.Probe("u", 200, ""))
    monkeypatch.setattr(workspace.character, "reload", lambda: restarts.append(True))
    workspace.probes, workspace.restarts = probes, restarts
    return workspace


def post(client, payload):
    return client.post("/api/tuning", headers=HEADERS, json=payload)


def test_view_lists_every_field_and_never_a_key(client, configured):
    sw.write_env(configured.env, {"LLM_FALLBACK_API_KEY": SECRET})
    response = client.get("/api/tuning", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert SECRET not in response.text and "sk-main-key" not in response.text
    assert body["secrets"]["LLM_FALLBACK_API_KEY"] == sw.mask_secret(SECRET)
    assert set(body["values"]) == set(tuning.BY_KEY) - {"LLM_FALLBACK_API_KEY"}
    assert body["llmBaseUrl"] == "https://api.deepseek.com"


def test_saving_writes_only_what_changed_and_restarts(client, configured):
    before = configured.env.read_text(encoding="utf-8")
    body = post(client, {"values": {"LLM_JUDGE_MODEL": "judge-small", "LLM_DM_MODEL": "chat-large",
                                    # The same as what is there: not written.
                                    "LLM_FALLBACK_MODEL": ""}}).json()
    assert body["changed"] and configured.restarts == [True]
    values = configured.config()
    assert values["LLM_JUDGE_MODEL"] == "judge-small" and values["LLM_DM_MODEL"] == "chat-large"
    after = configured.env.read_text(encoding="utf-8")
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert {b.split("=")[0] for _, b in changed} == {"LLM_JUDGE_MODEL", "LLM_DM_MODEL"}
    assert len(before.splitlines()) == len(after.splitlines())
    assert body["values"]["LLM_JUDGE_MODEL"] == "judge-small"


def test_nothing_changed_writes_nothing(client, configured):
    before = configured.env.read_bytes()
    body = post(client, {"values": {"LLM_JUDGE_MODEL": ""}}).json()
    assert body["changed"] is False and configured.restarts == []
    assert configured.env.read_bytes() == before


@pytest.mark.parametrize("key, value", [
    ("LLM_FALLBACK_MODEL", "two words"), ("LLM_FALLBACK_BASE_URL", "ftp://x"),
    ("LLM_JUDGE_MODEL", "${LLM_API_KEY}"), ("LLM_DM_MODEL", "x" * 257),
    ("CONTEXT_WINDOW", "128k"), ("CONTEXT_WINDOW", "512"),
])
def test_a_bad_value_is_refused_naming_its_field(client, configured, key, value):
    before = configured.env.read_bytes()
    response = post(client, {"values": {key: value}})
    assert response.status_code == 400
    assert response.json()["error"] == f"tuning_invalid:{key}"
    assert configured.env.read_bytes() == before and configured.restarts == []


def test_the_context_window_says_where_it_comes_from_and_a_typed_one_is_saved(client, configured):
    # Nothing states it: an assumption, which the page says.
    assert client.get("/api/tuning", headers=HEADERS).json()["contextWindow"] == {"tokens": 32768, "from": "assumed"}
    url = chat_completions_url("https://api.deepseek.com")
    path = drafting.usage_path(configured.home, configured.config()).with_name(abilities.FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"models": {abilities.key_of(url, "deepseek-chat"): {"context": 131072}}}),
                    encoding="utf-8")
    assert client.get("/api/tuning", headers=HEADERS).json()["contextWindow"] == {"tokens": 131072, "from": "host"}
    assert post(client, {"values": {"CONTEXT_WINDOW": " 65536 "}}).json()["changed"]
    assert configured.config()["CONTEXT_WINDOW"] == "65536"


def test_other_refusals(client, configured):
    assert post(client, {"values": {"SERVER_HOST": "0.0.0.0"}}).json()["error"] == "tuning_unknown"
    assert post(client, {"values": {"LLM_API_KEY": "x"}}).json()["error"] == "tuning_unknown"
    assert post(client, {"values": {"LLM_FALLBACK_BASE_URL": "http://example.com"}}).json()["error"] == "tuning_https"
    assert "SERVER_HOST" not in configured.env.read_text(encoding="utf-8")


def test_refused_before_a_model(client, configured):
    sw.write_env(configured.env, {"LLM_API_KEY": ""})
    assert post(client, {"values": {"LLM_JUDGE_MODEL": "j"}}).json()["error"] == "tuning_not_configured"


def test_backup_model_key_is_kept_with_its_address_and_never_returned(client, configured):
    other = "https://openrouter.ai/api/v1"
    response = post(client, {"values": {"LLM_FALLBACK_MODEL": "cheap", "LLM_FALLBACK_BASE_URL": other}})
    assert response.json()["error"] == "tuning_key_needed"
    response = post(client, {"values": {"LLM_FALLBACK_MODEL": "cheap", "LLM_FALLBACK_BASE_URL": other,
                                        "LLM_FALLBACK_API_KEY": SECRET}})
    assert response.status_code == 200 and SECRET not in response.text
    assert configured.probes[-1] == (other, SECRET, "cheap")
    values = configured.config()
    assert values["LLM_FALLBACK_API_KEY"] == SECRET and values["LLM_FALLBACK_BASE_URL"] == other
    # A blank key keeps the saved one; a new address drops it.
    post(client, {"values": {"LLM_FALLBACK_MODEL": "cheaper", "LLM_FALLBACK_API_KEY": ""}})
    assert configured.config()["LLM_FALLBACK_API_KEY"] == SECRET
    assert post(client, {"values": {"LLM_FALLBACK_BASE_URL": "https://api.groq.com/openai/v1"}}).json()["error"] \
        == "tuning_key_needed"
    post(client, {"values": {"LLM_FALLBACK_BASE_URL": ""}})
    assert configured.config()["LLM_FALLBACK_API_KEY"] == ""
    # On the main service, the main key is used to check it.
    post(client, {"values": {"LLM_FALLBACK_MODEL": "deepseek-other"}})
    assert configured.probes[-1] == ("https://api.deepseek.com", "sk-main-key-0123456789", "deepseek-other")


@pytest.mark.parametrize("status, code", [(401, "tuning_fallback_rejected"), (429, "tuning_fallback_limited"),
                                          (500, "tuning_fallback_unreachable")])
def test_a_backup_model_that_does_not_answer_is_not_saved(client, configured, monkeypatch, status, code):
    monkeypatch.setattr(sw, "probe_key", lambda *a, **kw: sw.Probe("u", status, "echo " + SECRET))
    response = post(client, {"values": {"LLM_FALLBACK_MODEL": "cheap"}})
    assert response.json()["error"] == code and SECRET not in response.text
    assert configured.config()["LLM_FALLBACK_MODEL"] == ""


def test_backup_model_unreachable_when_the_probe_raises(client, configured, monkeypatch):
    def boom(*a, **kw):
        raise OSError(SECRET)
    monkeypatch.setattr(sw, "probe_key", boom)
    response = post(client, {"values": {"LLM_FALLBACK_MODEL": "cheap"}})
    assert response.json()["error"] == "tuning_fallback_unreachable" and SECRET not in response.text

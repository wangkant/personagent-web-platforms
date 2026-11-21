"""First-run setup through the page: one key makes a home, a refused key keeps the working one, what someone
wrote survives, and the state the page shows. The server's boundary is a floor in tests/floors/boundary/."""
from __future__ import annotations

import pytest

from harness.app import TOKEN
from harness.model import fail, say
from mibo import setup_wizard as sw
from mibo import welcome

HEADERS = {welcome.TOKEN_HEADER: TOKEN}


@pytest.fixture(autouse=True)
def accepted(monkeypatch):
    """Every key the page checks is taken by its service."""
    monkeypatch.setattr(sw, "probe_key", lambda *a, **kw: sw.Probe("http://127.0.0.1", 200, ""))


def test_one_key_is_enough_and_state_does_not_leak_it(client, workspace):
    key = "sk-local-secret-123456789"
    response = client.post("/api/config", headers=HEADERS, json={"apiKey": key, "lang": "zh"})
    assert response.status_code == 200
    assert response.json()["configured"]
    assert key not in response.text
    values = workspace.config()
    assert values["LLM_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert values["PERSONA_NAME"] == "Mibo"
    assert (workspace.home / "persona.txt").is_file()
    assert key not in client.get("/api/state", headers=HEADERS).text


def test_invalid_key_does_not_replace_a_working_configuration(client, workspace, monkeypatch):
    sw.write_env(workspace.env, {"LLM_API_KEY": "old-key", "LLM_BASE_URL": "https://api.deepseek.com"})
    monkeypatch.setattr(sw, "probe_key", lambda *a, **kw: sw.Probe("url", 401, "echoed-sensitive-key"))
    response = client.post("/api/config", headers=HEADERS, json={"apiKey": "new-key"})
    assert response.status_code == 400 and response.json()["error"] == "key_rejected"
    assert "echoed-sensitive-key" not in response.text
    assert workspace.config()["LLM_API_KEY"] == "old-key"


def test_custom_persona_and_unrelated_settings_survive_editing(client, workspace):
    sw.write_env(workspace.env, {"LLM_API_KEY": "old-key", "LLM_BASE_URL": "https://api.deepseek.com",
                                 "LLM_JUDGE_MODEL": "judge-small", "PERSONA_NAME": "Mira"})
    persona = workspace.home / "persona.txt"
    persona.write_text("My own character", encoding="utf-8")
    response = client.post("/api/config", headers=HEADERS, json={"character": "custom", "name": "Mira"})
    assert response.status_code == 200
    assert persona.read_text(encoding="utf-8") == "My own character"
    assert workspace.config()["LLM_JUDGE_MODEL"] == "judge-small"
    assert not (workspace.home / "persona.txt.bak").exists()


def test_a_chosen_personality_keeps_a_copy_of_the_persona_someone_wrote(client, workspace):
    sw.write_env(workspace.env, {"LLM_API_KEY": "old-key", "LLM_BASE_URL": "https://api.deepseek.com",
                                 "PERSONA_NAME": "Mira"})
    persona = workspace.home / "persona.txt"
    persona.write_text("My own character", encoding="utf-8")
    kept = workspace.home / "persona.txt.bak"
    # An emptied name box keeps the name rather than making a new character.
    response = client.post("/api/config", headers=HEADERS, json={"character": "template", "name": ""})
    assert response.status_code == 200 and workspace.config()["PERSONA_NAME"] == "Mira"
    assert kept.read_text(encoding="utf-8") == "My own character"
    assert persona.read_text(encoding="utf-8") != "My own character"
    # A shipped persona is not worth a copy: the one someone wrote stays in the .bak.
    client.post("/api/config", headers=HEADERS, json={"character": "mibo", "name": "Mira"})
    assert kept.read_text(encoding="utf-8") == "My own character"
    # A second persona someone wrote takes the .bak; the first keeps its dated copy.
    persona.write_text("My second character", encoding="utf-8")
    client.post("/api/config", headers=HEADERS, json={"character": "template", "name": "Mira"})
    assert kept.read_text(encoding="utf-8") == "My second character"
    copies = sorted(p.read_text(encoding="utf-8") for p in workspace.home.glob("persona.txt.*.bak"))
    assert copies == ["My own character", "My second character"]


@pytest.mark.parametrize("key", ["密钥 sk-abc", "sk-abc def"])
def test_a_key_pasted_with_other_words_is_turned_down_as_a_key(client, workspace, key):
    response = client.post("/api/config", headers=HEADERS, json={"apiKey": key})
    # Its own message: not a key the service refused, but text around the key.
    assert response.status_code == 400 and response.json()["error"] == "key_malformed"
    assert not workspace.config().get("LLM_API_KEY")


def test_a_blank_name_is_asked_for_by_name(client, workspace):
    response = client.post("/api/config", headers=HEADERS, json={"apiKey": "k", "name": "   "})
    assert response.status_code == 400 and response.json()["error"] == "name_required"
    assert not workspace.config().get("LLM_API_KEY")


def test_unknown_provider_or_insecure_remote_url_is_rejected(client, workspace):
    for fields in ({"provider": "unknown"}, {"baseUrl": "http://remote.example/v1"},
                   {"baseUrl": "https://user:password@remote.example/v1"}):
        response = client.post("/api/config", headers=HEADERS, json={"apiKey": "test", **fields})
        assert response.status_code == 400, fields
    assert not workspace.config().get("LLM_API_KEY")


def test_changing_the_api_endpoint_never_sends_the_saved_key(client, workspace, monkeypatch):
    sw.write_env(workspace.env, {"LLM_API_KEY": "old-key", "LLM_BASE_URL": "https://api.deepseek.com"})
    monkeypatch.setattr(sw, "probe_key", lambda *a, **kw: pytest.fail("must not send a saved key to a new endpoint"))
    response = client.post("/api/config", headers=HEADERS, json={"baseUrl": "https://new.example/v1"})
    # Said as such: the saved key stays with the address it was saved for.
    assert response.status_code == 400 and response.json()["error"] == "key_again"


def test_the_state_says_when_the_character_cannot_reach_its_model(app):
    assert app.get("/api/state").json()["modelUnreachable"] is False
    app.model.script(fail(503))
    assert app.post("/api/chat", {"text": "hello"}).status_code == 502
    assert app.get("/api/state").json()["modelUnreachable"] is True
    # An answer from the model clears it.
    app.model.script(say("hi"))
    assert app.post("/api/chat", {"text": "hello again"}).status_code == 200
    assert app.get("/api/state").json()["modelUnreachable"] is False


def test_the_state_carries_the_admins_name(client, workspace):
    sw.write_env(workspace.env, {"ADMIN_NAME": "Kai"})
    assert client.get("/api/state", headers=HEADERS).json()["adminName"] == "Kai"

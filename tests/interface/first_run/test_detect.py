"""What the first-run page offers from this computer: a local model server that is running, a key in the
environment, and a save that takes its key from there."""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from harness.app import TOKEN
from mibo import home as homes
from mibo import setup_wizard as sw
from mibo import welcome
from mibo.panels import detect

HEADERS = {welcome.TOKEN_HEADER: TOKEN}


@pytest.fixture(autouse=True)
def no_keys_around(monkeypatch):
    """Whatever keys the machine running the suite has in its environment are not this test's."""
    for variable in detect.ENV_KEYS:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


class _Listing(BaseHTTPRequestHandler):
    """A local model server's listing: `answer` as JSON, or bytes as they are, sent `pace` seconds apart."""
    answer: object = {}
    pace = 0.0

    def log_message(self, *args):
        pass

    def do_GET(self):
        data = self.answer if isinstance(self.answer, bytes) else json.dumps(self.answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not self.pace:
            self.wfile.write(data)
            return
        try:
            for byte in data:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                time.sleep(self.pace)
        except OSError:
            # The page stopped reading.
            pass


@pytest.fixture()
def local_server():
    def serve(answer, pace: float = 0.0) -> str:
        handler = type("Handler", (_Listing,), {"answer": answer, "pace": pace})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    servers: list = []
    yield serve
    for server in servers:
        server.shutdown()
        server.server_close()


def _ollama_at(monkeypatch, listing: str) -> None:
    monkeypatch.setattr(detect, "LOCAL", (("ollama", "Ollama", "ollama", "http://localhost:11434/v1", listing),))


def test_nothing_running_and_no_key_offers_nothing(client, monkeypatch) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        gone = f"http://127.0.0.1:{sock.getsockname()[1]}"
    _ollama_at(monkeypatch, gone + "/api/tags")
    answer = client.get("/api/detect", headers=HEADERS)
    assert answer.status_code == 200
    assert answer.json() == {"local": [], "keys": []}


def test_a_running_ollama_is_offered_with_the_models_that_can_chat(client, monkeypatch, local_server) -> None:
    ollama = local_server({"models": [{"name": "nomic-embed-text:latest"}, {"name": "qwen3:latest"},
                                      {"name": "llama3.2"}, {"name": ""}, {"size": 3}]})
    studio = local_server({"data": [{"id": "text-embedding-nomic"}]})
    monkeypatch.setattr(detect, "LOCAL", (
        ("ollama", "Ollama", "ollama", "http://localhost:11434/v1", ollama + "/api/tags"),
        ("lmstudio", "LM Studio", "other", "http://localhost:1234/v1", studio + "/v1/models")))
    found = client.get("/api/detect", headers=HEADERS).json()
    # LM Studio is up with nothing that chats: no shortcut there.
    assert found["local"] == [{"id": "ollama", "name": "Ollama", "provider": "ollama",
                               "baseUrl": "http://localhost:11434/v1",
                               "models": ["qwen3:latest", "llama3.2"]}]


def test_the_openai_shape_and_an_answer_that_is_not_a_listing(local_server, monkeypatch) -> None:
    studio = local_server({"data": [{"id": "qwen3-8b"}, {"id": "embed-small"}]})
    odd = local_server({"models": "none"})
    monkeypatch.setattr(detect, "LOCAL", (
        ("lmstudio", "LM Studio", "other", "http://localhost:1234/v1", studio + "/v1/models"),
        ("ollama", "Ollama", "ollama", "http://localhost:11434/v1", odd + "/api/tags")))
    found = asyncio.run(detect.found())
    assert [(row["id"], row["models"]) for row in found["local"]] == [("lmstudio", ["qwen3-8b"])]


def test_the_page_selects_a_provider_the_catalog_has() -> None:
    for _id, _name, provider, base, _listing in detect.LOCAL:
        assert provider in sw.PROVIDER_KEYS
        assert base.endswith("/v1")
    assert set(detect.ENV_KEYS.values()) <= set(sw.PROVIDER_KEYS) - {"ollama", "other"}


def test_a_key_in_the_environment_is_named_and_never_shown(client, monkeypatch) -> None:
    monkeypatch.setattr(detect, "LOCAL", ())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-the-shell")
    monkeypatch.setenv("MOONSHOT_API_KEY", "   ")
    monkeypatch.setenv("ARK_API_KEY", "two words")
    answer = client.get("/api/detect", headers=HEADERS)
    assert answer.json()["keys"] == [{"variable": "DEEPSEEK_API_KEY", "provider": "deepseek"}]
    assert "sk-from-the-shell" not in answer.text


def test_a_key_this_home_s_own_env_file_set_is_not_a_find(client, monkeypatch) -> None:
    monkeypatch.setattr(detect, "LOCAL", ())
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-from-env-file")
    monkeypatch.setattr(homes, "FROM_ENV_FILE", {"OPENROUTER_API_KEY"})
    assert client.get("/api/detect", headers=HEADERS).json()["keys"] == []


def test_an_openai_key_for_another_address_is_left_alone(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proxy")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.example/v1")
    assert detect.env_key("OPENAI_API_KEY") == ""
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    assert detect.env_key("OPENAI_API_KEY") == "sk-proxy"
    assert detect.env_key("PATH") == ""
    assert detect.env_key("LLM_API_KEY") == ""


def test_a_save_takes_the_key_from_the_variable_the_page_names(client, workspace, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-the-shell")
    probed = []

    def probe(base, key, model, timeout=30):
        probed.append((base, key, model))
        return sw.Probe(base, 200)

    monkeypatch.setattr(sw, "probe_key", probe)
    saved = client.post("/api/config", headers=HEADERS, json={
        "provider": "deepseek", "apiKey": "", "apiKeyFrom": "DEEPSEEK_API_KEY", "lang": "en"})
    assert saved.status_code == 200, saved.text
    assert probed and probed[0][1] == "sk-from-the-shell"
    assert sw.env_get(workspace.home / ".env", "LLM_API_KEY") == "sk-from-the-shell"
    assert "sk-from-the-shell" not in saved.text


def test_a_variable_of_another_service_or_another_name_gives_no_key(client, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-the-shell")
    monkeypatch.setenv("HOME_SECRET", "not-a-model-key")
    monkeypatch.setattr(sw, "probe_key", lambda *a, **k: sw.Probe("", 200))
    for provider, variable in (("openrouter", "DEEPSEEK_API_KEY"), ("deepseek", "HOME_SECRET"),
                               ("deepseek", "MOONSHOT_API_KEY")):
        refused = client.post("/api/config", headers=HEADERS, json={
            "provider": provider, "apiKey": "", "apiKeyFrom": variable, "lang": "en"})
        assert refused.status_code == 400, (provider, variable)
        assert refused.json() == {"error": "key_required"}


def test_a_key_from_the_environment_goes_only_to_its_own_service(client, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-from-the-shell")
    sent = []
    monkeypatch.setattr(sw, "probe_key", lambda base, key, model, timeout=30: sent.append((base, key)) or sw.Probe(base, 200))
    refused = client.post("/api/config", headers=HEADERS, json={
        "provider": "openrouter", "apiKey": "", "apiKeyFrom": "OPENROUTER_API_KEY",
        "baseUrl": "https://elsewhere.example/v1", "model": "x", "lang": "en"})
    assert refused.status_code == 400 and refused.json() == {"error": "key_required"}
    assert sent == [], "the key went nowhere"
    saved = client.post("/api/config", headers=HEADERS, json={
        "provider": "openrouter", "apiKey": "", "apiKeyFrom": "OPENROUTER_API_KEY", "lang": "en"})
    assert saved.status_code == 200 and sent == [("https://openrouter.ai/api/v1", "sk-or-from-the-shell")]


def test_a_local_server_that_nests_its_answer_without_end_is_no_find(local_server, monkeypatch) -> None:
    # Deeper than any parser recurses, and well under the size cap.
    _ollama_at(monkeypatch, local_server(b"[" * 200_000 + b"]" * 200_000) + "/api/tags")
    assert asyncio.run(detect.found())["local"] == []


def test_a_local_server_whose_answer_runs_past_the_size_cap_is_no_find(local_server, monkeypatch) -> None:
    listing = {"models": [{"name": f"model-{n}", "details": "x" * 200} for n in range(100)]}
    _ollama_at(monkeypatch, local_server(listing) + "/api/tags")
    assert asyncio.run(detect.found())["local"] != []
    monkeypatch.setattr(detect, "MAX_ANSWER", 10_000)
    assert asyncio.run(detect.found())["local"] == []


def test_a_local_server_that_drips_its_answer_is_given_up(local_server, monkeypatch) -> None:
    # Each byte comes well within the wait for one, and the whole answer never within the time for all.
    _ollama_at(monkeypatch, local_server({"models": [{"name": "qwen3"}]}, pace=0.05) + "/api/tags")
    monkeypatch.setattr(detect, "ANSWER_S", 0.3)
    assert asyncio.run(detect.found())["local"] == []

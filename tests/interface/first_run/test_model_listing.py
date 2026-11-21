"""The models the saved service lists for the page's model field, against a stand-in service: free ones marked,
the key kept on the server, and a listing that is slow, huge or missing given up."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mibo import setup_wizard as sw
from mibo.panels import models as model_panel

KEY = "sk-or-v1-standin-0123456789abcdef"


class Service:
    """A model service's /api/v1/models: `listing` after `delay` seconds; who asked, with which key."""

    def __init__(self):
        self.listing: dict | None = None
        self.delay = 0.0
        self.asked: list[str] = []
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                stand_in.asked.append(self.headers.get("Authorization"))
                time.sleep(stand_in.delay)
                body = json.dumps(stand_in.listing or {}).encode() if self.path == "/api/v1/models" else b"{}"
                self.send_response(200 if self.path == "/api/v1/models" else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def service(monkeypatch, workspace):
    """The stand-in as OpenRouter's address in the catalog, with nothing kept from another test's listing."""
    stand_in = Service()
    model_panel._cache.clear()
    monkeypatch.setattr(sw, "PROVIDERS", tuple(
        sw.Provider(p.key, stand_in.base + "/api/v1" if p.key == "openrouter" else p.base_url, p.model, p.key_url)
        for p in sw.PROVIDERS))
    yield stand_in
    stand_in.close()
    model_panel._cache.clear()


def _listed(workspace) -> dict:
    return asyncio.run(model_panel.listing(workspace))


def _save(workspace, base: str) -> None:
    sw.write_env(workspace.home / ".env", {"LLM_BASE_URL": base, "LLM_API_KEY": KEY, "LLM_MODEL": "openrouter/free"})


def test_the_listing_marks_the_free_models_and_keeps_the_key_on_the_server(workspace, service) -> None:
    base = service.base + "/api/v1"
    assert _listed(workspace) == {"baseUrl": "", "models": None}
    _save(workspace, base)
    service.listing = {"data": [
        {"id": "z-ai/glm-4.7", "pricing": {"prompt": "0.0000004", "completion": "0.0000015"}},
        {"id": "meta/llama:free", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "qwen/qwen3", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "openai/text-embedding-3", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": ""}, {"name": "no id"}, "junk", {"id": "z-ai/glm-4.7"}]}
    answer = _listed(workspace)
    assert answer == {"baseUrl": base, "models": [
        {"id": "meta/llama:free", "free": True}, {"id": "openrouter/free", "free": True},
        {"id": "qwen/qwen3", "free": True}, {"id": "z-ai/glm-4.7", "free": False}]}
    assert KEY not in json.dumps(answer)
    assert service.asked == [f"Bearer {KEY}"]
    # Read once, then kept for a while.
    _listed(workspace)
    assert len(service.asked) == 1


def test_a_service_without_a_listing_leaves_the_field_as_it_is(workspace, service) -> None:
    sw.write_env(workspace.home / ".env", {"LLM_BASE_URL": service.base + "/other/v1", "LLM_API_KEY": KEY,
                                           "LLM_MODEL": "m"})
    assert _listed(workspace)["models"] is None
    model_panel._cache.clear()
    _save(workspace, service.base + "/api/v1")
    service.listing = {"object": "list", "data": "none"}
    assert _listed(workspace)["models"] is None


def test_a_slow_or_huge_listing_is_given_up(workspace, service, monkeypatch) -> None:
    _save(workspace, service.base + "/api/v1")
    monkeypatch.setattr(model_panel, "TIMEOUT_S", 0.3)
    service.listing = {"data": [{"id": "a"}]}
    service.delay = 1.0
    assert _listed(workspace)["models"] is None
    model_panel._cache.clear()
    service.delay = 0
    service.listing = {"data": [{"id": f"vendor/model-{i}", "description": "x" * 400} for i in range(3000)]}
    monkeypatch.setattr(model_panel, "MAX_BYTES", 100_000)
    assert _listed(workspace)["models"] is None
    model_panel._cache.clear()
    monkeypatch.setattr(model_panel, "MAX_BYTES", 16 * 1024 * 1024)
    monkeypatch.setattr(model_panel, "MAX_MODELS", 1000)
    models = _listed(workspace)["models"]
    # Hundreds of models arrive whole, up to the cap.
    assert len(models) == 1001 and models[0] == {"id": "openrouter/free", "free": True}

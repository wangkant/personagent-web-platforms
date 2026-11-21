"""Signing in with OpenRouter on the first-run page, against a stand-in for openrouter.ai: the attempt and each
way it fails. Who the callback admits, and its state used once, are floors in tests/floors/boundary/; that no
answer carries the key is checked for every interface test (interface/conftest.py)."""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from harness.app import TOKEN
from mibo import openrouter_signin as osr
from mibo import setup_wizard as sw
from mibo import welcome
from mibo.panels import signin

HEADERS = {welcome.TOKEN_HEADER: TOKEN}
# The page's port as the callback address names it; nothing listens on it.
PORT = 18911
KEY = "sk-or-v1-standin-0123456789abcdef"


class OpenRouter:
    """openrouter.ai's two addresses: /auth hands out a code for a challenge, /api/v1/auth/keys trades code and
    verifier for a key, once."""

    def __init__(self):
        self.codes: dict[str, str] = {}
        self.exchange_status = 200
        self.answer: dict | None = None
        self.seen: list = []
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status: int, body: bytes):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                query = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                code = f"code-{len(stand_in.codes)}"
                stand_in.codes[code] = query["code_challenge"]
                self._send(200, json.dumps({"code": code, **query}).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                stand_in.seen.append(body)
                if stand_in.exchange_status != 200:
                    return self._send(stand_in.exchange_status, b'{"error": "nope"}')
                challenge = stand_in.codes.pop(body.get("code"), None)
                verifier = body.get("code_verifier", "")
                expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
                if challenge is None or challenge != expected or body.get("code_challenge_method") != "S256":
                    return self._send(403, b'{"error": "Invalid code or code_verifier"}')
                self._send(200, json.dumps({"key": KEY, "user_id": "u1"} if stand_in.answer is None
                                           else stand_in.answer).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def approve(self, url: str) -> dict:
        """What a person approving in the browser makes OpenRouter send back."""
        return httpx.get(self.base + "/auth?" + urlsplit(url).query, trust_env=False).json()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def openrouter(monkeypatch):
    stand_in = OpenRouter()
    monkeypatch.setattr(osr, "AUTHORIZE_URL", stand_in.base + "/auth")
    monkeypatch.setattr(osr, "KEYS_URL", stand_in.base + "/api/v1/auth/keys")
    yield stand_in
    stand_in.close()


@pytest.fixture()
def page(workspace, monkeypatch, openrouter):
    opened: list[str] = []
    monkeypatch.setattr(signin, "open_browser", lambda url: opened.append(url) or True)
    probed: list = []
    monkeypatch.setattr(sw, "probe_key", lambda base, key, model, timeout=30: (
        probed.append((base, key, model)) or sw.Probe(base, 401 if key == "bad-key" else 200)))
    app = welcome.create_app(workspace.home, TOKEN, workspace=workspace)
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", client=("127.0.0.1", 50000))
    return {"workspace": workspace, "client": client, "opened": opened, "probed": probed, "app": app}


def _start(client, **form) -> dict:
    answer = client.post("/api/signin/start", headers=HEADERS, json={
        "provider": "openrouter", "pageLang": form.pop("pageLang", "en"),
        "form": {"model": "openrouter/free", "name": "Mira", "character": "mibo", "lang": "en", **form}})
    assert answer.status_code == 200, answer.text
    return answer.json()


def _back(client, query: dict):
    return client.get(signin.CALLBACK_PATH, params=query, headers={"host": f"localhost:{PORT}"})


def _exchanges(openrouter) -> int:
    return len(openrouter.seen)


def test_the_challenge_is_rfc_7636_s256() -> None:
    # The worked example in RFC 7636, appendix B.
    assert osr.challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == \
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    verifier = osr.new_verifier()
    assert 43 <= len(verifier) <= 128
    query = parse_qs(urlsplit(osr.authorize_url(verifier, callback="http://localhost:1/cb", state="s")).query)
    assert query["code_challenge_method"] == ["S256"] and query["callback_url"] == ["http://localhost:1/cb"]
    assert query["code_challenge"] == [osr.challenge(verifier)] and query["state"] == ["s"]
    assert verifier not in osr.authorize_url(verifier, callback="http://localhost:1/cb", state="s")


def test_signing_in_saves_the_form_with_the_new_key_and_never_shows_it(page, openrouter) -> None:
    client, workspace = page["client"], page["workspace"]
    started = _start(client, name="Juno", pageLang="zh")
    assert started["state"] == "waiting" and started["opened"] is True
    assert page["opened"] == [started["url"]]
    query = parse_qs(urlsplit(started["url"]).query)
    assert query["callback_url"] == [f"http://localhost:{PORT}{signin.CALLBACK_PATH}"]
    attempt = page["app"].state.signin.attempt
    # The verifier stays on the server: the page sees only its challenge.
    assert attempt.verifier not in json.dumps(started)

    back = openrouter.approve(started["url"])
    done = _back(client, {"code": back["code"], "state": back["state"]})
    # The tab is told in the page's language, in the app's own look.
    assert done.status_code == 200 and '<html lang="zh' in done.text
    assert 'href="/assets/signin.css"' in done.text
    assert client.get("/api/signin", headers=HEADERS).json() == {"state": "done", "error": "", "url": ""}
    assert sw.env_get(workspace.home / ".env", "LLM_API_KEY") == KEY
    assert sw.env_get(workspace.home / ".env", "LLM_BASE_URL") == "https://openrouter.ai/api/v1"
    assert sw.env_get(workspace.home / ".env", "PERSONA_NAME") == "Juno"
    assert page["probed"] == [("https://openrouter.ai/api/v1", KEY, "openrouter/free")]
    assert openrouter.seen[0]["code_verifier"] == attempt.verifier
    assert client.get("/api/state", headers=HEADERS).json()["configured"] is True
    # The page reads the outcome once, then the attempt is gone.
    assert client.post("/api/signin/cancel", headers=HEADERS, json={}).json()["state"] == "idle"


def test_another_address_in_the_form_falls_back_to_openrouter_s_own(page, openrouter) -> None:
    client, workspace = page["client"], page["workspace"]
    back = openrouter.approve(_start(client, baseUrl="https://llm.example/v1")["url"])
    _back(client, {"code": back["code"], "state": back["state"]})
    assert sw.env_get(workspace.home / ".env", "LLM_BASE_URL") == "https://openrouter.ai/api/v1"


def test_only_openrouter_signs_in_and_only_with_the_token(page) -> None:
    client = page["client"]
    refused = client.post("/api/signin/start", headers=HEADERS, json={"provider": "deepseek"})
    assert refused.status_code == 400 and refused.json() == {"error": "signin_provider"}
    assert client.post("/api/signin/start", json={"provider": "openrouter"}).status_code == 403
    assert client.get("/api/signin").status_code == 403
    assert page["opened"] == []


@pytest.mark.parametrize("status, error", [(403, "signin_code_rejected"), (400, "signin_exchange_failed"),
                                           (405, "signin_exchange_failed"), (500, "signin_exchange_failed")])
def test_an_exchange_that_fails_says_why(page, openrouter, status, error) -> None:
    client, workspace = page["client"], page["workspace"]
    back = openrouter.approve(_start(client)["url"])
    openrouter.exchange_status = status
    answer = _back(client, {"code": back["code"], "state": back["state"]})
    assert answer.status_code == 400
    assert client.get("/api/signin", headers=HEADERS).json() == {"state": "failed", "error": error, "url": ""}
    assert not (workspace.home / ".env").exists()


@pytest.mark.parametrize("answer", [{}, {"key": ""}, {"key": "two words"}, {"key": 5}, {"key": "${LLM_API_KEY}"}])
def test_an_answer_without_a_usable_key_is_no_key(page, openrouter, answer) -> None:
    client, workspace = page["client"], page["workspace"]
    back = openrouter.approve(_start(client)["url"])
    openrouter.answer = answer
    _back(client, {"code": back["code"], "state": back["state"]})
    assert client.get("/api/signin", headers=HEADERS).json()["error"] == "signin_exchange_failed"
    assert not (workspace.home / ".env").exists()


def test_openrouter_out_of_reach(page, openrouter, monkeypatch) -> None:
    client = page["client"]
    back = openrouter.approve(_start(client)["url"])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed = sock.getsockname()[1]
    monkeypatch.setattr(osr, "KEYS_URL", f"http://127.0.0.1:{closed}/api/v1/auth/keys")
    _back(client, {"code": back["code"], "state": back["state"]})
    assert client.get("/api/signin", headers=HEADERS).json()["error"] == "signin_unreachable"


def test_a_key_that_does_not_verify_is_not_saved(page, openrouter) -> None:
    client, workspace = page["client"], page["workspace"]
    back = openrouter.approve(_start(client)["url"])
    openrouter.answer = {"key": "bad-key"}
    answer = _back(client, {"code": back["code"], "state": back["state"]})
    assert answer.status_code == 400
    assert client.get("/api/signin", headers=HEADERS).json()["error"] == "signin_key_rejected"
    assert not (workspace.home / ".env").exists()


def test_an_attempt_left_waiting_expires(page, openrouter, monkeypatch) -> None:
    client = page["client"]
    back = openrouter.approve(_start(client)["url"])
    monkeypatch.setattr(osr, "CODE_LIFETIME_S", -1)
    assert client.get("/api/signin", headers=HEADERS).json()["error"] == "signin_timeout"
    assert _back(client, {"code": back["code"], "state": back["state"]}).status_code == 400
    assert _exchanges(openrouter) == 0


def test_cancel_drops_the_attempt(page, openrouter) -> None:
    client = page["client"]
    back = openrouter.approve(_start(client)["url"])
    assert client.post("/api/signin/cancel", headers=HEADERS, json={}).json()["state"] == "idle"
    assert _back(client, {"code": back["code"], "state": back["state"]}).status_code == 400
    assert _exchanges(openrouter) == 0


def test_a_second_press_replaces_the_first(page, openrouter) -> None:
    client = page["client"]
    first = openrouter.approve(_start(client)["url"])
    second = _start(client)
    assert parse_qs(urlsplit(second["url"]).query)["state"][0] != first["state"]
    assert _back(client, {"code": first["code"], "state": first["state"]}).status_code == 400
    back = openrouter.approve(second["url"])
    assert _back(client, {"code": back["code"], "state": back["state"]}).status_code == 200


def test_a_tab_in_a_language_the_page_has_not_is_told_in_english() -> None:
    assert '<html lang="en">' in signin.page("xx", "")

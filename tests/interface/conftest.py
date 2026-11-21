"""What the interface tests share: the page's app on this test's home, reached through TestClient with the
token, and a check that no answer the page gives in any interface test carries a key."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from harness.app import TOKEN
from mibo import welcome

# A setting, form field or variable that holds a key by its name.
KEY_NAME = re.compile(r"(?i)(api_?key|token|secret|password)$")
# Shorter values ("k", "old-key") are stand-ins too common to search for in a page.
SHORTEST_KEY = 8


@pytest.fixture
def workspace():
    """The page's workspace on this test's own home."""
    value = welcome.Workspace(Path(os.environ["AGENT_HOME"]))
    yield value
    value.close()


@pytest.fixture
def client(workspace):
    """The page's app without its lifespan: no character loops, no probe of public sites, no teardown wait."""
    return TestClient(welcome.create_app(workspace.home, TOKEN, workspace=workspace), base_url="http://127.0.0.1")


def _keys_in(value, name: str = "") -> set[str]:
    """The strings a posted form holds under a key's name, at any depth."""
    if isinstance(value, dict):
        return set().union(*(_keys_in(v, str(k)) for k, v in value.items()))
    if isinstance(value, list):
        return set().union(*(_keys_in(v, name) for v in value))
    return {value} if isinstance(value, str) and KEY_NAME.search(name) else set()


@pytest.fixture(autouse=True)
def _no_answer_carries_a_key(monkeypatch, tmp_path):
    """Every answer the page gives is kept, and at the end of the test none may hold a key the test put in
    reach: one posted in a form, one saved in a .env under the test's folders, or one in the environment."""
    answers: list[tuple[httpx.Request, httpx.Response]] = []
    real = httpx.Client.send

    def send(self, request, **options):
        response = real(self, request, **options)
        # The page's answers only: a stand-in service may hand the app a key, which is what it is for.
        if isinstance(self, TestClient) or welcome.TOKEN_HEADER in request.headers:
            answers.append((request, response))
        return response

    monkeypatch.setattr(httpx.Client, "send", send)
    yield
    keys = {value for name, value in os.environ.items() if KEY_NAME.search(name)}
    for request, _ in answers:
        if request.headers.get("content-type", "").startswith("application/json") and request.content:
            keys |= _keys_in(json.loads(request.content))
    for folder in {tmp_path, Path(os.environ["AGENT_HOME"])}:
        for env in folder.rglob(".env"):
            keys |= {value for name, value in dotenv_values(env).items() if value and KEY_NAME.search(name)}
    keys = {key.strip() for key in keys if len(key.strip()) >= SHORTEST_KEY}
    leaked = sorted({f"{request.method} {request.url.path}" for request, response in answers
                     for key in keys if key.encode() in response.read()})
    assert leaked == [], "these answers carried a key"

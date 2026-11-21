"""Settings › Connectors: adding servers by pasted JSON or one command line, no secret value ever sent back to
the page, a local program waiting for the person's leave, per-tool modes, removing, and the press that only
the app's own page may give."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from mibo import mcp_tools, settingsfile, welcome

TOKEN = "test-local-capability"
HEADERS = {welcome.TOKEN_HEADER: TOKEN}
STANDIN = str(Path(__file__).with_name("mcp_standin.py"))


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    home = tmp_path / "home"
    settingsfile.write(home, {"LLM_API_KEY": "sk-model", "PERSONA_NAME": "Nova"})
    value = welcome.Workspace(home)
    yield value
    value.close()


@pytest.fixture
def client(workspace):
    with TestClient(welcome.create_app(workspace.home, TOKEN, workspace=workspace),
                    base_url="http://127.0.0.1") as value:
        yield value


def post(client, payload, path="mcp"):
    return client.post(f"/api/{path}", headers=HEADERS, json=payload)


def _until(client, name, *states, seconds=30):
    deadline = time.monotonic() + seconds
    while True:
        row = next(s for s in client.get("/api/mcp", headers=HEADERS).json()["servers"] if s["name"] == name)
        if row["state"] in states or time.monotonic() > deadline:
            return row
        time.sleep(0.2)


def test_a_pasted_block_or_command_adds_servers_and_secrets_stay_home(client, workspace):
    assert client.get("/api/mcp").status_code == 403
    assert client.get("/api/mcp", headers=HEADERS).json()["servers"] == []
    snippet = json.dumps({"mcpServers": {"remote": {"url": "http://127.0.0.1:9/mcp",
                                                    "headers": {"Authorization": "Bearer hdr-secret"}}}})
    response = post(client, {"add": snippet})
    assert response.status_code == 200 and "hdr-secret" not in response.text
    response = post(client, {"add": json.dumps({"stand": {"command": sys.executable, "args": [STANDIN],
                                                          "env": {"API_KEY": "env-secret"}}})})
    assert response.status_code == 200 and "env-secret" not in response.text
    view = client.get("/api/mcp", headers=HEADERS)
    assert "env-secret" not in view.text and "hdr-secret" not in view.text
    servers = {s["name"]: s for s in view.json()["servers"]}
    assert servers["stand"]["values"] == [{"kind": "env", "name": "API_KEY", "set": True}]
    assert servers["remote"]["values"] == [{"kind": "headers", "name": "Authorization", "set": True}]
    saved = mcp_tools.config_path(workspace.home).read_text(encoding="utf-8")
    assert "env-secret" not in saved and "env-secret" not in settingsfile.path(workspace.home).read_text(encoding="utf-8")
    from mibo import secretstore
    assert secretstore.get(workspace.home, "MIBO_MCP_STAND_API_KEY") == "env-secret"
    assert post(client, {"add": f'"{sys.executable}" "{STANDIN}"', "name": "stand"}).json() == \
        {"error": "tools_name_taken"}
    assert post(client, {"add": "nonsense"}).json() == {"error": "tools_bad_snippet"}
    # A local program waits for the person's leave; nothing runs before it.
    assert _until(client, "stand", "waiting")["state"] == "waiting"


def test_allowing_the_start_takes_the_apps_own_page(client, workspace):
    post(client, {"add": f'"{sys.executable}" "{STANDIN}"', "name": "stand"})
    _until(client, "stand", "waiting")
    # Not from the character service's token alone when the app has a window that names its own page.
    welcome_window = type("W", (), {"is_own_page": staticmethod(lambda key: key == "own")})()
    client.app.state.window = welcome_window
    assert post(client, {"start": "stand", "answer": "once"}).json() == {"error": "local_only"}
    allowed = client.post("/api/mcp", headers={**HEADERS, "X-Mibo-Window": "own"},
                          json={"start": "stand", "answer": "once"})
    assert allowed.status_code == 200
    row = _until(client, "stand", "connected", "failed")
    assert row["state"] == "connected" and {t["name"] for t in row["tools"]} >= {"echo", "add"}
    echo = next(t for t in row["tools"] if t["name"] == "echo")
    assert echo["mode"] == "auto" and echo["description"] == "Say the text back."
    view = post(client, {"tool": {"server": "stand", "tool": "echo", "mode": "never"}}).json()
    assert next(t for t in view["servers"][0]["tools"] if t["name"] == "echo")["mode"] == "never"
    assert post(client, {"tool": {"server": "stand", "tool": "nope", "mode": "always"}}).json() == \
        {"error": "tools_unknown_tool"}
    # Never: the character's search does not find it at all, even in the server's whole list.
    found = client.post("/api/mcp/find", headers=HEADERS, json={"query": "", "server": "stand"}).json()
    assert found["status"] == "ok" and "echo" not in {t["tool"] for t in found["tools"]} and found["tools"]
    removed = post(client, {"remove": "stand"}).json()
    assert removed["servers"] == []


def test_every_section_string_is_in_english_and_chinese_and_every_code_is_named():
    from mibo.panels import tools

    source = (welcome.ASSETS / "setup" / "tools.js").read_text(encoding="utf-8")
    zh, en = source.split("\n    en: {\n")
    en = en.split("\n    },\n")[0]
    keys_zh = set(re.findall(r"^      (\w+): ", zh, re.M))
    keys_en = set(re.findall(r"^      (\w+): ", en, re.M))
    assert keys_zh and keys_zh == keys_en
    for code in tools.ERRORS:
        assert f"{code}:" in zh and f"{code}:" in en, code
    page = (welcome.ASSETS / "app.html").read_text(encoding="utf-8")
    assert "/assets/setup/tools.js" in page and "/assets/toolcard.js" in page

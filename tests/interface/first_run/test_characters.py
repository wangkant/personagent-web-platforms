"""Choosing a character on the first-run page: the catalog, the portraits, and a picture of one's own. Which
files the portrait route will read is a floor in tests/floors/boundary/."""
from __future__ import annotations

import pytest

from harness.app import TOKEN
from mibo import setup_wizard as sw
from mibo import welcome
from mibo import settingsfile

HEADERS = {welcome.TOKEN_HEADER: TOKEN}
FAN_MADE = ("chatgpt", "deepseek", "claude")
SPRITE = b"RIFF\x04\x00\x00\x00WEBP"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 32


@pytest.fixture(autouse=True)
def accepted(monkeypatch):
    """Every key the page checks is taken by its service."""
    monkeypatch.setattr(sw, "probe_key", lambda *a, **kw: sw.Probe("http://127.0.0.1", 200, ""))


@pytest.fixture
def portraits(monkeypatch, tmp_path_factory):
    """Portrait sprites for the fan-made characters, never the package's own folder."""
    folder = tmp_path_factory.mktemp("portraits")
    for key in FAN_MADE:
        (folder / f"{key}.webp").write_bytes(SPRITE)
    monkeypatch.setattr(sw, "PORTRAITS", folder)
    return folder


def test_the_catalog_names_each_character_and_its_portrait(client, portraits):
    (portraits / "deepseek.webp").unlink()
    characters = {c["id"]: c for c in client.get("/api/catalog", headers=HEADERS).json()["characters"]}
    assert list(characters) == list(sw.PERSONA_KEYS)
    assert characters["claude"]["defaultNames"]["en"] == "Milady"
    assert characters["claude"]["defaultNames"]["zh"] == "大小姐"
    assert set(characters["claude"]["ownNames"]) >= {"Claude", "Milady", "大小姐", "お嬢様", "아가씨"}
    assert characters["claude"]["avatar"] == "/assets/characters/claude.webp"
    assert characters["deepseek"]["defaultNames"]["ja"] == "おさかな" and characters["deepseek"]["avatar"] is None
    assert "ChatGPT" in characters["chatgpt"]["ownNames"]
    assert characters["template"]["defaultNames"] is None and characters["template"]["avatar"] is None
    assert characters["template"]["ownNames"] == []


def test_a_portrait_is_served_as_a_picture_kept_for_a_day(client, portraits):
    response = client.get("/assets/characters/claude.webp")
    assert response.status_code == 200 and response.content == SPRITE
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "max-age=86400"


def test_choosing_a_character_sets_its_portrait_and_editing_keeps_it(client, workspace, portraits):
    url = "/api/config"
    state = client.post(url, headers=HEADERS, json={"apiKey": "k", "character": "claude", "lang": "en"}).json()
    assert workspace.config()["PERSONA_AVATAR"] == "claude"
    assert (state["avatar"], state["character"], state["name"]) == ("claude", "claude", "Milady")
    (workspace.home / "persona.txt").write_text("Your name is Claude. Tea only.\n", encoding="utf-8")
    state = client.get("/api/state", headers=HEADERS).json()
    assert (state["avatar"], state["character"]) == ("claude", "custom")
    state = client.post(url, headers=HEADERS, json={"apiKey": "k", "character": "custom", "name": "Claude"}).json()
    assert workspace.config()["PERSONA_AVATAR"] == "claude" and state["avatar"] == "claude"
    state = client.post(url, headers=HEADERS, json={"apiKey": "k", "character": "template", "name": "Mira"}).json()
    assert workspace.config().get("PERSONA_AVATAR", "") == "" and state["avatar"] == ""
    state = client.post(url, headers=HEADERS, json={"apiKey": "k", "character": "deepseek", "name": "Mira"}).json()
    assert (state["avatar"], state["name"]) == ("deepseek", "Mira")


def test_a_written_character_takes_a_portrait_without_losing_its_persona(client, workspace, portraits):
    client.post("/api/config", headers=HEADERS, json={"apiKey": "k", "character": "claude", "lang": "en"})
    (workspace.home / "persona.txt").write_text("My own words.\n", encoding="utf-8")
    state = client.post("/api/config", headers=HEADERS,
                        json={"apiKey": "k", "character": "custom", "name": "Mira", "avatar": "deepseek"}).json()
    assert (state["avatar"], state["character"]) == ("deepseek", "custom")
    assert (workspace.home / "persona.txt").read_text(encoding="utf-8") == "My own words.\n"
    state = client.post("/api/config", headers=HEADERS,
                        json={"apiKey": "k", "character": "custom", "name": "Mira", "avatar": ""}).json()
    assert state["avatar"] == ""
    for bad in ("../claude", "template", "own"):
        response = client.post("/api/config", headers=HEADERS,
                               json={"apiKey": "k", "character": "custom", "name": "Mira", "avatar": bad})
        assert response.json()["error"] == "invalid_character", bad


def test_a_picture_of_ones_own_is_kept_checked_served_and_removed(client, workspace, portraits):
    client.post("/api/config", headers=HEADERS, json={"apiKey": "k", "character": "claude", "lang": "en"})
    assert client.get("/api/avatar", headers=HEADERS).status_code == 404
    assert client.post("/api/avatar", content=PNG).status_code == 403
    state = client.post("/api/avatar", headers=HEADERS, content=PNG).json()
    assert state["avatar"] == "own" and state["ownAvatar"]
    assert workspace.config()["PERSONA_AVATAR"] == "own"
    response = client.get("/api/avatar", headers=HEADERS)
    assert response.content == PNG and response.headers["content-type"] == "image/png"
    assert client.get("/api/avatar").status_code == 403
    # A JPEG replaces it: one file, never two.
    client.post("/api/avatar", headers=HEADERS, content=JPEG)
    assert sorted(p.name for p in workspace.home.glob("avatar.*")) == ["avatar.jpg"]
    assert client.get("/api/avatar", headers=HEADERS).headers["content-type"] == "image/jpeg"
    for data, error in ((b"just text, named .png", "avatar_type"), (b"GIF89a" + b"\0" * 8, "avatar_type"),
                        (PNG + b"\0" * sw.MAX_OWN_AVATAR, "avatar_too_large")):
        response = client.post("/api/avatar", headers=HEADERS, content=data)
        assert response.status_code == 400 and response.json()["error"] == error
    assert sorted(p.name for p in workspace.home.glob("avatar.*")) == ["avatar.jpg"]
    state = client.post("/api/config", headers=HEADERS,
                        json={"apiKey": "k", "character": "claude", "name": "Milady", "avatar": "own"}).json()
    assert state["avatar"] == "own"
    state = client.post("/api/avatar/remove", headers=HEADERS, json={}).json()
    assert state["avatar"] == "claude" and state["ownAvatar"] == ""
    assert not list(workspace.home.glob("avatar.*"))
    assert client.get("/api/avatar", headers=HEADERS).status_code == 404
    # A setting left pointing at a deleted picture shows the portrait instead.
    settingsfile.write(workspace.home, {"PERSONA_AVATAR": "own"})
    assert client.get("/api/state", headers=HEADERS).json()["avatar"] == "claude"


def test_state_shows_the_portrait_of_an_unedited_shipped_character(client, workspace, portraits):
    def avatar() -> str:
        return client.get("/api/state", headers=HEADERS).json()["avatar"]

    settingsfile.write(workspace.home, {"LLM_API_KEY": "k", "AGENT_LANG": "en"})
    assert avatar() == ""
    sw.write_persona(workspace.home, "chatgpt", name="ChatGPT", lang="en")
    assert avatar() == "chatgpt"
    settingsfile.write(workspace.home, {"PERSONA_AVATAR": "claude"})
    assert avatar() == "claude"
    settingsfile.write(workspace.home, {"PERSONA_AVATAR": "../claude"})
    assert avatar() == "chatgpt"
    (portraits / "chatgpt.webp").unlink()
    assert avatar() == ""
    settingsfile.write(workspace.home, {"PERSONA_AVATAR": ""})
    sw.write_persona(workspace.home, "template", name="Mira", lang="en")
    assert avatar() == ""

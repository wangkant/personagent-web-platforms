"""A model key saved on the page is kept in the credential store and nowhere else: in no file of the home and in
no backup made without keys. A backup made with keys carries them, and a restore puts them back. On a system with
no credential store at all they are in credentials.json, which Settings names and no backup without keys holds.
A home from before settings.json has its .env moved in once, and its key ends up the same way."""
from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import keyring
import pytest
from keyring.errors import NoKeyringError

from harness.app import KEY
from harness.home import forms, holding
from mibo import secretstore, settingsfile
from mibo import setup_wizard as sw
from mibo.panels import desk

FALLBACK_KEY = "sk-fallback-model-key-7f3a9c"


def in_backup(path: Path, words) -> dict[str, list[str]]:
    """{words: the members of the archive at `path` that hold them}."""
    found: dict[str, list[str]] = {w: [] for w in words}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            data = archive.read(name)
            for w in words:
                if any(form in data for form in forms(w)):
                    found[w].append(name)
    return found


def nowhere(words) -> dict[str, list[str]]:
    return {w: [] for w in words}


def test_a_saved_model_key_is_in_no_file_of_the_home_and_no_backup_without_keys(app, monkeypatch):
    home = app.home
    monkeypatch.setattr(sw, "probe_key", lambda *a, **kw: sw.Probe("http://127.0.0.1", 200, ""))
    answer = app.post("/api/tuning", {"values": {"LLM_FALLBACK_BASE_URL": "http://127.0.0.1:9/v1",
                                                 "LLM_FALLBACK_MODEL": "m", "LLM_FALLBACK_API_KEY": FALLBACK_KEY}})
    assert answer.status_code == 200 and "error" not in answer.json()
    keys = [KEY, FALLBACK_KEY]
    assert (secretstore.get(home, "LLM_API_KEY"), secretstore.get(home, "LLM_FALLBACK_API_KEY")) == tuple(keys)
    assert settingsfile.values(home)["LLM_FALLBACK_API_KEY"] == FALLBACK_KEY
    name = app.post("/api/desk/backup", {"to": "backups", "secrets": False}).json()["name"]
    assert holding(home, keys) == nowhere(keys)
    assert in_backup(home / "backups" / name, keys) == nowhere(keys)


def test_a_backup_with_keys_carries_them_and_a_restore_puts_them_back(app):
    home = app.home
    name = app.post("/api/desk/backup", {"to": "backups", "secrets": True}).json()["name"]
    assert in_backup(home / "backups" / name, [KEY]) == {KEY: [desk.KEYS]}
    secretstore.delete(home, "LLM_API_KEY")
    desk.Desk(app.workspace).restore("backups", name)
    desk.apply_pending(home)
    assert secretstore.get(home, "LLM_API_KEY") == KEY
    # No file of the home holds it; in the archives (the one made before the restore too) only the member
    # that carries the keys does.
    assert [found for found in holding(home, [KEY])[KEY] if not found.startswith("backups/")] == []
    for archive in (home / "backups").glob("*.zip"):
        assert in_backup(archive, [KEY])[KEY] in ([], [desk.KEYS]), archive.name


def test_with_no_credential_store_the_keys_are_in_credentials_json_which_settings_names(tmp_path, monkeypatch):
    def missing(*_):
        raise NoKeyringError("no backend")

    for call in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, call, missing)
    home = tmp_path / "home"
    settingsfile.write(home, {"LLM_API_KEY": KEY, "PERSONA_NAME": "Ann"})
    (home / "persona.txt").write_text("You are Ann.\n", encoding="utf-8")
    assert settingsfile.values(home)["LLM_API_KEY"] == KEY
    assert holding(home, [KEY]) == {KEY: [secretstore.FILE]}
    if os.name == "posix":
        assert (home / secretstore.FILE).stat().st_mode & 0o077 == 0
    told = desk.Desk(type("W", (), {"home": home})()).settings()
    assert (told["keys"], told["store"]) == ("file", str(home / secretstore.FILE))
    plain = desk.write(home, tmp_path / "out", False)
    assert in_backup(plain, [KEY]) == nowhere([KEY])
    carried = desk.write(home, tmp_path / "out", True)
    assert in_backup(carried, [KEY]) == {KEY: [desk.KEYS]}


def test_a_home_from_before_has_its_env_moved_in_and_the_key_is_kept_nowhere_else(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "persona.txt").write_text("You are Ann.\n", encoding="utf-8")
    (home / ".env").write_text(f"LLM_API_KEY={KEY}\nPERSONA_NAME=Ann\nLLM_MODEL=m\n", encoding="utf-8")
    assert settingsfile.values(home)["LLM_API_KEY"] == KEY
    assert not (home / ".env").exists()
    assert json.loads(settingsfile.path(home).read_text(encoding="utf-8")) == {"PERSONA_NAME": "Ann", "LLM_MODEL": "m"}
    assert secretstore.get(home, "LLM_API_KEY") == KEY
    assert holding(home, [KEY]) == nowhere([KEY])
    assert in_backup(desk.write(home, tmp_path / "out", False), [KEY]) == nowhere([KEY])


@pytest.fixture(autouse=True)
def _home_settles(monkeypatch):
    """settings.json read fresh each time: nothing of another test's environment stands in for it."""
    for name in settingsfile.KEYS:
        monkeypatch.delenv(name, raising=False)

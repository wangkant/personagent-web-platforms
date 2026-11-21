"""The home's settings.json (mibo/settingsfile.py): typed values as the environment spells them, a name it does
not know or a value that does not fit kept in the file and reported rather than dropped, the environment over the
file, and a .env from before moved in once with what it could not place kept beside."""
from __future__ import annotations

import json
import logging
import os

import pytest

from mibo import secretstore, settingsfile


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(settingsfile, "FROM_SETTINGS", set())
    monkeypatch.setattr(settingsfile, "_told", set())


def saved(home) -> dict:
    return json.loads(settingsfile.path(home).read_text(encoding="utf-8"))


def test_values_are_kept_typed_and_read_as_the_environment_spells_them(tmp_path):
    settingsfile.write(tmp_path, {"ROAM_ENABLED": "false", "ROAM_GAP_HOURS": "3", "PERSONA_TZ_OFFSET_HOURS": "5.5",
                                  "PERSONA_NAME": "Zed #1"})
    assert saved(tmp_path) == {"ROAM_ENABLED": False, "ROAM_GAP_HOURS": 3, "PERSONA_TZ_OFFSET_HOURS": 5.5,
                               "PERSONA_NAME": "Zed #1"}
    assert settingsfile.values(tmp_path) == {"ROAM_ENABLED": "false", "ROAM_GAP_HOURS": "3",
                                             "PERSONA_TZ_OFFSET_HOURS": "5.5", "PERSONA_NAME": "Zed #1"}
    settingsfile.write(tmp_path, {"PERSONA_NAME": ""})
    assert "PERSONA_NAME" not in saved(tmp_path)
    with pytest.raises(ValueError, match="ROAM_GAP_HOURS"):
        settingsfile.write(tmp_path, {"ROAM_GAP_HOURS": "soon"})


def test_an_unknown_name_and_a_value_that_does_not_fit_are_kept_and_reported(tmp_path, caplog):
    settingsfile.path(tmp_path).write_text(json.dumps(
        {"PERSONA_NAME": "Ann", "SERVER_PORT": 8000, "PAGE_PORT": "not a port", "ROAM_ENABLED": "maybe"}),
        encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="agent"):
        assert settingsfile.values(tmp_path) == {"PERSONA_NAME": "Ann"}
        settingsfile.values(tmp_path)
    assert settingsfile.problems(tmp_path) == {"invalid": ["PAGE_PORT", "ROAM_ENABLED"], "unknown": ["SERVER_PORT"]}
    told = [r.getMessage() for r in caplog.records]
    assert sum("SERVER_PORT" in line for line in told) == 1, "said once, not on every read"
    # A later save keeps what it does not use.
    settingsfile.write(tmp_path, {"ADMIN_NAME": "Kai"})
    assert saved(tmp_path) == {"PERSONA_NAME": "Ann", "SERVER_PORT": 8000, "PAGE_PORT": "not a port",
                               "ROAM_ENABLED": "maybe", "ADMIN_NAME": "Kai"}


def test_a_file_that_is_not_an_object_is_set_aside_before_a_save(tmp_path):
    settingsfile.path(tmp_path).write_text("PERSONA_NAME=Ann\n", encoding="utf-8")
    assert settingsfile.values(tmp_path) == {}
    settingsfile.write(tmp_path, {"PERSONA_NAME": "Ann"})
    assert saved(tmp_path) == {"PERSONA_NAME": "Ann"}
    assert (tmp_path / "settings.json.unreadable").read_text(encoding="utf-8") == "PERSONA_NAME=Ann\n"


def test_the_environment_wins_over_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "from-the-shell")
    monkeypatch.delenv("PERSONA_NAME", raising=False)
    settingsfile.write(tmp_path, {"LLM_MODEL": "from-the-file", "PERSONA_NAME": "Ann"})
    settingsfile.load(tmp_path)
    assert (os.environ["LLM_MODEL"], os.environ["PERSONA_NAME"]) == ("from-the-shell", "Ann")
    # A setting the file no longer holds goes back to unset; one the shell set stays.
    settingsfile.write(tmp_path, {"LLM_MODEL": "", "PERSONA_NAME": ""})
    settingsfile.load(tmp_path)
    assert "PERSONA_NAME" not in os.environ and os.environ["LLM_MODEL"] == "from-the-shell"
    monkeypatch.delenv("PERSONA_NAME", raising=False)


def test_a_key_typed_into_the_file_moves_to_the_credential_store(tmp_path):
    key = "sk-typed-by-hand-0123456789"
    settingsfile.path(tmp_path).write_text(json.dumps({"LLM_API_KEY": key, "LLM_MODEL": "m"}), encoding="utf-8")
    assert settingsfile.values(tmp_path)["LLM_API_KEY"] == key
    assert saved(tmp_path) == {"LLM_MODEL": "m"}
    assert secretstore.get(tmp_path, "LLM_API_KEY") == key


def test_a_env_from_before_is_moved_in_once_and_what_it_cannot_place_stays_beside(tmp_path, caplog):
    key = "sk-from-the-old-file-0123456789"
    (tmp_path / ".env").write_text(
        "# written by an older Mibo\n"
        f"LLM_API_KEY={key}\n"
        "PERSONA_NAME='Zed #1'\n"
        'ADMIN_NAME="say \\"hi\\""\n'
        "LLM_BASE_URL=https://api.example.com/v1\n"
        "LLM_MODEL=m\n"
        "LLM_DM_MODEL=${LLM_MODEL}\n"
        "ROAM_ENABLED=false\n"
        "SERVER_PORT=8000\n"
        "this line is not a setting\n"
        "EMPTY=\n", encoding="utf-8")
    # What the old writer left beside it: its lock, and a copy an interrupted save did not rename.
    (tmp_path / "..env.lock").write_text("", encoding="utf-8")
    (tmp_path / "..env.k2x9.tmp").write_text(f"LLM_API_KEY={key}\n", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="agent"):
        values = settingsfile.values(tmp_path)
    # As the app read the .env: quotes taken off, ${VAR} filled in, a blank value unset.
    assert values == {"LLM_API_KEY": key, "PERSONA_NAME": "Zed #1", "ADMIN_NAME": 'say "hi"',
                      "LLM_BASE_URL": "https://api.example.com/v1", "LLM_MODEL": "m", "LLM_DM_MODEL": "m",
                      "ROAM_ENABLED": "false"}
    assert saved(tmp_path)["SERVER_PORT"] == "8000", "an unknown name moves with the rest, kept and reported"
    assert key not in settingsfile.path(tmp_path).read_text(encoding="utf-8")
    assert secretstore.get(tmp_path, "LLM_API_KEY") == key
    assert sorted(p.name for p in tmp_path.iterdir() if "env" in p.name) == [".env.unmoved"]
    assert (tmp_path / ".env.unmoved").read_text(encoding="utf-8") == "this line is not a setting\n"
    assert any("moved .env into settings.json" in r.getMessage() for r in caplog.records)
    # Once: the next read finds nothing to move.
    caplog.clear()
    assert settingsfile.values(tmp_path) == values
    assert not any("moved" in r.getMessage() for r in caplog.records)


def test_a_move_that_does_not_read_back_leaves_the_env_as_it_was(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LLM_API_KEY=sk-kept-in-place-0123456789\nLLM_MODEL=m\n", encoding="utf-8")
    monkeypatch.setattr(secretstore, "get", lambda home, name: "")
    settingsfile.values(tmp_path)
    assert (tmp_path / ".env").read_text(encoding="utf-8").startswith("LLM_API_KEY=")
    assert not settingsfile.path(tmp_path).exists()

"""The interface languages (mibo/locales): every language has every key with its slots in its own script, the
language follows the home's settings, and a first run starts in the system's language."""
from __future__ import annotations

import pytest

from mibo import i18n, langs, settingsfile
from mibo import setup_wizard as sw
from tools import locale_gaps


@pytest.mark.parametrize("code", [code for code in langs.CODES if code != "en"])
def test_every_language_has_every_key(code: str) -> None:
    assert locale_gaps.problems(code) == [], f"locales/{code}.json"


def test_the_interface_language_follows_the_home_s_settings(tmp_path, monkeypatch) -> None:
    """Changed settings are read again, a file edited by hand too, and another home has its own answer."""
    monkeypatch.delenv("UI_LANG", raising=False)
    monkeypatch.delenv("AGENT_LANG", raising=False)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    settingsfile.write(tmp_path, {"AGENT_LANG": "ja"})
    assert i18n.lang() == "ja"
    settingsfile.path(tmp_path).write_text('{"AGENT_LANG": "de"}', encoding="utf-8")
    assert i18n.lang() == "de"
    other = tmp_path / "other"
    settingsfile.write(other, {"AGENT_LANG": "ko"})
    monkeypatch.setenv("AGENT_HOME", str(other))
    assert i18n.lang() == "ko"


def test_the_system_language_picks_the_first_language(monkeypatch) -> None:
    for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert sw.detect_lang() == "zh"
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert sw.detect_lang() == "en"
    # Windows' own name for a locale, and one that names no language.
    assert sw._lang_of("Chinese (Simplified)_China") == "zh"
    assert sw._lang_of("C.UTF-8") == ""

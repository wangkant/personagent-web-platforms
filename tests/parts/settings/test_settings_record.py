"""The one record the app is configured from (mibo/settings.py): what the home's settings set is read, a bad
value falls back to the documented default instead of stopping the start, and the defaults agree everywhere."""
from __future__ import annotations

import dataclasses
import re

from mibo.config_env import DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL, env_int, vision_endpoint_from_env
from mibo.settings import AgentSettings, normalize_lang
from mibo.settingsfile import Settings

# Settings with every setting these tests read set to something distinctive.
FULL_ENV = {
    "LLM_API_KEY": "sk-live", "LLM_BASE_URL": "https://llm.example/v1/",
    "LLM_MODEL": "live-model", "PERSONA_NAME": "Live", "MEMORY_FILE": "m.json",
    "ADMIN_NAME": "O", "ADMIN_RELATIONSHIP": "rel",
    "LLM_DM_MODEL": "dm-model", "LLM_FALLBACK_MODEL": "cheap-model",
    "LLM_FALLBACK_BASE_URL": "https://fb.example/v1/", "LLM_FALLBACK_API_KEY": "sk-fb",
    "LLM_FALLBACK_THINKING": "true",
    "LLM_RATE_WINDOW_S": "13", "LLM_RATE_THRESHOLD": "14", "LLM_FALLBACK_DURATION_S": "15",
    "LLM_RATE_LIMIT_COOLDOWN_S": "16",
    "MEMORY_CALLS_PER_DAY": "0", "MEMORY_PROMPT_CHARS": "900", "MEMORY_KEEP_RECENT": "false",
    "VISION_MODEL": "v-model", "VISION_API_KEY": "vk",
    "VISION_BASE_URL": "https://vision.example/v4/", "AGENT_LANG": " ZH ",
}


def test_the_vision_endpoint_has_its_own_names_and_no_vendor_default() -> None:
    assert vision_endpoint_from_env({"VISION_API_KEY": "vk", "VISION_BASE_URL": "https://v.example/v4/"}) \
        == ("vk", "https://v.example/v4")
    assert vision_endpoint_from_env({"VISION_API_KEY": "vk"}) == ("vk", "")
    assert vision_endpoint_from_env({}) == ("", "")
    s = AgentSettings.from_env(env={"LLM_API_KEY": "k", "VISION_API_KEY": "vk",
                                    "VISION_BASE_URL": "https://v.example/v4/"})
    assert (s.vision_api_key, s.vision_base_url) == ("vk", "https://v.example/v4")


def test_plain_construction_ignores_the_settings_around_it() -> None:
    plain = AgentSettings(api_key="k")
    assert (plain.model, plain.base_url) == (DEFAULT_LLM_MODEL, DEFAULT_LLM_BASE_URL)
    assert (plain.llm_fallback_duration_s, plain.llm_rate_limit_cooldown_s) == (180, 20)
    empty = AgentSettings.from_env(env={}, api_key="k")
    assert (empty.roam_walks_a_day, empty.llm_timeout_s) == (3, 120.0)


def test_every_setting_is_read() -> None:
    s = AgentSettings.from_env(env=FULL_ENV)
    assert (s.api_key, s.model, s.llm_fallback_api_key) == ("sk-live", "live-model", "sk-fb")
    # A trailing slash is trimmed off every base URL.
    assert (s.base_url, s.vision_base_url, s.llm_fallback_base_url) == (
        "https://llm.example/v1", "https://vision.example/v4", "https://fb.example/v1")
    assert (s.llm_fallback_duration_s, s.llm_rate_limit_cooldown_s) == (15, 16)
    assert (s.memory_calls_per_day, s.memory_prompt_chars, s.memory_keep_recent) == (0, 900, False)
    assert (s.agent_lang, s.admin_name, s.admin_relationship) == ("zh", "O", "rel")
    # A fallback endpoint left unset means the primary's, and is sent `thinking` only when told it takes it.
    unset = AgentSettings.from_env(env={"LLM_API_KEY": "k"})
    assert (unset.llm_fallback_base_url, unset.llm_fallback_api_key) == ("", "")
    assert s.llm_fallback_thinking is True and unset.llm_fallback_thinking is False
    assert AgentSettings.from_env(env=FULL_ENV, persona_name="Override").persona_name == "Override"


def test_an_out_of_range_setting_falls_back_rather_than_raising() -> None:
    def port(raw: str) -> int:
        return env_int("SERVER_PORT", 8080, minimum=1, maximum=65535, env={"SERVER_PORT": raw})

    assert (port("not-a-number"), port("70000"), port("9000")) == (8080, 8080, 9000)
    s = AgentSettings.from_env(env={
        "LLM_API_KEY": "k", "ROAM_GAP_HOURS": "lots", "LLM_TIMEOUT_S": "0", "LLM_RATE_LIMIT_COOLDOWN_S": "0",
        "MEMORY_CALLS_PER_DAY": "-1", "MEMORY_PROMPT_CHARS": "5"})
    assert (s.roam_gap_h, s.llm_timeout_s, s.llm_rate_limit_cooldown_s) == (3.0, 120.0, 20)
    assert (s.memory_calls_per_day, s.memory_prompt_chars) == (40, 2400)


def _documented() -> dict[str, str]:
    """The defaults the settings model's descriptions state as a value ("Blank = 120")."""
    found = {name: re.search(r"Blank = ([^\s,;]+)$", field.description or "")
             for name, field in Settings.model_fields.items()}
    return {name: m.group(1) for name, m in found.items() if m and not m.group(1).isupper()}


def test_one_source_of_defaults(monkeypatch) -> None:
    """The constructor, from_env() and the settings model's descriptions agree on every default, compared by
    value through the parser. `lang` is the raw input agent_lang comes from."""
    template = _documented()
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
    plain = AgentSettings(api_key="k")
    env_defaults = AgentSettings.from_env(env={"LLM_API_KEY": "k"})
    documented = AgentSettings.from_env(env={**template, "LLM_API_KEY": "k"})
    fields = [f.name for f in dataclasses.fields(AgentSettings) if f.name != "lang"]
    assert {name: getattr(plain, name) for name in fields} == {name: getattr(env_defaults, name) for name in fields}
    assert {name: getattr(env_defaults, name) for name in fields} == {name: getattr(documented, name) for name in fields}
    assert {"LLM_BASE_URL", "LLM_MODEL", "LLM_FALLBACK_DURATION_S", "ROAM_GAP_HOURS", "MEMORY_TIDY_EVERY"} <= set(template)


def test_agent_lang_spellings_are_normalised() -> None:
    for raw, want in (("zh-CN", "zh"), ("zh_cn", "zh"), ("cn", "zh"), ("Chinese", "zh"), (" ZH ", "zh"),
                      ("en-US", "en"), ("english", "en"), ("", "en"), ("ja", "ja")):
        assert normalize_lang(raw) == want, raw
    assert AgentSettings.from_env(env={"LLM_API_KEY": "k", "AGENT_LANG": "zh_CN"}).agent_lang == "zh"
    assert AgentSettings(api_key="k", lang="en-GB").agent_lang == "en"


def test_an_empty_model_falls_back_in_order_and_never_to_nothing() -> None:
    bare = AgentSettings(api_key="k", model="main")
    assert (bare.llm_fallback_model, bare.llm_dm_model) == ("main", "main")
    assert AgentSettings(api_key="k", model="main", llm_fallback_model="cheap").llm_judge_model == "cheap"
    assert AgentSettings(api_key="k", model="main", llm_fallback_model="cheap",
                         llm_judge_model="judge").llm_judge_model == "judge"


def test_resolving_a_resolved_record_again_changes_nothing() -> None:
    s = AgentSettings.from_env(env=FULL_ENV)
    again = dataclasses.replace(s)
    assert [f.name for f in dataclasses.fields(s) if getattr(s, f.name) != getattr(again, f.name)] == []

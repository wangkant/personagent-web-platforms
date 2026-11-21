"""The model providers the setup offers (setup_wizard.PROVIDERS)."""
from __future__ import annotations

from mibo import setup_wizard as sw
from mibo.config_env import DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL


def test_every_provider_has_an_address_a_model_and_where_keys_come_from() -> None:
    assert [p.key for p in sw.PROVIDERS] == [
        "openrouter", "deepseek", "siliconflow", "bailian", "zhipu", "moonshot", "ark", "minimax", "qianfan",
        "hunyuan", "openai", "anthropic", "gemini", "xai", "mistral", "groq", "ollama", "lmstudio", "other"]
    # DeepSeek first for Chinese, OpenRouter first elsewhere.
    assert sw.providers_for("zh")[0].key == "deepseek" and sw.providers_for("en")[0].key == "openrouter"
    for p in sw.PROVIDERS[:-1]:
        assert p.base_url.startswith("https://") or not p.needs_key, p.key
        assert p.model, p.key
        assert p.key_url or not p.needs_key, p.key
        assert sw.provider_for_base(p.base_url) is p, p.key
    assert sw.provider_for_base("https://api.openai.com/").key == "openai"
    deepseek = sw.provider_named("deepseek")
    assert (deepseek.base_url, deepseek.model) == (DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL)


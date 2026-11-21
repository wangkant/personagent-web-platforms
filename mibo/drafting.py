"""The page's own requests to the configured model (remarks, profiles, recommendations,
skills), outside the service, counted beside its tally."""
from __future__ import annotations

import time
from pathlib import Path

from .llm import ModelCalls, _call_purpose
from .usage import PAGE_USAGE_FILE, UsageBook

LANGUAGE_NAMES = {"en": "English", "zh": "Simplified Chinese", "zh-TW": "Traditional Chinese",
                  "ja": "Japanese", "ko": "Korean", "es": "Spanish", "fr": "French", "de": "German",
                  "pt-BR": "Brazilian Portuguese", "ru": "Russian"}

class _Client(ModelCalls):
    """The service's own model calls, outside the service: one endpoint, no fallback."""

    def __init__(self, values: dict, usage_path: Path) -> None:
        self.base_url = values.get("LLM_BASE_URL", "").strip()
        self.api_key = values.get("LLM_API_KEY", "").strip()
        self.model = values.get("LLM_MODEL", "").strip()
        self.llm_fallback_model = ""
        self.llm_fallback_base_url = ""
        self.llm_fallback_api_key = ""
        self.llm_fallback_thinking = False
        self.llm_timeout_s = 90.0
        self.api_max_retries = 1
        self.llm_rate_limit_cooldown_s = 60
        self.llm_fallback_duration_s = 60
        self._unreachable_until: dict = {}
        self._usage_path = usage_path

    def _count_usage(self, model: str, body: dict) -> None:
        try:
            book = UsageBook(self._usage_path)
            label = _call_purpose.get()
            book.add(model, body.get("usage") if isinstance(body, dict) else None, time.time(),
                     "persona" if label == "other" else label)
            book.flush(time.time())
        except Exception:
            pass


def usage_path(home: Path, values: dict) -> Path:
    # Beside the service's own tally, which the App view reads with it.
    memory = Path(str(values.get("MEMORY_FILE") or "memory.json"))
    memory = memory if memory.is_absolute() else home / "runtime" / memory
    return memory.with_name(PAGE_USAGE_FILE)

"""Everything the agent is configured with: one record, built once, passed in.

``Agent`` has no parsing logic or defaults of its own; it copies these fields.

* ``AgentSettings(...)``: literal defaults for the home's settings, the
  environment for the operational knobs (the memory and roam limits). A tool
  or test gets the agent it configured, not one the surrounding home's
  settings configured.
* ``AgentSettings.from_env()``: also reads the home's settings
  (``LLM_API_KEY``, ``PERSONA_NAME``, ...), which settingsfile.load put in the
  environment. This is the app.

Both share the defaults :class:`mibo.settingsfile.Settings` documents, and every
read goes through :mod:`mibo.config_env`: a bad value means the default and a
warning.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, fields
from typing import Optional

from . import langs
from .config_env import (DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL, env_bool,
                         env_float, env_int, env_str, vision_endpoint_from_env)

logger = logging.getLogger("agent")

_warned_langs: set[str] = set()


def _env(read):
    """A field whose default is ``read(None)`` (os.environ); ``from_env(env=...)``
    calls ``read(env)`` instead."""
    return field(default_factory=lambda: read(None), metadata={"env": read})


def walk_time(value: str) -> str:
    """`value` as "HH:MM" when it is a time of day, else ""."""
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", value or "")
    return f"{int(match[1]):02d}:{match[2]}" if match else ""


def normalize_lang(value: object) -> str:
    """AGENT_LANG as one of the language codes: "zh" for zh-CN and cn, "zh-TW"
    for zh_hk, "en" for en-US and blank. Anything else is kept (lowercased) with
    a warning, since it names data files and ledgers that may already exist."""
    raw = str(value or "").strip().lower().replace("_", "-")
    if not raw:
        return "en"
    lang = langs.normalize(raw)
    if lang:
        return lang
    if raw not in _warned_langs:
        _warned_langs.add(raw)
        logger.warning("AGENT_LANG=%r is not one of %s", value, ", ".join(langs.CODES))
    return raw


def content_lang(value: object) -> str:
    """The language of the prompts, bundled data and ledgers AGENT_LANG reads."""
    return langs.content(normalize_lang(value))


@dataclass
class AgentSettings:
    """One agent's configuration, fully resolved; the fields are the keywords
    ``Agent(...)`` accepts.

    ``__post_init__`` resolves every setting (empty-model fallbacks, URL
    trimming, coercions) and is idempotent, so ``dataclasses.replace`` is safe.
    """

    # ---- the LLM endpoint -------------------------------------------------
    api_key: str
    base_url: str = DEFAULT_LLM_BASE_URL
    model: str = DEFAULT_LLM_MODEL
    #: Model name for the chat's replies, on the primary endpoint unless it is the fallback's
    #: name (``endpoints.endpoint_for``). Blank resolves to ``model``, never "".
    llm_dm_model: str = ""
    llm_fallback_model: str = ""
    #: The fallback model's own endpoint, so a primary outage is not also the
    #: fallback's. Blank = the primary's; only a fallback model distinct from
    #: ``model`` is sent there.
    llm_fallback_base_url: str = ""
    llm_fallback_api_key: str = ""
    #: Send DeepSeek's ``thinking`` field to the fallback's host unasked. Off:
    #: an unlisted host is probed first (``llm.ModelCalls._thinking_verdict``).
    llm_fallback_thinking: bool = False
    #: The cheap model for the short calls beside a reply (memory folds, a
    #: page's summary). Blank = the fallback model.
    llm_judge_model: str = _env(
        lambda env: env_str("LLM_JUDGE_MODEL", "", strip=True, env=env))
    #: LLM transient-error retry count (jittered backoff; 0 disables).
    api_max_retries: int = _env(
        lambda env: env_int("LLM_MAX_RETRIES", 2, minimum=0, env=env))
    #: Main LLM call timeout (seconds); reasoning models can be slow. A turn
    #: as a whole gets one and a half of it (``turns._TURN_BUDGET_CALLS``).
    llm_timeout_s: float = _env(
        lambda env: env_float("LLM_TIMEOUT_S", 120.0, minimum=1.0, env=env))
    #: The main model's context window in tokens, for a model whose host does not list it; 0 = what the host
    #: lists, else what an overflow error stated, else 32768 assumed (context.window).
    context_window: int = 0

    # ---- identity ---------------------------------------------------------
    persona_name: str = ""
    #: The shipped character the persona was set up as (chatgpt, deepseek, claude).
    persona_avatar: str = ""
    #: What the person at the app is called, and what they are to the character.
    admin_name: str = ""
    admin_relationship: str = ""
    #: A language code; blank means whatever ``AGENT_LANG`` says.
    lang: str = ""
    #: The resolved language code.
    agent_lang: str = ""

    # ---- memory -----------------------------------------------------------
    memory_file: str = "memory.json"
    #: Notes per chat; past it, the least useful note the character wrote goes.
    memory_max_per_conversation: int = 50
    #: Notes written in replies since the last consolidation that also start
    #: one; 0 turns off every memory model call.
    memory_tidy_every: int = 8
    #: Consolidation calls per day for all chats together; 0 turns them off.
    memory_calls_per_day: int = 40
    #: Characters of remembered material in a reply: the summary of earlier
    #: conversation, facts and events.
    memory_prompt_chars: int = 2400
    #: Events kept per chat before the least useful are removed.
    memory_episodes_max: int = 120
    #: Keep each chat's recent messages on disk for 72 hours.
    memory_keep_recent: bool = True
    #: Days each chat's log of lines is kept, so notes can be checked against
    #: it; 0 keeps no log (nor does MEMORY_KEEP_RECENT=false).
    memory_log_days: int = 30
    #: Persona document; ``None`` means the agent loads ``PERSONA_FILE``, so
    #: file reads stay out of this record.
    persona: Optional[str] = None

    # ---- vision ------------------------------------------------------------
    vision_model: str = ""
    vision_api_key: str = ""
    vision_base_url: str = ""

    # ---- retrieval embeddings (optional) ---------------------------------
    #: An OpenAI-compatible /embeddings model that adds a semantic signal to
    #: retrieval. Blank = off: rows are ranked on words, scope and recency.
    embedding_model: str = ""
    #: Blank = the primary's endpoint and key. A URL of its own is sent
    #: ``embedding_api_key`` only, never ``api_key``.
    embedding_base_url: str = ""
    embedding_api_key: str = ""

    # ---- model-error fallback ---------------------------------------------
    #: A failed model is skipped for this long (the per-model error fallback).
    llm_fallback_duration_s: int = 180
    #: How long a model that answered 429 is skipped: metered, not broken, so
    #: only the next few turns avoid it.
    llm_rate_limit_cooldown_s: int = 20

    # ---- walks (roam.py) ----------------------------------------------------
    #: Going out on its own as a signed-out visitor; it also needs a browser
    #: and a model, and stays home without them.
    roam_enabled: bool = _env(
        lambda env: env_bool("ROAM_ENABLED", True, env=env))
    roam_walks_a_day: int = _env(
        lambda env: env_int("ROAM_WALKS_A_DAY", 3, minimum=0, maximum=6, env=env))
    #: "HH:MM" local time before which the day's first walk of its own does not leave; "" for any time.
    roam_at: str = _env(
        lambda env: walk_time(env_str("ROAM_AT", "", strip=True, env=env)))
    roam_gap_h: float = _env(
        lambda env: env_float("ROAM_GAP_HOURS", 3.0, minimum=1.0, maximum=24.0, env=env))
    #: Pages read in one walk at most; it reads at least 3.
    roam_pages: int = _env(
        lambda env: env_int("ROAM_PAGES_PER_WALK", 8, minimum=3, maximum=12, env=env))
    roam_site_pages_a_day: int = _env(
        lambda env: env_int("ROAM_SITE_PAGES_A_DAY", 30, minimum=1, maximum=60, env=env))

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.llm_fallback_base_url = self.llm_fallback_base_url.rstrip("/")
        self.vision_base_url = self.vision_base_url.rstrip("/")
        self.vision_model = self.vision_model.strip()
        self.embedding_model = self.embedding_model.strip()
        self.embedding_base_url = self.embedding_base_url.strip().rstrip("/")
        self.embedding_api_key = self.embedding_api_key.strip()
        if not self.agent_lang:
            self.agent_lang = self.lang or env_str("AGENT_LANG", "")
        self.agent_lang = normalize_lang(self.agent_lang)
        # Empty-model fallbacks, in dependency order: a blank model name 400s.
        self.llm_fallback_model = self.llm_fallback_model or self.model
        self.llm_judge_model = (
            self.llm_judge_model or self.llm_fallback_model or self.model)
        self.llm_dm_model = self.llm_dm_model or self.model

    @classmethod
    def from_env(cls, env=None, **overrides) -> "AgentSettings":
        """The app's configuration, the home's settings included.

        An out-of-range value falls back to the default and warns."""
        def _str(name: str, default: str = "", *, strip: bool = False) -> str:
            return env_str(name, default, strip=strip, env=env)

        vision_key, vision_base = vision_endpoint_from_env(env)
        # A default is written once, on its field.
        declared = {f.name: f.default for f in fields(cls)}
        defaults = dict(
            api_key=_str("LLM_API_KEY"),
            base_url=_str("LLM_BASE_URL", declared["base_url"]),
            model=_str("LLM_MODEL", declared["model"]),
            persona_name=_str("PERSONA_NAME"),
            persona_avatar=_str("PERSONA_AVATAR"),
            llm_dm_model=_str("LLM_DM_MODEL"),
            context_window=env_int("CONTEXT_WINDOW", declared["context_window"], minimum=4096,
                                   maximum=100_000_000, env=env),
            memory_file=_str("MEMORY_FILE", declared["memory_file"]),
            memory_max_per_conversation=env_int(
                "MEMORY_MAX_PER_CONVERSATION",
                declared["memory_max_per_conversation"],
                minimum=1, maximum=10_000, env=env),
            memory_tidy_every=env_int(
                "MEMORY_TIDY_EVERY", declared["memory_tidy_every"],
                minimum=0, maximum=10_000, env=env),
            memory_calls_per_day=env_int(
                "MEMORY_CALLS_PER_DAY", declared["memory_calls_per_day"],
                minimum=0, maximum=10_000, env=env),
            memory_prompt_chars=env_int(
                "MEMORY_PROMPT_CHARS", declared["memory_prompt_chars"],
                minimum=200, maximum=100_000, env=env),
            memory_episodes_max=env_int(
                "MEMORY_EPISODES_MAX", declared["memory_episodes_max"],
                minimum=1, maximum=10_000, env=env),
            memory_keep_recent=env_bool(
                "MEMORY_KEEP_RECENT", declared["memory_keep_recent"], env=env),
            memory_log_days=env_int(
                "MEMORY_LOG_DAYS", declared["memory_log_days"],
                minimum=0, maximum=3650, env=env),
            admin_name=_str("ADMIN_NAME"),
            admin_relationship=_str("ADMIN_RELATIONSHIP"),
            llm_fallback_model=_str("LLM_FALLBACK_MODEL"),
            llm_fallback_base_url=_str("LLM_FALLBACK_BASE_URL"),
            llm_fallback_api_key=_str("LLM_FALLBACK_API_KEY"),
            llm_fallback_thinking=env_bool(
                "LLM_FALLBACK_THINKING", declared["llm_fallback_thinking"],
                env=env),
            llm_fallback_duration_s=env_int(
                "LLM_FALLBACK_DURATION_S", declared["llm_fallback_duration_s"],
                minimum=1, maximum=86_400, env=env),
            llm_rate_limit_cooldown_s=env_int(
                "LLM_RATE_LIMIT_COOLDOWN_S", declared["llm_rate_limit_cooldown_s"],
                minimum=1, maximum=86_400, env=env),
            vision_model=_str("VISION_MODEL"),
            vision_api_key=vision_key,
            vision_base_url=vision_base,
            embedding_model=_str("EMBEDDING_MODEL"),
            embedding_base_url=_str("EMBEDDING_BASE_URL"),
            embedding_api_key=_str("EMBEDDING_API_KEY"),
            lang=normalize_lang(_str("AGENT_LANG", "en", strip=True)),
        )
        # The operational knobs' field defaults read os.environ; apply ``env`` to them too.
        if env is not None:
            defaults.update((f.name, f.metadata["env"](env))
                            for f in fields(cls) if "env" in f.metadata)
        defaults.update(overrides)
        return cls(**defaults)

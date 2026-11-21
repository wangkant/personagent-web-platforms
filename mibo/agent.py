"""The Agent: the character, wired from one settings record. Its behaviour lives
in one mixin per concern; this module builds its state. The app reaches it
through character.py."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional


from . import channels
from . import embeddings as embeddings_mod
from . import langs
from . import recent as recent_mod
from .paths import (
    ROOT,
    resolve_runtime_state_file,
    resolve_seed_lang_file,
)
from .consolidate import Consolidation
from .decision import ReplyDecision
from .dm import DirectMessages
from .followup import FollowUp
from .ingestion import ContentIngestion
from .llm import ModelCalls
from .loop import ToolLoop
from .memory import Memory
from .messages import MessageParsing
from .prompt import PromptBuilder
from .prompts import parse_persona_style, render_persona_template
from .retrieval import Retrieval
from .search import WebSearch
from .settings import AgentSettings
from .textproc import ReplyStyle
from .turns import Turns
from .views import DataViews

logger = logging.getLogger("agent")


def _load_persona_card() -> Optional[dict]:
    """The optional persona card (PERSONA_CARD_FILE) with the `reply_style`
    policy."""
    card_path = ROOT / os.getenv("PERSONA_CARD_FILE", "persona.card.json")
    if not card_path.is_file():
        return None
    return json.loads(card_path.read_text(encoding="utf-8"))


def _card_aliases(card: Optional[dict]) -> tuple[str, ...]:
    """The card's optional `aliases`: nicknames that call the persona too."""
    raw = card.get("aliases") if isinstance(card, dict) else None
    if not isinstance(raw, list):
        return ()
    names = (" ".join(a.split()) for a in raw if isinstance(a, str))
    return tuple(dict.fromkeys(n for n in names if n))


_persona_sources_logged: set[str] = set()


def _note_persona_source(source: str) -> None:
    """Say once per process which persona document the agent runs on."""
    if source not in _persona_sources_logged:
        _persona_sources_logged.add(source)
        logger.info("[Agent] persona: %s", source)


def _load_persona() -> str:
    """The text of PERSONA_FILE (default persona.txt); "" when there is none."""
    persona_path = ROOT / os.getenv("PERSONA_FILE", "persona.txt")
    if not persona_path.is_file():
        return ""
    _note_persona_source(str(persona_path))
    return persona_path.read_text(encoding="utf-8").strip()


def _first_character(s: AgentSettings, lang: str) -> str:
    """The shipped character a home with no persona of its own runs on, as
    the setup writes it: the first of the list, Mibo."""
    from . import setup_wizard as sw
    first = sw.CHARACTERS[0]
    _note_persona_source(f"the shipped character {first.key} (no persona text of its own)")
    return sw.persona_text(first.key, name=s.persona_name or first.stem, lang=lang,
                           admin_name=s.admin_name,
                           admin_relationship=s.admin_relationship).strip()


# A few seconds: a kill loses at most the last turn, and one file write a turn is cheap.
_RECENT_FLUSH_S = 5.0


def _loop_time() -> float:
    """The running loop's clock, which is the monotonic clock outside tests;
    one clock for the write interval and its timer keeps a replay exact."""
    try:
        return asyncio.get_running_loop().time()
    except RuntimeError:
        return time.monotonic()


class Agent(Turns, DirectMessages, MessageParsing, ReplyDecision, PromptBuilder,
            Retrieval, ModelCalls, ToolLoop, WebSearch, FollowUp, Consolidation, Memory,
            DataViews, ContentIngestion):
    def __init__(self, settings: Optional[AgentSettings] = None, **overrides):
        """Wire one agent from one settings record: ``Agent(settings)``, or
        the settings fields as keywords (``Agent(api_key=..., ...)``)."""
        if settings is None:
            settings = AgentSettings(**overrides)
        #: As configured; the attributes below are the live values.
        self.settings = settings
        self._apply_settings(settings)
        self._init_runtime_state()
        self._init_stores()

        self.enabled = bool(self.api_key)
        if not self.enabled:
            logger.warning("[Agent] LLM_API_KEY is not set, so the agent answers "
                           "nothing; the app's model settings set it")

    def _apply_settings(self, s: AgentSettings) -> None:
        """Copy the configuration onto the agent, one field per attribute, so
        callers can read and reassign `self.model` and the rest directly."""
        self.api_key = s.api_key
        self.base_url = s.base_url
        self.model = s.model
        # The validator, data files, ledgers and lexicons read `agent_lang`, en or
        # zh; what the agent says itself reads `chat_lang`, the configured language.
        self.agent_lang = langs.content(s.agent_lang)
        self.chat_lang = s.agent_lang
        self.llm_fallback_model = s.llm_fallback_model
        self.llm_fallback_base_url = s.llm_fallback_base_url
        self.llm_fallback_api_key = s.llm_fallback_api_key
        self.llm_fallback_thinking = s.llm_fallback_thinking
        self.llm_judge_model = s.llm_judge_model
        self.llm_dm_model = s.llm_dm_model
        self.api_max_retries = s.api_max_retries
        self.llm_timeout_s = s.llm_timeout_s
        self.llm_fallback_duration_s = s.llm_fallback_duration_s
        self.llm_rate_limit_cooldown_s = s.llm_rate_limit_cooldown_s

        self.persona_name = s.persona_name
        self.admin_name = s.admin_name
        self.admin_relationship = s.admin_relationship

        if s.persona is not None:
            raw_persona = s.persona
        else:
            # A copied template still carries placeholders and a note to the reader.
            loaded = _load_persona()
            raw_persona = render_persona_template(
                loaded, bot_name=s.persona_name, admin_name=s.admin_name,
                admin_relationship=s.admin_relationship, lang=self.chat_lang)
            if not raw_persona:
                raw_persona = _first_character(s, self.chat_lang)
            elif raw_persona != loaded:
                _note_persona_source(
                    "filled in the template's {placeholders} and dropped its "
                    "note to the reader")
        # A trailing [style] block is stripped from the prose and kept as knobs.
        self.persona_style, self.persona = parse_persona_style(raw_persona)
        card = _load_persona_card()
        self.persona_aliases = _card_aliases(card)
        self.reply_style = ReplyStyle.from_card(card)

        self.memory_file = resolve_runtime_state_file(s.memory_file)
        self.memory_max_per_conversation = s.memory_max_per_conversation
        self.memory_tidy_every = s.memory_tidy_every

        self.vision_model = s.vision_model
        self.vision_api_key = s.vision_api_key
        self.vision_base_url = s.vision_base_url
        self.embedding_model = s.embedding_model
        self.embedding_base_url = s.embedding_base_url
        self.embedding_api_key = s.embedding_api_key

    def _init_runtime_state(self) -> None:
        """The empty per-process state: locks, the chats' recent turns, caches."""
        # Error-driven fallback, per model name, for every mode.
        self._fallback_until: dict[str, float] = {}
        # Armed only by throttling and outages, never by one refused request.
        self._unreachable_until: dict[str, float] = {}
        self._http_pool: dict = {}
        # Strong refs: asyncio only weak-refs running tasks.
        self._bg_tasks: set[asyncio.Task] = set()

        # The recent turns of every chat on disk (recent.py), so a restart
        # keeps the conversation; turns carry `t` and `seq`.
        self._recent = recent_mod.RecentStore(self.memory_file.with_name("recent.json"))
        self._recent_dirty: set[str] = set()
        self._recent_flushed_at: Optional[float] = None
        self.window_seq: dict[str, int] = {}
        # Every line or turn numbered up to this is consolidated (consolidate.py).
        self.folded_upto: dict[str, int] = {}
        # Unconsolidated turns of a chat that the history's trim dropped.
        self._dm_spill: dict[str, list] = {}
        # A chat's state is read and written under its lock.
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # One turn of a chat at a time: an answer, or the follow-up of a plan (dm.py, followup.py).
        self.turn_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        self.image_caption_cache: dict[str, str] = {}
        self.bili_info_cache: dict[str, dict] = {}
        # url -> rendered descriptor, a bounded FIFO.
        self.url_info_cache: dict[str, str] = {}
        self._wbi_keys: tuple[str, str] = ("", "")
        self._wbi_keys_ts: float = 0.0
        self.dm_history: dict[str, list[dict]] = {}
        # The person's messages taken in and not answered yet, merged into
        # their next turn (_dm_turn).
        self._dm_unanswered: dict[str, list[str]] = {}
        # When they last wrote in each chat: a follow-up never cuts in (followup.py).
        self.last_dm_activity_at: dict[str, float] = defaultdict(float)
        self._restore_recent()

    # ---- the recent turns on disk -----------------------------------------

    def _restore_recent(self) -> None:
        """Load recent.json (or, with MEMORY_KEEP_RECENT off, delete it)."""
        store = self._recent
        if not self.chat_log.on:
            # Off: no line of any chat stays on disk in a log either.
            self.chat_log.clear()
        if not getattr(self.settings, "memory_keep_recent", True):
            store.delete()
            return
        store.load()
        store.trim()
        for conv, entry in store.convs.items():
            self._load_recent_entry(conv, entry)
        if store.convs:
            logger.info("[Agent] recent messages restored for %d chat(s)",
                        len(store.convs))

    def _load_recent_entry(self, conv: str, entry: dict) -> None:
        """A chat's saved entry into the running state; what is held wins."""
        self.window_seq[conv] = max(int(entry.get("seq") or 0),
                                    self.window_seq.get(conv, 0))
        self.folded_upto[conv] = max(int(entry.get("folded_upto") or 0),
                                     self.folded_upto.get(conv, 0))
        uid = channels.dm_user(conv)
        if entry.get("dm"):
            self.dm_history.setdefault(uid, list(entry["dm"]))
        if entry.get("dm_spill"):
            self._dm_spill.setdefault(uid, list(entry["dm_spill"]))
        if entry.get("unanswered"):
            self._dm_unanswered.setdefault(uid, list(entry["unanswered"]))
        if not self.last_dm_activity_at.get(uid):
            self.last_dm_activity_at[uid] = entry.get("last_at") or 0.0

    def _wake(self, conv: str) -> None:
        """A chat in use: what an eviction let go of comes back from disk."""
        if conv in self.window_seq:
            return
        snap = self._recent.snapshot(conv)
        if snap:
            self._load_recent_entry(conv, snap)
        # Numbers stay ahead of the log's, should recent.json have been lost.
        logged = self.chat_log.last_seq(conv) if self.chat_log.on else 0
        self.window_seq[conv] = max(self.window_seq.get(conv, 0), logged)

    def _recent_entry(self, conv: str) -> Optional[dict]:
        """A chat's entry for recent.json from what is held now; None when
        nothing of it is left."""
        snap = self._recent.snapshot(conv) or {}
        if conv not in self.window_seq:
            # Not held (evicted, or never woken): the copy on disk stands.
            return dict(snap) if snap else None
        uid = channels.dm_user(conv)
        folded = int(self.folded_upto.get(conv, snap.get("folded_upto", 0)) or 0)
        turns = (self.dm_history[uid] if uid in self.dm_history
                 else snap.get("dm") or [])
        entry = {"dm": list(turns),
                 "dm_spill": list(self._dm_spill.get(uid) or []),
                 "unanswered": list(self._dm_unanswered.get(uid) or [])[
                     -recent_mod.UNANSWERED_MAX:]}
        if not any(entry.values()):
            return None
        newest = max((float(r.get("t1") or r.get("t") or 0.0) for r in entry["dm"]), default=0.0)
        entry.update(seq=int(self.window_seq.get(conv, snap.get("seq", 0)) or 0),
                     folded_upto=folded,
                     last_at=max(float(self.last_dm_activity_at.get(uid, 0.0) or 0.0), newest,
                                 float(snap.get("last_at") or 0.0)))
        return entry

    def _maybe_flush_recent(self, force: bool = False, only: str = "") -> None:
        """Write the changed chats to recent.json: at most every few seconds, or
        now with `force` (shutdown) or `only` (a chat about to be evicted)."""
        dirty = self._recent_dirty
        if not getattr(self.settings, "memory_keep_recent", True):
            dirty.clear()
            return
        mono = _loop_time()
        if not (force or only) and (not dirty or (
                self._recent_flushed_at is not None
                and mono - self._recent_flushed_at < _RECENT_FLUSH_S)):
            if dirty:
                self._flush_recent_later()
            return
        store = self._recent
        convs = [only] if only else list(dirty)
        for conv in convs:
            self._log_chat(conv)
            # A 无痕对话 lives in memory only.
            entry = None if channels.private_chat(conv) else self._recent_entry(conv)
            if entry is None:
                store.drop(conv)
            else:
                store.put(conv, entry)
            dirty.discard(conv)
        trimmed = store.trim()
        if not convs and not trimmed:
            return
        try:
            store.write()
        except OSError as e:
            logger.warning("[Agent] recent messages save failed: %s", e)
        if not only:
            self._recent_flushed_at = mono

    def _flush_recent_later(self) -> None:
        """The last line of a turn has no later line to carry its write: a
        timer does, so a stop a few seconds on loses nothing."""
        if self.__dict__.get("_recent_timer") is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        def fire() -> None:
            self._recent_timer = None
            self._maybe_flush_recent(force=True)

        self._recent_timer = loop.call_later(_RECENT_FLUSH_S, fire)

    def _init_stores(self) -> None:
        """Open memory and the filters; after _apply_settings, since all of it
        hangs off the settings."""
        # The person fold, while it runs (consolidate.py).
        self._folding: set[str] = set()
        # The embedding backfill in flight, retry time and warned flag (retrieval.py).
        self._embedding_backfill: asyncio.Task | None = None
        self._embedding_retry_at = 0.0
        self._embedding_warned = False

        # Pre-send regex filter.
        self.output_filter_file = resolve_seed_lang_file(
            "output_filter", "json", self.agent_lang)
        self._filters_cache: list = []
        self._filters_stamp: tuple = ()

    def _sidecar(self, attr: str, cls, path: Path):
        """A lazy sidecar object, rebuilt whenever its path moves (harnesses
        repoint the pools after __init__)."""
        obj = getattr(self, attr, None)
        if obj is None or obj.path != path:
            obj = cls(path)
            setattr(self, attr, obj)
        return obj

    @property
    def embedding_cache(self) -> embeddings_mod.EmbeddingCache:
        """Retrieval's vectors, beside memory.json."""
        return self._sidecar("_embedding_cache", embeddings_mod.EmbeddingCache,
                             self.memory_file.with_name("embeddings.jsonl"))

    def _spawn(self, coro) -> asyncio.Task:
        """Launch a background task, held until it finishes; a crash is logged."""
        t = asyncio.create_task(coro, name=getattr(coro, "__qualname__", None))
        self._bg_tasks.add(t)
        t.add_done_callback(self._on_bg_task_done)
        return t

    def _on_bg_task_done(self, task: asyncio.Task) -> None:
        self._bg_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("[Agent] background task %s crashed: %s: %s",
                         task.get_name(), type(exc).__name__, exc, exc_info=exc)

    def flush_state(self) -> None:
        """Force out the throttled writes before shutdown."""
        self._maybe_flush_recent(force=True)
        self._flush_memory_counters(force=True)
        if self.memory_state._dirty:
            self._save_memory_state(force=True)
        book = self.__dict__.get("_usage_book")
        if book is not None:
            book.flush(time.time())

    async def aclose(self) -> None:
        """Stop owned work, close transports, then persist final state."""
        timer = self.__dict__.pop("_recent_timer", None)
        if timer is not None:
            timer.cancel()
        tasks = [task for task in self._bg_tasks
                 if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        clients = list(self._http_pool.values())
        self._http_pool.clear()
        if clients:
            await asyncio.gather(
                *(client.aclose() for client in clients
                  if not client.is_closed),
                return_exceptions=True)
        self.flush_state()

    CORE_MEMORY_MAX_CHARS = 400

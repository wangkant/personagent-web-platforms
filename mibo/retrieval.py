"""What past material a turn sees: examples and memories."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from .storage import num as _num
from . import channels, embeddings, ranking, transport
from .endpoints import embedding_endpoint
from .textproc import (
    _focus_tokens,
)

logger = logging.getLogger("agent")

#: MEMORY_PROMPT_CHARS when an agent carries no settings record.
_MEMORY_PROMPT_CHARS = 2400
#: Below this budget the running summary may take only half of it.
_THREAD_FULL_BUDGET = 800
_FACTS_MAX = 8
_EVENTS_MAX = 4
#: Lines of their past chats a message about the past brings, for a model that does not search them itself.
_LOG_LINES = 3
# A message asking what someone said.
_ASKS_SAID = re.compile(
    r"\b(?:said|say|says|told|tell|mention(?:ed)?|talk(?:ed)? about)\b"
    r"|\u8bf4\u8fc7|\u8bf4\u4e86|\u8bf4\u7684|\u63d0\u5230|\u63d0\u8fc7|"
    r"\u8bb2\u8fc7|\u8bb2\u7684|\u804a\u8fc7|\u804a\u4e86",
    re.IGNORECASE)
#: A reply must share this many focus tokens with an item to count as using it.
_USE_OVERLAP = 2
_USE_FLUSH_S = 60.0
# A message that leans on the turn before it: it points back, or names an item of the reply by its place.
_POINTS_BACK = re.compile(
    r"那个|这个|那篇|这篇|那条|那部|那家|那边|那里|它|上面|刚才|刚说|还有呢|然后呢|怎么办|为啥|为什么"
    r"|\b(?:that|this|it|those|them|what about|the other one)\b", re.IGNORECASE)
_PLACE = re.compile(r"第([一二两三四五六七八九十\d])(?:个|条|篇|部|家|种|样|项|点)"
                    r"|\bthe (first|second|third|fourth|fifth) one\b", re.IGNORECASE)
_PLACES = {**{c: n for n, c in enumerate("一二三四五六七八九十", 1)}, "两": 2,
           **{str(n): n for n in range(1, 10)},
           "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
_ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth")
# Where a reply's list breaks into items.
_ITEMS = re.compile(r"[\n。？！?!；;，,、]|或者|还有|\bor\b|\band\b")
# A message of this many terms or fewer says too little to rank on alone.
_FEW_TERMS = 3
# How much the turn before counts beside the message: when it leans back, and when it does not.
_LEANS, _FOLLOWS = 0.8, 0.25


def turn_before(turns: list, text: str, lang: str) -> tuple[str, float]:
    """What the last exchange before `text` adds to its ranking, and its weight:
    the item of the reply that "第二个" names; the exchange itself for a message
    that points back or says little; a little of it otherwise."""
    said = [(str(m.get("role") or ""), str(m.get("content") or "")) for m in turns[-2:]
            if isinstance(m, dict)]
    if not any(t.strip() for _, t in said):
        return "", 0.0
    place = _PLACE.search(text)
    reply = next((t for role, t in reversed(said) if role == "assistant"), "")
    if place and reply:
        items = [p.strip() for p in _ITEMS.split(reply) if p and p.strip()]
        n = _PLACES[(place.group(1) or place.group(2)).lower()]
        # The item the reply itself numbered ("第二个是…", "2. …") before counting: a lead-in line is no item.
        lead = re.compile(rf"(?:第[{'一二三四五六七八九十'[n - 1:n]}{n}]|{n}\s*[.、)）]|{_ORDINALS[n - 1]}\b)",
                          re.IGNORECASE) if n <= len(_ORDINALS) else None
        named = next((p for p in items if lead and lead.match(p)), "")
        if named:
            return named, 1.0
        if n <= len(items):
            return items[n - 1], 1.0
    leans = bool(_POINTS_BACK.search(text)) or len(ranking.plain_terms(_focus_tokens(text, lang))) <= _FEW_TERMS
    return "\n".join(t for _, t in said), _LEANS if leans else _FOLLOWS


def _moment(t: float, tz) -> str:
    """"Mon 10 Nov 19:05"."""
    return f"{ranking.day_label(t, tz)} {datetime.fromtimestamp(t, tz):%H:%M}"


def _days_ago(t: float, now: float, tz) -> str:
    """Calendar days in `tz`, so last night's event is "yesterday" this morning."""
    days = (datetime.fromtimestamp(now, tz).date()
            - datetime.fromtimestamp(t, tz).date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 60:
        return f"{days} days ago"
    return f"{days // 30} months ago"


def _overlaps(ep: dict, span: tuple[float, float]) -> bool:
    t0 = _num(ep.get("t0"))
    t1 = _num(ep.get("t1")) or t0
    t0 = t0 or t1
    return bool(t0) and t0 < span[1] and t1 >= span[0]


class Retrieval:
    def _stored_vector(self, key: str):
        if not self.embedding_model:
            return None
        return self.embedding_cache.get(self.embedding_model, key)

    def _query_vector(self, focus_text: str):
        if not (self.embedding_model and focus_text.strip()):
            return None
        return self._stored_vector(embeddings.text_key(focus_text))

    def _turn_before(self, conv: str, text: str) -> tuple[str, float]:
        """`turn_before` of a one-on-one chat's last exchange; a room has none."""
        user = channels.dm_user(conv)
        if not user or not text.strip():
            return "", 0.0
        return turn_before(self.dm_history.get(user) or [], text, self.agent_lang)

    def _recall_text(self, conv: str, text: str) -> str:
        """What a message is embedded as for recall: with the turn before when it leans on it."""
        before, weight = self._turn_before(conv, text)
        return f"{before}\n{text}" if weight >= _LEANS else text

    async def _embed(self, texts: list[str], timeout_s: float) -> list:
        url, key = embedding_endpoint(
            base_url=self.base_url, api_key=self.api_key,
            embedding_base_url=self.embedding_base_url,
            embedding_api_key=self.embedding_api_key)
        return await asyncio.wait_for(
            embeddings.fetch(transport.client(url, key), self.embedding_model, texts), timeout_s)

    def _embedding_failed(self, exc: BaseException) -> None:
        self._embedding_retry_at = time.monotonic() + embeddings.RETRY_AFTER_S
        if not self._embedding_warned:
            self._embedding_warned = True
            logger.warning(
                "[Agent] embedding endpoint failed (model=%s, %s: %s); retrieval "
                "ranks without embeddings, next try in %ds",
                self.embedding_model, type(exc).__name__, exc,
                embeddings.RETRY_AFTER_S)

    def _embedding_answered(self) -> None:
        if self._embedding_warned:
            self._embedding_warned = False
            logger.info("[Agent] embedding endpoint answers again (model=%s)",
                        self.embedding_model)

    async def _prepare_retrieval(self, focus_text: str, memory_key: str = "") -> None:
        """Embed the query before the prompt is built; what the day's block
        ranks is backfilled in the background. Never raises."""
        if not self.embedding_model or time.monotonic() < self._embedding_retry_at:
            return
        model, cache = self.embedding_model, self.embedding_cache
        try:
            query = [text for text in dict.fromkeys((focus_text, self._recall_text(memory_key, focus_text)))
                     if text.strip() and not cache.has(model, embeddings.text_key(text))]
            if query:
                vectors = await self._embed(query, embeddings.QUERY_TIMEOUT_S)
                cache.put(model, {embeddings.text_key(t): v for t, v in zip(query, vectors)},
                          persist=False)
                self._embedding_answered()
            self._start_embedding_backfill()
        except Exception as exc:
            self._embedding_failed(exc)

    def _start_embedding_backfill(self) -> None:
        """Embed what the character read that has no vector yet, for the
        day's block (today.py), in the background, one task at a time."""
        if self._embedding_backfill is not None and not self._embedding_backfill.done():
            return
        model, cache = self.embedding_model, self.embedding_cache
        wanted: dict[str, str] = {}
        from . import today
        from .library import Library
        for item in today._items(Library(today.runtime_of(self))):
            text = today.vector_text(item)
            wanted[embeddings.text_key(text)] = text
        cache.prune(model, set(wanted))
        # An empty input fails the whole batch it is in.
        missing = [(key, text) for key, text in wanted.items()
                   if not cache.has(model, key) and embeddings.clip(text)]
        if missing:
            self._embedding_backfill = self._spawn(self._embedding_backfill_run(missing))

    async def _embedding_backfill_run(self, missing: list[tuple[str, str]]) -> None:
        model, cache = self.embedding_model, self.embedding_cache
        try:
            for i in range(0, len(missing), embeddings.BATCH_SIZE):
                batch = missing[i:i + embeddings.BATCH_SIZE]
                vectors = await self._embed([text for _, text in batch],
                                            embeddings.BATCH_TIMEOUT_S)
                cache.put(model, {key: vec for (key, _), vec in zip(batch, vectors)},
                          persist=True)
            self._embedding_answered()
        except Exception as exc:
            self._embedding_failed(exc)

    # ---- memory: what a reply recalls --------------------------------------

    @staticmethod
    def _prompt_tz() -> timezone:
        """The zone of the prompt's "Current local time", so a date and a
        time word mean the same day the model is told it is."""
        from .textproc import _env_tz_offset
        return timezone(timedelta(hours=_env_tz_offset()))

    def _memory_budget(self) -> int:
        settings = getattr(self, "settings", None)
        return max(0, int(getattr(settings, "memory_prompt_chars", _MEMORY_PROMPT_CHARS)))

    def _remember_recalled(self, conv: str, placed: list[tuple[str, str, str]]) -> None:
        """What this chat's latest prompt placed, for `_mark_used` after the send."""
        if not hasattr(self, "_last_recalled"):
            self._last_recalled = {}
        self._last_recalled[conv] = list(placed)

    def _recalled_row(self, conv: str, kind: str, item_id: str):
        """The live row a placed id names, or None once it is gone."""
        return self.person_store.get(item_id)

    def _mark_used(self, conv: str, reply_text: str) -> list[dict]:
        """After a sent reply: each item its prompt placed that shares at least
        two focus tokens with the reply gets one more use. Counted in memory,
        written at most once a minute (`_flush_memory_counters`). The items it
        drew on ({id, text}), which the page lists under the reply."""
        from . import person
        placed = getattr(self, "_last_recalled", {}).pop(conv, None)
        if not placed or not str(reply_text or "").strip():
            return []
        said = _focus_tokens(str(reply_text), "zh")
        if len(said) < _USE_OVERLAP:
            return []
        now = time.time()
        dirty = self.__dict__.setdefault("_memory_counts_dirty", set())
        used = []
        for key, kind, item_id in placed:
            row = self._recalled_row(key, kind, item_id)
            if row is None:
                continue
            if len(_focus_tokens(str(row.get("text", "")), "zh") & said) >= _USE_OVERLAP:
                row["uses"] = int(_num(row.get("uses"))) + 1
                row["used"] = now
                dirty.add(kind)
                used.append({"id": item_id, "text": person.shown(row)})
        self._flush_memory_counters()
        return used

    def _flush_memory_counters(self, force: bool = False) -> None:
        """Write use counts kept in memory; at most once a minute unless forced.
        A count lost to a crash is harmless."""
        dirty = getattr(self, "_memory_counts_dirty", None)
        if not dirty:
            return
        tick = time.monotonic()
        last = getattr(self, "_memory_counts_flushed", None)
        if not force and last is not None and tick - last < _USE_FLUSH_S:
            return
        self._memory_counts_flushed = tick
        self._memory_counts_dirty = set()
        self._save_person()

    def _person_block(self, conv: str, focus_text: str) -> tuple[str, list[tuple[str, str, str]]]:
        """What a page chat's prompt knows of its one person (person.py): the
        same block in every chat of theirs, the facts ranked by `focus_text`."""
        from . import person, profile, today
        block, ids = person.recall_block(self.person_store, focus_text, time.time(), self._prompt_tz(),
                                         self.chat_lang, self._memory_budget(),
                                         manner=profile.manner_lines(today.runtime_of(self)))
        return block, [(conv, "person", i) for i in ids]

    def _past_block(self, conv: str, text: str) -> str:
        """Their earlier page chats for a reply model the app has not measured calling a tool by itself
        (abilities.py), which will not call search_past_chats: when the message asks about the past (time words
        that point back, or what was said), the best lines of those chats (pastchats.py) in the time it names.
        "" for any other message or model."""
        from . import abilities, pastchats, timewords, today
        if channels.private_chat(conv):
            return ""
        model = self.llm_dm_model
        if (abilities.record(today.runtime_of(self), self._endpoint_for(model)[0], model) or {}).get("tools") == "yes":
            return ""
        now = time.time()
        span = timewords.resolve(text, now, self._prompt_tz(), [self.chat_lang])
        past = bool(span and span[1] <= now)
        if not (past or _ASKS_SAID.search(text)):
            return ""
        at = pastchats.scope(self, conv)
        for key in at["convs"]:
            self._log_chat(key)
        found, _ = pastchats.search(at, text, text if past else "", hits=_LOG_LINES)
        return f"<past_chats>\n{found}\n</past_chats>\n\n"

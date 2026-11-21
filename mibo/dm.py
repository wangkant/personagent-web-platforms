"""The chat with the one person: their message read, a memory command run or
a turn of the model, and what the character says."""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
from datetime import datetime

from . import abilities
from . import catalog
from . import channels
from . import context
from . import db
from . import person
from . import today
from .llm import FROM_BUDGET, ProviderError, answer_text, purpose
from .loop import ANSWER_NOW, ChatLookups, stopped_reply
from .messages import turn_pictures
from .decision import PASS, SEND
from .pastchats import NAME as PAST_CHATS
from .prompt import history_messages
from .schedule_tool import run_of
from .turns import OutOfTime, Turn, current_turn
from .textproc import (
    TextProcessing,
    _detect_image_mime,
    _strip_web_desc,
    scrub_third_party_text,
)

logger = logging.getLogger("agent")

# The name the person's turns are kept under; the prompt calls them by the name they gave (ADMIN_NAME).
_THEM = "you"
# A page message said this many seconds before the checkpoint's first verbatim turn comes back with it when the
# chat is read from the page: the turn is stamped when the character hears it, after the page stamped it.
_HEARD_AFTER_S = 60.0
# The summary's own room: twice the checkpoint's size, so one a little long still finishes and is kept.
_SUMMARY_ROOM = 2
# The person's newest message past its share, cut at ingestion (context design: the person's newest message).
_MESSAGE_CUT = "\n\n[... {n} characters of this message left out: it is longer than the model can read ...]\n\n"
# After two failed summaries in a row the older turns are cut without one (context design: compaction failure).
_FAILS_BEFORE_CUT = 2
# The line a cut without a summary adds to the checkpoint: (zh, en).
_CUT_LINE = ("（{span}的对话未写入摘要，可使用 {tool} 查找。）",
             "(The conversation of {span} is not in this checkpoint; {tool} finds it.)")
# The system prompt and the tool definitions may take this share of the input limit at most; past it the model's
# window cannot hold the chat's basic setup (context design: the window-too-small sentence).
_SETUP_SHARE = 0.6

# The owner's turn on the page looks things up itself (loop.py); a
# context, so no fake `_chat_dm` needs a new argument or return value. None: no tools.
chat_lookups: contextvars.ContextVar[ChatLookups | None] = contextvars.ContextVar(
    "chat_lookups", default=None)
# What a tool is doing in the page's chat, which the page process reads (welcome.py).
CHAT_DOING_FILE = "chat-doing.json"


def _with_found(messages: list[dict], found: str) -> list[dict]:
    """`messages` with what this turn's lookups found ahead of the last turn,
    for the answer asked for without tools."""
    last = messages[-1]
    head = f"[What you looked up this turn]\n{found}\n\n"
    content = last["content"]
    if isinstance(content, list):
        content = [{**content[0], "text": head + content[0]["text"]}, *content[1:]]
    else:
        content = head + content
    return messages[:-1] + [{**last, "content": content}]


def _done_so_far(lookups: ChatLookups) -> str:
    """What a turn had done when its time ran out: the steps in its trace and each call it made."""
    steps = "; ".join(f"{step['tool']} {step['what']}".strip() for step in lookups.trace)
    calls = "; ".join(f"{entry['line']} ({entry['status']})" for entry in lookups.activity)
    return (f"(the time for this message ran out while you were working; steps taken: {steps or 'none finished'}"
            + (f"; calls made: {calls}" if calls else "") + ")")


def _at(stamp) -> float:
    """A page message's time (welcome.PageChat stamps ISO with its offset) as seconds; 0 without one."""
    return datetime.fromisoformat(stamp).timestamp() if isinstance(stamp, str) and stamp else 0.0


_OF_CHAT = "SELECT id FROM page_chats WHERE conversation = ?"


def _page_mark(runtime, conversation: str) -> dict:
    """The page's summary row of the chat `conversation`: its checkpoint or the mark of a clear; {} without one."""
    marks = db.at(runtime).read(f"SELECT body FROM page_messages WHERE kind = 'summary' AND chat IN ({_OF_CHAT})",
                                (conversation,))
    return json.loads(marks[0][0]) if marks else {}


def _foldable(rows: list[dict], start: int) -> bool:
    """Whether the turns before `start` can become the checkpoint: some beside the checkpoint, and a turn after."""
    first = 1 if rows and rows[0].get("role") == context.CHECKPOINT else 0
    return first < start < len(rows)


def _page_turns(runtime, conversation: str) -> list[dict]:
    """The page's own saved copy of the chat `conversation` (welcome.PageChat, in the runtime database) as turns,
    for a page chat that has no recent window yet: its checkpoint first (the page's summary row), then the turns
    from where the checkpoint's verbatim turns began; [] if the page keeps no such chat."""
    mark = _page_mark(runtime, conversation)
    since = float(mark.get("from") or 0.0)
    # Nothing said before the last 清空上下文 comes back, whatever the grace.
    after = max(since - _HEARD_AFTER_S, float(mark.get("floor") or 0.0))
    turns: list[dict] = []
    store = db.at(runtime)
    for (body,) in store.read(f"SELECT body FROM page_messages WHERE kind = 'message' AND chat IN ({_OF_CHAT}) "
                              "ORDER BY seq", (conversation,)):
        message = json.loads(body)
        # A message that was never answered is shown to the person, not a turn the character had.
        if message.get("failed"):
            continue
        at = _at(message.get("at"))
        # What the checkpoint holds; a turn may come back with it, never go missing.
        if at < after:
            continue
        role, text = message.get("role"), message.get("text")
        if not isinstance(text, str):
            continue
        # What the person attached, as their turn named it.
        attached = [f"[file: {a.get('file')}]" if a.get("kind") == "file" else "[image]"
                    for a in message.get("attachments") or []]
        text = "\n".join(t for t in (text, *attached) if t.strip())
        if role not in ("user", "assistant") or not text.strip():
            continue
        # One turn per side in a row, as the history keeps them.
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n" + text
        else:
            turns.append({"role": role, "content": text, "t": at})
    # The words still waiting for this answer are the turn being answered.
    while turns and turns[-1]["role"] == "user":
        turns.pop()
    head = [{"role": context.CHECKPOINT, "content": mark["text"], "t": since}] if mark.get("text") else []
    return head + turns


def chat_key(conversation: str) -> tuple[str, str]:
    """The names a chat of the page goes by inside: its reader and its key (channels.py)."""
    user_id = f"{channels.PAGE_PLATFORM}:{conversation}"
    return user_id, channels.dm_routing_key(user_id)


class DirectMessages:
    async def answer(self, conversation: str, parts: list[dict], *, choices=(),
                     run: dict | None = None) -> list[dict]:
        """One message of the person's in the chat `conversation`, answered: a
        memory command carried out, or a turn of the model. `parts` as
        `_read_message` takes them; `choices` what they chose for this message
        (turns.TURN_CHOICES); `run` a scheduled task's run in place of words of
        theirs. What the character says (turns.Turn.said): a line said because
        the model did not answer carries "reason": "excuse"."""
        user_id, pkey = chat_key(conversation)
        turn = Turn(choices, run)
        token = current_turn.set(turn)
        try:
            # One turn of a chat at a time; the follow-up of a plan waits its turn too.
            async with self.turn_locks[pkey]:
                await self._answer(user_id, pkey, parts)
        finally:
            current_turn.reset(token)
        texts = [item for item in turn.said if item.get("type") == "text"]
        if turn.thinking and texts:
            texts[-1]["thinking"] = turn.thinking
        if run and run.get("spent"):
            # A run's model use, for its record on the page (panels/schedule.py).
            turn.said.append({"type": "spent", "spent": run["spent"]})
        return turn.said

    async def _answer(self, user_id: str, pkey: str, parts: list[dict]) -> None:
        # A reply model that is shown pictures gets theirs itself; a vision model reads them
        # out whole for one that is not.
        pictures = [] if self._shows_pictures() else None
        shown = turn_pictures.set(pictures)
        try:
            text = await self._read_message(parts)
        finally:
            turn_pictures.reset(shown)
        if not text:
            return
        # Without what was fetched or attached: a page or a file can run no memory command.
        typed = _strip_web_desc(text)
        self.last_dm_activity_at[user_id] = time.time()
        if self._parse_memory_command(typed) is not None:
            # No model call, no turn of the history.
            said = self._say(self._handle_memory_command(pkey, typed) or "", by_line=True)
            # What a forget took out of every chat: the page drops it from its own copies (welcome.py).
            scrub = vars(self).get("_page_scrub", {}).pop(pkey, None)
            if scrub and said:
                said[-1]["scrub"] = scrub
            return
        self._wake(pkey)
        self.__dict__.setdefault("_dm_heard_at", {})[user_id] = time.time()
        self._recent_dirty.add(pkey)
        # Kept until a reply to them is committed: a turn that failed leaves its words for the next.
        self._dm_unanswered.setdefault(user_id, []).append(text)
        if pictures:
            self.__dict__.setdefault("_dm_pictures", {}).setdefault(user_id, []).extend(pictures)
        await self._dm_turn(user_id, pkey, text)

    @staticmethod
    def _say(text: str, *, by_line: bool = False) -> list[dict]:
        """The character's words into the turn being answered: one message, or
        with `by_line` one per line (a memory command's answer). The messages said."""
        text = TextProcessing._sanitize_reply(text)
        lines = [line.strip() for line in text.split("\n") if line.strip()] if by_line else [text]
        said = [{"type": "text", "text": line} for line in lines if line]
        current_turn.get().said.extend(said)
        return said

    def _commit_dm_history(self, user_id: str, pkey: str, history: list[dict]) -> None:
        """Keep `history` as the reader's turns, and give the chat's record what it lacks of them now: a compaction
        then never drops a turn the record does not have (the record is what the fold and past-chat search read)."""
        self.dm_history[user_id] = history
        vars(self).get("_dm_heard_at", {}).pop(user_id, None)
        self._log_chat(pkey)
        self._recent_dirty.add(pkey)
        self._maybe_flush_recent()

    def _rehydrate_page(self, user_id: str, pkey: str) -> None:
        """A page chat with no turns held reads the page's saved copy once: its checkpoint and the newest whole
        turns after it within the history's share. The chat is woken first, so new turns are numbered after the
        chat's record and the recent window wins over the page."""
        self._wake(pkey)
        if user_id in self.dm_history:
            return
        done = self.__dict__.setdefault("_page_rehydrated", set())
        if pkey in done:
            return
        done.add(pkey)
        turns = _page_turns(self.memory_file.parent, user_id.split(":", 1)[-1])
        head = turns[:1] if turns and turns[0]["role"] == context.CHECKPOINT else []
        rest = turns[len(head):]
        model = self.llm_dm_model
        url = self._endpoint_for(model)[0]
        rest = rest[context.tail_from(rest, self._budget(url, model).history / context.ratio(self, url, model)):]
        for turn in head:
            turn["seq"] = 0
        for turn in rest:
            turn["seq"] = self._next_seq(pkey)
        if head or rest:
            self.dm_history[user_id] = head + rest
            self._recent_dirty.add(pkey)

    # ---- the history kept within its share (context design, stage S4) ------------------------------

    def _history_plan(self, sent: tuple) -> tuple[context.Budget, float]:
        """(the reply model's budget, its tokens per estimated one) with `sent`, the (system prompt, tools) of the
        chat's last request, as the fixed part."""
        model = self.llm_dm_model
        url = self._endpoint_for(model)[0]
        ratio = context.ratio(self, url, model)
        system, tools = sent
        fixed = round(ratio * context.estimate({"messages": [{"role": "system", "content": system}],
                                                "tools": tools}))
        with purpose("reply"):
            return self._budget(url, model, self._reply_effort(url, model), fixed), ratio

    async def _compact_after(self, user_id: str, pkey: str, sent: tuple) -> None:
        """After a committed reply: once the checkpoint and the verbatim turns pass the history's share, the older
        turns become one checkpoint, the newest whole turns up to half the share kept as they are. Under the chat's
        lock, so a message sent meanwhile waits for it rather than racing it."""
        async with self.locks[pkey]:
            rows = self.dm_history.get(user_id)
            if not rows:
                return
            plan, ratio = self._history_plan(sent)
            held = ratio * context.rows_tokens(rows)
            if held <= plan.history:
                return
            # The next request with this history and a full turn context would pass the ceiling T.
            over = plan.limit - plan.available + held + plan.turn > plan.ceiling
            done = await self._compacted(user_id, rows, context.tail_from(rows, plan.tail / ratio), sent, plan,
                                         now=over)
            if done is not None:
                self.dm_history[user_id] = done[0]
                self._recent_dirty.add(pkey)
                self._maybe_flush_recent()

    async def _fit_history(self, system: str, tools: list | None, convo: list[dict], fit: context.Fit,
                           lookups: ChatLookups) -> bool:
        """The fit ladder's third step, once a turn: a request still past the input limit with its older results
        cut has the history before this message compacted now, its newest whole turn kept, and is fitted again.
        Whether it fits now."""
        rows = lookups.history
        if not rows or lookups.squeezed:
            return False
        lookups.squeezed = True
        before = rows[:-1]
        done = await self._compacted(chat_key(lookups.conversation)[0], before,
                                     context.tail_from(before, fit.plan.tail / fit.ratio), (system, tools), fit.plan,
                                     now=True)
        if done is None:
            return False
        kept = done[0]
        convo[:len(history_messages(before))] = history_messages(kept)
        rows[:-1] = kept
        return fit(convo)

    async def _compacted(self, user_id: str, rows: list[dict], start: int, sent: tuple, plan: context.Budget, *,
                         now: bool) -> tuple[list[dict], bool] | None:
        """(`rows` with those before `start` made one checkpoint, the one before merged into it, and kept as the
        page's summary row; whether it was a cut without a summary). None when there is nothing to fold, when a
        forget, a clear or another compaction changed the chat while the summary was written (what was read then
        is not written back), or when the summary failed and the turns can wait. A failed summary, the second in a
        row or with `now`, leaves a cut without one: the turns go, and the old checkpoint says which days went and
        that past-chat search finds them (the chat's record keeps them)."""
        if not _foldable(rows, start):
            return None
        span = rows[:start]
        pkey = channels.dm_routing_key(user_id)
        held, forgets = self.dm_history.get(user_id), self._memory_gen(pkey)
        summary = await self._summary(span, sent, plan)
        if self.dm_history.get(user_id) is not held or self._memory_gen(pkey) != forgets:
            logger.info("[Agent] the chat changed while its checkpoint was written; it was not kept")
            return None
        fails = self.__dict__.setdefault("_compact_fails", {})
        cut = summary is None
        if summary is None:
            fails[user_id] = fails.get(user_id, 0) + 1
            if fails[user_id] < _FAILS_BEFORE_CUT and not now:
                return None
            summary = self._cut_without_summary(span)
        fails.pop(user_id, None)
        head = {"role": context.CHECKPOINT, "content": summary, "t": float(rows[start].get("t") or time.time()),
                "seq": 0}
        self._keep_checkpoint(user_id, head)
        logger.info("[Agent] %d older turns of the chat condensed into its checkpoint",
                    sum(r.get("role") != context.CHECKPOINT for r in span))
        return [head, *rows[start:]], cut

    async def _summary(self, span: list[dict], sent: tuple, plan: context.Budget) -> str | None:
        """The checkpoint of `span`, written by the reply model on the same prefix as the chat's last request (its
        system prompt and tools as sent, then the turns), thinking off, with the request appended (dsh
        summarizer.ts); None when the call fails, the answer is cut off, calls a tool or does not shrink the span."""
        system, tools = sent
        steps = [r["steps"] for r in span if r.get("steps")]
        messages = ([{"role": "system", "content": system}] if system else []) + history_messages(span) + [
            {"role": "user", "content": context.instruction(plan.checkpoint, self.chat_lang, steps)}]

        async def ask(model: str) -> dict:
            url, key = self._endpoint_for(model)
            payload = {"model": model, "max_tokens": _SUMMARY_ROOM * plan.checkpoint, "messages": messages,
                       **({"tools": tools} if tools else {})}
            self._thinking_off(payload, url)
            body = await self._post_chat(url, key, payload, timeout=self.llm_timeout_s, retries=self._retries(model))
            if body.get("error") or not body.get("choices"):
                raise ProviderError(body)
            return body

        try:
            with purpose("reply"):
                body, _ = await self._recovered(ask, self.llm_dm_model)
        except Exception as e:
            logger.warning("[Agent] the chat's checkpoint was not written: %s", type(e).__name__)
            return None
        choice = body["choices"][0]
        message = choice.get("message") or {}
        text = self._checked_checkpoint(answer_text(message))
        if choice.get("finish_reason") == "length" or message.get("tool_calls") or not text \
                or context.text_tokens(text) >= context.rows_tokens(span):
            logger.warning("[Agent] the chat's checkpoint was not kept (%s)", choice.get("finish_reason"))
            return None
        return text

    def _checked_checkpoint(self, text: str) -> str:
        """The summary as it may be kept: nothing invisible, a line that reads as an instruction (the memory gate's
        shapes, its bullet aside) dropped, and an ID or card number, password or code masked."""
        lines = [person.mask(line, {})[0] for line in scrub_third_party_text(text).splitlines()
                 if not self._reads_as_instruction(line.strip().lstrip("-*•·").strip())]
        return "\n".join(lines).strip()

    def _cut_without_summary(self, span: list[dict]) -> str:
        """The checkpoint after a cut without a summary: the old one, and a line naming the days of the turns cut."""
        old = span[0]["content"] if span[0].get("role") == context.CHECKPOINT else ""
        days = [datetime.fromtimestamp(float(r["t"]), self._prompt_tz()).strftime("%Y-%m-%d")
                for r in span if r.get("role") != context.CHECKPOINT and r.get("t")]
        zh = self.chat_lang in ("zh", "zh-TW")
        span_words = (days[0] if not days or days[0] == days[-1] else
                      f"{days[0]}{'至' if zh else ' to '}{days[-1]}") if days else ("此前" if zh else "before")
        line = _CUT_LINE[0 if zh else 1].format(span=span_words, tool=PAST_CHATS)
        return f"{old}\n{line}".strip()

    def _keep_checkpoint(self, user_id: str, head: dict) -> None:
        """The checkpoint as the page's summary row of the chat (page_messages kind 'summary', seq 0, which the
        page's own messages never take), replaced on each compaction with the floor of the last clear carried on;
        a 无痕对话 has no row on the page."""
        conversation = user_id.split(":", 1)[-1]
        floor = _page_mark(self.memory_file.parent, conversation).get("floor")
        body = json.dumps({"text": head["content"], "from": head["t"], "at": time.time(),
                           **({"floor": floor} if floor else {})}, ensure_ascii=False)
        with db.at(self.memory_file.parent).write() as conn:
            conn.execute("INSERT OR REPLACE INTO page_messages(chat, seq, kind, body) "
                         "SELECT id, 0, 'summary', ? FROM page_chats WHERE conversation = ?", (body, conversation))

    async def _dm_turn(self, user_id: str, pkey: str, text: str) -> None:
        """One turn of the model: the call under the turn's time budget, the
        reply said, the history committed."""
        # The person's words this turn; kept for their next turn unless committed.
        said: list[str] = []
        committed = False
        conversation = user_id.split(":", 1)[-1]
        lookups = ChatLookups(self.memory_file.parent / "desktop" / CHAT_DOING_FILE, conversation)
        context = chat_lookups.set(lookups)
        # Where the stop button finds this turn (`stop_turn`).
        vars(self).setdefault("_page_turns", {})[conversation] = lookups
        try:
            async with self.locks[pkey]:
                self._rehydrate_page(user_id, pkey)
                history = list(self.dm_history.get(user_id, []))
                # Everything taken in since the last committed reply is one
                # user turn, so two user turns never sit in a row.
                said = list(self._dm_unanswered.get(user_id) or [text])
                # Shown with this turn only: later turns read their place as [image].
                pictures = vars(self).get("_dm_pictures", {}).pop(user_id, [])
                heard = vars(self).get("_dm_heard_at", {}).get(user_id)
                history.append({"role": "user",
                                "content": "\n".join(said),
                                "name": _THEM, "t": heard or time.time(),
                                "seq": self._next_seq(pkey)})
            # What a turn's fit compacts now is kept with the turn (_fit_history).
            lookups.history = history

            cue = {"pictures": pictures} if pictures else {}
            # Only this await is stopped: the commit below runs whole or not at all.
            lookups.work = asyncio.current_task()
            lookups.doing("recall")
            try:
                try:
                    try:
                        reply = await self._within_turn_budget(self._chat_dm(history, pkey=pkey, **cue))
                    except OutOfTime:
                        if not (lookups.trace or lookups.activity):
                            raise
                        # Work was under way: one more call says what was done, in place of a failure line.
                        reply = await asyncio.wait_for(self._chat_dm(
                            history, pkey=pkey, out_of_time=True, **cue), self.llm_timeout_s)
                    if not reply:
                        # No words even when asked without tools: the excuse below, and their words wait.
                        raise ValueError("the answer had no words")
                finally:
                    stoppable = lookups.work
                    lookups.work = None
            except asyncio.CancelledError:
                if not lookups.stopped:
                    raise
                # Python 3.11+ counts cancels; this one is handled here.
                getattr(stoppable, "uncancel", lambda: None)()
                # Stopped by the person: no excuse; what was said and done so far is the reply.
                logger.info("[Agent] turn stopped by the person (chat=%s)", conversation)
                reply = stopped_reply(lookups, getattr(self, "chat_lang", "") or "zh")
                if not reply:
                    said = []
                    self._dm_unanswered.pop(user_id, None)
                    return
            except Exception as e:
                logger.warning("[Agent] model call failed: %s", e)
                # Told to the waiting person in place of an answer, marked so the page shows its own
                # error; not committed to the history.
                for item in self._say(self._model_failure_excuse(text)):
                    item["reason"] = "excuse"
                return

            final = self._final_reply(reply, log_ctx=f"chat={conversation}")
            if final.verdict == PASS:
                logger.info("[Agent] PASS (chat=%s)", conversation)
            if final.verdict != SEND:
                return
            reply = final.text
            texts = self._say(reply)
            if lookups.trace and texts:
                # Shown under the reply and kept with it (welcome.py); the trace is what it looked up.
                texts[-1]["sources"], texts[-1]["trace"] = lookups.sources, lookups.trace
            if lookups.activity and texts:
                # One entry for each call the turn made (loop.py), as the waiting row showed them.
                texts[-1]["activity"] = lookups.activity
            if texts and lookups.feedback:
                # What the person said of finds; the page keeps it in the feed (welcome.py).
                texts[-1]["feedback"] = lookups.feedback
            if texts and lookups.bring:
                # The find it brought, as a card under the reply, the way the share step posts one.
                from . import life
                from .library import Library
                item = Library(today.runtime_of(self)).get(lookups.bring)
                card = life.said(item).get("card") if item is not None else None
                if card:
                    texts[-1]["card"] = card

            asked = "\n".join(said)
            unsaid = bool(person.unsaid(asked))
            async with self.locks[pkey]:
                history.append({"role": "assistant", "content": reply,
                                "t": time.time(), "seq": self._next_seq(pkey),
                                # What it did on the way, short: the next turns read it (prompt._recent_steps).
                                **({"steps": [f"{s['tool']} {s.get('what') or ''}".strip()[:160]
                                              for s in lookups.trace[-12:]]} if lookups.trace else {})})
                self._commit_dm_history(user_id, pkey, history)
                self._dm_unanswered.pop(user_id, None)
                committed = True
                private = channels.private_chat(pkey)
                if not run_of() and not private:
                    # The one person's store, which every chat reads (person.py); a scheduled run's
                    # request is not something they said now, and a 无痕对话 keeps nothing.
                    self._person_turn(pkey, asked, reply)
                if unsaid:
                    scrub = self._drop_unsaid(pkey, asked, reply)
                    if texts:
                        texts[-1]["scrub"] = scrub
                used = self._mark_used(pkey, reply)
                if used and texts:
                    # The memories the reply drew on, listed under it with edit and forget (activity.js).
                    texts[-1]["memory"] = {"used": used}
                self._maybe_consolidate(pkey, "trim")
            if lookups.sent is not None:
                self._note_request(user_id, pkey, lookups.sent)
                self._spawn(self._compact_after(user_id, pkey, lookups.sent))
            # Never the words: what they say may be forgotten later.
            logger.info("[Agent] reply (chat=%s, %d chars)", conversation, len(reply))
        finally:
            # No await: this also runs when the turn is cancelled. A scheduled run that said nothing
            # leaves nothing for their next turn.
            if said and not committed and not run_of():
                self._dm_unanswered[user_id] = said
                self._recent_dirty.add(pkey)
            chat_lookups.reset(context)
            lookups.done()
            if vars(self).get("_page_turns", {}).get(conversation) is lookups:
                del self._page_turns[conversation]

    def stop_turn(self, conversation: str) -> bool:
        """Cancel the reply under way in the chat `conversation`: its model
        and tool calls stop, and what it said and did so far is the reply."""
        lookups = vars(self).get("_page_turns", {}).get(conversation)
        if lookups is None or lookups.work is None:
            return False
        lookups.stopped = True
        lookups.work.cancel()
        return True

    async def _chat_dm(self, history: list[dict], pkey: str = "", follow_up: str = "",
                       pictures: list[bytes] = (), out_of_time: bool = False) -> str:
        """One model turn of the chat `pkey` (LLM_DM_MODEL): the reply, plain text. `follow_up`: nobody
        wrote; the note says which plan of theirs to ask after (followup.py), and the turn uses no tools.
        `pictures` go with the last turn for the model to see itself. `out_of_time`: the turn's time ran
        out with work under way, so this call only says what was done, without tools."""
        await self._prepare_retrieval(self._last_user_text(history), pkey)
        system = self._dm_system()
        plan, ratio = self._turn_plan(system, tools=not follow_up)
        if plan.limit - plan.available > _SETUP_SHARE * plan.limit:
            # Not even the system prompt and the tools fit: no request is sent, and the page names why.
            abilities.note_problem(self, "window_small", self._endpoint_for(self.llm_dm_model)[0])
            raise ValueError("the model's window cannot hold the chat's system prompt and tools")
        if not follow_up:
            self._cut_message(history, plan, ratio)
        prompt = self._build_dm_prompt(history, room=plan.turn / ratio, pkey=pkey, follow_up=follow_up,
                                       pictures=len(pictures))
        # The turn context's size, for the gauge's parts (`chat_context`).
        self.__dict__.setdefault("_turn_sizes", {})[pkey] = context.text_tokens(prompt.turn_context)
        # A turn of the person's: the reply model looks things up itself (loop.py).
        lookups = None if follow_up else chat_lookups.get()
        messages = prompt.messages_with_context(prompt.messages)
        if pictures:
            # Image parts after the words; an endpoint that refuses them gets the words alone (endpoints.py).
            urls = [await self._vision_data_url(data, _detect_image_mime(data)) for data in pictures]
            last = messages[-1]
            messages = messages[:-1] + [{**last, "content": [
                {"type": "text", "text": last["content"]},
                *({"type": "image_url", "image_url": {"url": url}} for url in urls if url)]}]
        if lookups is None or out_of_time:
            if out_of_time:
                messages = _with_found(messages, _done_so_far(lookups) + "\n" + ANSWER_NOW)
                lookups.sent = (system, None)
            with purpose("reply"):
                return await self._call_llm(system, messages, self.llm_dm_model, max_tokens=FROM_BUDGET)
        return await self._reply(system, messages, self.llm_dm_model, lookups, said=self._last_user_text(history))

    def _turn_plan(self, system: str, tools: bool) -> tuple[context.Budget, float]:
        """`_history_plan` of a turn with the chat's system prompt `system`, and with the tools the model's window
        is offered when `tools`."""
        return self._history_plan((system, self._offered_tools() if tools else None))

    def _offered_tools(self) -> list:
        """The tool definitions the reply model's window is offered up front (catalog.offered)."""
        model = self.llm_dm_model
        return catalog.offered(self._budget(self._endpoint_for(model)[0], model)).tools

    def _cut_message(self, history: list[dict], plan: context.Budget, ratio: float) -> None:
        """The person's newest message, when it alone passes its share of the request (half of what the system
        prompt and tools leave), cut at ingestion to its head and tail, and the page told (it marks their message,
        welcome.py)."""
        last = history[-1] if history else {}
        words = str(last.get("content") or "")
        if last.get("role") != "user" or context.text_tokens(words) <= plan.message / ratio:
            return
        last["content"] = context.cut(words, plan.message / ratio, _MESSAGE_CUT)
        current_turn.get().said.append({"type": "cut"})

    # ---- what the person sees of the context and can do with it (context design, stage S5) ----------

    def _note_request(self, user_id: str, pkey: str, sent: tuple) -> None:
        """The chat's last reply request, for its gauge: the prompt tokens the endpoint reported for it, the input
        limit, and the estimated size of each part (system prompt, tools, turn context, checkpoint, the turns and
        this turn's tool rounds), with the turns it held."""
        model = self.llm_dm_model
        url = self._endpoint_for(model)[0]
        anchor = (context._learned(self).get((url, model)) or {}).get("anchor")
        if anchor is None:
            return
        system, tools = sent
        rows = self.dm_history.get(user_id) or []
        held = rows[:-1] if rows and rows[-1].get("role") == "assistant" else rows
        summary = context.rows_tokens(held[:1]) if held and held[0].get("role") == context.CHECKPOINT else 0.0
        parts = {"system": context.estimate({"messages": [{"role": "system", "content": system}]}),
                 "tools": context.estimate({"tools": tools}) if tools else 0.0,
                 "memory": vars(self).get("_turn_sizes", {}).get(pkey, 0.0), "summary": summary}
        parts["recent"] = max(0.0, anchor[1] - sum(parts.values()))
        with purpose("reply"):
            limit = self._budget(url, model, self._reply_effort(url, model)).limit
        self.__dict__.setdefault("_chat_requests", {})[pkey] = {
            "prompt": anchor[0], "parts": parts, "limit": limit, "rows": rows,
            "held": context.rows_tokens(held), "summary": summary, "ratio": context.ratio(self, url, model),
            "sent": sent}

    def chat_context(self, conversation: str) -> dict:
        """The chat's gauge and what can be done with its context: the last reply request's prompt tokens over the
        input limit (once a compaction or a clear changed the turns since, that figure moved by what they changed,
        and marked estimated) with its parts in tokens; whether there are older turns to condense, and any turns
        held at all. {"known": False, ...} before the chat's first reply request in this run."""
        user_id, pkey = chat_key(conversation)
        self._rehydrate_page(user_id, pkey)
        rows = self.dm_history.get(user_id) or []
        first = 1 if rows and rows[0].get("role") == context.CHECKPOINT else 0
        out = {"known": False, "compactable": _foldable(rows, context.tail_from(rows, 0)), "held": bool(rows)}
        seen = vars(self).get("_chat_requests", {}).get(pkey)
        if not seen:
            return out
        parts, ratio, prompt = dict(seen["parts"]), seen["ratio"], seen["prompt"]
        estimated = rows is not seen["rows"]
        if estimated:
            # The turns changed under the gauge: the checkpoint and the turns as they are now.
            summary = context.rows_tokens(rows[:1]) if first else 0.0
            moved = context.rows_tokens(rows) - seen["held"]
            parts["recent"] = max(0.0, parts["recent"] + moved - (summary - seen["summary"]))
            parts["summary"] = summary
            prompt = max(0, round(prompt + ratio * moved))
        scale = prompt / max(1.0, sum(parts.values()))
        return {**out, "known": True, "tokens": prompt, "limit": seen["limit"], "estimated": estimated,
                "percent": min(100, round(100 * prompt / seen["limit"])),
                "parts": {name: round(scale * value) for name, value in parts.items()}}

    async def compact_now(self, conversation: str) -> dict:
        """立即归纳: the chat's turns before its newest exchange made one checkpoint now, on the prefix of its last
        request (dsh's manual compaction, its retained tail none but the newest turn). {"compacted": bool};
        {"failed": True} when nothing was written, and {"cut": True} when the turns went without a summary (after
        a failed one, `_compacted`)."""
        user_id, pkey = chat_key(conversation)
        async with self.turn_locks[pkey], self.locks[pkey]:
            self._rehydrate_page(user_id, pkey)
            rows = self.dm_history.get(user_id) or []
            start = context.tail_from(rows, 0)
            if not _foldable(rows, start):
                return {"compacted": False}
            seen = vars(self).get("_chat_requests", {}).get(pkey)
            sent = seen["sent"] if seen else (self._dm_system(), self._offered_tools())
            done = await self._compacted(user_id, rows, start, sent, self._history_plan(sent)[0], now=False)
            if done is None:
                return {"compacted": False, "failed": True}
            self.dm_history[user_id] = done[0]
            self._recent_dirty.add(pkey)
            self._maybe_flush_recent()
            return {"compacted": True, **({"cut": True} if done[1] else {})}

    async def clear_context(self, conversation: str) -> dict:
        """清空上下文: the model no longer reads the chat's turns or checkpoint; the page's copy, the chat's record
        and the memory keep everything. The page's summary row becomes the clear's mark, so the turns before it
        never come back on a restart, nor after a later compaction, which carries its floor on. The mark ({"text",
        "from", "at", "cleared", "floor"})."""
        user_id, pkey = chat_key(conversation)
        async with self.turn_locks[pkey], self.locks[pkey]:
            now = time.time()
            self.dm_history[user_id] = []
            vars(self).get("_compact_fails", {}).pop(user_id, None)
            mark = {"text": "", "from": now, "at": now, "cleared": True, "floor": now}
            with db.at(self.memory_file.parent).write() as conn:
                conn.execute("INSERT OR REPLACE INTO page_messages(chat, seq, kind, body) "
                             "SELECT id, 0, 'summary', ? FROM page_chats WHERE conversation = ?",
                             (json.dumps(mark), conversation))
            self._recent_dirty.add(pkey)
            self._maybe_flush_recent()
            logger.info("[Agent] the chat's context was cleared by the person")
            return mark

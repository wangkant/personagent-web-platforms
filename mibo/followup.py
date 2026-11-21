"""Asking after a plan: the one thing the character says in a chat unasked.
The person told it of something with a day ("面试在周四"); once the day has
come it asks, once, how it went."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Optional

from .decision import SEND
from .dm import chat_key
from .textproc import TextProcessing

logger = logging.getLogger("agent")

#: A follow-up waits while they talked this recently, so it never cuts in.
FOLLOW_UP_QUIET_S = 20 * 60.0
#: How far back a plan's day may be for its follow-up.
FOLLOW_UP_DAYS = 3
#: A plan of today's is asked after only from this hour, once it is likely done.
FOLLOW_UP_EVENING = 18


class FollowUp:
    def _follow_up_plan(self, today: date, evening: bool = True) -> Optional[dict]:
        """The plan to ask after: theirs, not asked about, its day (or the end
        of its span) 0 to FOLLOW_UP_DAYS days ago, today's only in the
        `evening`; the latest first."""
        due = []
        for plan in self.person_store.plans("open"):
            try:
                end = date.fromisoformat(plan.get("until") or plan["due"])
            except (KeyError, ValueError):
                continue
            if plan.get("by") != "me" and (0 if evening else 1) <= (today - end).days <= FOLLOW_UP_DAYS:
                due.append((end, plan))
        return max(due, key=lambda d: d[0])[1] if due else None

    async def follow_up(self, now: float, chats: list[str]) -> Optional[tuple[str, str]]:
        """Ask how a plan of the person's went: at most once a day, in the
        chat of `chats` they used last, never in sleep hours or while they are
        talking. (the chat, the line) once it is in that chat's history, for
        the page to show; None when nothing is asked."""
        if not self.enabled or not chats or TextProcessing._is_sleep_hour():
            return None
        store = self.person_store
        # Memory paused in Settings › 记忆: no plan is brought up.
        if not store.on:
            return None
        local = datetime.fromtimestamp(now, self._prompt_tz())
        today = local.date()
        if store.data["followed_day"] == today.isoformat():
            return None
        plan = self._follow_up_plan(today, local.hour >= FOLLOW_UP_EVENING)
        if plan is None:
            return None
        conversation = max(chats, key=lambda c: self.last_dm_activity_at.get(chat_key(c)[0], 0.0))
        user_id, pkey = chat_key(conversation)
        if (self.turn_locks[pkey].locked()
                or now - self.last_dm_activity_at.get(user_id, 0.0) < FOLLOW_UP_QUIET_S):
            return None
        store.data["followed_day"] = today.isoformat()
        self._save_person()
        # A turn like any other: what they write meanwhile waits behind it.
        async with self.turn_locks[pkey]:
            self._wake(pkey)
            history = list(self.dm_history.get(user_id, []))[-10:]
            said = f" (they said \"{plan['said']}\")" if plan.get("said") else ""
            note = f"They had planned this for {plan['due']}{said}: {plan.get('text', '')}"
            try:
                reply = await self._within_turn_budget(self._chat_dm(history, pkey=pkey, follow_up=note))
            except Exception as e:
                logger.warning("[Agent] follow-up of a plan failed: %s", e)
                return None
            final = self._final_reply(reply, log_ctx="follow-up")
            if final.verdict != SEND:
                return None
            self._commit_dm_history(user_id, pkey, [
                *self.dm_history.get(user_id, []),
                {"role": "assistant", "content": final.text,
                 "t": time.time(), "seq": self._next_seq(pkey)}])
            self._mark_used(pkey, final.text)
            store.mark_asked(plan, now)
            self._save_person()
        logger.info("[Agent] asked after a plan (%d chars)", len(final.text))
        return conversation, final.text

"""The character, as the app's page reaches it: the few calls between the two.

The character (agent.Agent) lives in the page's own process. The page builds
one `Character` for its home, starts it with the page and stops it with it;
everything it asks of the character goes through the methods here, and the one
thing the character says unasked (the follow-up of a plan) comes back through
`said`. A chat is named by the page's own id for it (PageChat.conversation).
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Callable, Optional

from . import life, paths, person, quiet, settingsfile
from .dm import chat_key
from .library import KEPT, Library
from .memstore import MemoryFileDamaged

logger = logging.getLogger("agent")

# Seconds between looks for a plan to ask after.
FOLLOW_UP_EVERY_S = 600.0
# What the memory view may do: to one thing the character knows of the person (forget_chats also takes it out of
# the chats), to all of it, add one, set the two switches, and read another assistant's list pasted and keep from it.
PERSON_ACTIONS = ("forget", "forget_chats", "edit", "restore", "forget_all", "add", "settings",
                  "import_preview", "import")


class NotReady(RuntimeError):
    """The character could not be built for this home; `str()` says why."""


class Character:
    def __init__(self, home: Path, runtime: Callable[[], Path],
                 said: Optional[Callable[[str, list], None]] = None,
                 chats: Optional[Callable[[], list]] = None):
        self.home = Path(home).resolve()
        #: runtime(): where the home keeps its state (the page reads it from the home's settings).
        self._runtime = runtime
        #: said(conversation, items): the character spoke in a chat on its own; the page shows it.
        self.said = said
        #: chats(): the chats the page lists now, by conversation.
        self.chats = chats
        self.agent = None
        self.problem = ""
        self._stale = False
        # The follow-up loop of each event loop the page is served on (one, but for a page opened twice).
        self._tasks: dict = {}

    # ---- its life with the page -------------------------------------------

    async def start(self) -> None:
        """Build the character and start what it does in the background.
        A home it cannot be built for leaves `problem` set, and a reply says so."""
        self._tasks[asyncio.get_running_loop()] = asyncio.create_task(self._follow_up_loop())
        if self.agent is None:
            self._build()

    async def close(self) -> None:
        task = self._tasks.pop(asyncio.get_running_loop())
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if not self._tasks and self.agent is not None:
            await self.agent.aclose()
            self.agent = None

    def reload(self) -> None:
        """The settings or the persona were saved: the character is built again
        from them before its next turn. Safe from any thread."""
        self._stale = True

    def _build(self) -> None:
        from .agent import Agent
        from .roam import Roam
        from .settings import AgentSettings

        self._stale = False
        self.problem = ""
        # The home's settings, as saved by the page, under what the environment the app started with says.
        settingsfile.load(self.home)
        paths.use_home(self.home)
        try:
            agent = Agent(AgentSettings.from_env())
        except MemoryFileDamaged as error:
            # One sentence naming the file and its daily copy.
            self.problem = str(error)
            logger.error("%s", self.problem)
            return
        agent._spawn(agent.probe_models())
        agent.roam = Roam(agent, home=self.home, runtime=paths.runtime_dir())
        agent._spawn(agent.roam.loop())     # stays home without ROAM_ENABLED, a browser or a model
        agent._spawn(agent.loop_memory())   # folds self-guard on MEMORY_CALLS_PER_DAY
        # What a fold keeps is shown in the chat it came from, as one quiet 已记住 line (activity.js).
        agent.saved_told = lambda conversation, items: self.said and self.said(
            conversation, [{"type": "memory", "saved": items}])
        self.agent = agent

    async def _ready(self):
        """The character, built again first when its settings changed and no turn is under way."""
        if self._stale and not (self.agent is not None and any(
                lock.locked() for lock in self.agent.turn_locks.values())):
            if self.agent is not None:
                await self.agent.aclose()
                self.agent = None
            self._build()
        if self.agent is None:
            raise NotReady(self.problem or "the character is not started")
        return self.agent

    async def _follow_up_loop(self) -> None:
        while True:
            await asyncio.sleep(FOLLOW_UP_EVERY_S)
            await quiet.wait()
            try:
                agent = await self._ready()
                asked = await agent.follow_up(time.time(), list(self.chats()) if self.chats else [])
                if asked and self.said:
                    self.said(asked[0], [{"type": "text", "text": asked[1]}])
            except NotReady:
                continue
            except Exception as error:
                logger.warning("[Agent] follow-up loop: %s: %s", type(error).__name__, error)

    # ---- the chat -----------------------------------------------------------

    async def reply(self, conversation: str, parts: list[dict], *, choices=(),
                    run: Optional[dict] = None) -> list[dict]:
        """One message of the person's in a chat, answered (dm.DirectMessages.answer)."""
        agent = await self._ready()
        return await agent.answer(conversation, parts, choices=choices, run=run)

    def stop(self, conversation: str) -> bool:
        """The stop button: the reply under way in a chat is cancelled, and nothing of it is said or kept."""
        return self.agent is not None and self.agent.stop_turn(conversation)

    def context(self, conversation: str) -> dict:
        """The chat's context gauge and what can be done with it (dm.DirectMessages.chat_context)."""
        if self.agent is None:
            return {"known": False, "compactable": False, "held": False}
        return self.agent.chat_context(conversation)

    async def compact(self, conversation: str) -> dict:
        """立即归纳 in a chat (dm.DirectMessages.compact_now)."""
        agent = await self._ready()
        return await agent.compact_now(conversation)

    async def clear_context(self, conversation: str) -> dict:
        """清空上下文 in a chat (dm.DirectMessages.clear_context): the mark it leaves."""
        agent = await self._ready()
        return await agent.clear_context(conversation)

    def busy(self) -> bool:
        """Whether a turn of any chat is under way."""
        return self.agent is not None and any(lock.locked() for lock in self.agent.turn_locks.values())

    def model_unreachable(self) -> bool:
        """Whether the character found its model unreachable (the start-up
        probe, the last answer or failure); never a call of its own."""
        return bool(self.agent is not None and self.agent.__dict__.get("model_unreachable"))

    # ---- what it remembers ----------------------------------------------------

    async def forget_chat(self, conversation: str, keep: list[str]) -> int:
        """Forget everything the chat `conversation` remembers, the chats it
        replaced included, but not the chats in `keep`: the ones the page still
        lists. How many things went."""
        agent = await self._ready()
        return await agent.forget_chats(conversation, keep)

    def person_view(self) -> dict:
        """What it knows of the one person, for the memory view."""
        if self.agent is not None:
            return person.page_view(self.agent.person_store)
        return person.page_view(person.Person(self._runtime() / person.FILE))

    async def person_write(self, action: str, conversation: str, item: str, text: str,
                           payload: Optional[dict] = None) -> dict:
        """forget, edit or restore one thing it knows, forget all, add one, set the switches, or keep the ticked
        items of a pasted list; the view after it, and for a forget from the chats too what the page marks in its
        own copies (`scrub`). import_preview answers {lines} and changes nothing. ValueError("activity_changed")
        when the thing is gone or the words cannot be kept; ValueError("memory_<why>") when an added item is
        not kept."""
        agent = await self._ready()
        _, key = chat_key(conversation)
        extra = {}
        payload = payload or {}
        async with agent.locks[key]:
            if action == "import_preview":
                return {"lines": agent._person_import_preview(text)}
            if action == "add":
                refused, _kept = agent._person_keep(key, text, agent.chat_lang, "page")
                if refused:
                    raise ValueError(f"memory_{refused}")
            elif action == "settings":
                agent._person_settings(payload.get("on"), payload.get("health"))
            elif action == "import":
                extra["imported"] = agent._person_import(payload["lines"], key)
            elif action in ("forget", "forget_chats"):
                scrub = agent._person_forget(key, item, chats=action == "forget_chats")
                if scrub is None:
                    raise ValueError("activity_changed")
                if scrub:
                    extra["scrub"] = scrub
            elif action == "edit":
                if agent._person_edit(item, text) is None:
                    raise ValueError("activity_changed")
            elif action == "restore":
                if agent._person_restore(item) is None:
                    raise ValueError("activity_changed")
            else:
                agent._person_forget_all()
        return {**person.page_view(agent.person_store), **extra}

    async def person_said(self, item: str) -> list[dict]:
        """The chat lines one kept item came from. ValueError("activity_changed") when it is gone."""
        agent = await self._ready()
        lines = agent._person_said(item)
        if lines is None:
            raise ValueError("activity_changed")
        return lines

    def person_export(self, fmt: str) -> str:
        """Everything kept that is still true, as JSON or Markdown."""
        from .textproc import _env_tz_offset
        from datetime import timedelta, timezone
        store = self.agent.person_store if self.agent is not None else person.Person(self._runtime() / person.FILE)
        lang = self.agent.chat_lang if self.agent is not None else "en"
        return person.export(store, fmt, lang, time.time(), timezone(timedelta(hours=_env_tz_offset())))

    # ---- its day --------------------------------------------------------------

    def now(self) -> dict:
        """What it is doing now: out, back, answering, asleep or at home."""
        from .textproc import _env_tz_offset
        at = life.clock()
        src = (life.Sources.of_agent(self.agent, self._runtime()) if self.agent is not None
               else life.Sources.from_files(self._runtime()))
        return life.home(src, answering=self.busy(), sleep_until=life.asleep_until(at, _env_tz_offset()), now=at)

    def mark(self, ident: str, action: str, kept: bool = True) -> Optional[bool]:
        """Keep, dismiss or mark told one thing it saw; whether it is kept now, None when it is gone."""
        item = life.mark(Library(self._runtime()), ident, action, kept)
        return None if item is None else item.state == KEPT

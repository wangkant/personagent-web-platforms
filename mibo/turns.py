"""A turn of the chat: what the person chose for their message, what the
character says in it, and the time its model work may take."""
from __future__ import annotations

import asyncio
import contextvars
import random
from typing import Optional

from . import chatlangs

# A turn's model time for its answer, in LLM_TIMEOUT_S: one full-length call
# and half of another, however many calls, retries and models it goes through.
# A turn with tools has, before that, the time its tool loop may take (loop.WITHIN_S).
_TURN_BUDGET_CALLS = 1.5

#: What the person may choose for one message in the chat: no web search this time,
#: do it in the app's browser, do it in other programs or by commands; or make it a
#: scheduled task (schedule_tool.py). A message typed beside the page in the app's browser carries "page", or
#: "page_on" to go on with the errand that waits there (panels/browser.py).
TURN_CHOICES = ("search_off", "browser", "computer", "schedule", "page", "page_on")


class OutOfTime(TimeoutError):
    """A turn's model work outran its budget (`_within_turn_budget`)."""


class Turn:
    """The turn being answered. `said`: what the character says in it, in
    order, as the page's chat takes it: {"type": "text", "text": ...}, the
    last one carrying what the reply looked up, brought or forgot."""

    def __init__(self, choices=(), run: Optional[dict] = None) -> None:
        self.said: list[dict] = []
        self.choices = frozenset(c for c in choices if c in TURN_CHOICES)
        # A scheduled task's run ({"id", "kind", "acts", "words"}, schedule_tool.py); None for a turn of the person's.
        self.run = run
        # The reply's reasoning where the model returned it (abilities.note_reasoning).
        self.thinking: Optional[dict] = None


# Set for the length of one `Agent.answer` call; None outside a turn.
current_turn: contextvars.ContextVar[Optional[Turn]] = contextvars.ContextVar("current_turn", default=None)


def turn_choices() -> frozenset[str]:
    """What the person chose for the turn being answered; empty outside one."""
    turn = current_turn.get()
    return turn.choices if turn is not None else frozenset()


class Turns:
    def _model_failure_excuse(self, text: str = "") -> str:
        """A line for when the model call failed, in the language `text`, the
        message waiting for an answer, was written in."""
        code = chatlangs.detect(text, self.chat_lang) if text else self.chat_lang
        return random.choice(chatlangs.spec(code).failure_excuses)

    async def _within_turn_budget(self, model_work):
        """Await a turn's model work (`_chat_dm`) under its one time budget;
        past it the work is abandoned and OutOfTime raised."""
        answer = self.llm_timeout_s * _TURN_BUDGET_CALLS
        from .dm import chat_lookups
        from .loop import WITHIN_S
        lookups = chat_lookups.get()
        loop = asyncio.get_running_loop()
        work = asyncio.ensure_future(model_work)
        began = loop.time()
        try:
            while True:
                # A turn with tools: what its tool loop allows itself, not counting the time spent
                # waiting on the person, ahead of the answer's own time.
                budget = answer + (WITHIN_S + lookups.away() if lookups is not None else 0.0)
                left = began + budget - loop.time()
                if left <= 0:
                    raise OutOfTime(f"no answer within the turn's {budget:g} s budget")
                done, _ = await asyncio.wait({work}, timeout=left)
                if done:
                    return work.result()
        finally:
            if not work.done():
                work.cancel()

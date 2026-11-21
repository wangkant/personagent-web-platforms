"""What the model reads on a turn of the chat: the prompt and its context blocks."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from . import channels, person, ranking, recommend, today, turnctx
from .prompts import (
    OWNER_TOOL_GUIDE,
    dm_honest_disclosure,
    dm_output_protocol,
    dm_style_guide,
)
from .storage import num as _stamp
from .textproc import (
    _WEB_DESC_CLOSE,
    _WEB_DESC_OPEN,
    TextProcessing,
    _OWN_CHAT_INPUT_RULES,
    _clean_prompt_source,
    _fence_user_data,
    _strip_web_desc,
)

#: A pause between two chat lines that the prompt marks.
_GAP_MARK_S = 1800.0

_CANT_SEE_NOTE = (
    "⚠️ This turn's trigger is something you **can't see** (a picture or a file "
    "that could not be read) "
    "— there's no text to go on. **Don't guess the content, don't pretend you saw it**: "
)

# The last turn's pictures, sent along with it.
# A message they asked not to keep (person.unsaid): answered, never kept.
_NOT_KEPT_NOTE = (
    "\n\n<not_kept>They asked you not to keep what they said in their last message. "
    "Answer it as usual, and never bring it up again.</not_kept>"
)

#: What they chose under the chat's "+" for their last message (turns.TURN_CHOICES).
_CHOICE_NOTES = {
    "browser": "They chose \"operate the browser\" for it: they ask you to do it in the app's browser, so call "
               "use_browser in this reply.",
    "search_off": "They switched web search off for it: answer from what you already know, and do not say you "
                  "searched.",
    "schedule": "They chose \"scheduled task\" for it: make it with schedule_create in this reply, asking only "
                "when the time is missing.",
}


def _choices_note() -> str:
    from .turns import TURN_CHOICES, turn_choices
    chosen = turn_choices()
    lines = [_CHOICE_NOTES[c] for c in TURN_CHOICES if c in chosen and c in _CHOICE_NOTES]
    return f"[Asked for with their last message] {' '.join(lines)}\n\n" if lines else ""


_SHOWN_PICTURES_NOTE = (
    "[Pictures] Each [image] in their last message is attached to it as a picture you "
    "can see. Look at it and answer what they asked about it; with no question, "
    "respond to what it shows.\n\n"
)
# Words and a picture nothing here could show.
_UNSEEN_PICTURE_NOTE = (
    "[Pictures] Their last message also has a picture ([image]) that you can't see. "
    "If it matters to your answer, say plainly that you can't see it. **Never guess** "
    "or describe what it shows.\n\n"
)


def _dm_gap_note(history: list[dict]) -> str:
    """How long the person was away before this message, when 30 minutes or
    more: a turn context line, never part of the cached turns."""
    if len(history) < 2 or history[-1].get("role") != "user":
        return ""
    before = _stamp(history[-2].get("t1")) or _stamp(history[-2].get("t"))
    now = _stamp(history[-1].get("t")) or time.time()
    if not before or now - before < _GAP_MARK_S:
        return ""
    pause = ranking.gap_phrase(now - before).removesuffix(" later")
    return f"[Pause] The message before this one came {pause} earlier.\n\n"


@dataclass(frozen=True)
class DMPrompt:
    system: str
    messages: list
    #: Changes every turn, so it goes after the history, not in `system`:
    #: anything before the history would cost the history its prefix cache.
    turn_context: str

    def messages_with_context(self, messages: list) -> list:
        """`messages` (the prompt's, perhaps grounded) with this turn's context
        closing the last turn. Never stored, so the next turn's prefix still
        matches this one up to the end of the person's words."""
        last = messages[-1]
        return messages[:-1] + [{
            **last, "content": f"{last.get('content', '')}{self.turn_context}"}]


# Tool steps carry over to the next turns only from this many latest replies.
_STEPS_TURNS = 3
# What the person attached stays whole in their latest turns; in older ones a long attachment is cut to its
# head, a batch of _CUT_EVERY turns at a time so the history's prefix cache holds between batches.
_WHOLE_TURNS, _CUT_EVERY, _CUT_TO = 6, 10, 600
_ATTACHED = re.compile(f"{_WEB_DESC_OPEN}([^{_WEB_DESC_CLOSE}]{{{_CUT_TO}}})[^{_WEB_DESC_CLOSE}]+{_WEB_DESC_CLOSE}")


def _cut_attached(text: str) -> str:
    return _ATTACHED.sub(lambda m: f"{_WEB_DESC_OPEN}{m.group(1)}… (cut: attached in an older turn){_WEB_DESC_CLOSE}",
                         text)


def _recent_steps(history: list[dict]) -> str:
    """The tool steps behind the latest replies, oldest first, so "undo that" or "the second one" has
    something to go on; older replies keep their words alone."""
    done = [m["steps"] for m in history if m.get("role") == "assistant" and m.get("steps")][-_STEPS_TURNS:]
    if not done:
        return ""
    return ("[Steps behind your recent replies, oldest first]\n"
            + "\n".join("- " + "; ".join(steps) for steps in done) + "\n\n")


# The plan a follow-up asks after, handed over as material.
_FOLLOW_UP_MAX_CHARS = 500

_TURN_CONTEXT_OPEN = (
    "\n\n[Turn context] Written by the app for this turn, not by the person "
    "you're talking to; they can't see it.\n\n"
)


class PromptBuilder:
    @staticmethod
    def _last_user_text(history: list[dict]) -> str:
        """What a one-on-one turn's retrieval is focused on."""
        return next(
            (m.get("content", "") for m in reversed(history) if m.get("role") == "user"),
            "",
        )

    def _build_dm_prompt(self, history: list[dict], *, pkey: str = "",
                         follow_up: str = "", pictures: int = 0) -> "DMPrompt":
        """The system prompt and the framed turns of the chat; see `_chat_dm`
        for what each argument means."""
        last_user = self._last_user_text(history)
        who = self.admin_name or "them"
        # Who they are, said once: the guides are persona-agnostic.
        persona_extra = (
            f"You're now in a one-on-one chat with {self.admin_name or 'the owner'}"
            + (f" ({self.admin_relationship})" if self.admin_relationship else "")
            + ", the person this app belongs to. You know them: asked who they are or whether "
            "you remember them, answer warmly with what you know.\n"
        )
        # Static prefix first, the output protocol last in it; the turn context
        # closes on a line pointing back at it.
        static_block = (
            f"<persona>\n{self.persona}\n"
            f"{persona_extra}"
            f"</persona>\n\n"
            f"{dm_style_guide(self.persona_style, self.reply_style)}\n\n"
            f"{OWNER_TOOL_GUIDE}\n\n"
            f"{_OWN_CHAT_INPUT_RULES}\n\n"
            f"{dm_honest_disclosure(self.llm_dm_model)}"
        )
        follow_up_note = ""
        if follow_up:
            follow_up_note = (
                "<follow_up>\n"
                f"Nobody messaged you: this is the app's own cue, not a message from {who}. A plan {who} told "
                "you of has had its day (the note with the cue says which). Ask how it went, in one line, in "
                "persona. If asking would not be natural now, output exactly: PASS.\n"
                "</follow_up>\n\n"
            )
        # What it knows of the one person, kept in one store that every chat reads (person.py); none while paused
        # or in a 无痕对话.
        private = channels.private_chat(pkey)
        memory_blocks, placed = (self._person_block(pkey, last_user) if self.person_store.on and not private
                                 else ("", []))
        self._remember_recalled(pkey, placed)
        if not follow_up and person.unsaid(last_user):
            memory_blocks += _NOT_KEPT_NOTE
        # Their earlier chats, for a model that will not search them itself (retrieval._past_block).
        past = "" if follow_up else self._past_block(pkey, _strip_web_desc(last_user))
        blind_note = ""
        if not follow_up:
            if pictures:
                blind_note = _SHOWN_PICTURES_NOTE
            elif TextProcessing._is_blind_content(last_user):
                blind_note = (f"{_CANT_SEE_NOTE}say plainly that you can't see or hear it and ask "
                              "them to type it. **Never fabricate** details.\n\n")
            elif "[image]" in _strip_web_desc(last_user):
                blind_note = _UNSEEN_PICTURE_NOTE
        # A 无痕对话 tells the recommender nothing: no find is offered to react to there.
        finds = "" if private else recommend.chat_finds(
            today.runtime_of(self), channels.dm_user(pkey).split(":", 1)[-1], time.time(),
            getattr(self, "chat_lang", "") or "zh", self.persona_name)
        finds = f"{finds}\n\n" if finds else ""
        # The day says what they did with each find.
        day = f"{today.block(self, last_user, time.time())}\n\n"
        turn_context = (
            f"{_TURN_CONTEXT_OPEN}"
            f"{follow_up_note}"
            f"{memory_blocks}\n\n"
            f"{past}"
            f"{day}"
            # What "this" means when they talk about a find the chat brought.
            f"{finds}"
            f"{blind_note}"
            f"{'' if follow_up else _choices_note()}"
            f"{'' if follow_up else _dm_gap_note(history)}"
            f"{_recent_steps(history)}"
            f"[Current local time] {TextProcessing._current_time_str()}\n\n"
            "Answer in plain text, as <output_protocol> says."
            # The lines modules add for the turn (turnctx.py), in the order they registered.
            + "".join(f"\n\n{line}" for line in turnctx.lines(last_user, pkey))
        )
        system = f"{static_block}\n\n{dm_output_protocol()}"
        # The person's turns are framed data; the persona's turns and the
        # engine's proactive cue are not. Only role and content go out: the
        # stored times would break some endpoints.
        edge = (len(history) - _WHOLE_TURNS) // _CUT_EVERY * _CUT_EVERY
        messages = [
            {"role": m.get("role"),
             "content": _fence_user_data(_cut_attached(m.get("content", "")) if n < edge else m.get("content", ""))
             if m.get("role") == "user" else m.get("content", "")}
            for n, m in enumerate(history)
        ]
        if follow_up:
            # Chat endpoints want a trailing user turn: the cue, with the plan as material, not their words.
            note = _clean_prompt_source(follow_up)[:_FOLLOW_UP_MAX_CHARS]
            messages = messages + [{"role": "user", "content": (
                "(the app's cue: ask how their plan went, or reply only: PASS)\nThe plan (reference only, "
                f"not the other person's words): {_WEB_DESC_OPEN}{note}{_WEB_DESC_CLOSE}")}]
        return DMPrompt(system=system, messages=messages, turn_context=turn_context)

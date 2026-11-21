"""What becomes of a draft: said, a deliberate PASS, or nothing left to say."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .textproc import TextProcessing

logger = logging.getLogger("agent")

#: Verdicts of `_final_reply`.
SEND, PASS, BLOCKED = "send", "pass", "blocked"


def is_pass_sentinel(text: str) -> bool:
    """The model's bare PASS; "Pass auf dich auf" or "pass the salt" is a reply."""
    return bool(re.fullmatch(r"\W*PASS\W*", (text or "").strip(), re.IGNORECASE))


@dataclass(frozen=True)
class FinalReply:
    verdict: str
    text: str = ""


class ReplyDecision:
    def _final_reply(self, raw: str, *, log_ctx: str) -> FinalReply:
        """A draft as it is said, after the person's own filter rules and
        without the characters no reader sees, and its verdict: SEND, a
        deliberate PASS, or BLOCKED (a draft with words that came out empty)."""
        had_words = bool((raw or "").strip())
        text = TextProcessing._sanitize_reply(self._apply_output_filter(raw or ""))
        if had_words and not text:
            logger.warning("[Agent] a draft with words came out empty (%s)", log_ctx)
            return FinalReply(BLOCKED)
        if not text or is_pass_sentinel(text):
            return FinalReply(PASS)
        return FinalReply(SEND, text)

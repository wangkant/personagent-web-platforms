"""The main chat's context budget and token count, after dsh's compaction-basic (resolveCompactSpec: the window less
the answer's reserve and a headroom) and token-meter (the last call's own usage as the anchor, an estimate for what
changed since) (MIT, NOTICE).

    window(rec, setting)            (W, where it came from): the CONTEXT_WINDOW setting, the host's model list, the
                                    number an overflow error stated, else ASSUMED
    budget(W, level, fixed)         the answer's room, the safety margin, the input limit and the shares of a request
    estimate(payload)               tokens of a chat request by characters, before any calibration
    measured(caller, url, model, payload, usage)
                                    a reply request's reported prompt_tokens: the anchor, and the model's ratio
    count(caller, url, model, payload)
                                    tokens of the next request: the anchor plus the ratio times what changed since
    Fit(plan, count, ratio, pictures)
                                    one turn's requests fitted before each is sent: a tool result held to its share
                                    when it comes in (dsh spill-policy), then past the ceiling the older results and
                                    the pictures tools returned cut once each (dsh compaction-tool-result-pruner)

The window is measured, never looked up by a model's name; counts are the endpoint's own usage.prompt_tokens.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

# W when nothing states it; Settings says it is assumed.
ASSUMED = 32768
# Where W came from, in the order the sources are asked.
SOURCES = ("setting", "host", "error", "assumed")
# Tokens a character (DeepSeek's published densities, checked on the bench) and each message's framing.
CJK_TOKENS, OTHER_TOKENS, MESSAGE_TOKENS = 0.6, 0.245, 4
# A picture until the endpoint's count of one is known.
PICTURE_TOKENS = 1500
_CJK = re.compile(r"[⺀-鿿가-힯豈-﫿＀-￯]")
# The ratio of reported to estimated tokens: before any call, its bounds, and the weight of each new request.
RATIO_FIRST, RATIO_LOW, RATIO_HIGH, RATIO_WEIGHT = 1.25, 0.5, 2.0, 0.2
# An overflow error's own statement of the window ("maximum context length is 8192 tokens").
_STATED = re.compile(r"(?:context(?:[ _](?:length|window|size))?|maximum (?:input )?length)\D{0,24}?(\d[\d,]{2,})",
                     re.IGNORECASE)
# One tool result at ingestion at most (dsh spill-policy's 12,500), and an older one once cut past the ceiling:
# head and tail kept four to one, the characters left out counted where the cut is.
RESULT_TOKENS, SMALL_TOKENS, HEAD_SHARE = 12_500, 1250, 0.8
CUT_MARK = "\n\n[... {n} characters of this result left out ...]\n\n"
PICTURE_GONE = "[an older picture, removed to save room; look again if it is still needed]"
# Past the ceiling T, older results are cut until the request is under this share of it.
FIT_TO = 0.7


def window(rec: dict | None, setting: int = 0) -> tuple[int, str]:
    """(W, its source): the person's setting, the host's model list, the number an overflow error stated, else
    ASSUMED. A stated number below the host's wins, since that server refused at it."""
    rec = rec or {}
    host, error = int(rec.get("context") or 0), int(rec.get("context_error") or 0)
    if setting > 0:
        return setting, "setting"
    if host and not 0 < error < host:
        return host, "host"
    if error:
        return error, "error"
    return ASSUMED, "assumed"


def stated_window(text: str) -> int:
    """The window an overflow error names, or 0."""
    found = _STATED.search(text or "")
    return int(found.group(1).replace(",", "")) if found else 0


@dataclass(frozen=True)
class Budget:
    window: int      # W
    answer: int      # O: max_tokens, thinking included
    safety: int      # S: the estimate's error on what is new since the last measured call
    limit: int       # L = W - O - S: no request above it
    available: int   # A = L - system - tool definitions
    turn: int        # C: the turn context
    history: int     # h: compaction trigger of checkpoint plus verbatim turns
    tail: int        # the verbatim tail kept after a compaction
    checkpoint: int  # the checkpoint's size at most
    ceiling: int     # T: the request size that prunes a turn's older results
    message: int     # the person's newest message whole up to this
    result: int      # one tool result at ingestion at most
    small: int       # an older tool result once cut past the ceiling

    def retry(self, prompt: int) -> int:
        """max_tokens for a reply cut off at the cap: four times the room, within what the window leaves."""
        return min(4 * self.answer, self.window - prompt)


def budget(window: int, level: str = "", fixed: int = 0) -> Budget:
    """The shares of one request for a window of `window` tokens, replies thinking at `level` (abilities.LEVELS,
    "" for off), with `fixed` tokens of system prompt and tool definitions."""
    from .abilities import LEVELS
    answer = min(32768, max(1024, window // 8))
    if level:
        answer += min(LEVELS[level][1], window // 8)
    safety = max(256, window // 32)
    limit = window - answer - safety
    available = limit - fixed
    history = min(round(0.45 * available), 16384)
    return Budget(window=window, answer=answer, safety=safety, limit=limit, available=available,
                  turn=min(round(0.15 * available), 2000), history=history, tail=history // 2,
                  checkpoint=max(300, round(0.15 * history)), ceiling=min(round(0.85 * limit), 100_000),
                  message=available // 2, result=RESULT_TOKENS, small=min(SMALL_TOKENS, round(0.1 * available)))


# ------------------------------------------------------------------ count --

def text_tokens(text: str) -> float:
    cjk = len(_CJK.findall(text))
    return cjk * CJK_TOKENS + (len(text) - cjk) * OTHER_TOKENS


def message_tokens(message: dict) -> float:
    """One message's estimate: its words, pictures, calls and the reasoning sent back with them, and framing."""
    content = message.get("content") or ""
    parts = content if isinstance(content, list) else [{"type": "text", "text": str(content)}]
    tokens = MESSAGE_TOKENS + sum(text_tokens(str(p.get("text") or "")) if p.get("type") == "text" else PICTURE_TOKENS
                                  for p in parts)
    for call in message.get("tool_calls") or []:
        tokens += text_tokens(json.dumps(call.get("function") or {}, ensure_ascii=False))
    return tokens + text_tokens(str(message.get("reasoning_content") or ""))


def estimate(payload: dict) -> float:
    """A request's tokens by characters: its messages, and the tool definitions by their JSON text."""
    tools = payload.get("tools")
    return (sum(message_tokens(m) for m in payload.get("messages") or [])
            + (text_tokens(json.dumps(tools, ensure_ascii=False)) if tools else 0))


def _learned(caller) -> dict:
    """What this process learned per (url, model): the anchor, the ratio and an overflow's window."""
    return caller.__dict__.setdefault("_context_learned", {})


def ratio(caller, url: str, model: str) -> float:
    from . import abilities
    mine = _learned(caller).get((url, model)) or {}
    rec = abilities.record(abilities.runtime_of(caller), url, model) or {}
    return float(mine.get("token_ratio") or rec.get("token_ratio") or RATIO_FIRST)


def measured(caller, url: str, model: str, payload: dict, usage: dict | None) -> None:
    """A reply request answered: its reported prompt_tokens become the anchor, and the ratio moves toward
    reported over estimated (a request with pictures moves only the anchor, its pictures being a guess)."""
    from . import abilities
    reported = int((usage or {}).get("prompt_tokens") or 0)
    if not reported:
        return
    guess = estimate(payload)
    mine = _learned(caller).setdefault((url, model), {})
    mine["anchor"] = (reported, guess)
    if any(isinstance(m.get("content"), list) for m in payload.get("messages") or []):
        return
    seen = reported / guess
    known = mine.get("token_ratio") or (abilities.record(abilities.runtime_of(caller), url, model) or {}).get(
        "token_ratio")
    now = min(RATIO_HIGH, max(RATIO_LOW, seen if not known else (1 - RATIO_WEIGHT) * known + RATIO_WEIGHT * seen))
    mine["token_ratio"] = now
    if not known or abs(now - known) >= 0.01:
        abilities.note_learned(caller, url, model, token_ratio=round(now, 3))


def count(caller, url: str, model: str, payload: dict) -> int:
    """Tokens of `payload` as the endpoint will count them: the last reply request's reported figure plus the
    ratio times the estimate of what changed since; the ratio times the whole estimate before any."""
    anchor = (_learned(caller).get((url, model)) or {}).get("anchor")
    r = ratio(caller, url, model)
    if anchor is None:
        return round(r * estimate(payload))
    return round(anchor[0] + r * (estimate(payload) - anchor[1]))


def overflowed(caller, url: str, model: str, text: str) -> None:
    """An overflow error that states the window: kept as the model's W from now on."""
    from . import abilities
    stated = stated_window(text)
    if stated:
        _learned(caller).setdefault((url, model), {})["context_error"] = stated
        abilities.note_learned(caller, url, model, context_error=stated)


def known_window(caller, url: str, model: str, setting: int = 0) -> tuple[int, str]:
    """`window` for a model as this caller knows it: its record, with what this process learned over it."""
    from . import abilities
    rec = dict(abilities.record(abilities.runtime_of(caller), url, model) or {})
    rec.update((k, v) for k, v in (_learned(caller).get((url, model)) or {}).items() if k == "context_error")
    return window(rec, setting)


# -------------------------------------------------------------------- fit --

def _within(text: str, tokens: float) -> int:
    """How many characters from the start of `text` its estimate keeps within `tokens`."""
    spent = 0.0
    for n, ch in enumerate(text):
        spent += CJK_TOKENS if _CJK.match(ch) else OTHER_TOKENS
        if spent > tokens:
            return n
    return len(text)


def cut(text: str, tokens: float) -> str:
    """`text` within about `tokens` estimated tokens: whole when it fits, else its head and tail four to one with
    the characters left out counted between them (dsh spill-policy's retention)."""
    if text_tokens(text) <= tokens:
        return text
    room = max(0.0, tokens - text_tokens(CUT_MARK.format(n=len(text))))
    head = text[:_within(text, HEAD_SHARE * room)]
    tail = text[len(text) - _within(text[::-1], room - text_tokens(head)):] if room else ""
    return head + CUT_MARK.format(n=len(text) - len(head) - len(tail)) + tail


class Fit:
    """One turn's requests fitted to the budget `plan` before each is sent (context-design's fit ladder). A tool
    result is held to its share when it comes in: RESULT_TOKENS, or less when its round alone would pass the
    ceiling T and shares FIT_TO of what is left under it. Past T, older results and the pictures tools returned
    are cut once each, oldest first, to their small size until the request is under FIT_TO of T; the newest round
    is left whole. `count(convo)` is the
    request's tokens with `convo` as its conversation, `ratio` the endpoint's tokens per estimated one, and a user
    message whose first words are `pictures` holds pictures a tool returned (the person's own are never cut)."""

    def __init__(self, plan: Budget, count, ratio: float, pictures: str) -> None:
        self.plan, self.count, self.ratio, self.pictures = plan, count, ratio, pictures
        # Results already held to their share, and messages already cut, by identity.
        self.seen: set[int] = set()
        self.cut: set[int] = set()
        self.rewrites = 0

    def _returned(self, message: dict) -> bool:
        content = message.get("content")
        return (message.get("role") == "user" and isinstance(content, list) and bool(content)
                and content[0].get("text") == self.pictures)

    def _shrink(self, message: dict) -> float:
        """Cut one result or one message of returned pictures to its small size; the estimated tokens saved."""
        before = message_tokens(message)
        if message.get("role") == "tool":
            message["content"] = cut(message["content"], self.plan.small)
        else:
            message["content"] = [{"type": "text", "text": PICTURE_GONE} if p.get("type") == "image_url" else p
                                  for p in message["content"]]
        self.cut.add(id(message))
        return before - message_tokens(message)

    def __call__(self, convo: list[dict]) -> bool:
        """Fit `convo` in place for its next request; whether that request is within the input limit L."""
        plan, ratio = self.plan, self.ratio
        start = max((n for n, m in enumerate(convo) if m.get("tool_calls")), default=len(convo))
        fresh = [m for m in convo[start:] if m.get("role") == "tool" and id(m) not in self.seen]
        for message in fresh:
            message["content"] = cut(message["content"], plan.result / ratio)
        total = self.count(convo)
        if total > plan.ceiling:
            for message in convo[:start]:
                if total < FIT_TO * plan.ceiling:
                    break
                if id(message) in self.cut or not (message.get("role") == "tool" or self._returned(message)):
                    continue
                if message.get("role") == "tool" and text_tokens(message["content"]) <= plan.small:
                    continue
                total -= ratio * self._shrink(message)
                self.rewrites += 1
        if total > plan.ceiling and fresh:
            # The newest round shares FIT_TO of what is left under T, a small result taking only what it needs: new
            # text is where the estimate errs (a Chinese wiki page counted 1.36 times its estimate on DeepSeek).
            left = FIT_TO * ((plan.ceiling - total) / ratio + sum(text_tokens(m["content"]) for m in fresh))
            for n, message in enumerate(sorted(fresh, key=lambda m: len(m["content"]))):
                message["content"] = cut(message["content"], max(0.0, left / (len(fresh) - n)))
                left -= text_tokens(message["content"])
            total = self.count(convo)
        self.seen.update(map(id, fresh))
        return total <= plan.limit

    def last(self, convo: list[dict]) -> None:
        """For a turn's last call when its request cannot fit: every result and returned picture cut small."""
        for message in convo:
            if id(message) not in self.cut and (message.get("role") == "tool" or self._returned(message)):
                self._shrink(message)

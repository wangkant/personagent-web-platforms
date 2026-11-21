"""Retrieval ranking: one score per row from lexical, embedding, recency, importance and use.

Pure functions over fields computed ahead, so the ranking can be tested on
hand-built rows and the prompt path never waits on anything in here."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from operator import mul
from typing import Iterable, Optional, Sequence

from .embeddings import text_key

BM25_K1 = 1.2
BM25_B = 0.75

# math.sumprod is 3.12+.
_dot = getattr(math, "sumprod", None) or (lambda a, b: sum(map(mul, a, b)))


@dataclass(frozen=True)
class Weights:
    lexical: float
    #: Multiplies cosine similarity, which is clamped at 0.
    embedding: float
    #: The bonus of a row written just now; it halves every half_life_days.
    recency: float
    half_life_days: float
    #: Per field of a row, in the order its fields are given.
    fields: tuple[float, ...] = (1.0,)
    #: Times (imp - 1) / 4 for an importance of 1 to 5.
    importance: float = 0.0
    #: Times min(uses, 5) / 5.
    use: float = 0.0
    #: The bonus of an event inside the time a message asks about.
    time_match: float = 0.0


#: Importance and use keep an old useful fact above new trivia.
MEMORY_WEIGHTS = Weights(lexical=0.5, embedding=1.0, recency=0.6,
                         half_life_days=14.0, importance=0.4, use=0.2,
                         time_match=2.0)

#: Days for the recency part of `retention` to halve.
RETENTION_HALF_LIFE_DAYS = 30.0

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
           "Oct", "Nov", "Dec")


class Lexicon:
    """BM25 over one pool for one query. Terms match as substrings (CJK
    bigrams need no segmenter); IDF is rescaled to average 1.0 over the
    query's matching terms."""

    def __init__(self, tokens: Iterable[str], docs: Sequence[Sequence[str]]):
        df = dict.fromkeys(tokens, 0)
        n = len(docs)
        totals = [0] * (len(docs[0]) if docs else 0)
        for doc in docs:
            for i, text in enumerate(doc):
                totals[i] += len(text)
            # One substring test per token; NUL never occurs in a token, so
            # no match can straddle two fields.
            blob = doc[0] if len(doc) == 1 else "\x00".join(doc)
            for tok in df:
                if tok in blob:
                    df[tok] += 1
        self.avg_len = [max(1.0, total / n) for total in totals]
        idf = {tok: math.log(1.0 + (n - d + 0.5) / (d + 0.5))
               for tok, d in df.items() if d}
        mean = sum(idf.values()) / len(idf) if idf else 1.0
        self.idf = {tok: value / mean for tok, value in idf.items()}

    def score(self, doc: Sequence[str], field_weights: Sequence[float]) -> float:
        s = 0.0
        for tok, idf in self.idf.items():
            for text, avg, weight in zip(doc, self.avg_len, field_weights):
                tf = text.count(tok)
                if tf:
                    norm = 1.0 - BM25_B + BM25_B * len(text) / avg
                    s += weight * idf * tf * (BM25_K1 + 1.0) / (tf + BM25_K1 * norm)
        return s


#: Han characters too common to say what a message is about.
_HAN_COMMON = frozenset(
    "我你他她它们的了是在吗呢吧啊呀哦嗯么什那这个有没不也就都还要会说去来到上下得过着和与跟很太好"
    "人一些点儿被把给让对从为以而且又再才只能可想看做咋啥呗嘛哪谁怎样多少几次回最近记小老阿大"
    # Places in a list ("第二个"): they say which one, not what it is.
    "第二三四五六七八九十两")
# Pairs that frame a question about the past rather than say what it is about.
_HAN_FILLER = frozenset({"最近", "上次", "上回", "之前", "以前", "记得", "还记", "觉得", "知道", "一直", "其实",
                         "然后", "因为", "所以", "但是", "可是", "就是", "还是", "已经", "现在", "感觉", "来着",
                         # and pairs that ask for more of something ("再展开讲讲", "帮我看看这个讲了啥")
                         "展开", "讲讲", "说说", "细说", "具体", "详细", "介绍", "讲了", "讲的", "帮我", "帮忙",
                         # or ask about a day or a kind of thing, not a topic ("今天有啥好玩的发现", "那个视频")
                         "今天", "昨天", "上午", "下午", "早上", "晚上", "视频", "文章", "好玩", "有趣", "发现"})
_PRONOUNS = frozenset("我你他她它咱")
_HAN = re.compile(r"[一-鿿]")
#: A paraphrase often keeps one character of the word it replaces (练琴, 练吉他).
CHAR_WEIGHT = 0.3


def plain_terms(tokens: Iterable[str]) -> set[str]:
    """`tokens` without the Han pairs made of two common characters (什么, 那个), led by a pronoun
    (我最) or framing a question (最近)."""
    return {t for t in tokens if t not in _HAN_FILLER and not (
        len(t) == 2 and (t[0] in _PRONOUNS or all(c in _HAN_COMMON for c in t)))}


def han_chars(text: str) -> set[str]:
    """The uncommon Han characters of `text`, each a term of its own; none from a filler pair."""
    for pair in _HAN_FILLER:
        text = text.replace(pair, " ")
    return {c for c in _HAN.findall(text) if c not in _HAN_COMMON}


class Query:
    """One message's lexical ask of a pool: its terms, its uncommon Han
    characters at CHAR_WEIGHT, and the turn before at `before_weight` for a
    message that leans on it ("那个呢", "第二个"). Each part is BM25 of its own."""

    def __init__(self, tokens: Iterable[str], text: str, docs: Sequence[Sequence[str]], *,
                 before_tokens: Iterable[str] = (), before: str = "", before_weight: float = 0.0):
        parts = [(plain_terms(tokens), 1.0), (han_chars(text.lower()), CHAR_WEIGHT)]
        if before_weight:
            parts += [(plain_terms(before_tokens), before_weight),
                      (han_chars(before.lower()), before_weight * CHAR_WEIGHT)]
        # Every other part is made of words, the rest of single characters.
        self.parts = [(Lexicon(terms, docs), weight, n % 2 == 0)
                      for n, (terms, weight) in enumerate(parts) if terms]

    def score(self, doc: Sequence[str], field_weights: Sequence[float]) -> float:
        return sum(weight * lexicon.score(doc, field_weights) for lexicon, weight, _ in self.parts)

    def word_score(self, doc: Sequence[str], field_weights: Sequence[float]) -> float:
        """`score` from shared words alone: single characters add up on a long page."""
        return sum(weight * lexicon.score(doc, field_weights) for lexicon, weight, words in self.parts if words)


def epoch(ts) -> float:
    """ISO timestamp -> epoch seconds; 0.0 when unparsable. Naive stamps are
    read as local time, matching every other timestamp in the pipeline."""
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OSError, OverflowError):
        return 0.0


def embedding_text(rec: dict) -> str:
    """What a row is embedded as: the situation it answers, not the reply."""
    ctx = rec.get("context") or []
    if not isinstance(ctx, list):
        ctx = [ctx]
    return "\n".join([str(rec.get("scenario") or ""), *(str(c) for c in ctx)])


def row_fields(rec: dict) -> tuple[str, str, float, str]:
    """What scoring needs from a row, computed once at load: lowercased
    scenario and context, the timestamp as epoch seconds (0.0 means no
    recency bonus) and the row's embedding key."""
    ctx = rec.get("context") or []
    if not isinstance(ctx, list):
        ctx = [ctx]
    return (
        str(rec.get("scenario") or "").lower(),
        " ".join(str(c) for c in ctx).lower(),
        epoch(rec.get("ts")),
        text_key(embedding_text(rec)),
    )


def cosine(a, b) -> Optional[float]:
    """Cosine similarity of two unit vectors; None when either is missing,
    so such a row is ranked on the other signals alone."""
    if a is None or b is None:
        return None
    return _dot(a, b)


def recency(age_s: float, half_life_days: float) -> float:
    """1.0 now, halving every half-life; a future timestamp counts as now."""
    return 0.5 ** (max(0.0, age_s) / 86400.0 / half_life_days)


def age_phrase(age_s: float) -> str:
    """How old a note is, as a prompt tells the model."""
    days = int(max(0.0, age_s) / 86400.0)
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 60:
        return f"{days} days ago"
    return f"{days // 30} months ago"


def score(weights: Weights, *, lexical: float = 0.0,
          similarity: Optional[float] = None, age_s: Optional[float] = None,
          importance: Optional[float] = None, uses: int = 0,
          time_match: bool = False) -> float:
    s = weights.lexical * lexical
    if similarity is not None:
        s += weights.embedding * max(0.0, similarity)
    if age_s is not None:
        s += weights.recency * recency(age_s, weights.half_life_days)
    if importance is not None:
        s += weights.importance * _importance_part(importance)
    if uses:
        s += weights.use * _use_part(uses)
    if time_match:
        s += weights.time_match
    return s


def _importance_part(imp) -> float:
    try:
        value = float(imp)
    except (TypeError, ValueError):
        value = 3.0
    return (min(5.0, max(1.0, value)) - 1.0) / 4.0


def _use_part(uses) -> float:
    try:
        count = int(uses or 0)
    except (TypeError, ValueError):
        count = 0
    return min(max(0, count), 5) / 5.0


def retention(imp, uses, last_s, now: float) -> float:
    """How much a remembered item is worth keeping: importance, use, and how
    long since it was written, confirmed or used. Missing fields are 3, 0, 0."""
    try:
        last = float(last_s or 0.0)
    except (TypeError, ValueError):
        last = 0.0
    days = max(0.0, now - last) / 86400.0
    return (0.4 * _importance_part(3 if imp is None else imp)
            + 0.2 * _use_part(uses)
            + 0.5 ** (days / RETENTION_HALF_LIFE_DAYS))


def memory_age(born, time_s, now: float) -> str:
    """"first noted X", and ", confirmed Y" when it was written again more
    than a day after it was first noted."""
    try:
        written = float(time_s or 0.0)
    except (TypeError, ValueError):
        written = 0.0
    try:
        first = float(born or 0.0) or written
    except (TypeError, ValueError):
        first = written
    phrase = f"first noted {age_phrase(now - first)}"
    if written - first > 86400.0:
        phrase += f", confirmed {age_phrase(now - written)}"
    return phrase


def gap_phrase(seconds: float) -> str:
    """A pause between two chat lines, as the prompt marks it."""
    seconds = max(0.0, seconds)
    if seconds < 3600:
        n, unit = max(1, int(seconds // 60)), "minute"
    elif seconds < 86400:
        n, unit = int(seconds // 3600), "hour"
    else:
        n, unit = int(seconds // 86400), "day"
    return f"{n} {unit}{'' if n == 1 else 's'} later"


def _local(t: float, tz=None) -> datetime:
    return datetime.fromtimestamp(t, tz)


def day_label(t: float, tz=None) -> str:
    """"Mon 10 Nov", in English whatever the system locale; local time
    unless `tz` is given."""
    d = _local(t, tz)
    return f"{_WEEKDAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}"

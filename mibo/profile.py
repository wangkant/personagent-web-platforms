"""What the character knows of its owner, as short text the owner can read and edit.

`<runtime>/profile.json` holds the owner text (likes, habits, situation), the
character's own "things my owner showed me" note, a lean per catalogue source
(-1..+1, applied to the draw by `situation.supply`) and a few walk search
words. The character's core taste is its persona and is never rewritten here.

It starts from the picked topics, an optional free-text answer (which asks the
model at once) and the owner notes, and is rewritten by one model call about
once a week. A line the owner edits is pinned and kept verbatim; a "not this"
they write becomes a hard rule.

What the person says in the chat about their tastes acts at once, in code, with
their words kept: a dislike is a rule (`dislike`), a like a prior and a walk
word (`like`), a wish for short or for one form a term on length and kind
(`form`), and how they want to be treated a pinned "manner" line (`manner`)
that the pick, the pet's remarks, the judge and the pacing read. A like said
for now ("这周想多看点 F1") is a wish that runs out after WISH_S. `forget`
takes any of it out again, and a change asks for a rewrite within the hour.
Settings › 推荐 also keeps here a lean per feature of the taste model
("leans"), the pace and the quiet hours pacing.py reads, and `reset`. The model calls here, in `roam.judge` and in
`recommend` are counted in `<runtime>/character_calls.json` for the settings page.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

from . import drafting, langs, situation
from . import setup_wizard as sw
from .llm import _call_purpose, purpose
from .storage import atomic_write_text
from .textproc import _UNTRUSTED_INPUT_RULES, _fence_user_data, strip_json_fences
from .usage import UsageBook

logger = logging.getLogger("mibo.profile")

FILE = "profile.json"
CALLS_FILE = "character_calls.json"
FIELDS = ("owner", "character_learned")
OWNER_MAX = 1000
LEARNED_LINES = 3
LINE_MAX = 200
FREE_MAX = 1000
WORDS_MIN, WORDS_MAX, WORD_LEN = 3, 5, 40
PINNED_MAX = 30
RULES_MAX = 30
EDITS_MAX = 200
NOTES = 15
LIKED, DISLIKED, DISAGREED = 30, 20, 12
# A rewrite of its own: 25 reactions since the last, or a week with 5.
REWRITE_REACTIONS, WEEK_REACTIONS, WEEK_S = 25, 5, 7 * 86400
# A topic the owner said no to is all but never shown.
AVOID_TOPIC = (-3.0, 100.0)
# A topic they said they like counts as a picked one; a length or a kind they said they do not want, firmly.
LIKE_TOPIC = (1.5, 3.0)
FORM_AGAINST = (-1.5, 10.0)
LIKES_MAX, MANNER_MAX, SAID_LEN = 20, 8, 120
# A like this short becomes a walk's search words (CJK carries about three times what Latin letters do).
WALK_LEN, WALK_LEN_LATIN = 10, 30
# What the person said in the chat asks for a rewrite at most this often.
SAID_GAP_S = 3600
# A wish for now ("这周想多看点 F1") lasts this long and is kept apart from lasting likes (Threads' Dear Algo: 3 days).
WISH_S = 3 * 86400
# How often things are brought (pacing.py), as Settings sets it; "less" is also what asking for fewer in the chat sets.
PACES = ("less", "normal", "more")
# A lean on one of the taste model's features (taste.LEANS): more, less, or not counted at all.
LEANS = (1, -1, 0)
_CLOCK = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


# ------------------------------------------------------------------ the file --

def _path(runtime: Path) -> Path:
    return Path(runtime) / FILE


def _state(runtime: Path) -> dict:
    try:
        saved = json.loads(_path(runtime).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return saved if isinstance(saved, dict) else {}


def _save(runtime: Path, state: dict) -> None:
    Path(runtime).mkdir(parents=True, exist_ok=True)
    atomic_write_text(_path(runtime), json.dumps(state, ensure_ascii=False, indent=1))


def _seed(runtime: Path, state: dict) -> str:
    """The owner text before any rewrite: the picked topics and the free-text answer."""
    from .panels import feed
    topics = feed.load_topics(runtime)
    lines = (["Picked topics: " + ", ".join(topics)] if topics else []) + (
        [str(state["free_text"])] if state.get("free_text") else [])
    return "\n".join(lines)


def load(runtime: Path, seed: bool = True) -> dict:
    """{owner, character_learned, source_lean, walk_words, pinned, rules, likes, form, leans, pace, fewer,
    quiet_hours, reset_at}; `likes` holds the wishes for now that have not run out (each with its `until`).
    Without `seed`, an owner text not written yet stays empty (the ranking's readers need only the rest)."""
    from .life import clock
    state = _state(runtime)
    lean = state.get("source_lean") if isinstance(state.get("source_lean"), dict) else {}
    now = clock()
    pace = state.get("pace") if state.get("pace") in PACES else "normal"
    return {"owner": str(state.get("owner") or "") or (_seed(runtime, state) if seed else ""),
            "character_learned": str(state.get("character_learned") or ""),
            "source_lean": {str(k): float(v) for k, v in lean.items() if isinstance(v, (int, float))},
            "walk_words": [str(w) for w in state.get("walk_words") or [] if isinstance(w, str)],
            "pinned": [p for p in state.get("pinned") or [] if isinstance(p, dict)],
            "rules": [r for r in state.get("rules") or [] if isinstance(r, dict)],
            "likes": [x for x in state.get("likes") or []
                      if isinstance(x, dict) and x.get("topic") and float(x.get("until") or now) >= now],
            "form": state.get("form") if isinstance(state.get("form"), dict) else {},
            "leans": state.get("leans") if isinstance(state.get("leans"), dict) else {},
            "pace": pace, "fewer": pace == "less",
            "quiet_hours": state.get("quiet_hours") if isinstance(state.get("quiet_hours"), dict) else None,
            "reset_at": float(state.get("reset_at") or 0)}


def rewritten(runtime: Path) -> bool:
    return bool(_state(runtime).get("at"))


def avoided(runtime: Path) -> list[str]:
    """The topics the owner said no to (the taste model gives each the AVOID_TOPIC prior)."""
    return [str(r["topic"]) for r in load(runtime, False)["rules"] if r.get("kind") == "topic" and r.get("topic")]


def hidden_sources(runtime: Path) -> set[str]:
    return {r["key"] for r in load(runtime, False)["rules"] if r.get("kind") == "hide_source"}


# ------------------------------------------------- what the person said in chat --

# A manner line asking for fewer messages, in the chat languages: the pacing and the remarks halve.
_FEWER = re.compile(
    r"少发|少發|别老发|別老發|别总发|別總發|别老找|別老找|别总找|別總找|少找我|别打扰|別打擾|少打扰|少打擾|"
    r"别烦我|別煩我|消息少|訊息少|"
    r"fewer messages|message me less|text me less|less often|not so often|don'?t message|stop messaging|"
    r"don'?t bother me|leave me alone|"
    r"控えめ|少なめ|送らないで|話しかけないで|邪魔しないで|덜 보내|연락 줄|방해하지|말 걸지|"
    r"реже|меньше сообщений|не беспокой|menos mensajes|no me molestes|con menos frecuencia|"
    r"menos mensagens|não me incomode|com menos frequência|moins de messages|ne me dérange pas|moins souvent|"
    r"weniger nachrichten|nicht so oft|stör mich nicht|seltener", re.I)
_VIDEO = re.compile(r"视频|視頻|影片|video|動画|동영상|видео|vídeo|vidéo", re.I)
_TEXT = re.compile(r"文字|文章|图文|圖文|text|article|記事|글|текст|стать|texto|artigo|artikel", re.I)
_SHORT = re.compile(r"短|short|corto|curto|court|kurz|корот|짧", re.I)
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
# A like said for now, not for good: "this week", "today", "these days", in the chat languages.
_PASSING = re.compile(
    r"这周|這週|这星期|這星期|本周|本週|这礼拜|這禮拜|今天|今晚|这几天|這幾天|这两天|這兩天|这阵子|這陣子|这周末|這週末|暂时|暫時|"
    r"this week|today|tonight|these days|this weekend|for now|for a few days|right now|"
    r"今週|今日|今夜|この数日|しばらく|이번 주|오늘|요즘 며칠|이번 주말|당분간|"
    r"на этой неделе|сегодня|в эти дни|пока что|esta semana|hoy|estos días|este fin de semana|por ahora|"
    r"nesta semana|hoje|estes dias|neste fim de semana|por enquanto|cette semaine|aujourd'hui|ces jours-ci|"
    r"ce week-end|pour l'instant|diese woche|heute|in diesen tagen|dieses wochenende|vorerst", re.I)
ABOUT = ("topic", "source", "kind", "length")


def _clip(text, limit: int = SAID_LEN) -> str:
    return " ".join(str(text or "").split())[:limit]


def _walk_len(word: str) -> int:
    return WALK_LEN if _CJK.search(word) else WALK_LEN_LATIN


def _kind(word) -> Optional[str]:
    """"video" or "text" from the person's word for a form; None when it names neither."""
    word = str(word or "")
    return "video" if _VIDEO.search(word) else "text" if _TEXT.search(word) else None


def _said(runtime: Path, state: dict, now: float) -> None:
    """Keep a change the person made in the chat: the ranking refits on it at once, and the rewrite follows
    within SAID_GAP_S."""
    from . import taste
    state["said_at"] = now
    _save(runtime, state)
    taste.fit(runtime, soon=True)


def like(runtime: Path, what: str, now: float, *, words: str = "") -> None:
    """A topic the person said they like (`words`: what they said): a prior the ranking reads at once
    (LIKE_TOPIC) and, when short, a walk's search words. A rule against the same topic goes: they changed
    their mind. Said for now ("这周想多看点 F1"), it is a wish that runs out after WISH_S, takes no walk word
    and leaves a lasting like of the same topic as it is."""
    passing = bool(_PASSING.search(f"{what} {words}"))
    if passing:
        # "这周想多看点 F1": the thing without the time words.
        from .person import topic_of
        what = topic_of(_PASSING.sub(" ", str(what or "")).strip(" ，,"))
    topic = _clip(what, WORD_LEN)
    if not topic:
        return
    key = topic.casefold()
    state = _state(runtime)
    rows = [x for x in state.get("likes") or [] if isinstance(x, dict)]
    if passing and any(str(x.get("topic")).casefold() == key and not x.get("until") for x in rows):
        return
    likes = [x for x in rows if str(x.get("topic")).casefold() != key]
    said = {"topic": topic, "words": _clip(words), "at": now, **({"until": now + WISH_S} if passing else {})}
    state["likes"] = (likes + [said])[-LIKES_MAX:]
    state["rules"] = [r for r in state.get("rules") or []
                      if not (r.get("kind") == "topic" and str(r.get("topic")).casefold() == key)]
    if not passing and len(topic) <= _walk_len(topic):
        state["walk_words"] = ([topic] + [w for w in state.get("walk_words") or []
                                          if str(w).casefold() != key])[:WORDS_MAX]
    _said(runtime, state, now)


def dislike(runtime: Path, what: str, about: str, now: float, *, words: str = "") -> None:
    """Something the person said they do not want, `about` one of ABOUT. A topic is a rule the ranking obeys
    at once (AVOID_TOPIC); a source hides the catalogue sources of that name, or is a rule on the name where
    none has it; a kind or a length is a wish for the other (`form`). A like of the same topic goes, and so
    does any walk word holding it."""
    what = _clip(what, WORD_LEN)
    if not what:
        return
    if about == "kind" and _kind(what):
        return form(runtime, None, "text" if _kind(what) == "video" else "video", now, words=words or what)
    if about == "length":
        # "Too long" wants short things; "too short", long ones.
        return form(runtime, not _SHORT.search(what), None, now, words=words or what)
    key = what.casefold()
    said = {"by": "chat", "words": _clip(words), "at": now}
    new = []
    if about == "source":
        new = [{"kind": "hide_source", "key": k, **said} for k, source in readable_sources(runtime).items()
               if key == k.casefold() or any(key in name.casefold() for name in source.name)]
    new = new or [{"kind": "topic", "topic": key, **said}]
    state = _state(runtime)
    named = {(r.get("kind"), r.get("key"), r.get("topic")) for r in new}
    state["rules"] = ([r for r in state.get("rules") or []
                       if (r.get("kind"), r.get("key"), r.get("topic")) not in named] + new)[-RULES_MAX:]
    state["likes"] = [x for x in state.get("likes") or [] if str(x.get("topic")).casefold() != key]
    # Nor a walk word inside it: walking for "动漫" brings back the 动漫解说 they said no to.
    state["walk_words"] = [w for w in state.get("walk_words") or []
                           if key not in str(w).casefold() and str(w).casefold() not in key]
    _said(runtime, state, now)


def form(runtime: Path, short: Optional[bool], kind: Optional[str], now: float, *, words: str = "") -> None:
    """A wish about length (`short`: True for short things, False for long ones) and form (`kind`: "video"
    or "text", or the person's word for one); None leaves that part as it was. The ranking's length and
    kind terms follow it at once (FORM_AGAINST on the other)."""
    state = _state(runtime)
    was = state.get("form") if isinstance(state.get("form"), dict) else {}
    state["form"] = {"short": was.get("short") if short is None else bool(short),
                     "kind": was.get("kind") if kind is None else _kind(kind),
                     "words": _clip(words) or str(was.get("words") or ""), "at": now}
    _said(runtime, state, now)


def manner(runtime: Path, text: str, now: float) -> None:
    """How the person wants to be treated, in their words ("叫我阿杰，别叫鱼片", "忙时别老发消息"): a pinned
    line every prompt that speaks to them reads (`manner_lines`). One asking for fewer messages sets the pace
    to "less", as Settings would: the pacing and the remarks bring half as much (`load()["fewer"]`)."""
    line = _clip(text, LINE_MAX)
    if not line:
        return
    state = _state(runtime)
    pinned = [p for p in state.get("pinned") or [] if isinstance(p, dict)]
    rows = [p for p in pinned if p.get("field") == "manner" and p["text"] != line]
    others = [p for p in pinned if p.get("field") != "manner"]
    state["pinned"] = (others + (rows + [{"field": "manner", "text": line, "at": now}])[-MANNER_MAX:])[-PINNED_MAX:]
    if _FEWER.search(line):
        state["pace"] = "less"
    _said(runtime, state, now)


def manner_lines(runtime: Path) -> list[str]:
    """How the person asked to be treated, oldest first."""
    return [str(p["text"]) for p in load(runtime, False)["pinned"] if p.get("field") == "manner"]


def manner_rule(runtime: Path) -> str:
    """The manner lines as a rule for a prompt that speaks to the person; "" when there are none."""
    lines = manner_lines(runtime)
    if not lines:
        return ""
    return ("How your person asked to be treated, in their own words (a later line wins over an earlier one). "
            "Follow it exactly: it comes before your persona's habits, such as what you call them.\n"
            + _fence_user_data("\n".join(f"- {line}" for line in lines)))


def liked(runtime: Path) -> list[str]:
    """The topics the person said they like, newest first."""
    return [str(x["topic"]) for x in reversed(load(runtime, False)["likes"])]


def told(runtime: Path) -> list[str]:
    """What the person said in the chat about their tastes, as lines for a prompt."""
    known = load(runtime, False)
    names: dict = {}
    lines = [("Wants more of {} for the next few days" if x.get("until") else "Likes {}").format(x["topic"])
             + (f" (their words: {x['words']})" if x.get("words") else "") for x in reversed(known["likes"])]
    for rule in known["rules"]:
        if rule.get("kind") == "topic":
            lines.append(f"Does not want {rule['topic']}"
                         + (f" (their words: {rule['words']})" if rule.get("words") else ""))
        elif rule.get("kind") == "hide_source" and rule.get("by") == "chat":
            names = names or {k: s.name[0] for k, s in readable_sources(runtime).items()}
            lines.append(f"Does not want things from {names.get(rule['key'], rule['key'])}")
    shape = known["form"]
    wants = [w for w in ({True: "short things", False: "longer things"}.get(shape.get("short"), ""),
                         {"video": "videos", "text": "things to read"}.get(shape.get("kind"), "")) if w]
    if wants:
        lines.append("Wants " + " and ".join(wants)
                     + (f" (their words: {shape['words']})" if shape.get("words") else ""))
    return lines


def unwanted(runtime: Path, words: str) -> bool:
    """Whether `words` hold a topic the person said they do not want."""
    words = str(words or "").casefold()
    return any(topic.casefold() in words for topic in avoided(runtime))


def walk_topics(runtime: Path) -> list[str]:
    """A walk's search words: the topics the person said they like, short enough, then the rewrite's walk
    words; never one they said they do not want."""
    words = [w for w in liked(runtime) if len(w) <= _walk_len(w)] + load(runtime, False)["walk_words"]
    return [w for w in dict.fromkeys(words) if not unwanted(runtime, w)]


def _matcher(tokens: list[str]) -> Optional[re.Pattern]:
    from .panels import feed
    words = {w for w in (" ".join(str(t or "").split()).casefold() for t in tokens) if len(w) >= 2}
    if not words:
        return None
    return re.compile("|".join(feed._word(w) for w in sorted(words, key=len, reverse=True)))


_SENTENCE = re.compile(r"(?<=[。！？!?；;])|(?<=\.)\s+")


def forget(runtime: Path, tokens: list[str]) -> list[str]:
    """Take out everything that holds one of `tokens` (the forgotten thing's content words; one character
    alone is too little to go by): likes, rules, the form wish, pinned and manner lines, walk words, the
    sentences of the owner text and of the free-text answer, the character's lines; and beyond this file,
    the person's words about finds in the feed and the search words a chat raised. What went, as it read,
    for the answer that names it."""
    from . import taste, today
    from .panels import feed
    match = _matcher(tokens)
    if match is None:
        return []
    runtime = Path(runtime)
    state = _state(runtime)
    gone: list[str] = []

    def hit(*texts) -> bool:
        return any(match.search(str(t or "").casefold()) for t in texts)

    def kept(rows: list, *keys) -> list:
        out = []
        for row in rows:
            if isinstance(row, dict) and hit(*(row.get(k) for k in keys)):
                gone.append(str(next((row[k] for k in keys if row.get(k)), "")))
            else:
                out.append(row)
        return out

    def sentences(text: str) -> str:
        lines = []
        for line in str(text).splitlines():
            parts = [part.strip() for part in _SENTENCE.split(line) if part.strip()]
            gone.extend(part for part in parts if hit(part))
            lines.append(("" if _CJK.search(line) else " ").join(part for part in parts if not hit(part)))
        return "\n".join(line for line in lines if line)

    state["likes"] = kept(state.get("likes") or [], "topic", "words")
    state["rules"] = kept(state.get("rules") or [], "topic", "key", "words")
    state["pinned"] = kept(state.get("pinned") or [], "text")
    if isinstance(state.get("form"), dict) and hit(state["form"].get("words")):
        gone.append(str(state.pop("form")["words"]))
    words = [str(w) for w in state.get("walk_words") or []]
    gone.extend(w for w in words if hit(w))
    state["walk_words"] = [w for w in words if not hit(w)]
    for field in ("owner", "character_learned", "free_text"):
        if state.get(field):
            state[field] = sentences(state[field])
    if gone:
        _save(runtime, state)
        taste.fit(runtime, soon=True)
    feed.forget_said(runtime, match)
    gone.extend(today.forget_wonders(runtime, match))
    return [g for g in dict.fromkeys(gone) if g]


# ------------------------------------------------------------------- the model --

class _Model(drafting._Client):
    """The configured model, outside the service; keeps the usage the last answer reported."""
    usage: dict = {}

    def _count_usage(self, model: str, body: dict) -> None:
        try:
            self.usage = body["usage"] if isinstance(body, dict) and isinstance(body.get("usage"), dict) else {}
            book = UsageBook(self._usage_path)
            book.add(model, self.usage, time.time(), _call_purpose.get())
            book.flush(time.time())
        except Exception:
            pass


def values_of(home: Path) -> dict:
    return {k: str(v or "") for k, v in dotenv_values(Path(home) / ".env").items()}


def character(home: Path, values: dict) -> tuple[str, str, str]:
    """(name, persona text, language) the way the page's own persona view reads them."""
    lang = langs.normalize(values.get("AGENT_LANG")) or "en"
    name = values.get("PERSONA_NAME") or sw.DEFAULT_NAMES.get(lang, "Nova")
    card = Path(home) / "persona.txt"
    text = card.read_text(encoding="utf-8") if card.is_file() else sw.persona_text(
        sw.CHARACTERS[0].key, name=name, lang=lang, admin_name=values.get("ADMIN_NAME", ""),
        admin_relationship=values.get("ADMIN_RELATIONSHIP", ""))
    return name, text.strip(), lang


def configured(values: dict) -> bool:
    return all(values.get(k) for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"))


def count(runtime: Path, kind: str, prompt: int, answer: int, now: float) -> None:
    """One call of `kind` ("judge", "profile" or "pick") and its tokens, in this month's row."""
    path = Path(runtime) / CALLS_FILE
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    saved = saved if isinstance(saved, dict) else {}
    month = saved.setdefault("months", {}).setdefault(time.strftime("%Y-%m", time.localtime(now)), {})
    row = month.setdefault(kind, {"calls": 0, "input": 0, "output": 0})
    row["calls"] += 1
    row["input"] += int(prompt)
    row["output"] += int(answer)
    Path(runtime).mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(saved, ensure_ascii=False))


def calls(runtime: Path) -> dict:
    """{month: {kind: {calls, input, output}}}, newest month first."""
    try:
        months = json.loads((Path(runtime) / CALLS_FILE).read_text(encoding="utf-8")).get("months") or {}
    except (OSError, ValueError, AttributeError):
        months = {}
    return dict(sorted(months.items(), reverse=True))


async def ask(runtime: Path, home: Path, values: dict, kind: str, system: str, user: str, *,
              max_tokens: int, now: float) -> str:
    """One JSON call to the configured model, counted; tokens as reported, else characters / 4."""
    client = _Model(values, drafting.usage_path(Path(home), values))
    with purpose("recommend" if kind == "pick" else "other"):
        answer = await client._call_llm(system, [{"role": "user", "content": user}], client.model,
                                        max_tokens=max_tokens, disable_thinking=True,
                                        temperature=0.4, json_object=True)
    usage = client.usage
    count(runtime, kind, usage.get("prompt_tokens") or (len(system) + len(user)) // 4,
          usage.get("completion_tokens") or len(answer or "") // 4, now)
    return answer


# ------------------------------------------------------------- what it reads --

def _owner_notes(runtime: Path) -> list[str]:
    from . import life
    rows = life.owner_notes(life.Sources.from_files(Path(runtime)))
    rows.sort(key=life._note_time, reverse=True)
    return [" ".join(str(r.get("text")).split()) for r in rows][:NOTES]


def readable_sources(runtime: Path) -> dict:
    """The public catalogue sources the owner can read and reach, by key."""
    from .sources import catalog
    readable = situation.readable(runtime)
    return {s.key: s for s in catalog.all_sources(catalog.load_choice(runtime)["own"]).values()
            if s.group != "own" and s.lang in readable and situation.gate(runtime, key=s.key)}


def _reactions(runtime: Path) -> list:
    from .panels import feed
    return [e for e in feed.events(runtime) if e[2] != "seen"]


def _history(runtime: Path) -> tuple[list[str], list[str], list[str]]:
    """Titles liked or opened, skipped or hidden (newest first), and where the character's judgement disagreed."""
    from .library import Library
    from .panels import feed
    # Nothing from before the person started their taste over.
    since = load(runtime, False)["reset_at"]
    events = feed.events(runtime, since)
    marks = feed._marks(events)[0]
    items = {item.id: item for item in Library(runtime).items()}
    liked, disliked, seen = [], [], {}
    for at, ident, action, *_ in reversed(events):
        item = items.get(ident)
        if item is None or ident in seen or action == "seen":
            continue
        seen[ident] = True
        mark = marks[ident]
        if mark["like"] or mark["open"]:
            liked.append(item.title)
        elif mark["skip"]:
            disliked.append(item.title)
    disagreed = []
    for ident in seen:
        item, mark = items[ident], marks[ident]
        love = getattr(item, "love", -1)
        if love < 0:
            continue
        said_yes, did_yes = item.fits or love >= 2, mark["like"] or mark["open"]
        if said_yes != did_yes and (did_yes or mark["skip"]):
            disagreed.append(f"{item.title} - you said love {love}, fits {str(item.fits).lower()}; "
                             f"the owner {'opened or liked it' if did_yes else 'skipped it'}")
    return liked[:LIKED], disliked[:DISLIKED], disagreed[:DISAGREED]


def _signature(runtime: Path) -> str:
    return json.dumps([situation.value(runtime, "region"), sorted(situation.readable(runtime))])


# ---------------------------------------------------------------- the rewrite --

_INSTRUCTION = (
    "You are {name}. Your persona follows; it is your own taste and it is never rewritten.\n{persona}\n\n"
    "Below is what you know of your owner. Rewrite your notes about them as JSON only:\n"
    '{{"owner": "<at most about 200 tokens: stable likes and dislikes, format and attention habits, '
    'and situation lines such as job, studies, exams, current project, travel>", '
    '"character_learned": "<at most 3 lines: things my owner showed me that I came to like>", '
    '"source_lean": {{"<source key>": <-1 to 1>}}, '
    '"walk_words": ["<3 to 5 search words for their interests and hobbies>"], '
    '"avoid_sources": ["<source key>"], "avoid_topics": ["<short topic>"]}}\n'
    "- Write owner and character_learned in {language}. Keep every pinned line exactly as written.\n"
    "- source_lean: only keys from the source list, and only where their situation or taste says more "
    "(+) or less (-) of that source; leave the rest out.\n"
    "- walk_words: words a search box on a video site would take, in the language they read; never their "
    "work, studies, thesis or anything private, and never what they do not want.\n"
    "- avoid_sources and avoid_topics: only what the owner wrote or said themselves that they do not want; "
    "never inferred from what they skipped. Empty lists are fine.\n"
    "- What they told you in the chat is theirs to say: follow it over anything older.\n\n"
    + _UNTRUSTED_INPUT_RULES)


def _prompt(runtime: Path, home: Path, values: dict, state: dict, now: float) -> tuple[str, str]:
    from .roam import fence_pages
    name, persona, lang = character(home, values)
    current = load(runtime)
    liked, disliked, disagreed = _history(runtime)
    sources = readable_sources(runtime)
    own_words = [p["text"] for p in current["pinned"]] + ([state["free_text"]] if state.get("free_text") else [])

    def block(title: str, lines: list[str], fence=_fence_user_data) -> str:
        return f"{title}:\n" + (fence("\n".join(f"- {x}" for x in lines)) if lines else "(none)")

    user = "\n\n".join([
        block("Your notes about the owner now", current["owner"].splitlines()),
        block("Things your owner showed you that you came to like", current["character_learned"].splitlines()),
        block("Pinned lines (keep verbatim)", [f"[{p['field']}] {p['text']}" for p in current["pinned"]]),
        block("What the owner wrote themselves", own_words),
        block("What they told you in the chat about their tastes", told(runtime)),
        block("What you remember of the owner", _owner_notes(runtime)),
        # Titles are the pages' own words.
        block("Recently opened or liked", liked, fence_pages),
        block("Recently skipped or hidden", disliked, fence_pages),
        block("Where your judgement and the owner's reaction disagreed", disagreed, fence_pages),
        "Their situation:\n" + (situation.notes(runtime, now) or "(unknown)"),
        "Sources:\n" + "\n".join(f"{s.key}: {s.about[0]}" for s in sources.values())])
    return _INSTRUCTION.format(name=name, persona=persona[:1500], language=langs.NAMES.get(lang, lang)), user


def _text(value, limit: int) -> str:
    return "\n".join(" ".join(line.split()) for line in str(value or "").splitlines() if line.strip())[:limit]


def _with_pinned(text: str, pinned: list[dict], field: str) -> str:
    """`text` with every pinned line of `field` in it, verbatim."""
    lines = text.splitlines()
    missing = [p["text"] for p in pinned if p["field"] == field and p["text"] not in lines]
    return "\n".join(missing + lines)


def apply(runtime: Path, answer: str, state: dict) -> dict:
    """The rewrite's answer into the saved state; unknown source keys and malformed fields are dropped."""
    value = json.loads(strip_json_fences(str(answer or "")))
    keys = set(readable_sources(runtime))
    pinned = load(runtime)["pinned"]
    owner = _with_pinned(_text(value.get("owner"), OWNER_MAX), pinned, "owner")
    learned = "\n".join(_with_pinned(_text(value.get("character_learned"), LEARNED_LINES * LINE_MAX),
                                     pinned, "character_learned").splitlines()[:LEARNED_LINES])
    lean = value.get("source_lean") if isinstance(value.get("source_lean"), dict) else {}
    words = [w for w in (" ".join(str(x).split())[:WORD_LEN] for x in value.get("walk_words") or []
                         if isinstance(x, str)) if w]
    rules = [{"kind": "hide_source", "key": k} for k in value.get("avoid_sources") or [] if k in keys]
    rules += [{"kind": "topic", "topic": t} for t in (" ".join(str(x).split())[:WORD_LEN].casefold()
                                                     for x in value.get("avoid_topics") or []
                                                     if isinstance(x, str)) if t]
    # Rules the owner set by hand or said in the chat stay; the rest follow from their own lines each time.
    own = [r for r in state.get("rules") or [] if r.get("by") in ("owner", "chat")]
    named = {(r.get("kind"), r.get("key"), r.get("topic")) for r in own}
    rules = own + [r for r in rules if (r.get("kind"), r.get("key"), r.get("topic")) not in named]
    # The topics they said they like lead the walk words; nothing they said no to is one.
    avoid = [r["topic"] for r in rules if r.get("kind") == "topic"]
    said = [w for w in (str(x["topic"]) for x in reversed(state.get("likes") or []) if not x.get("until"))
            if len(w) <= _walk_len(w)]
    words = [w for w in dict.fromkeys(said + words) if not any(t in w.casefold() or w.casefold() in t for t in avoid)]
    state.update(owner=owner or state.get("owner", ""), character_learned=learned,
                 source_lean={k: max(-1.0, min(1.0, float(v))) for k, v in lean.items()
                              if k in keys and isinstance(v, (int, float)) and not isinstance(v, bool)},
                 walk_words=words[:WORDS_MAX] if len(words) >= WORDS_MIN else state.get("walk_words", []),
                 rules=rules[:RULES_MAX])
    return state


async def rewrite(runtime: Path, home: Optional[Path] = None, now: Optional[float] = None) -> bool:
    """One rewrite call now; False when no model is set or the answer could not be used."""
    runtime = Path(runtime)
    home = runtime.parent if home is None else Path(home)
    now = time.time() if now is None else now
    values = values_of(home)
    if not configured(values):
        return False
    # Reading the feed, the library and the catalogue is slow work: off the app's loop.
    state = await asyncio.to_thread(_state, runtime)
    system, user = await asyncio.to_thread(_prompt, runtime, home, values, state, now)
    try:
        answer = await ask(runtime, home, values, "profile", system, user, max_tokens=1200, now=now)
        # Read again: what they said in the chat while the model was writing must not be lost.
        state = await asyncio.to_thread(lambda: apply(runtime, answer, _state(runtime)))
    except Exception as exc:
        logger.warning("profile: the rewrite did not come back usable (%s)", type(exc).__name__)
        return False
    await asyncio.to_thread(_finish, runtime, state, now)
    return True


def _finish(runtime: Path, state: dict, now: float) -> None:
    state.update(at=now, day=date.fromtimestamp(now).isoformat(), reactions=len(_reactions(runtime)),
                 signature=_signature(runtime))
    _save(runtime, state)


def due(runtime: Path, now: float) -> bool:
    """A rewrite of its own: within SAID_GAP_S of something the person said in the chat, without waiting for
    reactions; else at most once a day, after enough reactions or a changed situation."""
    state = _state(runtime)
    last = float(state.get("at") or 0)
    if float(state.get("said_at") or 0) > last and now - last >= SAID_GAP_S:
        return True
    if state.get("day") == date.fromtimestamp(now).isoformat():
        return False
    signature = _signature(runtime)
    if "signature" not in state:
        # The situation as first seen is where it starts, not a change; the first week counts from here.
        _save(runtime, {**state, "signature": signature, "since": now})
        return False
    new = len(_reactions(runtime)) - int(state.get("reactions") or 0)
    return (new >= REWRITE_REACTIONS or signature != state["signature"]
            or (new >= WEEK_REACTIONS and now - float(state.get("at") or state.get("since") or now) >= WEEK_S))


async def refresh(runtime: Path, home: Optional[Path] = None, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    return await asyncio.to_thread(due, runtime, now) and await rewrite(runtime, home, now)


# ---------------------------------------------------------- the owner's edits --

def _lines(text: str) -> list[str]:
    return _text(text, 10_000).splitlines()


async def answer(runtime: Path, home: Path, text: str, now: Optional[float] = None) -> bool:
    """The first run's free-text answer: kept, and one rewrite at once."""
    text = " ".join(str(text or "").split())
    if not text or len(text) > FREE_MAX:
        raise ValueError("profile_invalid")
    state = _state(runtime)
    _save(runtime, {**state, "free_text": text, "edits": (state.get("edits") or [])[-EDITS_MAX:]
                    + [{"at": time.time() if now is None else now, "field": "free_text"}]})
    return await rewrite(runtime, home, now)


def edit(runtime: Path, payload: dict, now: Optional[float] = None) -> bool:
    """The owner's own changes. A line they add to a text is pinned; a removed pinned line is unpinned.
    Returns whether a text changed, so the caller can ask for a rewrite that turns a "not this" into a rule."""
    now = time.time() if now is None else now
    state = _state(runtime)
    current = load(runtime)
    pinned = current["pinned"]
    changed = False
    for field in FIELDS:
        if field not in payload:
            continue
        if not isinstance(payload[field], str) or len(payload[field]) > OWNER_MAX * 2:
            raise ValueError("profile_invalid")
        lines = _lines(payload[field])
        if field == "character_learned":
            lines = lines[:LEARNED_LINES]
        added = [line[:LINE_MAX] for line in lines if line not in current[field].splitlines()]
        pinned = [p for p in pinned if p["field"] != field or p["text"] in lines] + [
            {"field": field, "text": line} for line in added]
        state[field] = "\n".join(lines)
        changed = changed or bool(added) or lines != current[field].splitlines()
    if "pinned" in payload:
        rows = payload["pinned"]
        if not isinstance(rows, list) or not all(isinstance(p, dict) and p.get("field") in (*FIELDS, "manner")
                                                 and isinstance(p.get("text"), str) for p in rows):
            raise ValueError("profile_invalid")
        pinned = [{"field": p["field"], "text": p["text"][:LINE_MAX]} for p in rows]
    if "likes" in payload:
        likes = payload["likes"]
        if not isinstance(likes, list) or not all(isinstance(t, str) for t in likes):
            raise ValueError("profile_invalid")
        # A like already there keeps the words it was said in.
        before = {str(x["topic"]).casefold(): x for x in current["likes"]}
        state["likes"] = [before.get(t.casefold()) or {"topic": t, "words": "", "at": now}
                          for t in dict.fromkeys(_clip(x, WORD_LEN) for x in likes) if t][-LIKES_MAX:]
    if "form" in payload:
        shape = payload["form"]
        if shape is None:
            state.pop("form", None)
        elif (not isinstance(shape, dict) or shape.get("short") not in (True, False, None)
              or shape.get("kind") not in ("video", "text", None)):
            raise ValueError("profile_invalid")
        else:
            state["form"] = {**current["form"], "short": shape.get("short"), "kind": shape.get("kind"), "at": now}
    if "source_lean" in payload:
        lean = payload["source_lean"]
        if not isinstance(lean, dict) or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                 for v in lean.values()):
            raise ValueError("profile_invalid")
        keys = set(readable_sources(runtime))
        state["source_lean"] = {k: max(-1.0, min(1.0, float(v))) for k, v in lean.items() if k in keys}
    if "walk_words" in payload:
        words = payload["walk_words"]
        if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
            raise ValueError("profile_invalid")
        state["walk_words"] = [w for w in (" ".join(x.split())[:WORD_LEN] for x in words) if w][:WORDS_MAX]
    if "rules" in payload:
        rules = payload["rules"]
        if not isinstance(rules, list) or not all(
                isinstance(r, dict) and ((r.get("kind") == "hide_source" and isinstance(r.get("key"), str))
                                         or (r.get("kind") == "topic" and isinstance(r.get("topic"), str)))
                for r in rules):
            raise ValueError("profile_invalid")
        # A rule already there keeps who made it, so removing one rule does not make the rewrite's rules the owner's.
        before = {(r.get("kind"), r.get("key"), r.get("topic")): r for r in current["rules"]}
        state["rules"] = [before.get((r.get("kind"), r.get("key"), r.get("topic")))
                          or {**{k: r[k] for k in ("kind", "key", "topic") if k in r}, "by": "owner"}
                          for r in rules][:RULES_MAX]
    if "leans" in payload:
        # {feature: 1 | -1 | 0 | None}: merged into the leans there are; None takes one out.
        leans = payload["leans"]
        if not isinstance(leans, dict) or not all(
                isinstance(k, str) and k.partition(":")[0] in ("topic", "like", "subj", "origin", "kind")
                and len(k) <= 120 and (v is None or v in LEANS and not isinstance(v, bool)) for k, v in leans.items()):
            raise ValueError("profile_invalid")
        state["leans"] = {k: v for k, v in {**current["leans"], **leans}.items() if v is not None}
    if "pace" in payload:
        if payload["pace"] not in PACES:
            raise ValueError("profile_invalid")
        state["pace"] = payload["pace"]
    if "quiet_hours" in payload:
        # {"from": "23:00", "to": "08:00"}, or None for none.
        hours = payload["quiet_hours"]
        if hours is not None and not (isinstance(hours, dict) and all(
                isinstance(hours.get(k), str) and _CLOCK.fullmatch(hours[k]) for k in ("from", "to"))
                and hours["from"] != hours["to"]):
            raise ValueError("profile_invalid")
        state["quiet_hours"] = hours and {"from": hours["from"], "to": hours["to"]}
    state["pinned"] = pinned[-PINNED_MAX:]
    # Every edit is a sign that inference or the rewrite was wrong; kept to look back on.
    state["edits"] = (state.get("edits") or [])[-EDITS_MAX:] + [
        {"at": now, "field": f} for f in ("owner", "character_learned", "source_lean", "walk_words", "rules",
                                          "pinned", "likes", "form", "leans") if f in payload]
    _save(runtime, state)
    if {"rules", "likes", "form", "leans"} & set(payload):
        from . import taste
        taste.fit(runtime, soon=True)
    return changed


def reset(runtime: Path, now: float) -> None:
    """Start the person's taste over: what their reactions taught (taste.train counts none from before
    `reset_at`), what they said of their tastes in the chat or wrote themselves, the leans, the rewrite's
    notes, rules, source leans and walk words. The picked topics, the pace, the quiet hours, how they asked
    to be treated and the character's own notes stay."""
    from . import taste
    state = _state(runtime)
    for field in ("owner", "free_text", "likes", "rules", "form", "leans", "source_lean", "walk_words", "at"):
        state.pop(field, None)
    state["pinned"] = [p for p in state.get("pinned") or [] if isinstance(p, dict) and p.get("field") != "owner"]
    state.update(reset_at=now, said_at=now)
    _save(runtime, state)
    taste.reset(runtime)

"""The one thing the character brings next, chosen by its own model.

When a recommendation is due (pacing.py says it is a moment for the pet's
card or the main chat's share step), `Chooser` takes the ranking's best
CANDIDATES unseen things, text and video both where the library has both, none
from the source or on a topic of the last one where there is a choice, and asks the person's model which one is for them
now, with one sentence in the character's voice on why. The model is given the
candidates in a shuffled order and what the person did with the last few
things brought (`lately`), so it reads their taste from the things themselves;
where its choice stood in the ranking goes to shown.jsonl. What the sentence says
of the person is a part of its own (`tie`) that must name what it rests on, an
interest the ranking matched the thing to or an earlier thing they opened or
liked, and is dropped when it does not (`grounds`, `parse`). One small call per
thing shown: the choice is kept until a surface shows it or it is STALE_S old,
both surfaces draw from it, and nothing is asked while quiet, while a choice
waits or with nothing to choose from. The next choice is made as soon as one is
shown, so it is there when the person asks for another; asked with none ready,
the ranking's first goes out at once and its sentence follows (`Chooser.line`).
Without a model, or on an answer it cannot use, the ranking's top thing goes
with no line. The calls are counted with the character's others
(`profile.count`, "pick").

Each thing goes out with the chip the card shows under its site (`Why`): the
ranking's own reason for it where the thing's words and the person's record
bear that out, else a weaker reason that is true, else none.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import re
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from . import drafting, life, profile, quiet, subjects
from .library import DISMISSED, Item, Library
from .roam import _manner, fence_pages, knowing
from .textproc import _UNTRUSTED_INPUT_RULES, _fence_user_data, salvage_json_object, strip_json_fences

logger = logging.getLogger("mibo.recommend")

CANDIDATES = 6
# How many of the last things brought the model is shown, each with what the person did with it.
LATELY = 12
# How far down the ranking the candidates may come from, when the best are kept apart from the last one.
POOL = 30
STALE_S = 3 * 3600
# After a look found nothing to choose from, the next waits this long, however often the surfaces ask.
EMPTY_S = 30
MAX_TOKENS = 240
SUMMARY_MAX = 300
# The line's length in characters: a sentence of CJK carries about twice what one of Latin letters does.
LINE_MAX, LINE_MAX_CJK = 160, 80
CJK = ("zh", "zh-TW", "ja", "ko")

_SYSTEM = """You are {name}. This is your persona:
<<<
{persona}
>>>
You live on your person's computer desktop and now and then bring them one thing you found on your walks. Below are a few you found, in no order; pick the one that is most for them right now, and tell them in one sentence why this one is for them.

- Answer JSON only: {{"id": "<the id of the thing you picked>", "line": "<your sentence on the thing>", "tie": "<a short clause that links it to them, or empty>", "from": "<what the tie rests on, or empty>"}}
- line and tie in {language} whatever language the thing is in, in your own voice as the persona describes it, said as you would say it aloud to them, together at most {limit} characters.
- Which one: a thing whose `theirs` line names something they told you in the chat comes first (when none does, what they said does not decide); then what they did with the last things you brought (more of the kind they opened or liked, less of the kind they closed or left; not one more on a subject they have just had several of); then the topics they ticked. Never pick a thing on what they said they do not want.
- line: the one thing in it that is worth their time, taken from its title and summary only; with no summary, only what the title says. Concrete: not a label of what kind of piece it is, not a summary of it, no generic praise. It says nothing about your person: none of their interests, habits or doings.
- tie: empty unless one of these holds, and then `from` carries the proof. (a) The picked thing's `theirs` line names an interest of theirs: `from` is that interest, copied. (b) The list of earlier things shows they opened or liked one on the same subject: `from` is that thing's title, copied. A tie whose `from` is neither is thrown away, so do not write one. Word the tie in your own way, as what you would add when handing it to them. An interest is something they care for: never say that they ticked, chose or mentioned it.
- Never make up an interest, a habit, something they said or did, or something they are busy with, and never mention what they closed or left unopened. A ticked topic is a box they ticked once, not something they talk about.
- Plain text only: no links, no markdown, no emoji, no quotation marks, no name tag.
- Never say how you know what they like or do.

"""


def limit(lang: str) -> int:
    return LINE_MAX_CJK if lang in CJK else LINE_MAX


def _settled(runtime) -> bool:
    """The person said which form they want, or their reactions have shown it beyond doubt: the other
    form need not be among the candidates."""
    from . import taste
    if profile.load(runtime, False)["form"].get("kind"):
        return True
    model = taste.load(runtime)
    m, q = model["m"], model["q"]
    if "kind:video" not in m:
        return False
    text = max(("kind:text", "kind:page"), key=lambda k: q.get(k, 0.0))
    spread = math.sqrt(1 / q["kind:video"] + 1 / q.get(text, 1.0))
    return abs(m["kind:video"] - m.get(text, 0.0)) > 2 * spread


def candidates(library, events: list, now: float, topics: list[str], here: set[str]) -> list:
    """The ranking's best CANDIDATES unseen things from the last week, not told and not in the main chat
    already, as (shaped, item). None shares a source or a topic with the last thing brought where others
    are left; failing that, a source. With text and video both there, both are among them until the
    person has said or shown which they take to."""
    from .panels import feed
    pool = []
    for shaped, item in feed.ranked(library, events, now, topics, seed=int(now * 1000)):
        if shaped["seen"] or len(pool) >= POOL:
            break
        if item.id not in here and not item.told and item.seen_at >= now - life.TELL_DAYS * 86400:
            pool.append((shaped, item))
    veins = feed.Veins(topics)
    last = veins.last(library, events)
    apart = ([row for row in pool if not veins.same(veins.of(*row), last)]
             or [row for row in pool if not veins.same(veins.of(*row), last, False)] or pool)
    rows = apart[:CANDIDATES]
    if rows and not _settled(library.root.parent):
        kinds = {feed.is_video(item, shaped) for shaped, item in rows}
        other = next((row for row in apart[CANDIDATES:] if feed.is_video(row[1], row[0]) not in kinds), None)
        if len(kinds) == 1 and other:
            rows[-1] = other
    return rows


# How the last thing brought ended, in the prompt's words; an ending that says nothing is left out.
_ENDED = {"open": "opened", "like": "liked", "quick": "closed at once", "close": "closed", "timeout": "left unopened"}


def _lately(runtime, now: float) -> list[tuple]:
    """(what they did, title, site, kind, how long ago) of the last LATELY things the card or the chat
    brought, newest first."""
    from .panels import feed
    events = feed._load(Path(runtime) / feed.TASTE_FILE)
    marks = feed._marks(events)[0]
    library = Library(runtime)
    lines, named = [], set()
    for at, ident, action, *rest in reversed(events):
        if len(lines) >= LATELY:
            break
        extra = rest[2] if len(rest) > 2 else {}
        if action != "seen" or extra.get("s") not in ("card", "chat") or ident in named:
            continue
        named.add(ident)
        mark = marks[ident]
        did = ("liked" if mark["like"] else "opened" if mark["open"] or mark["play"]
               else "turned down" if mark["skip"] and not mark["end"] else _ENDED.get(mark["end"], ""))
        item = library.get(ident)
        shaped = feed.shape(item, {}) if item else None
        if did and shaped:
            lines.append((did, shaped["title"][:70], shaped["site"], shaped["kind"], _ago(now - at)))
    return lines


def lately(runtime, now: float) -> str:
    """What the person did with the last things brought, as lines for the prompt: the model reads what they
    open from the things themselves."""
    return "\n".join("- " + " | ".join(row) for row in _lately(runtime, now))


def grounds(runtime, topics: list[str], rows: list, now: float, lang: str) -> dict:
    """What a sentence may say of the person: per candidate id, the ticked topics (by their names in `lang`)
    and said likes the ranking matched that thing to (a subject only guessed from its source is good enough
    to rank by, not to tell the person); under "", the titles of the earlier things they opened or liked."""
    from . import taste
    reader = taste.Reader(runtime, topics)
    spelled = {t.casefold(): t for t in topics}
    found = {"": [title for did, title, *_ in _lately(runtime, now) if did in ("opened", "liked")]}
    for shaped, item in rows:
        snap = reader.snapshot(item, shaped, now)
        names = [taste._topic_name(spelled[topic], lang) for topic in snap["topics"]] + snap["likes"]
        found[shaped["id"]] = [] if snap["guess"] else list(dict.fromkeys(names))
    return found


def prompt(runtime, name: str, persona: str, lang: str, topics: list[str], rows: list, now: float,
           found: Optional[dict] = None) -> tuple[str, str]:
    """`found` is `grounds` of these rows when the caller keeps it to check the answer against."""
    found = grounds(runtime, topics, rows, now, lang) if found is None else found
    # How they asked to be treated goes after the format: their words are theirs, braces and all.
    system = (_SYSTEM.format(name=name, persona=persona[:1500], limit=limit(lang),
                             language=drafting.LANGUAGE_NAMES.get(lang, "English"))
              + _manner(runtime) + _UNTRUSTED_INPUT_RULES)
    # In another order each time: a model leans to the first of a list (Hou et al. 2024).
    mixed = random.Random(int(now)).sample(rows, len(rows))
    things = "\n\n".join(
        f"id: {shaped['id']}\ntitle: {shaped['title']}\nsite: {shaped['site']}\nkind: {shaped['kind']}"
        f"\ntheirs: {', '.join(found[shaped['id']]) or 'none'}"
        + (f"\nsummary: {' '.join(shaped['text'].split())[:SUMMARY_MAX]}" if shaped["text"] else "")
        for shaped, _ in mixed)
    brought = lately(runtime, now)
    user = "\n\n".join([
        knowing(runtime, now),
        "Topics they ticked when they set you up (ticked once, not said):\n"
        + (_fence_user_data(", ".join(topics)) if topics else "(none)"),
        # Titles and summaries are the pages' own words.
        "What they did with the last things you brought, newest first:\n"
        + (fence_pages(brought) if brought else "(nothing brought yet)"),
        "The things:\n" + fence_pages(things)])
    return system, user


_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_MARKUP = re.compile(r"[*_`#>|~<>\[\]]")


def clean_line(text, lang: str) -> str:
    """The model's sentence as plain text on one line, clipped to the language's limit."""
    line = _MARKUP.sub("", _URL.sub("", _LINK.sub(r"\1", str(text or ""))))
    line = " ".join("".join(c for c in line if c.isprintable() or c.isspace()).split())
    line = line.strip(" \"'“”‘’「」『』«»")
    most = limit(lang)
    return line if len(line) <= most else line[:most - 1].rstrip() + "…"


# A `from` shorter than this cannot tell one earlier title from another.
FROM_MIN = 4


def backed(source, chosen: str, found: dict) -> bool:
    """Whether a tie's `from` names an interest the picked thing is about or an earlier thing they took."""
    source = " ".join(str(source or "").casefold().split())
    if not source:
        return False
    if source in (name.casefold() for name in found.get(chosen, ())):
        return True
    return len(source) >= FROM_MIN and any(source[:30] in " ".join(title.casefold().split())
                                           for title in found.get("", ()))


def parse(answer: str, rows: list, lang: str, found: Optional[dict] = None) -> Optional[tuple[dict, Item, str]]:
    """The candidate the answer named, with its sentence; None when the answer is not that. The tie is
    kept when `found` (`grounds`) backs what it says it rests on and both fit the line's length."""
    value = salvage_json_object(strip_json_fences(str(answer or "")))
    if not isinstance(value, dict):
        return None
    chosen = next((row for row in rows if row[0]["id"] == str(value.get("id") or "")), None)
    line = clean_line(value.get("line"), lang) if isinstance(value.get("line"), str) else ""
    if not (chosen and line):
        return None
    tie = clean_line(value.get("tie"), lang) if isinstance(value.get("tie"), str) else ""
    if tie and found and backed(value.get("from"), chosen[0]["id"], found):
        stop = line[-1] in "。！？…，.!?,"
        both = line + (("" if stop else "，") if lang in CJK else (" " if stop else ", ")) + tie
        if len(both) <= limit(lang):
            line = both
    return (*chosen, line)


# ------------------------------------------------------------ why this one

# The chip under a card's site, in the page's language (zh and en; the language pass adds the rest).
WHY = {
    "en": {"picked": "Because you picked {name}", "said": "Because you mentioned {name}",
           "subject": "You often open {name}", "often": "You often open {name}",
           "opened": "You opened {name} before", "about": "Topic: {name}", "new": "New find",
           "from": "From {name}"},
    "zh": {"picked": "你选择的主题：{name}", "said": "你提到过：{name}", "subject": "你常看：{name}",
           "often": "你常打开：{name}", "opened": "你打开过：{name}", "about": "主题：{name}", "new": "新发现",
           "from": "来源：{name}"},
}
# A thing's words carry a subject when they name it OWN times (a word in the title counts twice) and twice
# as often as any other. The word lists' plain reading (subjects.of) is wrong for about a quarter of the
# things it puts under a ticked topic, too often to tell a person "a topic you picked"; this one was a
# model's label for 95% of 322 such things, and leaves the rest to a weaker reason (tools/cardcheck).
OWN = 4
# "Often": this many things of the source or on the subject opened, played or liked.
OFTEN = 3
# A find is new for this long after it was found.
NEW_S = 2 * 86400


def own_subject(item: Item, shaped: dict) -> str:
    """The one subject a thing's own words carry beyond doubt, or ""."""
    found = subjects.mentioned(shaped["title"], " ".join((shaped["text"], shaped["site"], *map(str, item.tags))))
    first, *rest = sorted(found, key=lambda k: -found[k]) or [""]
    return first if first and found[first] >= OWN and (not rest or found[first] >= 2 * found[rest[0]]) else ""


class Why:
    """What a card may say of why it is here, for one moment's events and profile."""

    def __init__(self, runtime, topics: list[str], events: list, now: float, lang: str):
        from . import taste
        self.reader = taste.Reader(runtime, topics, events)
        self.now, self.lang = now, lang
        self.words = WHY["zh" if lang.startswith("zh") else "en"]
        # The person's own spelling of each topic they ticked and each like they said.
        self.topics = {t.casefold(): t for t in topics}
        self.likes = {t.casefold(): t for t in profile.liked(runtime)}
        self.leans = profile.load(runtime, False)["leans"]
        # How many things from each origin and on each subject they opened, played or liked.
        self.took: Counter = Counter()
        for h in taste.history(events).values():
            if h["f"] and (h["open"] or h["like"] or h["play"] > 0):
                self.took.update(["origin:" + h["f"]["origin"], *("subj:" + k for k in h["f"].get("subj", ()))])

    def _topic(self, word: str) -> str:
        from . import taste
        return taste._topic_name(word, self.lang) if word in subjects.OFFERED else self.topics[word]

    def of(self, shaped: dict, item: Item) -> dict:
        """{"why", "topic"} for the card. "why": {"text", "key", "lean"}, the chip and the feature a tap on
        it leans (with the lean it has now): every card has one. It is the ranking's reason (`reason_term`)
        only where true: a topic or a subject must be the one the thing's own words carry, a habit must be in
        the events; failing that the source when they opened things from it, then the subject its own words
        carry, that the find is new, and last where it is from.
        "topic": {"name", "key"}, what "less of this topic" would turn down, or None: the ticked topic or
        the like the ranking files the thing under (a no to it leans that very feature, so it names what
        would be turned down even where the thing is filed wrongly), else the subject its words carry."""
        own = own_subject(item, shaped)
        text = " ".join((shaped["title"], shaped["text"])).casefold()
        snap = self.reader.snapshot(item, shaped, self.now)

        def on(word: str) -> bool:
            # A word that stands for subjects holds when the thing's own subject is one of them; any other
            # when the thing's words have it. A typed phrase holds when the thing says it, or when it names
            # several subjects and the thing is on each, its own subject among them: one that names a
            # single subject beside words no list knows ("电影配乐") is not told on that subject alone.
            keys = subjects.named(word)
            if not keys:
                return word in text
            if subjects.whole(word):
                return own in keys
            return subjects.holds(word, set(), text) or (
                len(subjects.parts(word)) > 1 and own in keys and subjects.holds(word, {own, *snap["subj"]}, text))

        filed = [] if snap["guess"] else [("topic:" + t, self._topic(t)) for t in snap["topics"]] + [
            ("like:" + t, self.likes[t]) for t in snap["likes"]]
        topic = next(iter(filed), ("subj:" + own, subjects.name(own, self.lang)) if own else None)
        family, _, rest = shaped["reason_term"].partition(":")
        origin = "src:" + item.source if item.source else "site:" + shaped["site"]
        opened = self.took["origin:" + origin]
        site = shaped["site"].removeprefix("www.")
        if family == "topic" and on(rest):
            chip = "picked", self._topic(rest), shaped["reason_term"]
        elif family == "like" and on(rest):
            chip = "said", self.likes[rest], shaped["reason_term"]
        elif family == "subj" and rest == own and self.took[shaped["reason_term"]] >= OFTEN:
            chip = "subject", subjects.name(own, self.lang), shaped["reason_term"]
        elif family and opened:
            chip = "often" if opened >= OFTEN else "opened", site, "origin:" + origin
        elif own:
            chip = "about", subjects.name(own, self.lang), "subj:" + own
        elif self.now - item.seen_at < NEW_S:
            chip = "new", "", ""
        else:
            chip = "from", site, "origin:" + origin
        return {"why": {"text": self.words[chip[0]].format(name=chip[1]), "key": chip[2],
                        "lean": self.leans.get(chip[2])},
                "topic": topic and {"key": topic[0], "name": topic[1]}}

    def told(self, said: dict, item: Item, shaped: dict, name: str, title_of) -> str:
        """`facts` as the sentence the 推荐记录 list shows under a find; `title_of(id)` names an earlier find."""
        words = REASONS["zh" if self.lang.startswith("zh") else "en"]
        zh = int(self.lang.startswith("zh"))
        known = self.reader.sources.get(item.source)
        source = known.name[zh] if known else shaped["site"].removeprefix("www.")
        kind = said["k"]
        if kind == "browsed":
            return words["browsed"].format(site=source)
        if kind in ("topic", "like"):
            topic = self._topic(said["name"]) if kind == "topic" else self.likes.get(said["name"], said["name"])
            return words[kind].format(name=topic, src=source)
        if kind == "took":
            family, _, rest = said["on"].partition(":")
            text = words["took_" + ("subj" if family == "subj" else "origin")]
            return text.format(how=words["how"][said["how"]], title=title_of(said["from"]),
                               topic=subjects.name(rest, self.lang), src=source)
        if kind == "said":
            return words["said"].format(name=name, words=said["words"])
        # What it is, for a reason the ranking could not give: its subject beyond doubt, else what its source
        # says it brings, else its own tags, else its kind.
        own = own_subject(item, shaped)
        about = known.about[zh] if known else ""
        if own:
            what = words["what"].format(src=source, topic=subjects.name(own, self.lang))
        elif about:
            what = words["what_about"].format(src=source, about=about)
        else:
            what = words["what"].format(src=source, topic=str((item.tags or [words["kinds"][shaped["kind"]]])[0]))
        if kind == "try":
            return words["try_first" if said["first"] else "try"].format(what=what)
        return words["first_run" if said["first_run"] else "empty" if said["empty"] else "new"].format(what=what)


# The sentence under each find in the 推荐记录 list, in the page's language (zh and en; the language pass adds the
# rest). The character's own words (said) are its voice; the rest are the app's.
REASONS = {
    "en": {"browsed": "You opened this page yourself in the built-in browser ({site})",
           "topic": "On {name}, a topic you picked; from {src}",
           "like": "On {name}, which you said you like; from {src}",
           "took_subj": "You {how} “{title}”, also on {topic}",
           "took_origin": "You {how} “{title}” from {src} before",
           "how": {"like": "liked", "open": "opened", "play": "watched or read"},
           "said": "{name}: {words}",
           "what": "{topic} from {src}", "what_about": "{src} ({about})",
           "kinds": {"video": "video", "page": "article", "text": "article"},
           "try": "A deliberate try outside your usual: {what}",
           "try_first": "A deliberate try outside your usual: {what}, a kind you have not opened before",
           "first_run": "A sample for the first run, before any reading history: {what}",
           "empty": "No reading history to go on yet, so a recent find: {what}",
           "new": "Nothing in your reading points here yet, so a recent find: {what}"},
    "zh": {"browsed": "你在内置浏览器中自行打开了此页（{site}）",
           "topic": "符合你选择的主题「{name}」，来自{src}",
           "like": "与你表示喜欢的「{name}」相关，来自{src}",
           "took_subj": "你{how}过同属「{topic}」主题的《{title}》",
           "took_origin": "你此前{how}过{src}的《{title}》",
           "how": {"like": "喜欢", "open": "打开", "play": "看完"},
           "said": "{name}的推荐理由：{words}",
           "what": "{src}的「{topic}」内容", "what_about": "{src}（{about}）",
           "kinds": {"video": "视频", "page": "文章", "text": "文章"},
           "try": "有意尝试的新方向：{what}",
           "try_first": "有意尝试的新方向：{what}，此类内容你此前未打开过",
           "first_run": "首次使用时的示例推荐，尚无阅读记录：{what}",
           "empty": "尚无阅读记录可供参考，按新近程度推荐：{what}",
           "new": "与你的阅读记录暂无关联，按新近程度推荐：{what}"},
}
# The character's words in a reason, at most this many characters.
SAID_MAX = 160


def _took(before: dict, ident: str, term: str) -> Optional[dict]:
    """The latest earlier find the person liked, opened or played that shares `term` (a subject, or where it
    came from), as {"from", "how"}; None when there is none."""
    family, _, rest = term.partition(":")
    found = None
    for other, h in before.items():
        snap = h["f"] or {}
        if other == ident or not (h["like"] or h["open"] or h["play"] > 0):
            continue
        if rest in snap.get("subj", ()) if family == "subj" else snap.get("origin") == rest:
            if found is None or (h["acted"] or h["at"]) > (found[1]["acted"] or found[1]["at"]):
                found = other, h
    if found is None:
        return None
    return {"from": found[0], "how": "like" if found[1]["like"] else "play" if found[1]["play"] > 0 else "open"}


def facts(item: Item, snap: Optional[dict], slot: str, surface: str, term: str, line: str, before: dict) -> dict:
    """What a showing's reason rests on, kept with its "seen" event so the list can say it later (`Why.told`).
    `snap` is the thing as the ranking read it then, `slot` and `term` how the ranking served it (taste.Ranker),
    `line` the character's sentence when it brought it, and `before` the person's history until then
    (taste.history). In order: a page they opened themselves; a topic they picked or a like they said, that the
    ranking's reason names; an earlier find they took on the subject or from the source the reason names; the
    character's own words on it; a deliberate try outside their usual; else that nothing pointed here."""
    snap = snap or {}
    family, _, rest = term.partition(":")
    if surface == "browser":
        return {"k": "browsed"}
    if family in ("topic", "like") and rest in snap.get(family + "s", ()):
        return {"k": family, "name": rest}
    took = _took(before, item.id, term) if family in ("subj", "origin") else None
    if took:
        return {"k": "took", "on": term, **took}
    words = " ".join(str(line or item.why or "").split())
    if words:
        return {"k": "said", "words": words[:SAID_MAX]}
    taken = [h["f"] or {} for other, h in before.items() if other != item.id and (h["like"] or h["open"] or h["play"] > 0)]
    near = any(f.get("origin") == snap.get("origin") or set(f.get("subj", ())) & set(snap.get("subj", ()))
               for f in taken)
    if slot == "explore":
        return {"k": "try", "first": not near}
    return {"k": "new", "first_run": surface == "first", "empty": not taken}


def term_of(top: list, guess: bool) -> str:
    """The ranking's reason term from the top terms shown.jsonl kept for a showing, for one shown before the
    reason was kept with its event: the largest the person's reactions or picks could have lifted
    (taste.Ranker.reason, without the draw that is not kept)."""
    from .taste import REASON_MIN
    named = ("origin:",) if guess else ("origin:", "topic:", "like:", "subj:")
    best, key = REASON_MIN, ""
    for name, value in top:
        if name.startswith(named) and value > best:
            best, key = value, name
    return key


# ------------------------------------------------------------ from the chat

# The finds the reply model is told the main chat shared lately.
CHAT_FINDS_DAYS, CHAT_FINDS_MAX = 3, 5
# What a person's words about a find were about.
ABOUT = ("thing", "topic", "source", "kind", "length")
# What a wish says of the form it wants; the rest of its words are what it is about.
_SHORT = re.compile(r"短|简短|几分钟|short|quick|brief", re.I)
_LONG = re.compile(r"长一点|长的|深入|long|in-depth", re.I)
_TEXT = re.compile(r"文字|文章|图文|text|article|read", re.I)
_VIDEO = re.compile(r"视频|video|watch", re.I)
_CJK = re.compile(r"[㐀-鿿]")
_FILLER = re.compile(r"一点|一些|有没有|还有|其他|别的|推荐|来个|来点|好看|有趣|好玩|有意思|教程|入门|新手|纪录片|讲|的|个|点|吗|呢|"
                     r"\b(?:a|an|the|some|something|more|other|one|please|bit|of|about|how to|making|tutorials?|"
                     r"guides?|beginners?|documentar(?:y|ies))\b", re.I)


def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    return ("just now" if minutes < 1 else f"{minutes} min ago" if minutes < 60
            else f"{minutes // 60} h ago" if minutes < 1440 else f"{minutes // 1440} days ago")


def _size(item: Item, shaped: dict) -> str:
    from . import taste
    size = taste._length(item, shaped)
    if shaped["kind"] == "video" and item.length:
        return f"{round(item.length / 60)} min" + (f" ({size})" if size else "")
    return f"{size} text" if size else ""


def _did(mark: Optional[dict]) -> str:
    if not mark:
        return "no reaction yet"
    if mark["like"]:
        return "they liked it"
    if mark["skip"]:
        return "they turned it down"
    return "they opened it" if mark["open"] or mark["play"] else "no reaction yet"


def chat_finds(runtime, now: float) -> str:
    """The finds the main chat shared in the last CHAT_FINDS_DAYS, newest first, with what the person did
    with each: what "this" or a quoted card means to the reply model. "" when there are none."""
    from .panels import feed
    events = feed._load(Path(runtime) / feed.TASTE_FILE)
    marks = feed._marks(events)[0]
    library = Library(runtime)
    lines, named = [], set()
    for at, ident, action, *rest in reversed(events):
        if now - at > CHAT_FINDS_DAYS * 86400 or len(lines) >= CHAT_FINDS_MAX:
            break
        extra = rest[2] if len(rest) > 2 else {}
        if action != "seen" or extra.get("s") != "chat" or ident in named:
            continue
        named.add(ident)
        item = library.get(ident)
        shaped = feed.shape(item, {}) if item else None
        if shaped:
            facts = ", ".join(v for v in (shaped["kind"], _size(item, shaped)) if v)
            lines.append(f"- id {ident} | {shaped['title'][:80]} | {shaped['site']} | {facts} | "
                         f"shared {_ago(now - at)} | {_did(marks.get(ident))}")
    if not lines:
        return ""
    return ("<finds_in_this_chat>\nWhat you shared in this chat lately, newest first. \"This\" or a quoted "
            "card is one of these; the tools take its id. Asked for another one, call bring_find: never "
            "recommend from <your_day> or memory yourself.\n" + fence_pages("\n".join(lines))
            + "\n</finds_in_this_chat>")


def another(runtime, wish: str, avoid: list[dict], now: float) -> Optional[tuple[dict, Item]]:
    """One find to bring into the chat now: unseen and not told, not like what the person turned down in
    the way they said (`avoid`: {"id", "about"}) and, where there is a choice, not like the last find
    brought; of the form `wish` names, those on what its other words name first. None when the library
    has nothing of that form."""
    from . import taste
    from .panels import feed
    path = Path(runtime) / feed.TASTE_FILE
    events, topics = feed._load(path), feed.load_topics(path)
    library = Library(runtime)
    wish = " ".join(str(wish or "").split())[:80]
    kind = "video" if _VIDEO.search(wish) else "text" if _TEXT.search(wish) else ""
    size = "short" if _SHORT.search(wish) else "long" if _LONG.search(wish) else ""
    words = [w for w in _FILLER.sub(" ", _VIDEO.sub(" ", _TEXT.sub(" ", _LONG.sub(" ", _SHORT.sub(" ", wish)))))
             .casefold().split() if len(w) > 1]
    veins = feed.Veins(topics)
    hard, soft = [], []
    last = veins.last(library, events)
    if last:
        soft.append(lambda s, i: not veins.same(veins.of(s, i), last))
    for row in avoid:
        item = library.get(row["id"])
        shaped = feed.shape(item, {}) if item else None
        if shaped is None:
            continue
        vein, video, length = veins.of(shaped, item), feed.is_video(item, shaped), taste._length(item, shaped)
        about = row["about"]
        if about == "source":
            hard.append(lambda s, i, vein=vein: veins.of(s, i)[0] != vein[0])
        elif about == "kind":
            hard.append(lambda s, i, video=video: feed.is_video(i, s) != video)
        elif about == "length":
            hard.append(lambda s, i, length=length: taste._length(i, s) != length)
            # "Too long" wants a shorter one.
            size = size or ("short" if length == "long" else "")
        soft.append(lambda s, i, vein=vein: not veins.same(veins.of(s, i), vein))
    if kind:
        hard.append(lambda s, i: feed.is_video(i, s) == (kind == "video"))
    if size:
        hard.append(lambda s, i: taste._length(i, s) in (size, "mid" if size == "short" else size))
    rows = [(s, i) for s, i in feed.ranked(library, events, now, topics, seed=int(now * 1000))
            if not s["seen"] and not i.told and all(test(s, i) for test in hard)]
    # On what the wish names first; a wish is free words, so a miss still leaves the rest. Chinese has no
    # spaces, so a Chinese word is found when half its two-character pieces are ("做蛋糕" by "蛋糕", not
    # "蛋糕制作" by "制作"); an English word from its start, so "cat" is not found in "education".
    def found(word: str, text: str) -> bool:
        if not _CJK.search(word):
            return bool(re.search(r"\b" + re.escape(word), text))
        pieces = [word[n:n + 2] for n in range(len(word) - 1)]
        return 2 * sum(p in text for p in pieces) >= len(pieces)

    def on(row) -> bool:
        text = " ".join((row[0]["title"], row[0]["text"], row[1].description)).casefold()
        return any(found(w, text) for w in words)
    rows.sort(key=lambda row: not on(row))
    chosen = next((row for row in rows if all(test(*row) for test in soft)), rows[0] if rows else None)
    if chosen is not None:
        # Whether it is on what the wish names: the chat says a miss rather than pass it off (search.py).
        chosen[0]["on_wish"] = not words or on(chosen)
    return chosen


class Chooser:
    """The choice both the pet's card and the main chat bring, one at a time, the next one made as soon as
    one is shown."""

    def __init__(self, workspace):
        self.ws = workspace
        # {"at", "shaped", "item"}, until shown or stale.
        self.choice: Optional[dict] = None
        self._task: Optional[asyncio.Task] = None
        # The surfaces that asked while it was being made, told when it is ready.
        self._asked: set[str] = set()
        self._empty_at = -EMPTY_S
        # Goes up when the person says what they want less or more of: a choice under way is made again.
        self._turn = 0
        # What the chat took, kept out of the next choice until its showing is on record.
        self._taken = ""
        # The thing that went out before its sentence was written (`take` with `now`), as it was handed out,
        # and the call writing it.
        self._late: Optional[dict] = None
        self._writing: Optional[asyncio.Task] = None

    def _here(self) -> set[str]:
        return {m["card"]["id"] for m in self.ws._main.shared()} | {self._taken}

    def _unshown(self, choice: dict) -> bool:
        from .panels import feed
        if life.clock() - choice["at"] > STALE_S:
            return False
        item = self.ws._library().get(choice["item"].id)
        mark = feed._marks(feed._load(self.ws._runtime() / feed.TASTE_FILE))[0].get(choice["item"].id, {})
        return (item is not None and item.state != DISMISSED and not item.told and not mark.get("seen")
                and item.id not in self._here())

    def _look(self) -> dict:
        """The candidates now, with what choosing among them and dressing the choice need."""
        from .panels import feed
        runtime, values = self.ws._runtime(), self.ws.config()
        now = life.clock()
        path = runtime / feed.TASTE_FILE
        topics, events = feed.load_topics(path), feed._load(path)
        name, persona, lang = profile.character(self.ws.home, values)
        return {"runtime": runtime, "values": values, "now": now, "topics": topics, "events": events, "name": name,
                "persona": persona, "lang": lang,
                "rows": candidates(self.ws._library(), events, now, topics, self._here())}

    @staticmethod
    def _dress(seen: dict, shaped: dict, item: Item, line: str, more: bool = False) -> dict:
        """The thing as a surface is handed it: with the sentence, the chip (`Why`), and "more" while the
        sentence is still being written."""
        why = Why(seen["runtime"], seen["topics"], seen["events"], seen["now"], seen["lang"])
        return dict(shaped, line=line, more=more, **why.of(shaped, item))

    @staticmethod
    async def _cover(seen: dict, shaped: dict, item: Item, asked: bool = False) -> None:
        """A thing whose source gave no picture is shown with its page's own, as a link preview would;
        `asked`: the person is waiting for this one, so the page gets little time to answer."""
        if not item.picture:
            from .sources import catalog
            within = catalog.ASKED_COVER_S if asked else catalog.SHOWN_COVER_S
            item.picture = await catalog.cover_shown(seen["runtime"], item, within)
            shaped["picture"] = item.card()["picture"]

    def _ahead(self) -> None:
        if (self._task is None or self._task.done()) and time.monotonic() - self._empty_at >= EMPTY_S:
            self._task = asyncio.create_task(self._choose())

    async def take(self, surface: str, now: bool = False) -> Optional[tuple[dict, Item]]:
        """The choice when one waits that no surface has shown. Else, with `now` (the person asked for one),
        the ranking's first as soon as its picture is there, its sentence written after it (`line`); otherwise None, and one is
        chosen in the background, `surface` told when it is ready (the card through `news`, the chat by the
        share step)."""
        choice = self.choice
        if choice and await asyncio.to_thread(self._unshown, choice):
            # The chat always shows what it takes; the card may yet not, and says so when it does (`shown`).
            if surface == "chat":
                self.choice, self._taken = None, choice["item"].id
                self._ahead()
            return choice["shaped"], choice["item"]
        self.choice = None
        if now:
            seen = await asyncio.to_thread(self._look)
            if not seen["rows"]:
                return None
            row = seen["rows"][0]
            await self._cover(seen, *row, asked=True)
            writes = profile.configured(seen["values"])
            shaped = await asyncio.to_thread(self._dress, seen, *row, "", writes)
            self.choice = {"at": life.clock(), "shaped": shaped, "item": row[1]}
            self._late = shaped
            if writes:
                self._writing = asyncio.create_task(self._write(seen, row, shaped))
            return shaped, row[1]
        if time.monotonic() - self._empty_at < EMPTY_S:
            return None
        self._asked.add(surface)
        self._ahead()
        return None

    def shown(self, ident: str) -> None:
        """The card showed the choice: the next is chosen now, to be there when it is asked for."""
        if self.choice and self.choice["item"].id == ident:
            self.choice = None
        self._ahead()

    def again(self) -> None:
        """The person said what they want less or more of, or took a no back: the choice that waits or is
        under way was made before that, and is made again."""
        self._turn += 1
        self.choice = None
        self._ahead()

    def line(self, ident: str) -> dict:
        """The sentence of the thing that went out before it was written: {"line", "wait"}, `wait` while
        it is on its way."""
        late = self._late if self._late and self._late["id"] == ident else {"line": "", "more": False}
        return {"line": late["line"], "wait": late["more"]}

    async def _pick(self, seen: dict, rows: list) -> tuple[dict, Item, str]:
        """The model's choice among `rows` with its sentence; the first of them with none where there is no
        model or its answer cannot be used."""
        shaped, item = rows[0]
        line = ""
        runtime, values, now, lang = seen["runtime"], seen["values"], seen["now"], seen["lang"]
        if profile.configured(values):
            found = await asyncio.to_thread(grounds, runtime, seen["topics"], rows, now, lang)
            system, user = await asyncio.to_thread(prompt, runtime, seen["name"], seen["persona"], lang,
                                                   seen["topics"], rows, now, found)
            try:
                answer = await profile.ask(runtime, self.ws.home, values, "pick", system, user,
                                           max_tokens=MAX_TOKENS, now=now)
            except Exception as exc:
                logger.warning("the pick call failed: %s", exc)
                answer = ""
            picked = parse(answer, rows, lang, found)
            if picked:
                shaped, item, line = picked
                from . import taste
                # Where the model's choice stood in the ranking's order, for shown.jsonl.
                taste.served.get(item.id, {})["six"] = [row[1].id for row in rows].index(item.id)
        return shaped, item, line

    async def _write(self, seen: dict, row: tuple, shaped: dict) -> None:
        """The sentence for a thing already out: put on what was handed out, which the chat reads too when
        the card is opened there (feed.on_card)."""
        _, _, line = await self._pick(seen, [row])
        shaped.update(line=line, more=False)

    async def _choose(self) -> None:
        await quiet.wait()
        turn = self._turn
        seen = await asyncio.to_thread(self._look)
        if not seen["rows"]:
            self._asked.clear()
            self._empty_at = time.monotonic()
            return
        shaped, item, line = await self._pick(seen, seen["rows"])
        await self._cover(seen, shaped, item)
        choice ={"at": life.clock(), "shaped": await asyncio.to_thread(self._dress, seen, shaped, item, line),
                  "item": item}
        if turn != self._turn or not await asyncio.to_thread(self._unshown, choice):
            # Made before the person said what they want less or more of, or of a thing that went out
            # meanwhile (asked for with `now`): made again, from what is left.
            return await self._choose()
        self.choice = choice
        asked, self._asked = self._asked, set()
        if "chat" in asked:
            self.ws._share_wake.set()
        if "card" in asked:
            self.ws.news()

"""The reply model's way to the person's finds in a chat. bring_find lists the finds on what they asked
(recommend.matching), the model picks one for the card under its reply or none, and the last pick of the turn is the
card (dm.py); a page read this turn can be the card instead, and a wish can wait for a walk when they said yes to
that. react keeps what the person said of a find this chat showed, as the page records it in the feed
(welcome._chat_finds)."""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger("agent")

NAME, REACT = "bring_find", "react"
# What a person's words about a find were about.
ABOUT = ("thing", "topic", "source", "kind", "length")
# The one instruction for a wish no find fits: the same words in bring_find's description and the tool guide
# (prompts.OWNER_TOOL_GUIDE).
LOOK = ("When none of your finds is on what they asked, look it up now with web_search and read_page, and bring the "
        "page you read with bring_find url; never ask whether to look.")

TOOLS = [
    {"type": "function", "function": {
        "name": NAME,
        "description": (
            "Recommend one of your own finds, shown as a card under your reply: when they ask for something to "
            "watch or read, or for another one. Without pick it lists your finds for the wish; then call it again "
            "with pick, the id of one that is on what they asked (its subject, not only its site or its kind), or "
            "pick none for no card. The last pick is the card. " + LOOK + " A find you name comes from this tool "
            "only, never from memory."),
        "parameters": {"type": "object", "properties": {
            "wish": {"type": "string", "description": "what they asked for, in their words and in English "
                                                      "(烘焙 baking); empty when they named nothing"},
            "kind": {"type": "string", "enum": ["video", "text"],
                     "description": "only when they asked for a video, or for something to read"},
            "length": {"type": "string", "enum": ["short", "long"],
                       "description": "only when they asked for a short or a long one"},
            "pick": {"type": "string", "description": "the id of a find the list gave, for the card; none for no card"},
            "url": {"type": "string", "description": "a page you read with read_page this turn, as the card"},
            "later": {"type": "boolean", "description": "true only when they said yes to your looking for the wish "
                                                        "later, on a walk"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": REACT,
        "description": (
            "Keep what they think of a find in <finds_in_this_chat>, so later finds suit them. Only when their "
            "current message says it (\"这个不喜欢\", \"太长了\", \"那个其实我挺喜欢\", \"这个网站的别推了\"); asking "
            "for another one says nothing of the last."),
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string", "description": "the find's id"},
            "liked": {"type": "boolean"},
            "about": {"type": "string", "enum": list(ABOUT),
                      "description": "what they meant: \"不喜欢\" with no reason is the thing, length only for "
                                     "\"太长\""},
            "words": {"type": "string", "description": "their own words about it, short"}},
            "required": ["id", "liked", "about"]}}},
]

# How a walk for a wish stands, by roam's reason a walk cannot leave now ("" when it can).
WALK_SAID = {
    "": "A walk goes out for it now; its finds come as cards when it is back.",
    "out": "A walk is out now; this one goes right after it, and its finds come as cards.",
    "limit": "Today's walks are used up; it goes on tomorrow's first walk.",
    "walks_off": "Walks are off in Settings › 外出, so nothing goes now; the wish waits until they turn walks on.",
}


async def use(agent, called: dict, args: dict, lookups) -> str:
    if called["name"] == REACT:
        return await asyncio.to_thread(react, agent, args, lookups)
    return await bring(agent, args, lookups)


async def bring(agent, args: dict, lookups) -> str:
    """bring_find: a list of finds for the wish, a pick of one of them (or none) for the card, a page read this
    turn as the card, or the wish kept for a walk."""
    from . import today
    from .library import Library, item_id
    runtime = today.runtime_of(agent)
    wish = " ".join(str(args.get("wish") or "").split())[:80]
    pick = str(args.get("pick") or "").strip()
    url = str(args.get("url") or "").strip()
    if args.get("later") is True:
        return await _later(runtime, wish, lookups)
    if url:
        # Only a page read this turn: the library's own finds are picked from the list.
        item = Library(runtime).get(item_id(url)) if url in {s["url"] for s in lookups._read} else None
        if item is None:
            return "Error: url takes a page you read with read_page this turn"
        lookups.doing("pick", item.title[:80])
        lookups.bring = item.id
        return _picked(item, "")
    if pick.casefold() == "none":
        lookups.bring = ""
        return "No card under your reply. Say plainly that none of your finds is on it. " + LOOK
    if pick:
        if pick not in lookups.offered:
            return "Error: pick takes an id the list gave this turn; call bring_find without pick to list them"
        item = Library(runtime).get(pick)
        lookups.bring = pick
        return _picked(item, lookups.offered[pick])
    lookups.doing("pick", wish)
    avoid = [{"id": f["id"], "about": f["about"]} for f in lookups.feedback if not f["liked"]]
    listed = await asyncio.to_thread(_list, agent, runtime, wish, avoid, args.get("kind") or "",
                                     args.get("length") or "")
    logger.info("[Agent] bring_find (%d-char wish) -> %d finds", len(wish), len(listed))
    if not listed:
        return "You have no finds to bring now. " + LOOK
    lookups.offered.update({ident: chip for ident, chip, _, _ in listed})
    from .roam import fence_pages
    on_words = [line for _, _, line, words in listed if words]
    by_taste = [line for _, _, line, words in listed if not words]
    return ("Your finds, to pick from. Pick one only if it is on what they asked: its subject, not only its site or "
            "its kind; when they named nothing, the one most for them. Then write about the one you picked by its "
            "title. There is no card under your reply until you call bring_find with pick or url. " + LOOK + "\n"
            + fence_pages("\n".join((["On the words of the wish:"] + on_words if on_words else [])
                                    + (["By their taste:"] + by_taste if by_taste else []))))


def _list(agent, runtime, wish: str, avoid: list, kind: str, length: str) -> list[tuple[str, str, str, bool]]:
    """The finds recommend.matching gives, as (id, Why chip, the line the model reads, on the wish's words)."""
    from . import recommend
    from .panels import feed
    now = time.time()
    rows = recommend.matching(runtime, wish, avoid, kind, length, now)
    why = recommend.Why(runtime, feed.load_topics(runtime), feed.events(runtime), now,
                        getattr(agent, "chat_lang", "") or "zh")
    listed = []
    for shaped, item, words in rows:
        chip = why.of(shaped, item)["why"]["text"]
        line = " | ".join(v for v in (f"[{item.id}] {shaped['title'][:100]}", shaped["site"], shaped["kind"],
                                      recommend.size(item, shaped), chip) if v)
        listed.append((item.id, chip, line, words))
    return listed


def _picked(item, chip: str) -> str:
    """What the reply model writes about once a find is the card."""
    from . import recommend
    from .panels import feed
    from .roam import fence_pages
    shaped = feed.shape(item, {}) or {"kind": item.kind, "site": item.site}
    facts = " | ".join(v for v in (f"title: {item.title}", shaped["site"], shaped["kind"],
                                   recommend.size(item, shaped) if "title" in shaped else "", chip) if v)
    return ("The card under your reply is now this find. Write about it by its title, a sentence of your own on why "
            "it is for them, and no link. To change it, pick another or none.\n"
            + fence_pages(f"{facts}\n{' '.join((item.description or item.text).split())[:400]}"))


async def _later(runtime, wish: str, lookups) -> str:
    """The wish kept for a walk (the chat's "+" asks the same, panels/compose.py), and how that walk stands."""
    from .browsed import _ask
    if not wish:
        return "Error: later needs the wish"
    sent = await asyncio.to_thread(_ask, runtime.parent, "POST", "/api/compose/wonder", 30, json={"words": wish})
    lookups.doing("pick", wish)
    said = WALK_SAID.get(sent.get("held") or "")
    if not sent.get("words") or said is None:
        # No browser, no driver or no model for walks.
        return "No walk can take it: walks cannot go out on this computer now. Tell them plainly."
    return said + " Tell them in a sentence; name no find."


def react(agent, args: dict, lookups) -> str:
    """react: what the person said of a find this chat showed, kept with the turn; the page records it in the feed
    (welcome._chat_finds)."""
    from . import recommend, today
    ident = str(args.get("id") or "").strip()
    shown = recommend.chat_shown(today.runtime_of(agent), lookups.conversation, time.time())
    if all(ident != got for got, _ in shown):
        return "Error: react takes the id of a find in <finds_in_this_chat>"
    about = args["about"]
    liked = args["liked"] is True
    lookups.feedback.append({"id": ident, "liked": liked, "about": about,
                             "words": " ".join(str(args.get("words") or "").split())[:120]})
    logger.info("[Agent] react %s liked=%s about=%s", ident, liked, about)
    return "Kept."

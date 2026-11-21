"""open and look: one way to every place the character works on, a page of the app's browser, another program's
window, a file or a folder (tool-layer-design.json, one_surface; places.py keeps them by id).

open finds what the person named (resolve.py) and opens it either in the middle, for them to look at (show true:
the page in front in the app's browser, the window raised, the file in its own program), or out of sight for the
character to work on (a page in a tab behind the others, a window read where it is). Opening a site or a program
they named is free; a file opens through the commands and files gate as before (shell/panel.py), and a program on
the never lists is refused (desk/panel.py).

look reads a place as it is now, in the snapshot form the page tools converged on (Playwright MCP's
browser_snapshot and browser_find, Chrome DevTools MCP's take_snapshot, Claude in Chrome's read_page and find,
OpenAI's BrowserToolset read_page, read_page_scoped and find): a text accessibility tree with a ref in brackets on
each control, one grammar for a page and a window; ref reads one part (read_page's ref_id, read_page_scoped's
root), filter interactive keeps the controls, find returns up to twenty lines with refs for words, and an Omitted
line says what was left out and why (BrowserReadV1's omitted: report_limit, private). A Noticed line gives what
the app spotted (a sign-in box, a check for people, a password field, a drawn canvas): hints, never a verdict. No
errand starts and nothing on the page or in the window is pressed.
"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit

from . import places, resolve, today

OPEN = {"type": "function", "function": {
    "name": "open",
    "description": (
        "Open what they name, as they would find it: a program, a shortcut on their desktop, a file, a folder, a web "
        "page or a site (\"打开B站首页\", \"打开桌面的 grok bot\", \"切回记事本\"). show true when they want to see "
        "it: it comes to the middle (the page in front in the app's browser, the window raised, the file in its "
        "program). Left out, it opens out of sight for you to read or work on. When names tie you get the "
        "candidates. Returns the place id, and a first look of a place opened out of sight."),
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "what they called it, or a web address or a full path"},
        "near": {"type": "string", "description": "where it is, when they said: desktop, downloads, documents, "
                                                  "pictures, music, videos, or a full folder path"},
        "as": {"type": "string", "enum": list(resolve.AS), "description": "only when the name could be either"},
        "show": {"type": "boolean", "description": "true when they want to see it"}},
        "required": ["name"]}}}
LOOK = {"type": "function", "function": {
    "name": "look",
    "description": (
        "See a place as it is now. With no place: the page in front of them in the app's browser (\"这个网页\"), "
        "else the place last shown to them. A page or a window comes as lines, each control with its ref in "
        "brackets [12], tables as rows; a long one in parts (from). ref reads one part whole, filter interactive "
        "only the controls, find the lines with these words (at most 20). A file comes as its text, a folder as its "
        "list. A closing Noticed line gives what the app spotted; you judge what the page asks of them. Nothing is "
        "pressed."),
    "parameters": {"type": "object", "properties": {
        "place": {"type": "string", "description": "the place id, such as p1; left out for the one in front"},
        "ref": {"type": "integer", "description": "the ref of a part to read whole: a list, a row, a box"},
        "filter": {"type": "string", "enum": ["all", "interactive"]},
        "find": {"type": "string", "description": "words to find; their lines come with their refs"},
        "from": {"type": "integer", "description": "the line to go on from, as the Omitted line says"},
        "picture": {"type": "boolean", "description": "true to see a picture too, when your model takes pictures"}},
        "required": []}}}
TOOLS = [OPEN, LOOK]


def _lang() -> str:
    from . import langs
    return "zh" if (langs.normalize(os.getenv("AGENT_LANG", "")) or "en").startswith("zh") else "en"


def _ask(home, path: str, timeout: float, fields: dict) -> dict:
    from .browsed import _ask as ask
    return ask(home, "POST", path, json=fields, timeout=timeout) or {"status": "no_app"}


async def _browser(home, op: str, timeout: float = 60, **fields) -> dict:
    return await asyncio.to_thread(_ask, home, "/api/browser/op", timeout, {"op": op, **fields})


async def _desk(home, op: str, timeout: float = 90, **fields) -> dict:
    return await asyncio.to_thread(_ask, home, "/api/programs/op", timeout, {"op": op, **fields})


async def run(agent, called: dict, args: dict, lookups) -> str:
    if called["name"] == "open":
        return await open_place(agent, args, lookups)
    return await look(agent, args, lookups)


async def _programs_on(agent, home, lookups) -> str:
    """"" when other programs' windows may be read (turnon.py's programs switch, its card asked here), else the
    Error line the call answers with."""
    from . import turnon
    if "programs" not in await asyncio.to_thread(turnon.off, home):
        return ""
    return await turnon.use(agent, "programs", "", lookups)


# ---------------------------------------------------------------- open

async def open_place(agent, args: dict, lookups) -> str:
    home = today.runtime_of(agent).parent
    name = " ".join(str(args.get("name") or "").split())[:300]
    show = args.get("show") is True
    if not name:
        return "Error: invalid arguments: name is required"
    tabs = (await _browser(home, "tabs", 15)).get("tabs") or []
    found = await asyncio.to_thread(resolve.find, name, str(args.get("near") or "").strip(), str(args.get("as") or ""),
                                    [t for t in tabs if not t.get("private")])
    if found.kind == "several":
        return (f"Not opened yet: “{name}” could be any of: {' | '.join(found.candidates)}. Call open again with the "
                f"full name of the one they mean, or ask them which.")
    if found.kind == "none":
        listed = f" Programs open now: {', '.join(found.candidates)}." if found.candidates else ""
        return (f"Error: nothing called “{name}” was found: no open tab or window, no file there, no program or "
                f"shortcut in Start or on the Desktop, and no site the app knows.{listed} Ask them where it is, or "
                f"use a web address or a full path.")
    if found.kind in ("page", "tab"):
        return await _open_page(agent, home, found, show, lookups)
    if found.kind in ("window", "program"):
        return await _open_window(agent, home, found, show, lookups)
    return await _open_file(agent, found, show, lookups)


async def _open_page(agent, home, found: resolve.Resolved, show: bool, lookups) -> str:
    from .web import signin
    if found.kind == "tab":
        tabs = (await _browser(home, "front" if show else "tabs", 30, tab=found.target)).get("tabs") or []
        tab = next((t for t in tabs if t["id"] == found.target), None)
    else:
        url = str(found.target)
        if not show and not signin.chose(home / "runtime" / "desktop", url, "task"):
            return ("Error: they turned off your opening this site on your own. Ask them, or open it with show true "
                    "for them to see.")
        tab = await _browser(home, "place", 60, url=url, show=show)
    if not tab or not tab.get("id"):
        return ("Error: the app's browser is not open, so nothing was opened. For a public page, read_page reads it "
                "without the browser.")
    url = str(tab.get("url") or found.target)
    place = places.add(lookups.conversation, "page", tab["id"], tab.get("title") or found.title,
                       urlsplit(url).hostname or "", shown=show)
    if show:
        lookups.trace.append({"tool": "look", "what": place.title[:80] or place.where})
        return (f"Opened {url} in the middle of the app window as place {place.id}; they see it now. "
                f"look with place {place.id} reads it.")
    first = await _look_page(agent, home, place, {}, lookups)
    return f"Opened {url} out of sight as place {place.id}.\n{first}"


async def _open_window(agent, home, found: resolve.Resolved, show: bool, lookups) -> str:
    from .desk import operate
    if not show:
        refusal = await _programs_on(agent, home, lookups)
        if refusal:
            return refusal
    lang = _lang()
    target = {"hwnd": found.target} if found.kind == "window" else {"program": found.target}
    out = await _desk(home, "place_open", 60, conversation=lookups.conversation, show=show, lang=lang, **target)
    status = out.get("status")
    line = str(out.get("line") or status)
    if status == "done":
        # A shortcut to a script, a folder or a page: it opened, and it has no window of its own to work in.
        lookups.trace.append({"tool": "desk", "status": "done",
                              "what": f"{found.title}：打开" if lang == "zh" else f"{found.title}: open"})
        return f"{line}. It has no window to work in; tell them it is open."
    if status != "ok":
        return f"Error: not opened: {line}. Tell them in a sentence; do not try another way."
    program, title = out.get("program") or found.title, out.get("title") or ""
    place = places.add(lookups.conversation, "window", out["hwnd"], title or program, program, shown=show)
    lookups.trace.append({"tool": "desk", "status": "done",
                          "what": f"{program}：打开" if lang == "zh" else f"{program}: open"})
    if show:
        return (f"{program} is in front of them as place {place.id} ({operate.fence(title)}). look with place "
                f"{place.id} reads its window.")
    first = await _look_window(agent, home, place, {}, lookups)
    return f"{program} is open as place {place.id}, not brought to the front.\n{first}"


async def _open_file(agent, found: resolve.Resolved, show: bool, lookups) -> str:
    from .shell import tool as shell_tool
    path = str(found.target)
    place = places.add(lookups.conversation, found.kind, path, found.title, os.path.dirname(path), shown=show)
    if not show:
        return f"{path} is place {place.id} ({found.kind}). look with place {place.id} reads it."
    opened = await shell_tool.use_file(agent, "open", {"path": path}, lookups)
    return f"{opened}\nIt is place {place.id}."


# ---------------------------------------------------------------- look

def _most(agent) -> int:
    """Characters one look may carry: twice the tokens of page the reply model's measured window leaves a result
    (search.py's page room), between the least able model's part and the most a print gives."""
    from .web.pageops import STATE_MAX, STATE_MOST
    return max(STATE_MAX, min(STATE_MOST, 2 * int(agent._page_room())))


def _part(args: dict) -> dict:
    """The part of a place a look asks the app for."""
    return {k: args[k] for k in ("ref", "filter", "find", "from") if args.get(k) not in (None, "")}


def _pictures(agent, args: dict) -> bool:
    """A picture goes along only when asked and the reply model was measured to take pictures (abilities.py)."""
    from . import abilities
    if args.get("picture") is not True or not agent._shows_pictures():
        return False
    url, _ = agent._endpoint_for(agent.llm_dm_model)
    return (abilities.record(abilities.runtime_of(agent), url, agent.llm_dm_model) or {}).get("pictures") == "yes"


def _closing(out: dict, args: dict, picture: bool) -> str:
    lines = [f"Omitted: {out['omitted']}"] if out.get("omitted") else []
    if args.get("picture") is True and not picture:
        lines.append("Omitted: no picture: their model is not measured to take pictures")
    if out.get("noticed"):
        lines.append(f"Noticed: {out['noticed']}. You judge what it asks of them.")
    return "\n".join(lines)


async def look(agent, args: dict, lookups) -> str:
    home = today.runtime_of(agent).parent
    conversation = lookups.conversation
    ident = str(args.get("place") or "").strip()
    if ident:
        place = places.get(conversation, ident)
        if place is None:
            listed = "; ".join(p.line() for p in places.all_of(conversation)) or "none"
            return f"Error: there is no place {ident[:20]}. The open places: {listed}"
    else:
        tabs = (await _browser(home, "tabs", 15)).get("tabs") or []
        front = next((t for t in tabs if t.get("front")), None)
        if front is not None:
            if front.get("private"):
                return ("Error: the page in front is in a private tab: it is not read. Tell them; they can open it in "
                        "an ordinary tab.")
            place = places.add(conversation, "page", front["id"], front.get("title") or "",
                               urlsplit(front.get("url") or "").hostname or "", shown=True)
        else:
            place = next((p for p in places.all_of(conversation) if p.shown), None)
        if place is None:
            listed = "\n".join(p.line() for p in places.all_of(conversation))
            return ("No page is open in the app's browser and nothing was shown to them in this chat. "
                    + (f"The open places:\n{listed}" if listed else "open what they mean first, or ask them which."))
    if place.kind == "page":
        return await _look_page(agent, home, place, args, lookups)
    if place.kind == "window":
        refusal = await _programs_on(agent, home, lookups)
        return refusal or await _look_window(agent, home, place, args, lookups)
    from .shell import tool as shell_tool
    return await shell_tool.use_file(agent, "list_files" if place.kind == "folder" else "read_file",
                                     {"path": str(place.handle)}, lookups)


async def _look_page(agent, home, place: places.Place, args: dict, lookups) -> str:
    from .roam import fence_pages
    from .web import signin
    picture = _pictures(agent, args)
    lookups.doing("look", place.title[:80] or place.where)
    out = await _browser(home, "place_look", 120, tab=place.handle, most=_most(agent), picture=picture, **_part(args))
    status = out.get("status")
    if status == "gone":
        places.drop(lookups.conversation, place.id)
        return f"Error: place {place.id} is closed: its tab is no longer open."
    if status == "private":
        return "Error: that tab is private: it is not read."
    if status != "ok":
        return "Error: the app's browser is not open, so the page could not be looked at."
    url = str(out.get("url") or "")
    if not signin.chose(home / "runtime" / "desktop", url, "here" if place.shown else "task"):
        return "Error: they turned off your reading this site on your own. Ask them; do not read it another way."
    place.title = out.get("title") or place.title
    lookups.trace.append({"tool": "look", "what": place.title[:80] or urlsplit(url).hostname or ""})
    if out.get("picture"):
        lookups.pictures.append(f"data:image/jpeg;base64,{out['picture']}")
    head = (f"Place {place.id} · page · {url} · {'in the middle' if place.shown else 'out of sight'} · snapshot "
            f"{out.get('snapshot', 1)}")
    return f"{head}\n{fence_pages(out.get('state') or '')}\n{_closing(out, args, picture)}".rstrip()


async def _look_window(agent, home, place: places.Place, args: dict, lookups) -> str:
    from .desk import operate
    picture = _pictures(agent, args)
    what = f"{place.where}：查看窗口" if _lang() == "zh" else f"{place.where}: look"
    lookups.doing("desk", what)
    out = await _desk(home, "place_look", 120, conversation=lookups.conversation, hwnd=place.handle,
                      most=_most(agent), picture=picture, lang=_lang(), **_part(args))
    status = out.get("status")
    if status == "gone":
        places.drop(lookups.conversation, place.id)
        return f"Error: place {place.id} is closed: its window is gone."
    if status != "ok":
        return f"Error: the window could not be looked at: {out.get('line') or status}."
    lookups.trace.append({"tool": "desk", "status": "done", "what": what})
    if out.get("picture"):
        lookups.pictures.append(f"data:image/jpeg;base64,{out['picture']}")
    head = (f"Place {place.id} · window · {place.where} · {'in front' if place.shown else 'where it was'} · "
            f"snapshot {out.get('snapshot', 1)}")
    return f"{head}\n{operate.fence(out.get('state') or '')}\n{_closing(out, args, picture)}".rstrip()

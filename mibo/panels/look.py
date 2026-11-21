"""The look panel: the character, chosen whole (its personality, its name and
its figure on the desktop at once), the first run's tour, and a check that the
saved model still answers."""
from __future__ import annotations

import asyncio
import os
import re
import threading
from pathlib import Path

from fastapi import Request

from .. import i18n, langs, settingsfile
from .. import setup_wizard as sw
from ..endpoints import model_problem
from ..storage import atomic_write_text

ERRORS: frozenset[str] = frozenset({"invalid_figure", "invalid_lang", "custom_name", "custom_about",
                                    "custom_empty"})
# Seconds; the model save waits as long for the same probe.
CHECK_S = 20
# The characters the app offers, Mibo first; the fifth is the person's own, which is
# Mibo's figure on the desktop (in the person's colours: panels/colors.py).
BUILT_IN = ("mibo", "chatgpt", "deepseek", "claude")
CUSTOM = "custom"
OWN_FIGURE = "mibo"
NAME_MAX = 64


def _desktop(workspace) -> Path:
    return workspace.home / "runtime" / "desktop"


def _lang(values: dict) -> str:
    return langs.normalize(values.get("AGENT_LANG")) or sw.detect_lang()


def _name_line(name: str, lang: str) -> str:
    return sw.NAME_LINE.get(lang, sw.NAME_LINE["en"]).format(name=name)


def chosen(workspace, values: dict) -> str:
    """Which character the home is: a built-in one by its persona.txt, else its own.
    A new home, with no persona and no name yet, is Mibo."""
    card = workspace.home / "persona.txt"
    if card.is_file():
        origin = sw.shipped_persona(card.read_text(encoding="utf-8"))
        return origin[0] if origin and origin[0] in BUILT_IN else CUSTOM
    name = values.get("PERSONA_NAME", "").strip()
    if not name:
        return BUILT_IN[0]
    # A name a shipped character goes by is that character; any other name is the person's own.
    return next((key for key in BUILT_IN if name == sw.character_for(key).name_in(_lang(values))), CUSTOM)


def _theirs(own: dict, lang: str) -> bool:
    """Whether a kept character is the person's own writing: a shipped text never was."""
    about = own.get("about", "")
    return not (sw.shipped_persona(about) or sw.shipped_persona(_name_line(own.get("name", ""), lang) + "\n" + about))


def own_character(workspace, values: dict, now: str) -> dict | None:
    """The person's own character: as the home is now when it is the one in use,
    else as kept when another was chosen; None before there is one."""
    from ..welcome import app_settings

    kept = app_settings(_desktop(workspace)).get("custom") or {}
    if now == CUSTOM:
        name = values.get("PERSONA_NAME", "").strip()
        lines = workspace.persona()["text"].split("\n")
        # A first line the app wrote names it; the rest is the person's own words.
        if lines and lines[0].strip() == _name_line(name, _lang(values)):
            lines = lines[1:]
        return {"name": name, "about": "\n".join(lines).strip()}
    if kept.get("name") and _theirs(kept, _lang(values)):
        return {"name": kept["name"], "about": kept.get("about", "")}
    return None


def characters(workspace, lang: str = "") -> dict:
    """The five as the pickers show them, the one in use, its name and its figure."""
    from ..welcome import PERSONA_LIMIT, pet_figure

    values = workspace.config()
    lang = langs.normalize(lang) or _lang(values)
    now = chosen(workspace, values)
    name = values.get("PERSONA_NAME", "").strip()
    rows = []
    for key in BUILT_IN:
        character = sw.character_for(key)
        # Who it is: its menu line after the name, in the interface's language.
        line = re.split(r"[:：]", i18n.t(f"catalog.characters.{key}", i18n.lang(values)), maxsplit=1)[-1].strip()
        rows.append({"key": key, "name": name if key == now and name else character.name_in(lang),
                     "line": line, "figure": key})
    own = own_character(workspace, values, now)
    if own:
        own["figure"] = OWN_FIGURE
    return {"chosen": now, "set": (workspace.home / "persona.txt").is_file(), "name": name,
            "figure": pet_figure(_desktop(workspace), values), "characters": rows, "custom": own,
            "picture": workspace._own_avatar_version(sw.own_avatar(workspace.home)),
            "limits": {"name": NAME_MAX, "about": PERSONA_LIMIT, "picture": sw.MAX_OWN_AVATAR}}


def choose(workspace, payload: dict) -> dict:
    """Make the home one of the characters: persona.txt, its name, its picture and
    the pet's figure in one step. The person's own ("custom") takes the name and
    words given, else those it had, and is kept aside while another is in use."""
    from ..welcome import PERSONA_LIMIT, save_app_settings

    key = str(payload.get("key") or "")
    if key not in (*BUILT_IN, CUSTOM):
        raise ValueError("invalid_character")
    desktop = _desktop(workspace)
    with workspace._lock:
        values = workspace.config()
        lang = _lang(values)
        now = chosen(workspace, values)
        own = own_character(workspace, values, now)
        if key != CUSTOM:
            name = values.get("PERSONA_NAME", "").strip()
            # Its own name, unless it is the one in use and was renamed (a new home's Mibo
            # is shown in use with no name yet). The person's own character keeps its name.
            if key != now or not name:
                name = (sw.character_for(key).name_in(lang) if now == CUSTOM
                        else sw.name_for(key, name or sw.DEFAULT_NAMES[lang], lang))
            if own and now == CUSTOM and _theirs(own, lang):
                save_app_settings(desktop, custom=own)
            sw.back_up_persona(workspace.home)
            sw.write_persona(workspace.home, key, name=name, lang=lang,
                             admin_name=values.get("ADMIN_NAME", ""),
                             admin_relationship=values.get("ADMIN_RELATIONSHIP", ""))
            settingsfile.write(workspace.home, {"PERSONA_NAME": name, "PERSONA_AVATAR": sw.avatar_for(key)})
            save_app_settings(desktop, pet=key)
        else:
            given = "name" in payload or "about" in payload
            if not given and not own:
                raise ValueError("custom_empty")
            name = str((payload.get("name") if given else own["name"]) or "").strip()
            about = str((payload.get("about") if given else own["about"]) or "").replace("\r\n", "\n").strip()
            if not name or len(name) > NAME_MAX or "\n" in name:
                raise ValueError("custom_name")
            if not about or len(about) > PERSONA_LIMIT:
                raise ValueError("custom_about")
            # The words name it themselves, or a first line does.
            text = about if name in about else _name_line(name, lang) + "\n" + about
            sw.back_up_persona(workspace.home)
            atomic_write_text(workspace.home / "persona.txt", text + "\n")
            picture = sw.OWN_AVATAR if sw.own_avatar(workspace.home) else ""
            settingsfile.write(workspace.home, {"PERSONA_NAME": name, "PERSONA_AVATAR": picture})
            save_app_settings(desktop, pet=OWN_FIGURE, custom={"name": name, "about": about})
        workspace.character.reload()
    workspace.figure_changed()
    return characters(workspace, str(payload.get("lang") or ""))


def names(workspace, lang: str) -> list[dict]:
    """The characters as the pet's own menu names them, in `lang`. The one in use is
    keyed by its figure, which the menu ticks; the others "character:<key>"."""
    listed = characters(workspace, lang)
    rows = [(row["key"], row["name"]) for row in listed["characters"]]
    if listed["custom"]:
        rows.append((CUSTOM, listed["custom"]["name"]))
    return [{"key": listed["figure"] if key == listed["chosen"] else f"character:{key}", "name": name}
            for key, name in rows]


def choose_figure(workspace, key: str) -> None:
    """The pet's menu: a character not in use, chosen off the window's thread, as
    restarting the character takes a moment."""
    if key.startswith("character:"):
        threading.Thread(target=choose, args=(workspace, {"key": key.partition(":")[2]}), daemon=True).start()


def mount(app, workspace, body) -> None:
    from ..welcome import PERSONA_LIMIT, app_settings, pet_figure, save_app_settings

    desktop = workspace.home / "runtime" / "desktop"

    def look(lang: str) -> dict:
        # As the window's figure() picks it.
        return {"figure": pet_figure(desktop, workspace.config()),
                "figures": names(workspace, lang),
                "toured": bool(app_settings(desktop).get("toured"))}

    @app.get("/api/look")
    async def read_look(lang: str = ""):
        return await asyncio.to_thread(look, lang)

    @app.post("/api/look")
    async def choose_look(request: Request):
        # A figure is a character's: choosing it chooses that character whole.
        payload = await body(request)
        key = str(payload.get("figure") or "")
        if key not in BUILT_IN:
            raise ValueError("invalid_figure")
        await asyncio.to_thread(choose, workspace, {"key": key})
        return await asyncio.to_thread(look, str(payload.get("lang") or ""))

    @app.get("/api/characters")
    async def read_characters(lang: str = ""):
        return await asyncio.to_thread(characters, workspace, lang)

    @app.post("/api/character")
    async def choose_character(request: Request):
        payload = await body(request, PERSONA_LIMIT * 4 + 1024)
        return await asyncio.to_thread(choose, workspace, payload)

    @app.post("/api/look/toured")
    async def toured(request: Request):
        # The first run was walked or skipped: it is not shown again.
        await body(request)
        await asyncio.to_thread(save_app_settings, desktop, toured=True)
        # Sharing what it found waits for the tour: it may start now.
        workspace.found()
        return {"toured": True}

    @app.post("/api/look/lang")
    async def choose_lang(request: Request):
        # The first step of the first run: kept before any model is connected.
        payload = await body(request)
        lang = langs.normalize(payload.get("lang"))
        if not lang:
            raise ValueError("invalid_lang")
        # The screens speak it too, until Settings › Language picks another for them.
        await asyncio.to_thread(settingsfile.write, workspace.home, {"AGENT_LANG": lang, "UI_LANG": lang})
        os.environ["UI_LANG"] = lang
        return {"lang": lang}

    @app.post("/api/look/ui")
    async def choose_ui_lang(request: Request):
        # Settings › Language: the interface's language alone; the character keeps speaking its own.
        lang = langs.normalize((await body(request)).get("lang"))
        if not lang:
            raise ValueError("invalid_lang")
        await asyncio.to_thread(settingsfile.write, workspace.home, {"UI_LANG": lang})
        os.environ["UI_LANG"] = lang
        return {"lang": lang}

    def check() -> dict:
        values = workspace.config()
        key = values.get("LLM_API_KEY", "")
        if not key:
            return {"ok": False, "error": "key_required"}
        try:
            probe = sw.probe_key(values.get("LLM_BASE_URL", ""), key, values.get("LLM_MODEL", ""),
                                 timeout=CHECK_S)
        except Exception:
            return {"ok": False, "error": "model_unreachable"}
        if probe.ok:
            return {"ok": True, "error": ""}
        # The codes the save gives for the same answers (an empty balance included); never the service's own text.
        problem = model_problem(probe.status, probe.detail) if probe.status else ""
        return {"ok": False, "error": {"key": "key_rejected", "limited": "model_limited", "no_balance": "model_no_balance",
                                       "not_found": "model_not_found"}.get(problem, "model_unreachable")}

    @app.post("/api/look/check")
    async def check_model(request: Request):
        await body(request)
        return await asyncio.to_thread(check)

"""The app's local page: setup, settings and chat, behind its own token. No
account is needed to open it. The character lives in this process; the page
reaches it through character.py."""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import contextlib
from contextlib import asynccontextmanager
from datetime import datetime
import functools
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback
from typing import Callable
from urllib.parse import urlsplit
import webbrowser

import httpx
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from . import __version__, channels, home as homes, i18n, langs
from . import setup_wizard as sw
from . import life
from . import quiet
from . import pacing
from . import recommend
from . import updates
from . import window as appwindow
from .panels import modelbar
from .character import PERSON_ACTIONS, Character, NotReady
from .endpoints import model_problem
from .library import Library, item_id
from .memstore import MemoryFileDamaged
from .storage import FileLock, LockUnavailable, atomic_write_text

ASSETS = Path(__file__).with_name("static") / "welcome"
# Parts of the page beyond its four first views. Each is a module in
# mibo/panels with `mount(app, workspace, body)`, drawn by the app
# page's own files (setup.js and its sections, chat.js).
PANELS = ("runtime", "desk", "tuning", "detect", "signin", "feed", "sources", "look", "profile", "quiet", "remarks",
          "browser", "colors", "compose", "links", "modelbar", "signins", "tools", "pet", "schedule", "walks")
# The figures the pet can be, <key>.svg under the page's figures folder; the first is the
# default, and a character of one's own is it too.
PET_FIGURES = ("mibo", "chatgpt", "deepseek", "claude")
# The page's own files, served by name.
PAGE_ASSETS = ("i18n.js", "app.js", "app.css", "chat.js", "chat.css", "finds.js", "finds.css", "signin.css",
               "onboard.js", "onboard.css", "setup.js", "setup.css",
               "pet.js", "pet.css", "card.js", "card.css", "characters.js", "characters.css",
               "browse.js", "browsetabs.js", "browsemenu.js", "browsestrip.js", "browse.css", "plus.js", "plus.css", "activity.js", "activity.css",
               "linkcard.js", "linkcard.css", "modelbar.js", "modelbar.css", "providermarks.js", "sites.js", "sites.css",
               "toolcard.js", "petux.css", "petbox.js", "petline.js", "petedge.js", "petdial.js",
               "petclick.js", "petoff.js", "compact.js", "compact.css", "schedulechat.js")
# Settings' sections, each a script of its own under ASSETS/setup (setup.js is their shell).
SETUP_PARTS = ("character", "model", "connect", "language", "recs", "walks", "memory", "privacy", "device", "about", "shell", "programs",
               "skills", "tools", "schedule")
TOKEN_HEADER = "x-mibo-setup-token"
# Bytes; a chat message may be 8000 characters, three bytes each in Chinese.
MAX_BODY = 32_768
# Characters; the shipped personas are under 800, imported cards often run past 4,000,
# and the engine itself sets no limit. Every reply sends the whole persona.
PERSONA_LIMIT = 16_000
# Another assistant's memory list pasted into Settings › 记忆: characters read, items kept at most (person.FACTS_MAX).
PERSON_PASTE_MAX = 8_000
PERSON_IMPORT_MAX = 200
CHAT_KEPT = 50
# The chat that is the character's own: what it finds arrives there. The others are plain chats.
MAIN_CHAT = "main"
CHAT_ID = re.compile(r"[0-9a-f]{16}")
# A plain chat is named by the first message sent in it, cut to TITLE_FIRST; a name given may be longer.
TITLE_FIRST, TITLE_MAX = 40, 60
# What the character found comes into the main chat one at a time, the character's own choice
# (recommend), when the pacing says it is a moment for one (pacing.py, as for the pet's card).
# Seconds between the share loop's looks.
SHARE_EVERY_S = 300
# What the character shared since the person last spoke, quoted in their next message so it knows.
SHARE_QUOTED = 5
# Seconds a reply may take before the page stops waiting: a turn may work in the browser, waiting on
# the person's press.
REPLY_WITHIN_S = 900
# A picture the character sent, stored by its content hash; nothing else is served.
PICTURE_NAME = re.compile(r"[0-9a-f]{40}\.(png|jpg|gif|webp)")
# A thing the character saw, as the library names it.
ITEM_ID = re.compile(r"[0-9a-f]{16}")
LIBRARY_PICTURE_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
PICTURE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
MAX_PICTURE = 8 * 1024 * 1024
# What the person attaches to a message (chat.js): at most ATTACH_MAX things. A picture
# (png, jpg, webp, gif) of at most ATTACH_PICTURE bytes, the page having made a larger one
# smaller; a text file of at most ATTACH_FILE bytes, read here, of the kinds below, whose text
# goes to the character cut to ATTACH_TEXT characters.
ATTACH_MAX = 4
ATTACH_PICTURE = 1_000_000
ATTACH_FILE = 512 * 1024
ATTACH_TEXT = 10_000
ATTACH_TEXT_KINDS = frozenset((
    "txt text md markdown rst csv tsv json jsonl yaml yml toml ini cfg conf log xml html htm "
    "css scss js mjs cjs ts tsx jsx vue py ipynb java kt kts scala go rs c h cc cpp hpp cs m mm "
    "swift rb php pl lua r sql sh bash zsh ps1 bat cmd tex bib srt vtt diff patch").split())
CHAT_BODY = MAX_BODY + ATTACH_MAX * ATTACH_PICTURE * 4 // 3 + 4096
# A file the person attached, its text stored by its content hash.
FILE_NAME = re.compile(r"[0-9a-f]{40}\.txt")

# The page listens here only: it is this computer's alone.
PAGE_HOST = "127.0.0.1"
# Seconds a start after a restore or a reset waits for the app before it to close.
RESTART_WAIT_S = 60
LOOPBACK = ("localhost", "127.0.0.1", "::1")
FORWARDED = ("x-forwarded-host", "forwarded", "x-forwarded-for")


def keep_recent(messages: list[dict]) -> tuple[list[dict], bool]:
    """The last CHAT_KEPT messages, starting at one the person sent so that no
    reply is cut in half, and whether any were dropped."""
    if len(messages) <= CHAT_KEPT:
        return messages, False
    kept = messages[-CHAT_KEPT:]
    first = next((i for i, m in enumerate(kept) if m.get("role") == "user"), 0)
    return kept[first:], True


class _CommandReader:
    """The character's own reading of a memory command, without the character:
    its name, the nicknames on its card and the language it speaks."""

    def __init__(self) -> None:
        from .memory import Memory
        self._parse = functools.partial(Memory._parse_memory_command, self)
        self._memory_cmd_patterns = functools.partial(Memory._memory_cmd_patterns, self)
        self.persona_name, self.persona_aliases, self.chat_lang = "", (), "en"

    def read(self, text: str, name: str, aliases: tuple, lang: str):
        self.persona_name, self.persona_aliases, self.chat_lang = name, aliases, lang
        return self._parse(text)


def _card_aliases(home: Path, values: dict) -> tuple:
    try:
        card = json.loads((home / (values.get("PERSONA_CARD_FILE") or "persona.card.json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    raw = card.get("aliases") if isinstance(card, dict) else None
    names = (" ".join(a.split()) for a in raw if isinstance(a, str)) if isinstance(raw, list) else ()
    return tuple(dict.fromkeys(n for n in names if n))


def chat_failure(error: BaseException) -> str:
    # The character itself could not be built: a different thing to fix than a reply that failed.
    if isinstance(error, NotReady):
        return "agent_failed"
    # No answer in time: not the model's setup.
    return "timeout" if isinstance(error, asyncio.TimeoutError) else "chat_failed"


def _picture_kind(data: bytes) -> str:
    """The file extension of an image the page can show, or ""."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def _file_text(data: bytes) -> str | None:
    """A text file's words: UTF-8 (or UTF-16 with its mark, or GB18030 as Windows in
    Chinese saves); None for what is not text."""
    encodings = ("utf-16",) if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return None if "\x00" in text else text
    return None


def _stored_names(message: dict) -> set:
    """The stored pictures and files a message shows: the character's picture, the person's attachments."""
    return {message.get("picture"), *(a.get("name") for a in message.get("attachments") or ())} - {None}


def _memories(rows) -> list[dict]:
    """Kept items as a message carries them: {id, text}, at most six."""
    return [{"id": str(r.get("id") or "")[:64], "text": str(r.get("text") or "")[:200]}
            for r in rows or [] if isinstance(r, dict) and r.get("id")][:6]


def _now_iso() -> str:
    return datetime.fromtimestamp(life.clock()).astimezone().isoformat(timespec="seconds")


class PageChat:
    """One chat of the page: its own conversation with the character and the
    messages the page shows. The main chat is kept where the page always kept
    its one chat, a plain chat in a file of its own named by its id."""

    def __init__(self, path: Path, ident: str = MAIN_CHAT):
        self.path, self.id = path, ident
        saved = sw._read_json(path)
        self.conversation = str(saved.get("conversation") or secrets.token_hex(12))[:64]
        self.title = str(saved.get("title") or "")[:TITLE_MAX]
        self.at = str(saved.get("at") or "")
        messages = [m for m in saved.get("messages", []) if isinstance(m, dict)]
        self.history, dropped = keep_recent(messages)
        self.trimmed = bool(saved.get("trimmed")) or dropped
        self.seq = int(saved.get("seq") or 0) if str(saved.get("seq") or 0).isdigit() else 0
        for message in self.history:
            # Sent while the page process ended: shown as not answered, to send again.
            if message.pop("pending", None):
                message["failed"] = "interrupted"

    def tag(self) -> str:
        return hashlib.sha256(self.conversation.encode()).hexdigest()[:12]

    def append(self, entries: list[dict]) -> list[dict]:
        self.at = _now_iso()
        for entry in entries:
            self.seq += 1
            entry["seq"] = self.seq
            # When it was said: the page draws its days and times from this.
            entry.setdefault("at", self.at)
        self.history, dropped = keep_recent(self.history + entries)
        self.trimmed = self.trimmed or dropped
        return entries

    def view(self, after: int | None = None) -> dict:
        """The chat as the page shows it; with `after`, only what came since that number.
        `chat` changes when a new chat starts, so the page knows to start over."""
        messages = [dict(m) for m in self.history if after is None or m["seq"] > after]
        return {"messages": messages, "chat": self.tag(), "last": self.seq, "kept": CHAT_KEPT,
                "trimmed": self.trimmed, **({"private": True} if self.private else {})}

    def reset(self) -> None:
        self.conversation = secrets.token_hex(12)
        self.history = []
        self.trimmed = False

    @property
    def private(self) -> bool:
        """A 无痕对话: kept in this process only, and never in the character's record or memory."""
        return channels.private_chat(self.conversation)

    def save(self) -> None:
        if self.private:
            return
        saved = {"conversation": self.conversation, "seq": self.seq, "messages": self.history,
                 "trimmed": self.trimmed}
        if self.id != MAIN_CHAT:
            saved.update(title=self.title, at=self.at)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(saved, ensure_ascii=False))

    def listed(self) -> dict:
        return {"id": self.id, "title": self.title, "at": self.at, **({"private": True} if self.private else {})}

    def shared(self) -> list[dict]:
        """The messages that brought something the character found, oldest first."""
        return [m for m in self.history if (m.get("card") or {}).get("id")]


class Workspace:
    def __init__(self, home: Path):
        self.home = home.resolve()
        # The character this page talks to, started and stopped with the page (create_app's lifespan).
        self.character = Character(self.home, runtime=self._runtime, said=self.take_said,
                                   chats=lambda: [c.conversation for c in self._all_chats() if not c.private])
        self._lock = threading.RLock()
        self._chat_lock = asyncio.Lock()
        # (chat, message) while a reply to it is on its way: what the stop button stops.
        self._replying: tuple | None = None
        # The scheduled tasks, set when their panel is mounted (panels/schedule.py).
        self.schedule = None
        self._forgetting = False
        self._commands = None
        # Set when something new came in (found): the share step looks then.
        self._share_wake = asyncio.Event()
        self._closed = False
        self._history_path = self.home / "runtime" / "desktop" / "browser-chat.json"
        self._pictures = self._history_path.with_name("chat-pictures")
        # The text files the person attached.
        self._files = self._history_path.with_name("chat-files")
        # The main chat, where what the character finds arrives too, is the page's one chat of before.
        self._main = PageChat(self._history_path)
        self._chats: dict[str, PageChat] = {}
        folder = self._history_path.with_name("chats")
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
            if CHAT_ID.fullmatch(path.stem):
                self._chats[path.stem] = PageChat(path, path.stem)
        # The one thing the pet's card and the share step bring next, chosen by the character's model.
        self.chooser = recommend.Chooser(self)
        # When the card and the share step bring it (pacing.py).
        self.pace = pacing.Pacer(self)

    def _all_chats(self) -> list[PageChat]:
        return [self._main, *self._chats.values()]

    def _chat(self, ident) -> PageChat:
        """The chat an id names: "main" or nothing for the main chat."""
        ident = str(ident or MAIN_CHAT)
        if ident == MAIN_CHAT:
            return self._main
        if ident not in self._chats:
            raise ValueError("invalid_chat")
        return self._chats[ident]

    @property
    def env(self) -> Path:
        return self.home / ".env"

    def config(self) -> dict:
        return {k: str(v or "") for k, v in dotenv_values(self.env).items()}

    def summary(self) -> dict:
        with self._lock:
            values = self.config()
            known = sw.provider_for_base(values.get("LLM_BASE_URL", ""))
            card = self.home / "persona.txt"
            origin = sw.shipped_persona(card.read_text(encoding="utf-8")) if card.is_file() else None
            updates.refresh_soon(self.home)
            avatar = values.get("PERSONA_AVATAR", "").strip()
            own_picture = sw.own_avatar(self.home)
            if avatar == sw.OWN_AVATAR and not own_picture:
                avatar = ""
            if avatar != sw.OWN_AVATAR and not sw.portrait(avatar):
                avatar = origin[0] if origin and sw.portrait(origin[0]) else ""
            return {"version": __version__, "update": updates.notice(self.home),
                    "configured": bool(values.get("LLM_API_KEY")),
                    "provider": known.key if known else ("other" if values.get("LLM_BASE_URL")
                                                          else "deepseek" if values.get("LLM_API_KEY")
                                                          else sw.PROVIDERS[0].key),
                    "model": values.get("LLM_MODEL", ""), "baseUrl": values.get("LLM_BASE_URL", ""),
                    "name": values.get("PERSONA_NAME", ""), "lang": langs.normalize(values.get("AGENT_LANG")),
                    # What the page calls the person at it, where a name is set.
                    "adminName": values.get("ADMIN_NAME", "").strip()[:64],
                    "character": sw.default_character(origin) if origin else ("custom" if card.is_file() else sw.CHARACTERS[0].key),
                    "avatar": avatar,
                    # Changes with the file, so the page fetches a replaced picture again.
                    "ownAvatar": self._own_avatar_version(own_picture)}

    @staticmethod
    def _own_avatar_version(path: Path | None) -> str:
        try:
            return str(path.stat().st_mtime_ns) if path else ""
        except OSError:
            return ""

    def save(self, payload: dict) -> dict:
        with self._lock:
            old = self.config()
            previous = sw.provider_for_base(old.get("LLM_BASE_URL", ""))
            provider = sw.provider_named(str(payload.get("provider") or (previous or sw.PROVIDERS[0]).key))
            if provider is None:
                raise ValueError("invalid_provider")
            same = ((previous is not None and previous.key == provider.key)
                    or (previous is None and provider.key == "other" and bool(old.get("LLM_BASE_URL"))))
            base = str(payload.get("baseUrl") or provider.base_url).strip()
            same = same and base.rstrip("/") == old.get("LLM_BASE_URL", "").rstrip("/")
            key = str(payload.get("apiKey") or "").strip()
            taken = str(payload.get("apiKeyFrom") or "")
            if not key and taken:
                # A key the page found in the environment and never saw.
                from .panels import detect
                # Only to that service's own address: a key must not follow a typed one.
                home_of_key = sw.provider_for_base(base)
                if (detect.ENV_KEYS.get(taken) == provider.key
                        and home_of_key is not None and home_of_key.key == provider.key):
                    key = detect.env_key(taken)
            if not key and same:
                key = old.get("LLM_API_KEY", "")
            if not provider.needs_key:
                key = key or "ollama"
            if not key and old.get("LLM_API_KEY"):
                # The saved key belongs to the service it was saved for.
                raise ValueError("key_again")
            if not key or len(key) > 4096:
                raise ValueError("key_required")
            if not key.isascii() or any(c.isspace() for c in key):
                # Pasted with other words around it: no service would take it.
                raise ValueError("key_malformed")
            base = _endpoint(base)
            model = str(payload.get("model") or provider.model).strip()
            if not model or len(model) > 256:
                raise ValueError("model_required")
            lang = langs.normalize(payload.get("lang")) or "en"
            character = str(payload.get("character") or sw.CHARACTERS[0].key)
            if character not in (*sw.PERSONA_KEYS, "custom"):
                raise ValueError("invalid_character")
            own = sw.character_for(character).name_in(lang) if character != "custom" else ""
            avatar = payload.get("avatar")
            if avatar is not None:
                avatar = str(avatar)
                if not (avatar == "" or sw.portrait(avatar)
                        or (avatar == sw.OWN_AVATAR and sw.own_avatar(self.home))):
                    raise ValueError("invalid_character")
            name = str(payload.get("name") or own or old.get("PERSONA_NAME")
                       or sw.DEFAULT_NAMES[lang]).strip()
            if not name:
                raise ValueError("name_required")
            if len(name) > 64 or _expands(name):
                raise ValueError("invalid_character")
            # dotenv would read ${VAR} back as another setting, a key included.
            if _expands(key):
                raise ValueError("key_required")
            if _expands(model):
                raise ValueError("model_required")
            try:
                probe = sw.probe_key(base, key, model, timeout=20)
            except Exception:
                raise ValueError("model_unreachable") from None
            if not probe.ok:
                # Never return an upstream response body: it may echo a key.
                problem = model_problem(probe.status, probe.detail) if probe.status else ""
                raise ValueError({"key": "key_rejected", "limited": "model_limited", "no_balance": "model_no_balance",
                                  "not_found": "model_not_found"}.get(problem, "model_unreachable"))
            sw.copy_env_template(self.home)
            values = {"LLM_BASE_URL": base, "LLM_MODEL": model, "LLM_API_KEY": key,
                      "PERSONA_NAME": name, "AGENT_LANG": lang}
            # The page names the picture; without it, a written character keeps its own.
            if avatar is not None:
                values["PERSONA_AVATAR"] = avatar
            elif character != "custom":
                values["PERSONA_AVATAR"] = sw.kept_avatar(self.env, character)
            sw.write_env(self.env, values)
            if character != "custom":
                self._keep_written_persona()
                sw.write_persona(self.home, character, name=name, lang=lang,
                                 admin_name=old.get("ADMIN_NAME", ""),
                                 admin_relationship=old.get("ADMIN_RELATIONSHIP", ""))
            self.character.reload()
            self.figure_changed()
            return self.summary()

    def _keep_written_persona(self) -> None:
        """Copy a persona.txt someone wrote to persona.txt.bak before a shipped one replaces it."""
        sw.back_up_persona(self.home)

    def persona(self) -> dict:
        """The persona the agent runs on: persona.txt, else the first shipped
        character, Mibo, as the agent takes it."""
        with self._lock:
            path = self.home / "persona.txt"
            if path.is_file():
                return {"text": path.read_text(encoding="utf-8").strip()}
            values = self.config()
            lang = langs.normalize(values.get("AGENT_LANG")) or "en"
            text = sw.persona_text(sw.CHARACTERS[0].key, name=values.get("PERSONA_NAME") or sw.DEFAULT_NAMES[lang],
                                   lang=lang, admin_name=values.get("ADMIN_NAME", ""),
                                   admin_relationship=values.get("ADMIN_RELATIONSHIP", ""))
            return {"text": text.strip()}

    def save_own_avatar(self, data: bytes) -> dict:
        """Keep `data` as the character's picture on this page and show it."""
        if len(data) > sw.MAX_OWN_AVATAR:
            raise ValueError("avatar_too_large")
        if not sw.own_avatar_kind(data):
            raise ValueError("avatar_type")
        with self._lock:
            sw.write_own_avatar(self.home, data)
            sw.copy_env_template(self.home)
            sw.write_env(self.env, {"PERSONA_AVATAR": sw.OWN_AVATAR})
            self.figure_changed()
            return self.summary()

    def remove_own_avatar(self) -> dict:
        """Delete the picture; the character goes back to its portrait or initial."""
        with self._lock:
            for kind in sw.OWN_AVATAR_TYPES:
                path = self.home / f"avatar.{kind}"
                if path.is_file() and not path.is_symlink():
                    path.unlink(missing_ok=True)
            if self.config().get("PERSONA_AVATAR", "").strip() == sw.OWN_AVATAR:
                card = self.home / "persona.txt"
                origin = sw.shipped_persona(card.read_text(encoding="utf-8")) if card.is_file() else None
                sw.write_env(self.env, {"PERSONA_AVATAR": sw.avatar_for(origin[0]) if origin else ""})
                self.figure_changed()
            return self.summary()

    def history(self, after: int | None = None, chat: str = MAIN_CHAT) -> dict:
        room = self._chat(chat)
        view = room.view(after)
        # While a reply is on its way: the step it is on (loop.ChatLookups.doing).
        if self._chat_lock.locked():
            doing = sw._read_json(self._runtime() / "desktop" / "chat-doing.json")
            if (doing.get("conversation") == room.conversation
                    and doing.get("tool") in ("recall", "search", "read", "pick", "write", "look", "pages", "operate",
                                              "shell", "desk", "mcp")
                    # A step in the browser, a command or a program may wait minutes on the person's press.
                    and time.time() - float(doing.get("at") or 0) < (300 if doing["tool"] in ("operate", "shell", "desk",
                                                                                              "mcp")
                                                                      else 120)):
                view["doing"] = {"tool": doing["tool"], "what": str(doing.get("what") or "")[:120]}
        return view

    def reset_chat(self) -> None:
        self._main.reset()
        self._save_chat()

    def _append(self, entries: list[dict], chat: PageChat | None = None) -> list[dict]:
        return (chat or self._main).append(entries)

    # -- the chats beside the main one

    def chats(self) -> dict:
        """The main chat and the plain chats, the latest first."""
        main = self._main
        listed = sorted(self._chats.values(), key=lambda c: datetime.fromisoformat(c.at), reverse=True)
        return {"main": {"id": MAIN_CHAT, "name": self.config().get("PERSONA_NAME", "").strip(),
                         "last": main.history[-1]["seq"] if main.history else 0},
                "chats": [chat.listed() for chat in listed]}

    async def new_chat(self, private: bool = False) -> dict:
        """A new plain chat, or with `private` a 无痕对话; one of that kind not written in yet is given again."""
        async with self._chat_lock:
            blank = next((c for c in self._chats.values() if not c.history and c.private == private), None)
            if blank is not None:
                return blank.listed()
            ident = secrets.token_hex(8)
            chat = PageChat(self._history_path.with_name("chats") / f"{ident}.json", ident)
            if private:
                chat.conversation = channels.PRIVATE_CHAT + secrets.token_hex(12)
            chat.at = _now_iso()
            self._chats[ident] = chat
            self._save_chat(chat)
            return chat.listed()

    async def rename_chat(self, payload: dict) -> dict:
        title = " ".join(str(payload.get("title") or "").split())
        if not title or len(title) > TITLE_MAX:
            raise ValueError("invalid_title")
        async with self._chat_lock:
            chat = self._chat(payload.get("id"))
            if chat is self._main:
                raise ValueError("invalid_chat")
            chat.title = title
            self._save_chat(chat)
        return {"ok": True}

    async def delete_chat(self, payload: dict) -> dict:
        async with self._chat_lock:
            chat = self._chat(payload.get("id"))
            if chat is self._main:
                raise ValueError("invalid_chat")
            del self._chats[chat.id]
            chat.path.unlink(missing_ok=True)
            self._prune_pictures()
            # Its chat record goes with it (what was kept about them stays), so no search of past chats finds it.
            try:
                await self.character.forget_chat(chat.conversation, [c.conversation for c in self._all_chats()])
            except NotReady:
                pass
        return {"ok": True}

    def _entry(self, item: dict) -> dict | None:
        """A reply item as a chat message: text, or a picture stored for the page
        to show; None for what the page cannot show."""
        kind = item.get("type")
        if kind == "text" and str(item.get("text") or "").strip():
            card = life.clean_card(item.get("card"))
            # A find of the library's, brought by the reply: its card as the share step makes it.
            found = self._library().get(str(item["card"].get("id") or "")) if card else None
            if found is not None:
                card = life.said(found).get("card") or card
            # What the character looked up for this reply: its sources, links the page shows, and the calls.
            sources = [{k: str(s.get(k) or "")[:300] for k in ("title", "url", "site")}
                       for s in item.get("sources") or [] if isinstance(s, dict)
                       and str(s.get("url") or "").startswith(("https://", "http://"))][:4]
            trace = [{k: str(t.get(k) or "")[:200] for k in ("tool", "what")}
                     # A command keeps what was run and how it ended, for the step log (shell/tool.py).
                     | ({k: str(t.get(k, ""))[:600] for k in ("cmd", "out", "status", "code")}
                        if t.get("tool") in ("shell", "desk", "mcp") else {})
                     # Work in the page keeps its steps and the cards the person answered (search.py); a plan
                     # of a computer task keeps its list of steps, each marked ✓ done, → under way or · to do; a
                     # search of past chats keeps the chats it found (pastchats.py).
                     | ({k: str(t.get(k, ""))[:600] for k in ("out", "status", "confirmed")}
                        if t.get("tool") in ("operate", "plan", "chats") else {})
                     # At most 32 steps of a long task (loop.py allows up to 50 rounds).
                     for t in item.get("trace") or [] if isinstance(t, dict)][:32]
            # The reply's reasoning where the model returned it: seconds and text (abilities.note_reasoning).
            thought = item.get("thinking") if isinstance(item.get("thinking"), dict) else {}
            thinking = {"s": int(thought["s"]) if isinstance(thought.get("s"), (int, float)) else 0,
                        "text": str(thought.get("text") or "")[:8000]} if thought.get("text") else None
            # The memories the reply drew on (dm.py), listed under it.
            used = _memories((item.get("memory") or {}).get("used"))
            # One entry for each call the turn made (loop.ChatLookups.note).
            activity = [{k: str(a.get(k) or "")[:200] for k in ("place", "mark", "line", "status", "thumb")}
                        for a in item.get("activity") or [] if isinstance(a, dict)][:50]
            return {"role": "assistant", "text": str(item["text"]), **({"card": card} if card else {}),
                    **({"sources": sources} if sources else {}), **({"trace": trace} if trace else {}),
                    **({"activity": activity} if activity else {}),
                    **({"thinking": thinking} if thinking else {}), **({"memory": {"used": used}} if used else {})}
        if kind == "memory" and _memories(item.get("saved")):
            # What a fold kept from this chat: one quiet 已记住 line (character.py).
            return {"role": "memory", "saved": _memories(item["saved"])}
        if kind == "image":
            name = self._keep_picture(item.get("b64"))
            if name:
                return {"role": "assistant", "picture": name}
        return None

    def _entries(self, items: list) -> list[dict]:
        return [entry for entry in map(self._entry, items) if entry]

    def _keep_picture(self, encoded) -> str:
        """Store a picture the character sent; its file name, or "" when it is not one."""
        text = str(encoded or "")
        if not text or len(text) > MAX_PICTURE * 4 // 3 + 4:
            return ""
        try:
            data = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            return ""
        kind = _picture_kind(data)
        if not kind:
            return ""
        return self._store(self._pictures, f"{hashlib.sha256(data).hexdigest()[:40]}.{kind}", data)

    def _store(self, folder: Path, name: str, data: bytes) -> str:
        """Keep `data` as `name` in a chat's own folder; the name, or "" when it cannot be kept there."""
        path = folder / name
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if not self._own_folder(folder) or path.is_symlink():
                return ""
            if not path.is_file():
                # Exclusive: never through a link or file that appeared since the check.
                with open(path, "xb") as file:
                    file.write(data)
        except FileExistsError:
            return name if path.is_file() and not path.is_symlink() else ""
        except OSError:
            return ""
        return name

    def picture(self, name: str) -> Path | None:
        """A picture a message in one of the chats shows; None for any other name, link or missing file."""
        return self._stored(self._pictures, PICTURE_NAME, name)

    def chat_file(self, name: str) -> Path | None:
        """A text file the person attached to a message in one of the chats, as `picture`."""
        return self._stored(self._files, FILE_NAME, name)

    def _stored(self, folder: Path, pattern: re.Pattern, name: str) -> Path | None:
        if not pattern.fullmatch(str(name or "")) or not any(
                name in _stored_names(m) for chat in self._all_chats() for m in chat.history):
            return None
        path = folder / name
        try:
            # Resolving follows links and junctions: one leading out is refused.
            if not path.is_file() or not self._own_folder(folder) or path.resolve() != folder.resolve() / name:
                return None
        except OSError:
            return None
        return path

    @staticmethod
    def _own_folder(folder: Path) -> bool:
        """A chat's pictures or files folder exists and is not a link to another place."""
        try:
            return folder.is_dir() and folder.resolve() == folder.parent.resolve() / folder.name
        except OSError:
            return False

    def _prune_pictures(self) -> None:
        """Pictures and attached files no message shows any more are removed with the messages."""
        kept = set().union(*(_stored_names(m) for chat in self._all_chats() for m in chat.history))
        for folder, pattern in ((self._pictures, PICTURE_NAME), (self._files, FILE_NAME)):
            try:
                stored = list(folder.iterdir()) if self._own_folder(folder) else []
            except OSError:
                continue
            for path in stored:
                if pattern.fullmatch(path.name) and path.name not in kept and not path.is_symlink():
                    try:
                        path.unlink()
                    except OSError:
                        pass

    def _save_chat(self, chat: PageChat | None = None) -> None:
        from . import person
        chat = chat or self._main
        if not chat.private:
            # No ID or card number, password or code is kept in the page's copy (person.mask): not in a line, its
            # steps or the chat's title, nor where a reply repeats a password of theirs without its word.
            values = {k: v for m in chat.history if m.get("role") == "user" and isinstance(m.get("text"), str)
                      for k, v in person.secret_values(m["text"]).items()}
            chat.title = person.mask(chat.title, values)[0]
            for m in chat.history:
                if isinstance(m.get("text"), str):
                    m["text"] = person.mask(m["text"], values)[0]
                for step in m.get("trace") or []:
                    for key in ("what", "out"):
                        if isinstance(step.get(key), str):
                            step[key] = person.mask(step[key], values)[0]
                for entry in m.get("activity") or []:
                    entry["line"] = person.mask(entry["line"], values)[0]
        chat.save()
        self._prune_pictures()
        self.chat_changed()

    # The window app replaces these two to tell its pages at once (window.py); elsewhere nothing is told.
    def chat_changed(self) -> None:
        """A chat was saved: a reply, a remark, something shared or said unasked."""

    def figure_changed(self) -> None:
        """The character's figure may be another now."""

    def take_said(self, conversation: str, items: list) -> None:
        """What the character said on its own (the follow-up of a plan) joins the chat it is for."""
        chat = next((c for c in self._all_chats() if c.conversation == conversation), None)
        # A chat replaced or deleted since, or one being forgotten: nothing is shown for it.
        if chat is None or self._forgetting:
            return
        entries = self._entries(items)
        if entries:
            chat.append(entries)
            self._save_chat(chat)

    async def stop_chat(self, chat: str = MAIN_CHAT, why: str = "stopped") -> dict:
        """The stop button, or the page done waiting (`why` "timeout"): the reply
        under way in `chat` is cancelled, and the message it was answering is
        kept, marked, to send again. Answers once the reply has ended, so the
        page then reads the message as kept."""
        if self._replying is None or self._replying[0] is not self._chat(chat):
            return {"stopped": False}
        room, said, ended = self._replying
        said["stopping"] = why if why in ("stopped", "timeout") else "stopped"
        stopped = self.character.stop(room.conversation)
        if stopped:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(ended.wait(), 10)
        return {"stopped": stopped}

    def _runtime(self) -> Path:
        # Where the character keeps its state: .env over this process's
        # environment, else <home>/runtime.
        configured = dotenv_values(self.env).get("AGENT_RUNTIME_DIR")
        if configured is None:
            configured = os.environ.get("AGENT_RUNTIME_DIR", "")
        runtime = Path(configured.strip() or "runtime").expanduser()
        return runtime if runtime.is_absolute() else self.home / runtime

    async def forget_chat(self) -> dict:
        """Forget everything the main chat remembers, the chats it left behind
        included, then start it anew. The plain chats the page still lists keep
        theirs."""
        async with self._chat_lock:
            self._forgetting = True
            try:
                forgotten = await self.character.forget_chat(
                    self._main.conversation, [chat.conversation for chat in self._chats.values()])
            except NotReady:
                raise ValueError("activity_unavailable") from None
            finally:
                self._forgetting = False
            self.reset_chat()
            return {"ok": True, "forgotten": forgotten}

    @staticmethod
    def _read_attachments(raw) -> list[tuple[dict, bytes]]:
        """The pictures and files sent with a message, checked: each one's record
        for the message and what is stored for it. A file is kept as its text."""
        if not isinstance(raw, list) or len(raw) > ATTACH_MAX:
            raise ValueError("attach_too_many")
        taken = []
        for item in raw:
            # A page from the app's browser, by its address: the character reads it itself.
            if isinstance(item, dict) and item.get("kind") == "page":
                url = str(item.get("url") or "").strip()
                if urlsplit(url).scheme not in ("http", "https") or len(url) > 2000:
                    raise ValueError("generic")
                taken.append(({"kind": "page", "url": url,
                               "title": " ".join(str(item.get("title") or "").split())[:TITLE_MAX]}, b""))
                continue
            try:
                data = base64.b64decode(str(item.get("data") or ""), validate=True)
            except (AttributeError, binascii.Error, ValueError):
                raise ValueError("generic") from None
            if item.get("kind") == "picture":
                kind = _picture_kind(data)
                if not kind:
                    raise ValueError("picture_type")
                if len(data) > ATTACH_PICTURE:
                    raise ValueError("picture_too_large")
                name = f"{hashlib.sha256(data).hexdigest()[:40]}.{kind}"
                taken.append(({"kind": "picture", "name": name, "size": len(data)}, data))
                continue
            label = " ".join(str(item.get("name") or "").split())[:TITLE_MAX]
            if Path(label).suffix.lower()[1:] not in ATTACH_TEXT_KINDS:
                raise ValueError("file_type")
            if len(data) > ATTACH_FILE:
                raise ValueError("file_too_large")
            words = _file_text(data)
            if words is None:
                raise ValueError("file_unreadable")
            stored = words.encode("utf-8")
            taken.append(({"kind": "file", "name": f"{hashlib.sha256(stored).hexdigest()[:40]}.txt",
                           "file": label, "size": len(data), "clipped": len(words) > ATTACH_TEXT}, stored))
        return taken

    def _keep_attachments(self, taken: list[tuple[dict, bytes]]) -> list[dict]:
        """Store what was attached beside the character's pictures; the records for the message."""
        for record, data in taken:
            if record["kind"] == "page":
                continue
            folder = self._pictures if record["kind"] == "picture" else self._files
            if not self._store(folder, record["name"], data):
                raise ValueError("generic")
        return [record for record, _ in taken]

    def _attachment_parts(self, records: list[dict]) -> list[dict]:
        """What was attached, as the character reads it (messages._read_message): a picture inline, a
        file by its name and its text cut to ATTACH_TEXT, which it frames as material, not words to obey."""
        parts = []
        for record in records:
            if record["kind"] == "picture":
                data = (self._pictures / record["name"]).read_bytes()
                parts.append({"type": "picture", "b64": base64.b64encode(data).decode()})
                continue
            if record["kind"] == "page":
                parts.append({"type": "text", "text": f"\n[attached page from their in-app browser: "
                                                      f"“{record['title']}” {record['url']}]"})
                continue
            words = (self._files / record["name"]).read_text(encoding="utf-8")
            if len(words) > ATTACH_TEXT:
                words = (f"{words[:ATTACH_TEXT]}\n[cut here: the last {len(words) - ATTACH_TEXT} "
                         "characters were not sent]")
            parts.append({"type": "file", "name": record["file"], "text": words})
        return parts

    async def chat(self, text: str, after: int | None = None, retry: int | None = None,
                   chat: str = MAIN_CHAT, reply_to: str = "", attachments: list | None = None,
                   choices: list | None = None) -> dict:
        """Send `text` in one chat; the reply, and every message since `after` in
        order, including what the character sent on its own meanwhile. `retry`
        names the unanswered message this one sends again, which then goes, and
        whose attachments go again when none come with it. `reply_to` names a thing
        the character brought that this message answers. `attachments`: pictures and
        text files, as chat.js sends them (`_read_attachments`). `choices`: what the
        person chose under "+" for this message (turns.TURN_CHOICES), which the turn honours."""
        from .turns import TURN_CHOICES
        picked = [c for c in TURN_CHOICES if c in (choices if isinstance(choices, list) else [])]
        if not self.config().get("LLM_API_KEY"):
            raise ValueError("key_required")
        if not text.strip() and not attachments and retry is None:
            raise ValueError("generic")
        if len(text) > 8000:
            raise ValueError("invalid_message")
        taken = self._read_attachments(attachments or [])
        began = time.monotonic()
        asked_at = time.time()
        async with self._chat_lock:
            room = self._chat(chat)
            again = []
            if retry is not None:
                again = next((m.get("attachments") or [] for m in room.history
                              if m.get("seq") == retry and m.get("failed")), [])
                picked = picked or next((m.get("choices") or [] for m in room.history
                                         if m.get("seq") == retry and m.get("failed")), [])
                room.history = [m for m in room.history
                                if not (m.get("seq") == retry and m.get("failed"))]
            # Stored and recorded with no wait between, so no save in between finds them unshown.
            records = self._keep_attachments(taken) if taken else again
            if not text.strip() and not records:
                raise ValueError("generic")
            if room is not self._main and not room.title:
                room.title = (" ".join(text.split())
                              or next((r["file"] for r in records if r["kind"] == "file"), ""))[:TITLE_FIRST]
            # The character's own record of the chat holds only what was said in turns: what it
            # shared since the person last spoke reaches it as quotes in this message.
            quotes = self._quotes(room)
            asked = {"role": "user", "text": text, **({"attachments": records} if records else {}),
                     **({"choices": picked} if picked else {})}
            # The thing answered is quoted last, nearest the words about it, and shown in the message.
            answered = next((m for m in reversed(room.history)
                             if reply_to and (m.get("card") or {}).get("id") == reply_to), None)
            if answered:
                quotes = [q for q in quotes if q["of"] != answered["seq"]]
                quotes.append(self._quote(answered))
                card = answered["card"]
                asked["quote"] = {k: card.get(k) or "" for k in ("id", "title", "kind", "site", "picture")}
            # Recorded first, so what the character says on its own during the reply comes after it.
            (said,) = room.append([asked])
            # Kept now, marked, so a page process that ends mid-reply leaves it to send again.
            said["pending"] = True
            with contextlib.suppress(OSError):
                room.save()
            self._replying = (room, said, asyncio.Event())
            try:
                # Its own line, apart from what follows it.
                words = [{"type": "text", "text": f"{text}\n" if records else text}] if text.strip() else []
                items = await asyncio.wait_for(self.character.reply(
                    room.conversation, [*quotes, *words, *self._attachment_parts(records)], choices=picked),
                    REPLY_WITHIN_S)
            except BaseException as error:
                self._unanswered(said, chat_failure(error), room)
                raise
            finally:
                self._replying[2].set()
                self._replying = None
            said.pop("pending", None)
            stopped = said.pop("stopping", "")
            if stopped and not items:
                # Stopped from the page: nothing came, and the message waits to be sent again.
                self._unanswered(said, stopped, room)
                return {"reply": "", "stopped": True, **room.view(said["seq"] - 1 if after is None else after)}
            if any(item.get("reason") == "excuse" for item in items):
                # The model failed: here the admin checks the setup, so it is reported, not chatted about.
                # An account out of balance is named, with its console (abilities.note_problem).
                from . import abilities
                if abilities.problem_since(self._runtime(), asked_at) == "no_balance":
                    self._unanswered(said, "model_no_balance", room)
                    raise ValueError("model_no_balance")
                self._unanswered(said, "chat_failed", room)
                raise RuntimeError("the model did not answer")
            if not items:
                # The character had no words for it: shown as not sent, to send again.
                self._unanswered(said, "generic", room)
                raise ValueError("generic")
            entries = self._entries(items)
            if self.schedule is not None:
                # What the reply did to scheduled tasks, under it (panels/schedule.py).
                self.schedule.attach(entries)
            command = self._memory_command(text) if entries and text.strip() else None
            for entry in entries:
                # An answer to a memory command is shown whole at once, not typed out.
                if command:
                    entry["command"] = command[0]
                # How long the turn took, shown with its steps (activity.js).
                if entry.get("trace"):
                    entry["took"] = round(time.monotonic() - began)
            if command:
                # Under its last line, the way to Settings › Memory (chat.js).
                entries[-1]["manage"] = True
            room.append(entries)
            dropped = 0
            scrub = next((i["scrub"] for i in items if isinstance(i.get("scrub"), dict)), None)
            if scrub:
                # A memory command and its answer stay as they are; a turn not to keep goes whole.
                dropped = self._scrub_chats(scrub, room, {id(said), *map(id, entries)} if command else set())
            self._save_chat(room)
            if not room.private:
                # A 无痕对话 tells the recommender nothing.
                await self._chat_finds(items, entries, asked.get("quote"))
            reply = "\n".join(str(i.get("text", "")) for i in items if i.get("type") == "text")
            if dropped:
                # Earlier messages went: the page draws the chat afresh.
                return {"reply": reply, "forgotLines": dropped, **room.view()}
            since = said["seq"] - 1 if after is None else after
            return {"reply": reply, **room.view(since)}

    async def _chat_finds(self, items: list, entries: list, answered: dict | None = None) -> None:
        """What a reply did with finds: one it brought is seen in the feed and told, as the share step's
        are, and what the person said of one is kept as their reaction from the chat (feed.remember).
        `answered`: the find this message of theirs answers, which is a reaction to it by itself."""
        from .panels import feed
        runtime = self._runtime()
        item = self._library().get(answered["id"]) if answered else None
        if item is not None:
            await asyncio.to_thread(feed.remember, runtime, item, "talk", surface="chat")
        for entry in entries:
            ident = (entry.get("card") or {}).get("id")
            item = self._library().get(ident) if ident else None
            if item is not None:
                await asyncio.to_thread(self.pace.shown, "chat", item.id)
                await asyncio.to_thread(feed.remember, runtime, item, "seen", surface="chat", rank=0)
                await self.mark_item(item.id, "told")
        for said in (f for i in items for f in i.get("feedback") or [] if isinstance(f, dict)):
            item = self._library().get(str(said.get("id") or ""))
            if item is None:
                continue
            liked = said.get("liked") is True
            # "Not this source" is the feed's "less like this source".
            action = ("like" if liked else "hide_source" if said.get("about") == "source" and item.source
                      else "skip")
            words = {"about": str(said.get("about") or "")[:20], "words": str(said.get("words") or "")[:120]}
            await asyncio.to_thread(feed.remember, runtime, item, action, surface="chat", said=words)
            await asyncio.to_thread(self.pace.ended, item.id, "like" if liked else "close", "chat")
            if liked:
                await self.mark_item(item.id, "keep", True)
            else:
                self.fold_card(item.id, action)

    @staticmethod
    def _quotes(room: PageChat) -> list[dict]:
        """What the character shared in this chat since the person last spoke, as
        the quoted messages of theirs that it reads: its sentence, then the card."""
        shared = []
        for message in reversed(room.history):
            if message.get("role") == "user" and not message.get("failed"):
                break
            if (message.get("card") or {}).get("id"):
                shared.append(Workspace._quote(message))
            elif (message.get("task") or {}).get("quote"):
                # A reminder or a failure line the page said for a scheduled task (panels/schedule.py).
                shared.append({"type": "quote", "of": message["seq"],
                               "text": f"[{message['task']['title']}] {message.get('text', '')}"})
        return shared[:SHARE_QUOTED][::-1]

    @staticmethod
    def _quote(message: dict) -> dict:
        """One message that brought something, as a quote the character reads."""
        card = message["card"]
        # A thing it had no sentence for is said by its title: not twice.
        title = card.get("title") if card.get("title") != message.get("text") else ""
        facts = " · ".join(v for v in (card.get("kind"), title, card.get("site")) if v)
        return {"type": "quote", "of": message["seq"],
                "text": " ".join(v for v in (message.get("text"), f"[{facts}]", card.get("excerpt")) if v)}

    def _memory_command(self, text: str):
        """(kind, argument, chat language) when the character reads `text` as a
        memory command; None for conversation or when it cannot be told."""
        values = self.config()
        try:
            if self._commands is None:
                self._commands = _CommandReader()
            return self._commands.read(text, values.get("PERSONA_NAME", "").strip(),
                                       _card_aliases(self.home, values),
                                       langs.normalize(values.get("AGENT_LANG")) or "en")
        except Exception:
            return None

    def _scrub_chats(self, scrub: dict, room: PageChat, keep: frozenset | set = frozenset()) -> int:
        """What the character forgot, or was asked not to keep, leaves the chats'
        own copies. A forget (`mark`: its language) puts the forgotten mark in place
        of each sentence holding the words, in every chat, and of the lines it names
        in this one; a turn not to keep goes from this chat whole. The messages
        `keep` names stay. How many changed."""
        from . import person
        lines = {" ".join(part.split()) for x in scrub.get("lines") or [] for part in [str(x), *str(x).split("\n")]}
        lines.discard("")
        said = person.forget_hit(scrub.get("words") or [], scrub.get("texts") or [])
        mark = scrub.get("mark")
        changed = 0
        for chat in self._all_chats():

            def hit(text: str, chat=chat) -> bool:
                return said(text) or chat is room and " ".join(text.split()) in lines
            rows = []
            for m in chat.history:
                text = str(m.get("text") or "")
                if id(m) in keep or not hit(text):
                    rows.append(m)
                    continue
                changed += 1
                if mark:
                    rows.append({**m, "text": person.scrub(text, hit, mark)})
            if len(rows) != len(chat.history) or any(a is not b for a, b in zip(rows, chat.history)):
                chat.history = rows
                if chat is not room:
                    with contextlib.suppress(OSError):
                        chat.save()
        return changed

    def _unanswered(self, said: dict, code: str, room: PageChat) -> None:
        """Kept, marked, so the person can see which message went unanswered and send it again."""
        said.pop("pending", None)
        said["failed"] = code
        with contextlib.suppress(OSError):
            self._save_chat(room)

    # -- what the character knows of the one person (person.json)

    async def person_write(self, action: str, payload: dict) -> dict:
        """forget, edit or restore one item, forget all, add one, the two switches, or a pasted list read and its
        ticked items kept; the view after it."""
        item, text, lines = str(payload.get("id") or ""), str(payload.get("text") or ""), payload.get("lines")
        switches = {k: payload[k] for k in ("on", "health") if isinstance(payload.get(k), bool)}
        if (action not in PERSON_ACTIONS or len(item) > 64
                or len(text) > (PERSON_PASTE_MAX if action == "import_preview" else 400)
                or (action in ("forget", "forget_chats", "edit", "restore") and not item)
                or (action in ("edit", "add", "import_preview") and not text.strip())
                or (action == "settings" and not switches)
                or (action == "import" and not (isinstance(lines, list) and 0 < len(lines) <= PERSON_IMPORT_MAX
                                                and all(isinstance(x, str) and 0 < len(x) <= 400 for x in lines)))):
            raise ValueError("invalid_chat")
        try:
            raw = await self.character.person_write(action, self._main.conversation, item, text,
                                                    {**switches, "lines": lines})
        except NotReady:
            raise ValueError("activity_unavailable") from None
        scrub = raw.pop("scrub", None)
        if scrub:
            # The page's own copies of the chats lose the words too.
            async with self._chat_lock:
                self._scrub_chats(scrub, self._main)
                self._save_chat()
        return raw

    # -- the character's finds

    def _library(self) -> Library:
        return Library(self._runtime())

    def _item(self, payload) -> life.Item:
        ident = str((payload or {}).get("id") or "")
        item = self._library().get(ident) if ITEM_ID.fullmatch(ident) else None
        if item is None:
            raise ValueError("item_gone")
        return item

    async def mark_item(self, ident: str, action: str, kept: bool = True) -> dict:
        """Keep, dismiss or mark told one thing the character saw."""
        done = await asyncio.to_thread(self.character.mark, ident, action, kept)
        if done is None:
            raise ValueError("item_gone")
        if action == "keep":
            # A card in the main chat shows the same choice the next time it is drawn.
            cards = [m["card"] for m in self._main.shared() if m["card"]["id"] == ident]
            for card in cards:
                card["liked"] = done
            if cards:
                self._save_chat()
        return {"ok": True, "kept": done}

    def fold_card(self, ident: str, action: str) -> None:
        """A card turned down in the main chat stays folded the next time it is drawn, to the line `action`
        names (skip, topic, hide_source, or known for seen already); "" unfolds it (the no was taken back)."""
        cards = [m["card"] for m in self._main.shared() if m["card"]["id"] == ident]
        for card in cards:
            card["skipped"] = action
        if cards:
            self._save_chat()

    async def _say(self, items: list, served: dict | None = None) -> dict:
        """Bring things it saw into the main chat as its own messages, each with
        its card; each is then seen in the feed and told, so neither the pet's
        card nor this chat brings it again. `served`: the choice's fields by id: the
        character's line, said as the message, and the ranking's slot; its rank is its
        place in the batch. Without it, a find the pet's card showed keeps what the card was handed."""
        from .panels import feed

        served = feed.on_card if served is None else served
        since = self._main.seq
        if items:
            messages = [life.said(item) for item in items]
            for rank, (item, message) in enumerate(zip(items, messages)):
                shaped = (served or {}).get(item.id, {})
                if shaped.get("line"):
                    message["text"] = shaped["line"]
                if message.get("card"):
                    # The chip under its site and the topic a no may name, as the chooser dressed it (recommend.Why).
                    message["card"].update(rank=rank, slot=shaped.get("slot", ""), why=shaped.get("why"),
                                           topic=shaped.get("topic"))
            self._append(messages)
            self._save_chat()
            for rank, item in enumerate(items):
                await asyncio.to_thread(feed.remember, self._runtime(), item, "seen", surface="chat", rank=rank,
                                        slot=(served or {}).get(item.id, {}).get("slot", ""))
                await self.mark_item(item.id, "told")
        return {"count": len(items), **self.history(since)}

    async def _share_due(self) -> bool:
        """Whether the share step brings something now, as the pacing says; never before the first run is done."""
        if not app_settings(self._history_path.parent).get("toured"):
            return False
        return await self.pace.due("chat")

    async def share(self) -> int:
        """The share step: the character's choice comes into the main chat as its
        own message, once one is ready (the chooser wakes this step then). One at a
        time with the chats: skipped while one is busy."""
        if self._chat_lock.locked():
            return 0
        async with self._chat_lock:
            if not await self._share_due():
                return 0
            chosen = await self.chooser.take("chat")
            if chosen is None:
                return 0
            shaped, item = chosen
            await asyncio.to_thread(self.pace.shown, "chat", item.id)
            await self._say([item], {item.id: shaped})
            return 1

    async def share_loop(self) -> None:
        """The share step every SHARE_EVERY_S, and at once when something new came in (found)."""
        while not self._closed:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._share_wake.wait(), SHARE_EVERY_S)
            self._share_wake.clear()
            await quiet.wait()
            try:
                await self.share()
            except Exception:
                traceback.print_exc()

    def found(self) -> None:
        """Something new came in (a sources round kept some, the tour ended): the
        share step looks now, and so does the pet (news)."""
        self._share_wake.set()
        self.news()

    def news(self) -> None:
        """The window app replaces this to have the pet look at once (window.py)."""

    def busy(self, doing: str) -> None:
        """The window app replaces this: a reply is on its way ("thinking"), or "" when it is in (window.py pet_busy)."""

    async def bring(self, payload: dict) -> dict:
        """One thing into the main chat (the pet's card opens it there): the message
        that already carries it, else the character shares it now."""
        item = self._item(payload)
        async with self._chat_lock:
            here = [m for m in self._main.shared() if m["card"]["id"] == item.id]
            if not here:
                await self._say([item])
                here = [m for m in self._main.shared() if m["card"]["id"] == item.id]
            return {"seq": here[-1]["seq"]}

    async def remark(self, text: str) -> bool:
        """A line of its own about what its person is doing, into the main chat; False while a chat is busy."""
        if self._chat_lock.locked():
            return False
        async with self._chat_lock:
            # The page draws a remark as a quiet aside, not a reply.
            self._append([{"role": "assistant", "text": text, "remark": True}])
            self._save_chat()
        return True

    def library_picture(self, ident: str) -> Path | None:
        return self._library().picture_path(ident) if ITEM_ID.fullmatch(str(ident or "")) else None

    def close(self) -> None:
        with self._lock:
            self._closed = True


def _expands(text: str) -> bool:
    """Would dotenv read `text` back as another setting? It expands ${VAR}
    even inside quotes, so ${LLM_API_KEY} saved as a vision key is the chat key."""
    return "${" in str(text)


def _endpoint(value) -> str:
    """An OpenAI-compatible base URL: https, or http only on this computer."""
    base = str(value or "").strip()
    parsed = urlsplit(base)
    if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username
            or parsed.password or _expands(base)):
        raise ValueError("invalid_url")
    if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("https_required")
    return base


def create_app(home: Path, token: str, *, workspace: Workspace | None = None) -> FastAPI:
    """The page, for this computer alone: a loopback address, and the token for the API."""
    workspace = workspace or Workspace(home)

    @asynccontextmanager
    async def lifespan(app):
        # The character, with its own loops: its memory, its walks, the follow-up of a plan.
        await workspace.character.start()
        # The saved model measured once when nothing is kept for it, before the first reply (loop.py waits for it).
        modelbar.check_at_start(workspace)
        # Another program in front, or another tab: maybe a good moment to bring something (pacing.py).
        quiet.on_front(lambda: asyncio.ensure_future(workspace.pace.look()))
        # Games and fullscreen in front: the background loops wait and the pet hides (quiet.py).
        later = [asyncio.create_task(quiet.watch(workspace._runtime))]
        # The public sources need no model: they are read whenever the page runs.
        later.append(asyncio.create_task(app.state.sources.loop()))
        # What the character found comes into the main chat at its own pace, whether or not it is open.
        later.append(asyncio.create_task(workspace.share_loop()))
        # The person's MCP servers run with the page and stop with it (mcp_hub.py).
        later.append(asyncio.create_task(app.state.mcp.run()))
        # Scheduled tasks run when due, whether or not the window is open (panels/schedule.py).
        later.append(asyncio.create_task(app.state.schedule.loop()))
        # Now and then a line about what the person is doing (remarks.py).
        later.append(asyncio.create_task(app.state.remarks.loop()))
        try:
            yield
        finally:
            for task in later:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            for stop in app.state.closers:
                await asyncio.to_thread(stop)
            await workspace.character.close()
            workspace.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.workspace = workspace
    # What a panel started and must stop; called before the character stops.
    app.state.closers = []

    def refuse(code: str = "forbidden", status: int = 403) -> JSONResponse:
        return JSONResponse({"error": code}, status_code=status)

    def judge(request: Request) -> JSONResponse | None:
        """None when the request may go on: addressed to a loopback name, and to the
        API only with the token, from the page's own origin, through no proxy."""
        try:
            host = urlsplit("//" + request.headers.get("host", "")).hostname
        except ValueError:
            return refuse()
        if host not in LOOPBACK:
            return refuse()
        if request.url.path.startswith("/api/"):
            if not secrets.compare_digest(request.headers.get(TOKEN_HEADER, "").encode(), token.encode()):
                return refuse()
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return refuse()
            if any(request.headers.get(key) for key in FORWARDED):
                return refuse()
        return None

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        response = judge(request) or await call_next(request)
        # Only the portraits set their own: they never change while the page runs.
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
            "connect-src 'self'; frame-src " + " ".join(f"https://{host}" for host in life.PLAYER_HOSTS) + "; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    async def body(request: Request, limit: int = MAX_BODY) -> dict:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > limit:
                raise HTTPException(413, "too_large")
        try:
            value = json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(400, "invalid_json") from None
        if not isinstance(value, dict):
            raise HTTPException(400, "invalid_json")
        return value

    @app.exception_handler(ValueError)
    async def bad_value(request, exc):
        allowed = {"busy", "invalid_provider", "key_required", "invalid_url", "https_required",
                   "model_required", "invalid_character", "key_rejected", "model_limited", "model_unreachable",
                   "model_no_balance", "model_not_found",
                   "key_malformed", "key_again", "name_required",
                   "invalid_message", "invalid_chat", "activity_unavailable", "activity_changed",
                   "memory_secret", "memory_not_instructions", "memory_paused", "memory_health_off",
                   "avatar_type", "avatar_too_large", "item_gone", "invalid_title",
                   "attach_too_many", "picture_type", "picture_too_large", "file_type", "file_too_large",
                   "file_unreadable"}
        error = str(exc)
        known = error in allowed or any(
            error in importlib.import_module(f"{__package__}.panels.{name}").ERRORS for name in PANELS)
        return JSONResponse({"error": error if known else "generic"}, status_code=400)

    @app.get("/")
    async def index():
        # The app: what the character found, and the chat.
        return FileResponse(ASSETS / "app.html")

    @app.get("/pet")
    async def pet():
        # The character alone, for the small window mibo starts as.
        return FileResponse(ASSETS / "pet.html")

    @app.get("/compact")
    async def compact():
        # The app page again, laid out as the small chat beside the pet (compact.js).
        return FileResponse(ASSETS / "app.html")

    @app.get("/locale.js")
    async def locale_script():
        # The interface's language and its catalog, English beside it, for the page's other scripts (i18n.js).
        code = i18n.lang(await asyncio.to_thread(workspace.config))
        data = json.dumps([code, i18n.catalog(code), i18n.catalog("en")], ensure_ascii=False)
        return Response(f"paI18n.load(...{data});\n", media_type="text/javascript; charset=utf-8",
                        headers={"Cache-Control": "no-store"})

    @app.get("/assets/locales/{name}")
    async def locale_file(name: str):
        # A language's catalog, for a page that changes to it.
        if name.removesuffix(".json") not in langs.CODES or not name.endswith(".json"):
            raise HTTPException(404)
        return FileResponse(i18n.LOCALES / name, media_type="application/json")

    @app.get("/health")
    async def health():
        # That the page answers, nothing more.
        return {"ok": True}

    @app.get("/assets/{name}")
    async def asset(name: str):
        if name not in PAGE_ASSETS:
            raise HTTPException(404)
        return FileResponse(ASSETS / name)

    @app.get("/assets/figures/{name}")
    async def pet_figure(name: str):
        # A figure is a drawing (<key>.svg) or a sheet of frames cut from a painting (<key>.webp),
        # and a painted one has the pet's poses too (<key>.pet.webp, laid out in <key>.pet.json).
        key, _, kind = name.partition(".")
        path = ASSETS / "figures" / name
        if key not in PET_FIGURES or kind not in ("svg", "webp", "pet.webp", "pet.json") or not path.is_file():
            raise HTTPException(404)
        if name == "mibo.svg":
            # Mibo in the colours chosen for it.
            from .panels import colors
            chosen = colors.chosen(await asyncio.to_thread(app_settings, workspace.home / "runtime" / "desktop"))
            return Response(colors.paint(path.read_text(encoding="utf-8"), chosen), media_type="image/svg+xml")
        return FileResponse(path, media_type={"svg": "image/svg+xml", "pet.json": "application/json"}.get(kind, "image/webp"))

    @app.get("/assets/setup/{name}")
    async def setup_part(name: str):
        stem, _, kind = name.rpartition(".")
        # A section's script, and the styles of the one that has its own (skills.css).
        if stem not in SETUP_PARTS or kind not in ("js", "css") or not (ASSETS / "setup" / name).is_file():
            raise HTTPException(404)
        return FileResponse(ASSETS / "setup" / name)

    @app.get("/assets/characters/{name}")
    async def character_portrait(name: str):
        path = sw.portrait(name.removesuffix(".webp")) if name.endswith(".webp") else None
        if path is None:
            raise HTTPException(404)
        return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/state")
    async def state():
        summary = await asyncio.to_thread(workspace.summary)
        summary["modelUnreachable"] = workspace.character.model_unreachable()
        return summary

    @app.get("/api/catalog")
    async def catalog(request: Request):
        values = await asyncio.to_thread(workspace.config)
        # The saved language, else the one this computer's browser asks for: DeepSeek leads for Chinese.
        lang = langs.normalize(values.get("AGENT_LANG")) or request.headers.get("accept-language", "")
        # Names and notes in the interface's language.
        words = i18n.words("catalog", i18n.lang(values))["providers"]
        return {"providers": [{"id": p.key, "name": words[p.key]["name"], "baseUrl": p.base_url,
                               "model": p.model, "keyUrl": p.key_url, "note": words[p.key].get("note", ""),
                               "local": not p.needs_key} for p in sw.providers_for(lang)],
                "characters": [{"id": c.key,
                                # By the character's language; ownNames adds the names it had before.
                                "defaultNames": {lang: c.name_in(lang) for lang in sw.LANGS} if c.stem else None,
                                "ownNames": sorted(c.own_names),
                                "avatar": f"/assets/characters/{c.key}.webp" if sw.portrait(c.key) else None}
                               for c in sw.CHARACTERS],
                "ownAvatarLimit": sw.MAX_OWN_AVATAR, "personaLimit": PERSONA_LIMIT}

    @app.post("/api/config")
    async def config(request: Request):
        return await asyncio.to_thread(workspace.save, await body(request))

    @app.get("/api/avatar")
    async def own_avatar():
        path = sw.own_avatar(workspace.home)
        if path is None:
            raise HTTPException(404)
        return FileResponse(path, media_type=sw.OWN_AVATAR_TYPES[path.suffix[1:]])

    @app.post("/api/avatar")
    async def save_own_avatar(request: Request):
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > sw.MAX_OWN_AVATAR:
            raise ValueError("avatar_too_large")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > sw.MAX_OWN_AVATAR:
                raise ValueError("avatar_too_large")
        return await asyncio.to_thread(workspace.save_own_avatar, bytes(raw))

    @app.post("/api/avatar/remove")
    async def remove_own_avatar(request: Request):
        await body(request)
        return await asyncio.to_thread(workspace.remove_own_avatar)

    @app.post("/api/chat")
    async def chat(request: Request):
        # Room for what is attached: pictures go as base64.
        payload = await body(request, CHAT_BODY)

        def number(value):
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        workspace.busy("thinking")
        try:
            return await workspace.chat(str(payload.get("text") or ""), number(payload.get("after")),
                                        number(payload.get("retry")), str(payload.get("chat") or MAIN_CHAT),
                                        str(payload.get("reply_to") or ""), payload.get("attachments"),
                                        **({"choices": payload["choices"]} if payload.get("choices") else {}))
        except ValueError:
            raise
        except Exception as error:
            return JSONResponse({"error": chat_failure(error)}, status_code=502)
        finally:
            workspace.busy("")

    @app.get("/api/chat/doing")
    async def chat_doing(after: int | None = None):
        # The line above the pet's head: whether a reply is on its way, the main chat's step, and its last
        # message's number; with `after`, the last words the character said since (the bubble of a reply
        # that came in after its compact chat was put away). Nothing shared.
        view = workspace.history(1 << 30 if after is None else after)
        said = [m.get("text") for m in view.get("messages") or [] if m.get("role") != "user" and m.get("text")]
        return {"busy": workspace._chat_lock.locked(), "doing": view.get("doing"), "last": view["last"],
                "said": said[-1] if after is not None and said else ""}

    @app.post("/api/chat/stop")
    async def stop(request: Request):
        payload = await body(request)
        return await workspace.stop_chat(str(payload.get("chat") or MAIN_CHAT), str(payload.get("why") or "stopped"))

    @app.post("/api/chat/reset")
    async def reset(request: Request):
        await body(request)
        async with workspace._chat_lock:
            workspace.reset_chat()
        return {"ok": True}

    @app.post("/api/chat/forget")
    async def forget(request: Request):
        await body(request)
        return await workspace.forget_chat()

    @app.get("/api/chat/history")
    async def history(after: int | None = None, chat: str = MAIN_CHAT):
        # Not in the middle of a reply, when the page looks only for what it is looking up.
        if workspace._chat(chat) is workspace._main and not workspace._chat_lock.locked():
            # Looked at: what the character found may come in now, when it is time.
            try:
                await workspace.share()
            except Exception:
                # The chat still opens with what it holds; the loop tries again.
                traceback.print_exc()
        return workspace.history(after, chat)

    @app.post("/api/chat/bring")
    async def bring(request: Request):
        return await workspace.bring(await body(request))

    @app.get("/api/chats")
    async def chats():
        return workspace.chats()

    @app.post("/api/chats")
    async def new_chat(request: Request):
        return await workspace.new_chat((await body(request)).get("private") is True)

    @app.post("/api/chats/rename")
    async def rename_chat(request: Request):
        return await workspace.rename_chat(await body(request))

    @app.post("/api/chats/delete")
    async def delete_chat(request: Request):
        return await workspace.delete_chat(await body(request))

    @app.get("/api/chat/picture/{name}")
    async def chat_picture(name: str):
        path = workspace.picture(name)
        if path is None:
            raise HTTPException(404)
        # Not cached: a picture removed since must show as removed.
        return FileResponse(path, media_type=PICTURE_TYPES[path.suffix[1:]])

    @app.get("/api/chat/file/{name}")
    async def chat_file(name: str):
        path = workspace.chat_file(name)
        if path is None:
            raise HTTPException(404)
        # Stored as UTF-8 text whatever it was written in; never run or shown as a page.
        return FileResponse(path, media_type="text/plain; charset=utf-8")

    @app.get("/api/person")
    async def person_view():
        try:
            return await asyncio.to_thread(workspace.character.person_view)
        except MemoryFileDamaged:
            raise ValueError("activity_unavailable") from None

    @app.get("/api/person/said/{item}")
    async def person_said(item: str):
        """The chat lines one kept item came from (查看原话)."""
        try:
            return {"lines": await workspace.character.person_said(item[:64])}
        except NotReady:
            raise ValueError("activity_unavailable") from None

    def person_export(fmt: str) -> dict:
        if fmt not in ("json", "md"):
            raise ValueError("invalid_chat")
        day = time.strftime("%Y-%m-%d")
        return {"file": f"mibo-memory-{day}.{fmt}", "text": workspace.character.person_export(fmt)}

    @app.get("/api/person/export")
    async def person_export_get(format: str = "json"):
        """Everything kept, as a file's name and words; a browser page saves it itself."""
        return await asyncio.to_thread(person_export, format)

    @app.post("/api/person/export")
    async def person_export_save(request: Request):
        """In the app window: the system's Save dialog, and the file written where the person chose; cancelled,
        nothing is written."""
        out = await asyncio.to_thread(person_export, str((await body(request)).get("format") or ""))
        window = getattr(app.state, "window", None)
        if window is None:
            raise ValueError("no_window")
        path = await asyncio.to_thread(window.save_file, out["file"])
        if path:
            await asyncio.to_thread(Path(path).write_text, out["text"], encoding="utf-8")
        return {"saved": bool(path)}

    @app.post("/api/person/{action}")
    async def person_write(request: Request, action: str):
        return await workspace.person_write(action, await body(request))

    @app.get("/api/home")
    async def home_now():
        return await asyncio.to_thread(workspace.character.now)

    @app.get("/api/library/picture/{ident}")
    async def library_picture(ident: str):
        path = workspace.library_picture(ident)
        if path is None or path.suffix not in LIBRARY_PICTURE_TYPES:
            raise HTTPException(404)
        return FileResponse(path, media_type=LIBRARY_PICTURE_TYPES[path.suffix])

    @app.post("/api/show")
    async def show(request: Request):
        await body(request)
        callback = getattr(app.state, "show", None)
        return {"ok": True, "window": bool(callback and await asyncio.to_thread(callback))}

    @app.post("/api/exit")
    async def exit_app(request: Request):
        await body(request)
        callback = getattr(app.state, "shutdown", None)
        if callback:
            callback()
        return {"ok": True}

    for name in PANELS:
        importlib.import_module(f"{__package__}.panels.{name}").mount(app, workspace, body)
    # A turn's MCP tools come from the hub the tools panel made (mcp_offer.py).
    from . import mcp_offer
    mcp_offer.offer_source = app.state.mcp.offer
    # Commands on this computer: their cards, Settings' section and the log (shell/panel.py).
    from .shell import panel as shell_panel
    shell_panel.mount(app, workspace, body)
    # The card that turns a switched-off ability on from the chat (turnon.py).
    from . import turnon
    turnon.mount(app, workspace, body)
    # Skills: Settings' section, adding and removing them (skills/panel.py).
    from .skills import panel as skills_panel
    skills_panel.mount(app, workspace, body)
    # Other programs' windows, on Windows: their cards, Settings' section and the log (desk/panel.py).
    if os.name == "nt":
        from .desk import panel as desk_panel
        desk_panel.mount(app, workspace, body)
    return app


def _raise_window(url: str) -> bool:
    """Ask the mibo already running to bring its window to the front."""
    address = urlsplit(url)
    if os.name == "nt":
        import ctypes
        # Windows lets a window take the foreground only when the process the
        # person just started says it may.
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    try:
        answer = httpx.post(f"{address.scheme}://{address.netloc}/api/show", json={}, timeout=5,
                            trust_env=False,
                            headers={TOKEN_HEADER: address.fragment.partition("token=")[2]})
        return bool(answer.json().get("window"))
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


def app_settings(desktop: Path) -> dict:
    """What the person chose about the app itself, not about the character."""
    return sw._read_json(desktop / "app.json")


def save_app_settings(desktop: Path, **changes) -> dict:
    values = {**app_settings(desktop), **changes}
    atomic_write_text(desktop / "app.json", json.dumps(values))
    return values


def pet_figure(desktop: Path, config: dict) -> str:
    """Which figure the pet is: the one chosen from its menu, else the character's own, else the first."""
    settings = app_settings(desktop)
    if settings.get("pet") in PET_FIGURES:
        return settings["pet"]
    avatar = config.get("PERSONA_AVATAR", "")
    return avatar if avatar in PET_FIGURES else PET_FIGURES[0]


def _serve_in_window(app: FastAPI, server, sockets: list, url: str, desktop: Path,
                     hidden: bool = False) -> None:
    """The page in its own window on this thread, the server behind it on another."""
    def words() -> dict:
        # The notification-area icon's menu and the pet's own.
        return i18n.words("tray", i18n.lang(app.state.workspace.config()))

    def figure() -> str:
        return pet_figure(desktop, app.state.workspace.config())

    def figures() -> list[tuple[str, str]]:
        from .panels import look
        return [(row["key"], row["name"]) for row in look.names(app.state.workspace, "")]

    def choose(key: str) -> None:
        # The pet's menu chooses a whole character, as the app's pickers do.
        from .panels import look
        look.choose_figure(app.state.workspace, key)

    window = appwindow.AppWindow(
        url, storage=desktop / "webview", words=words,
        background=lambda: bool(app_settings(desktop).get("background")),
        # Hidden only makes sense where closing can hide too.
        start_hidden=hidden and bool(app_settings(desktop).get("background")),
        pet=True, figure=figure, figures=figures,
        choose=choose,
        # Links open in the browser inside the app window unless the person chose the system's
        # (panels/browser.py), where a find's reading is timed: the library knows a find by its address.
        links=lambda: app_settings(desktop).get("links", "app"),
        find=lambda address: getattr(app.state.workspace._library().get(item_id(address)), "id", ""),
        address=lambda ident: getattr(app.state.workspace._library().get(ident), "url", ""))
    app.state.window = window
    # The window's pages are told of a chat or a figure that changed, or of something found, and need not keep asking.
    app.state.workspace.chat_changed = lambda: window.chats_changed()
    app.state.workspace.figure_changed = lambda: window.refigure()
    app.state.workspace.news = lambda: window.pet_look()
    app.state.workspace.busy = lambda doing: window.pet_busy(doing)

    def stop() -> None:
        server.should_exit = True
        window.close()

    def serve() -> None:
        try:
            server.run(sockets=sockets)
        finally:
            window.close()

    app.state.shutdown = stop
    app.state.show = window.show
    serving = threading.Thread(target=serve, name="mibo-page")
    serving.start()
    try:
        # No error and no window: it was closed before it finished opening.
        if not window.run() and window.error:
            app.state.show = None
            print(i18n.t("start.noWindow", i18n.lang(app.state.workspace.config()))
                  + f" ({window.error})", flush=True)
            webbrowser.open(url)
            while serving.is_alive():
                serving.join(0.5)
    except KeyboardInterrupt:
        # Ctrl+C closed the window; the signal itself may land only here.
        pass
    finally:
        stop()
        serving.join()


def main(argv: list[str] | None = None, prog: str = "mibo start") -> int:
    lang = i18n.lang()
    parser = argparse.ArgumentParser(prog=prog, description=i18n.t("start.about", lang))
    parser.add_argument("--browser", action="store_true", help=i18n.t("start.browser", lang))
    parser.add_argument("--no-browser", action="store_true", help=i18n.t("start.noBrowser", lang))
    parser.add_argument("--background", action="store_true", help=i18n.t("start.background", lang))
    parser.add_argument("--port", type=int, default=0, help=i18n.t("start.port", lang))
    # Started by the app itself after a restore or a reset: it waits for the one before to close.
    parser.add_argument("--restart", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    home = homes.find_home()
    # Before anything is written in it: app.log and welcome.json hold the page's token link.
    sw.secure_home(home)
    desktop = home / "runtime" / "desktop"
    # Started by pythonw, as the shortcut does: no terminal, so sentences go to
    # a log and a refusal to a dialog.
    quiet = sys.stdout is None or sys.stderr is None
    log = None
    if quiet:
        desktop.mkdir(parents=True, exist_ok=True)
        # Appended: a second start must not empty the log of the one running.
        log = sys.stdout = sys.stderr = open(desktop / "app.log", "a", encoding="utf-8", buffering=1)

    def refuse(text: str) -> int:
        print(text, flush=True)
        if quiet:
            appwindow.alert(text)
        return 1

    try:
        return _start(args, home, refuse, log)
    except Exception:
        if not quiet:
            raise
        traceback.print_exc()
        appwindow.alert(i18n.t("cli.broken", lang, log=desktop / "app.log"))
        return 1


def _start(args: argparse.Namespace, home: Path, refuse: Callable[[str], int], log=None) -> int:
    import uvicorn
    from .textproc import DESKTOP_ENV
    # The desktop app keeps this computer's clock (textproc.default_tz_offset).
    os.environ[DESKTOP_ENV] = "1"
    desktop = home / "runtime" / "desktop"
    info = desktop / "welcome.json"
    lang = i18n.lang()
    lock = FileLock(info.with_suffix(".lock"), timeout=RESTART_WAIT_S if args.restart else 0)
    try:
        lock.acquire()
        if log:
            # This start is the one that runs: its log begins here.
            log.truncate(0)
    except LockUnavailable:
        url = sw._read_json(info).get("url", "")
        if not url:
            return refuse(i18n.t("start.starting", lang))
        print(url, flush=True)
        if not args.no_browser and (args.browser or not _raise_window(url)):
            webbrowser.open(url)
        return 0
    token = secrets.token_urlsafe(32)
    sockets: list[socket.socket] = []
    try:
        port = args.port or _page_port(home)
        if port is None:
            return refuse(i18n.t("start.pagePortBad", lang))
        from .panels import desk
        desk.apply_pending(home)
        try:
            sockets = [_listen(port)]
        except OSError:
            if args.port:
                return refuse(i18n.t("start.portFlagBusy", lang, port=args.port))
            return refuse(i18n.t("start.portBusy", lang, port=port))
        port = sockets[0].getsockname()[1]
        url = f"http://{PAGE_HOST}:{port}/#token={token}"
        atomic_write_text(info, json.dumps({"url": url}))
        windowed = not (args.browser or args.no_browser) and appwindow.available()
        if windowed:
            print(i18n.t("start.inWindow", lang, version=__version__, url=url), flush=True)
        else:
            print(i18n.t("start.inTerminal", lang, version=__version__, url=url), flush=True)
        found = updates.notice(home)
        if found:
            print(i18n.t("start.update", lang, **found), flush=True)
        app = create_app(home, token)
        app.state.relaunch = functools.partial(relaunch, home, args)
        server = uvicorn.Server(uvicorn.Config(app, host=PAGE_HOST, port=port, log_level="warning"))
        if windowed:
            _serve_in_window(app, server, sockets, url, desktop, hidden=args.background)
            return 0
        if not args.no_browser:
            # The socket already listens, so an early request waits its turn.
            timer = threading.Timer(0, webbrowser.open, args=(url,))
            timer.daemon = True
            timer.start()
        app.state.shutdown = lambda: setattr(server, "should_exit", True)
        server.run(sockets=sockets)
        return 0
    finally:
        for sock in sockets:
            sock.close()
        info.unlink(missing_ok=True)
        lock.release()


def relaunch(home: Path, args: argparse.Namespace) -> None:
    """Start the app again as it was started, to take the home over once this one has closed."""
    from . import autostart
    command = [sys.executable, "start"] if autostart.frozen() else [sys.executable, "-m", "mibo", "start"]
    command += ["--home", str(home), "--restart"]
    if args.port:
        command += ["--port", str(args.port)]
    if args.no_browser or args.browser:
        command.append("--no-browser" if args.no_browser else "--browser")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    subprocess.Popen(command, stdin=subprocess.DEVNULL, close_fds=True, creationflags=flags,
                     start_new_session=os.name != "nt")


def _page_port(home: Path) -> int | None:
    """PAGE_PORT, the environment over the home's .env: 0 when blank, None when it is no port."""
    raw = (os.environ.get("PAGE_PORT") or sw.env_get(home / ".env", "PAGE_PORT") or "").strip()
    if not raw:
        return 0
    return int(raw) if raw.isdigit() and 0 < int(raw) < 65536 else None


def _listen(port: int) -> socket.socket:
    """The page's socket, on this computer only. OSError when it cannot bind."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((PAGE_HOST, port))
        sock.listen(128)
    except (OSError, OverflowError):
        sock.close()
        raise OSError("bind") from None
    return sock


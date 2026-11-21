"""The home's settings: settings.json, one JSON object a person can read and edit by hand, each value checked
against the Settings model below; the model keys (LLM_API_KEY and the other *_API_KEY settings) in the
system's credential store (secretstore.py), never in the file.

JSON rather than TOML: the standard library reads and writes it on every Python the app supports (tomllib
reads from 3.11 only and writes nowhere, so TOML would add two packages), pydantic checks it as it is, and
storage.atomic_write_text keeps it whole.

* A setting that does not fit its type is kept in the file as written, logged, named in Settings › 本机, and
  read as its default. A name the model does not know is kept, logged and named there too; it changes nothing.
* A variable set in the environment the app started with wins over the file (load), so a developer's shell
  and CI keep working.
* A .env left in the home (written by hand, by a tool, or by a start from before settings.json) is moved in
  once: its keys to the credential store, the rest into the file, read back, then the .env is deleted. A line
  of it that could not be read stays beside it as .env.unmoved. A key typed into settings.json by hand is
  moved to the credential store the same way.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import secretstore
from .storage import FileLock, atomic_write_text

logger = logging.getLogger("agent")

FILE = "settings.json"
#: The file settings were kept in before settings.json, moved in once (move_in).
OLD = ".env"
#: A name that holds a key, a token or a password: kept in the credential store, never in the file.
SECRET = re.compile(r"(_KEY|_TOKEN|_SECRET)$|PASSWORD|^MIBO_MCP_")


class Settings(BaseModel):
    """Every setting a home's settings.json may hold. Blank or absent means the default described."""
    model_config = ConfigDict(extra="allow")

    # ---- the model ----------------------------------------------------------------------------------
    LLM_API_KEY: Optional[str] = Field(None, description="The chat model's key; kept in the credential store")
    LLM_BASE_URL: Optional[str] = Field(None, description=(
        "The API address as the provider documents it: a root such as https://api.deepseek.com, a version base "
        "such as .../v1, or the full /chat/completions URL. Blank = https://api.deepseek.com"))
    LLM_MODEL: Optional[str] = Field(None, description="The model id as the service names it. Blank = deepseek-flash")
    LLM_TIMEOUT_S: Optional[float] = Field(None, description=(
        "Per-request timeout in seconds; a turn's calls share 1.5 times it. Blank = 120"))
    LLM_MAX_RETRIES: Optional[int] = Field(None, description="Retries after a transient error. Blank = 2")
    CONTEXT_WINDOW: Optional[int] = Field(None, description=(
        "The main model's context window in tokens. Blank = what the host lists, else the catalogue, else what "
        "an overflow error named, else 32768"))
    LLM_DM_MODEL: Optional[str] = Field(None, description=(
        "Another model for the chat's replies, on the same endpoint and key. Blank = LLM_MODEL"))
    LLM_JUDGE_MODEL: Optional[str] = Field(None, description=(
        "The model for the short calls beside a reply. Blank = LLM_FALLBACK_MODEL, else the main model"))
    LLM_FALLBACK_MODEL: Optional[str] = Field(None, description="A cheaper model while the main one fails or is busy")
    LLM_FALLBACK_BASE_URL: Optional[str] = Field(None, description=(
        "The fallback model's own endpoint. Blank = LLM_BASE_URL with LLM_API_KEY"))
    LLM_FALLBACK_API_KEY: Optional[str] = Field(None, description="The fallback endpoint's key; kept in the credential store")
    LLM_FALLBACK_THINKING: Optional[bool] = Field(None, description=(
        "Send DeepSeek's thinking field to the fallback's host without asking first. Blank = false"))
    LLM_FALLBACK_DURATION_S: Optional[int] = Field(None, description="Seconds a failed model is skipped. Blank = 180")
    LLM_RATE_LIMIT_COOLDOWN_S: Optional[int] = Field(None, description=(
        "Seconds a model that answered 429 is skipped. Blank = 20"))

    # ---- the character ------------------------------------------------------------------------------
    PERSONA_NAME: Optional[str] = Field(None, description="The character's name")
    PERSONA_AVATAR: Optional[str] = Field(None, description=(
        "The portrait: a shipped character's key, or own for avatar.png/.jpg/.webp in the home. Blank = the "
        "name's first letter"))
    AGENT_LANG: Optional[str] = Field(None, description=(
        "The character's language: en, zh, zh-TW, ja, ko, es, fr, de, pt-BR or ru. Blank = en"))
    UI_LANG: Optional[str] = Field(None, description="The app's own screens' language. Blank = AGENT_LANG")
    PERSONA_FILE: Optional[str] = Field(None, description="The persona document, under the home. Blank = persona.txt")
    PERSONA_CARD_FILE: Optional[str] = Field(None, description=(
        "The persona's reply-style card, under the home. Blank = persona.card.json"))
    PERSONA_TZ_OFFSET_HOURS: Optional[float] = Field(None, description=(
        "The character's time zone as a UTC offset in hours, -12 to 14. Blank = UTC+8 for zh and zh-TW, this "
        "computer's otherwise"))
    ADMIN_NAME: Optional[str] = Field(None, description="What the character calls the person at the app")
    ADMIN_RELATIONSHIP: Optional[str] = Field(None, description="What that person is to the character")

    # ---- the page, updates and storage --------------------------------------------------------------
    PAGE_PORT: Optional[int] = Field(None, ge=0, le=65535, description="The page's port on 127.0.0.1. Blank = a free one")
    UPDATE_CHECK_ENABLED: Optional[bool] = Field(None, description=(
        "Ask GitHub's release page once a day whether a newer Mibo is out. Blank = true"))
    AGENT_RUNTIME_DIR: Optional[str] = Field(None, description=(
        "Where everything the character remembers is written, under the home when relative. Blank = runtime"))

    # ---- memory -------------------------------------------------------------------------------------
    MEMORY_FILE: Optional[str] = Field(None, description="The facts file under the runtime folder. Blank = memory.json")
    MEMORY_MAX_PER_CONVERSATION: Optional[int] = Field(None, description="Facts each chat keeps. Blank = 50")
    MEMORY_TIDY_EVERY: Optional[int] = Field(None, description=(
        "Notes since the last consolidation that start one; 0 turns memory calls off. Blank = 8"))
    MEMORY_CALLS_PER_DAY: Optional[int] = Field(None, description="Consolidation calls a day; 0 turns them off. Blank = 40")
    MEMORY_EPISODES_MAX: Optional[int] = Field(None, description="Events kept per chat. Blank = 120")
    MEMORY_KEEP_RECENT: Optional[bool] = Field(None, description="Keep each chat's recent messages for 72 hours. Blank = true")
    MEMORY_LOG_DAYS: Optional[int] = Field(None, description=(
        "Days each chat's record is kept; 0 keeps none. Blank = 30"))

    # ---- pictures -----------------------------------------------------------------------------------
    VISION_MODEL: Optional[str] = Field(None, description=(
        "A model for pictures. Blank = pictures go to the reply model when it reads them"))
    VISION_API_KEY: Optional[str] = Field(None, description="The vision endpoint's key; kept in the credential store")
    VISION_BASE_URL: Optional[str] = Field(None, description="The vision model's endpoint; it has no default")
    VISION_MAX_IMAGE_BYTES: Optional[int] = Field(None, description="The largest picture accepted, in bytes. Blank = 5000000")

    # ---- walks --------------------------------------------------------------------------------------
    ROAM_ENABLED: Optional[bool] = Field(None, description="The character goes out on its own as a visitor. Blank = true")
    ROAM_WALKS_A_DAY: Optional[int] = Field(None, description="Walks a day it starts itself, 0 to 6. Blank = 3")
    ROAM_AT: Optional[str] = Field(None, description="HH:MM before which the day's first walk does not leave")
    ROAM_GAP_HOURS: Optional[float] = Field(None, description="Least hours between two walks, 1 to 24. Blank = 3")
    ROAM_PAGES_PER_WALK: Optional[int] = Field(None, description="Most pages read in one walk, 3 to 12. Blank = 8")
    ROAM_SITE_PAGES_A_DAY: Optional[int] = Field(None, description="Most pages a day on one site, 1 to 60. Blank = 30")

    # ---- retrieval by meaning -----------------------------------------------------------------------
    EMBEDDING_MODEL: Optional[str] = Field(None, description=(
        "An OpenAI-compatible /embeddings model; blank turns retrieval by meaning off"))
    EMBEDDING_BASE_URL: Optional[str] = Field(None, description="Its endpoint. Blank = LLM_BASE_URL with LLM_API_KEY")
    EMBEDDING_API_KEY: Optional[str] = Field(None, description="Its key; kept in the credential store")


FIELDS = frozenset(Settings.model_fields)
#: The settings that are keys: read back from the credential store with the rest.
KEYS = tuple(name for name in Settings.model_fields if SECRET.search(name))

#: Settings this process took from the home's settings rather than from the environment it started with.
FROM_SETTINGS: set[str] = set()
# (home, kind, name) already logged, so a poll does not log it again.
_told: set[tuple] = set()


def path(home) -> Path:
    return Path(home) / FILE


def values(home, *, keys: bool = True) -> dict[str, str]:
    """The home's settings as the environment spells them ("true", "3"), the keys too unless `keys` is False.
    A setting that does not fit its type, and a name the model does not know, are left out."""
    home = Path(home)
    raw = _read(home)
    if (home / OLD).is_file() or any(SECRET.search(name) for name in raw):
        with _lock(home):
            move_in(home)
        raw = _read(home)
    invalid, unknown = _check(raw)
    _tell(home, invalid, unknown)
    out = {name: _text(value) for name, value in raw.items()
           if name in FIELDS and name not in invalid and value not in (None, "")}
    if keys:
        out.update((name, value) for name in KEYS if (value := secretstore.get(home, name)))
    return out


def problems(home) -> dict:
    """{"invalid": names whose value does not fit, "unknown": names the model does not know}, for Settings."""
    invalid, unknown = _check(_read(Path(home)))
    return {"invalid": invalid, "unknown": unknown}


def write(home, changes: dict) -> None:
    """Merge `changes` into the home's settings: a key to the credential store, the rest to settings.json.
    A blank value removes the setting; one that does not fit its type is a ValueError naming it."""
    home = Path(home)
    if not path(home).exists():
        # The first settings make the folder a home: it is narrowed to its user then.
        from .setup_wizard import secure_home
        secure_home(home)
    with _lock(home):
        move_in(home)
        raw = _read(home, set_aside=True)
        for name, value in changes.items():
            value = "" if value is None else value
            if SECRET.search(name):
                secretstore.put(home, name, str(value))
                raw.pop(name, None)
            elif value == "":
                raw.pop(name, None)
            else:
                raw[name] = _stored(name, value)
        _save(home, raw)


def load(home) -> None:
    """Take the home's settings into this process's environment, where the character reads them. A variable
    the environment already held when the app started wins. Called again after a save, a setting the home
    no longer holds goes back to unset."""
    saved = values(home)
    for name in FROM_SETTINGS - saved.keys():
        os.environ.pop(name, None)
        FROM_SETTINGS.discard(name)
    for name, value in saved.items():
        if name in os.environ and name not in FROM_SETTINGS:
            continue
        os.environ[name] = value
        FROM_SETTINGS.add(name)


def move_in(home: Path) -> None:
    """The .env left in `home` moved into settings.json, and any key in settings.json into the credential
    store; each read back before the .env is deleted. Called with the home's lock held."""
    old = home / OLD
    raw = _read(home, set_aside=True)
    moved, unplaced = _read_old(old) if old.is_file() else ({}, [])
    if not moved and not unplaced and not any(SECRET.search(name) for name in raw):
        old.unlink(missing_ok=True)
        return
    before = path(home).read_text(encoding="utf-8") if path(home).is_file() else None
    merged = {**raw, **{name: _fitting(name, value) for name, value in moved.items()}}
    secret = {name: str(value) for name, value in merged.items() if SECRET.search(name)}
    kept = {name: value for name, value in merged.items() if name not in secret}
    secretstore.put_many(home, secret)
    _save(home, kept)
    if _read(home) != kept or any(secretstore.get(home, name) != value for name, value in secret.items()):
        logger.error("[Agent] the settings did not read back from %s; %s stays as it was", FILE, OLD)
        if before is None:
            path(home).unlink(missing_ok=True)
        else:
            atomic_write_text(path(home), before)
        return
    if unplaced:
        atomic_write_text(home / (OLD + ".unmoved"), "".join(unplaced))
        logger.warning("[Agent] %d lines of %s could not be read; they are kept in %s.unmoved",
                       len(unplaced), OLD, OLD)
    if moved:
        keys = sum(1 for name in moved if name in secret)
        logger.info("[Agent] moved %s into %s: %d settings, %d keys to the %s", OLD, FILE, len(moved) - keys, keys,
                    "credential store" if secretstore.kept_in(home) == "store" else secretstore.FILE)
    # With the .env go its lock and the temporary copies an interrupted save left, which may hold a key.
    for leftover in (old, home / f".{OLD}.lock", home / f"{OLD}.tmp", *home.glob(f".{OLD}.*.tmp")):
        leftover.unlink(missing_ok=True)


def _read_old(old: Path) -> tuple[dict, list[str]]:
    """(the settings a .env holds, as the app read them, ${VAR} expanded; the lines it could not read)."""
    from dotenv import dotenv_values
    from dotenv.parser import parse_stream

    text = old.read_text(encoding="utf-8-sig")
    unplaced = [binding.original.string for binding in parse_stream(io.StringIO(text)) if binding.error]
    held = {name: value for name, value in dotenv_values(stream=io.StringIO(text)).items()
            if value and name != "AGENT_HOME"}
    return held, unplaced


def _lock(home: Path) -> FileLock:
    return FileLock(home / f".{FILE}.lock", timeout=10)


def _read(home: Path, *, set_aside: bool = False) -> dict:
    """settings.json's object; {} when there is none. One that is not a JSON object is logged and read as
    empty, and with `set_aside` (before a write) kept beside as settings.json.unreadable."""
    try:
        data = json.loads(path(home).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        return data
    if set_aside:
        path(home).replace(path(home).with_name(FILE + ".unreadable"))
        logger.warning("[Agent] %s was not a JSON object; it is kept as %s.unreadable", FILE, FILE)
    elif (home, "unreadable", FILE) not in _told:
        _told.add((home, "unreadable", FILE))
        logger.warning("[Agent] %s is not a JSON object; the defaults stand until it is fixed", FILE)
    return {}


def _check(raw: dict) -> tuple[list[str], list[str]]:
    """(names whose value does not fit their type, names the model does not know)."""
    known = {name: value for name, value in raw.items() if name in FIELDS}
    try:
        Settings.model_validate(known)
        invalid: list[str] = []
    except ValidationError as error:
        invalid = sorted({str(problem["loc"][0]) for problem in error.errors()})
    return invalid, sorted(name for name in raw if name not in FIELDS)


def _tell(home: Path, invalid: list[str], unknown: list[str]) -> None:
    for kind, names in (("invalid", invalid), ("unknown", unknown)):
        for name in names:
            if (home, kind, name) not in _told:
                _told.add((home, kind, name))
                logger.warning("[Agent] %s: %s %s; it is kept in the file and changes nothing", FILE, name,
                               "does not fit its type" if kind == "invalid" else "is not a setting")


def _stored(name: str, value):
    """`value` as settings.json keeps it: true/false and numbers for those settings, a name it does not know
    as given. A ValueError when it does not fit."""
    if name not in FIELDS:
        return value
    try:
        checked = getattr(Settings.model_validate({name: value}), name)
    except ValidationError:
        raise ValueError(f"{name} does not take {value!r}") from None
    return int(checked) if isinstance(checked, float) and checked.is_integer() else checked


def _fitting(name: str, value: str):
    """A moved value as settings.json keeps it; one that does not fit is kept as written."""
    try:
        return _stored(name, value)
    except ValueError:
        return value


def _text(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _save(home: Path, raw: dict) -> None:
    atomic_write_text(path(home), json.dumps(raw, ensure_ascii=False, indent=2) + "\n")

"""The interface's words in the person's language.

One catalog per language, `locales/<code>.json`, in i18next's JSON layout: a
namespace per surface, keys under it, plural forms as `key_one`, `key_other` and
the rest of the language's own forms, `key_zero` for none, and `{name}` where a
value goes. The page reads the same files (static/welcome/i18n.js). A missing
word falls back to English, then to its key.
"""
from __future__ import annotations

import functools
import json
import os
import re
from pathlib import Path

from .chatlangs import CODES

LOCALES = Path(__file__).with_name("locales")
_SLOT = re.compile(r"\{(\w+)\}")


@functools.lru_cache(maxsize=None)
def catalog(lang: str) -> dict:
    path = LOCALES / f"{lang}.json"
    return json.loads(path.read_text(encoding="utf-8")) if lang in CODES and path.is_file() else {}


def plural(lang: str, count: float) -> str:
    """The CLDR cardinal form of `count` in `lang`, as Intl.PluralRules gives it on the page."""
    whole = float(count).is_integer()
    n = int(count) if whole else 0
    if lang in ("zh", "zh-TW", "ja", "ko"):
        return "other"
    if lang == "ru":
        if not whole:
            return "other"
        if n % 10 == 1 and n % 100 != 11:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    if lang in ("fr", "pt-BR"):
        if int(abs(count)) in (0, 1):
            return "one"
    elif whole and n == 1:
        return "one"
    if lang in ("es", "fr", "pt-BR") and whole and n and n % 1_000_000 == 0:
        return "many"
    return "other"


def _find(tree, key: str):
    for part in key.split("."):
        tree = tree.get(part) if isinstance(tree, dict) else None
    return tree


def _pick(lang: str, key: str, count) -> object:
    tree = catalog(lang)
    if isinstance(count, (int, float)) and not isinstance(count, bool):
        for form in (["zero"] if count == 0 else []) + [plural(lang, count), "other"]:
            found = _find(tree, f"{key}_{form}")
            if isinstance(found, str):
                return found
    return _find(tree, key)


def t(key: str, lang: str, **values) -> str:
    """`key` ("namespace.key") in `lang` with its slots filled; `count` picks the plural form."""
    found = _pick(lang, key, values.get("count"))
    if not isinstance(found, str):
        found = _pick("en", key, values.get("count"))
    if not isinstance(found, str):
        return key
    return _SLOT.sub(lambda m: str(values[m[1]]) if m[1] in values else m[0], found)


def words(ns: str, lang: str) -> dict:
    """A namespace's words as one tree, the language's over English's."""
    def overlay(base, over):
        if isinstance(base, dict) and isinstance(over, dict):
            return {key: overlay(base.get(key), over[key]) if key in base else over[key] for key in over} | {
                key: value for key, value in base.items() if key not in over}
        return base if over is None else over
    return overlay(catalog("en").get(ns, {}), catalog(lang).get(ns))


def lang(values: dict | None = None) -> str:
    """The interface's language: UI_LANG, else the character's (AGENT_LANG), else the system's. `values` are the
    home's settings; without them this process's environment over the home's settings, as a start reads them."""
    from .home import find_home
    from .langs import normalize
    from .setup_wizard import detect_lang
    from . import settingsfile

    if values is None:
        values = {**settingsfile.values(find_home(), keys=False), **os.environ}
    return normalize(values.get("UI_LANG")) or normalize(values.get("AGENT_LANG")) or detect_lang()

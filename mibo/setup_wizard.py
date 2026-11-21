"""A home's setup: model providers, the .env file, the character's persona and
picture, a key probe, and how this install spells its own command."""
from __future__ import annotations

import functools
import json
import locale
import os
import re
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from mibo.endpoints import chat_completions_url as completions_url
from mibo import home as homes
from mibo import langs
from mibo.prompts import render_persona_template
from mibo.storage import PRIVATE_FILE_MODE, atomic_write_text, dated_copy

LANGS = langs.CODES
DEFAULT_NAMES = langs.DEFAULT_NAMES

SECRET_FILE_MODE = PRIVATE_FILE_MODE


# ---------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------

_TRADITIONAL_WORDS = ("traditional", "taiwan", "hong kong", "macao", "macau")
# The primary language in the low byte of a Windows LANGID, and the LANGIDs of
# traditional Chinese.
_WINDOWS_LANGS = {0x04: "zh", 0x11: "ja", 0x12: "ko", 0x0A: "es", 0x0C: "fr",
                  0x07: "de", 0x16: "pt-BR", 0x19: "ru"}
_WINDOWS_TRADITIONAL = (0x0404, 0x0C04, 0x1404)


def _lang_of(tag: str) -> str:
    """A language code for a locale name such as zh_CN.UTF-8 or 'Chinese (Simplified)_China'."""
    tag = (tag or "").strip().lower().split(".")[0]
    if not tag or tag in ("c", "posix"):
        return ""
    if "chinese" in tag:
        return "zh-TW" if any(word in tag for word in _TRADITIONAL_WORDS) else "zh"
    return langs.normalize(tag) or "en"


def detect_lang() -> str:
    """The system's language, en when mibo does not have it."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        found = _lang_of(os.environ.get(var, "").split(":")[0])
        if found:
            return found
    if os.name == "nt":
        import ctypes

        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        if langid in _WINDOWS_TRADITIONAL:
            return "zh-TW"
        if langid & 0xFF in _WINDOWS_LANGS:
            return _WINDOWS_LANGS[langid & 0xFF]
    return _lang_of(locale.getlocale()[0] or "") or "en"


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Provider:
    key: str
    base_url: str
    model: str
    key_url: str

    @property
    def needs_key(self) -> bool:
        return self.key not in LOCAL_PROVIDERS


# Servers on this computer: no key, and what works depends on the model loaded.
LOCAL_PROVIDERS = ("ollama", "lmstudio")

# Base URLs as each vendor documents them for OpenAI-compatible chat.
PROVIDERS: tuple[Provider, ...] = (
    Provider("openrouter", "https://openrouter.ai/api/v1",
             "openrouter/free", "https://openrouter.ai/keys"),
    Provider("deepseek", "https://api.deepseek.com",
             "deepseek-flash", "https://platform.deepseek.com/api_keys"),
    Provider("siliconflow", "https://api.siliconflow.cn/v1",
             "deepseek-ai/DeepSeek-V4-Flash", "https://cloud.siliconflow.cn/account/ak"),
    Provider("bailian", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus",
             "https://bailian.console.aliyun.com/"),
    Provider("zhipu", "https://open.bigmodel.cn/api/paas/v4",
             "glm-4.7-flash", "https://bigmodel.cn/usercenter/proj-mgmt/apikeys"),
    Provider("moonshot", "https://api.moonshot.cn/v1",
             "kimi-k3", "https://platform.kimi.com/console/api-keys"),
    Provider("ark", "https://ark.cn-beijing.volces.com/api/v3", "doubao-seed-2-1-lite-260915",
             "https://console.volcengine.com/ark/region:ark+cn-beijing/apikey"),
    Provider("minimax", "https://api.minimax.cn/v1", "MiniMax-M3",
             "https://platform.minimax.cn/"),
    Provider("qianfan", "https://qianfan.baidubce.com/v2",
             "ernie-5.1", "https://console.bce.baidu.com/iam/#/iam/apikey/list"),
    Provider("hunyuan", "https://tokenhub.tencentcloudmaas.com/v1", "hy4-flash",
             "https://console.cloud.tencent.com/tokenhub/apikey"),
    Provider("openai", "https://api.openai.com/v1", "gpt-6-luna",
             "https://platform.openai.com/api-keys"),
    Provider("anthropic", "https://api.anthropic.com/v1",
             "claude-haiku-5-5", "https://platform.claude.com/settings/keys"),
    Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.8-flash",
             "https://aistudio.google.com/apikey"),
    Provider("xai", "https://api.x.ai/v1", "grok-4.3", "https://console.x.ai"),
    Provider("mistral", "https://api.mistral.ai/v1", "mistral-small-latest",
             "https://console.mistral.ai/api-keys"),
    Provider("groq", "https://api.groq.com/openai/v1",
             "llama-3.3-70b-versatile", "https://console.groq.com/keys"),
    Provider("ollama", "http://localhost:11434/v1", "qwen3", ""),
    Provider("lmstudio", "http://localhost:1234/v1", "qwen/qwen3-8b", ""),
    Provider("other", "", "", ""),
)
PROVIDER_KEYS = tuple(p.key for p in PROVIDERS)

# The order a person sees: DeepSeek first for Chinese, and the services
# reachable from mainland China before the rest; elsewhere OpenRouter's free
# models first. The tuple's own order (OpenRouter first) is the default endpoint's.
_ORDER_ZH = ("deepseek", "bailian", "zhipu", "moonshot", "ark", "siliconflow", "minimax", "qianfan",
             "hunyuan", "openrouter", "openai", "anthropic", "gemini", "xai", "mistral", "groq",
             "ollama", "lmstudio", "other")
_ORDER = ("openrouter", "deepseek", "openai", "anthropic", "gemini", "xai", "mistral", "groq",
          "bailian", "moonshot", "zhipu", "minimax", "siliconflow", "ark", "qianfan", "hunyuan",
          "ollama", "lmstudio", "other")


def providers_for(lang: str) -> list[Provider]:
    """PROVIDERS in the order a person speaking `lang` should see them."""
    order = _ORDER_ZH if (lang or "").lower().startswith("zh") else _ORDER
    return sorted(PROVIDERS, key=lambda p: order.index(p.key) if p.key in order else len(order))


def provider_named(key: str) -> Provider:
    for provider in PROVIDERS:
        if provider.key == key:
            return provider
    raise ValueError(f"unknown provider {key!r}; one of {', '.join(PROVIDER_KEYS)}")


def _same_url(a: str, b: str) -> bool:
    return bool(a) and a.strip().rstrip("/").lower() == b.strip().rstrip("/").lower()


def provider_for_base(base_url: str) -> Provider | None:
    """The preset serving `base_url`, also when it was written without /v1."""
    base_url = base_url.strip().rstrip("/")
    for provider in PROVIDERS:
        if provider.base_url and (
                _same_url(base_url, provider.base_url)
                or _same_url(base_url + "/v1", provider.base_url)
                or _same_url(base_url, provider.base_url + "/v1")):
            return provider
    return None


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def mask_secret(value: str) -> str:
    """Enough of a key to recognise it, never enough to use it."""
    if len(value) >= 12:
        return f"{value[:3]}…{value[-4:]}"
    return "…"


# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------

def env_literal(value) -> str:
    """`value` as one .env right-hand side that dotenv reads back unchanged.

    Single quotes keep it whole: a '#' after any whitespace would start a
    comment, and a line break of any kind (U+2028 included) must not let a
    value write a line of its own. dotenv expands ${VAR} even inside quotes,
    so callers taking values from outside refuse it."""
    text = str(value)
    if (text != text.strip() or text.startswith("#")
            or re.search(r"\s#", text)
            or any(ch in "\"'" or (ch.isspace() and ch != " ") or not ch.isprintable() and ch != " "
                   for ch in text)):
        return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return text


def set_env_values(env_text: str, values: dict) -> str:
    """`env_text` with every uncommented KEY= line of a key in `values`
    rewritten (dotenv reads the last one, so all of them); keys not present
    are appended. Comments and order are kept."""
    # Lines as dotenv splits them; str.splitlines also breaks at U+2028 and
    # friends, which would turn part of a quoted value into a line of its own.
    lines = re.split(r"\r\n|\n|\r", env_text) if env_text else []
    if lines and lines[-1] == "":
        lines.pop()
    found = set()
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if m and m.group(1) in values:
            key = m.group(1)
            lines[i] = f"{key}={env_literal(values[key])}"
            found.add(key)
    for key, value in values.items():
        if key not in found:
            lines.append(f"{key}={env_literal(value)}")
    out = "\n".join(lines)
    if env_text.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out


def _replace(source, target) -> None:
    # Imported here: this module is read before any home is chosen.
    from mibo.storage import replace
    replace(source, target)


def write_env(env_path: Path, values: dict) -> None:
    """Merge `values` into .env through a 0600 temp file and an atomic replace,
    so an interrupted run never leaves a truncated or world-readable .env.
    One writer at a time, across processes (the page, the terminal, the
    service), each with a temporary file of its own, so no write is lost."""
    import tempfile

    from mibo.storage import FileLock

    if not env_path.exists():
        # The first .env makes its folder a home, as in copy_env_template.
        secure_home(env_path.parent)
    with FileLock(env_path.with_name("." + env_path.name + ".lock"), timeout=10):
        text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        updated = set_env_values(text, values)
        # An earlier release's fixed temporary name, left by an interrupted run.
        env_path.with_name(env_path.name + ".tmp").unlink(missing_ok=True)
        fd, name = tempfile.mkstemp(dir=env_path.parent, prefix="." + env_path.name + ".", suffix=".tmp")
        tmp = Path(name)
        try:
            os.chmod(tmp, SECRET_FILE_MODE)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(updated)
            # Keep a stricter mode the operator chose, never a wider one.
            if env_path.exists():
                os.chmod(tmp, env_path.stat().st_mode & SECRET_FILE_MODE)
            _replace(tmp, env_path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


def secure_env_file(env_path: Path) -> None:
    """Narrow .env to its user (0600); a copy of the template starts at 0644."""
    os.chmod(env_path, SECRET_FILE_MODE)


def env_get(env_path: Path, key: str) -> str:
    """The value of `key` in .env exactly as the agent loads it ('' if blank or missing)."""
    if not env_path.exists():
        return ""
    from dotenv import dotenv_values

    return str(dotenv_values(env_path).get(key) or "").strip()


def secure_home(home: Path) -> None:
    """Make `home` if missing and open it, and all it holds, to its user alone.
    Files the app writes without a mode (covers, logs, the page's token link)
    take it from here: on POSIX the umask leaves them readable to all, and on
    Windows a home outside the profile (D:\\...) inherits "Authenticated Users:
    Modify", where chmod does nothing. Cheap once done, so every start calls
    it, and homes made by earlier versions are narrowed too."""
    home.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(home, 0o700)
        return
    if _acl_protected(home):
        # Narrowed already, here or by its user; redoing it rewrites every file below.
        return
    whoami = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                            errors="replace", stdin=subprocess.DEVNULL)
    sid = whoami.stdout.strip().rsplit(",", 1)[-1].strip('"')
    # What a profile folder grants: the user, SYSTEM and Administrators.
    grants = [f"*{who}:(OI)(CI)F" for who in (sid, "S-1-5-18", "S-1-5-32-544")]
    # A drive with no permissions to set (FAT, exFAT) refuses; nothing to narrow there.
    subprocess.run(["icacls", str(home), "/inheritance:r", "/grant:r", *grants],
                   capture_output=True, stdin=subprocess.DEVNULL)


def _acl_protected(path: Path) -> bool:
    """Whether `path` keeps its own permissions rather than its parent folder's."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32")
    descriptor = ctypes.c_void_p()
    # SE_FILE_OBJECT, DACL_SECURITY_INFORMATION
    if advapi.GetNamedSecurityInfoW(str(path), 1, 4, None, None, None, None, ctypes.byref(descriptor)):
        return False
    control, revision = wintypes.WORD(), wintypes.DWORD()
    advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision))
    ctypes.WinDLL("kernel32").LocalFree(descriptor)
    return bool(control.value & 0x1000)  # SE_DACL_PROTECTED


def copy_env_template(home: Path) -> Path:
    """<home>/.env, copied from the shipped template when missing, narrowed to 0600.
    The first .env makes the folder a home, so the home is narrowed then too."""
    env_path = home / ".env"
    if not env_path.exists():
        secure_home(home)
        template = homes.resource(".env.example")
        if template.is_file():
            shutil.copyfile(template, env_path)
        else:
            env_path.write_text("", encoding="utf-8")
    secure_env_file(env_path)
    return env_path


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Persona
# ---------------------------------------------------------------------------

# A personification's name in each language: the product name plus this.
_NAME_SUFFIX = {"zh": "娘", "zh-TW": "娘", "ja": "ちゃん", "ko": "쨩"}


@dataclass(frozen=True)
class Character:
    key: str
    # What the character's own name is built on; "" = it has none.
    stem: str = ""
    # The name it had before 1.5, which homes set up then still carry.
    old_name: str = ""
    # Its own name in each language, given whole instead of built on the stem.
    names: tuple[tuple[str, str], ...] = ()

    def name_in(self, lang: str) -> str:
        """The character's own name in `lang`, which a name nobody chose follows."""
        if not self.stem:
            return ""
        if self.names:
            return dict(self.names)[lang]
        return self.stem + _NAME_SUFFIX.get(lang, "-chan")

    @property
    def own_names(self) -> frozenset:
        """Every name this character has had in any language."""
        if not self.stem:
            return frozenset()
        return frozenset({self.old_name, *(self.name_in(lang) for lang in LANGS)} - {""})


TEMPLATE = "template"
CHARACTERS: tuple[Character, ...] = (
    # Mibo, the app's own robot, the first: new homes start as it.
    Character("mibo", stem="Mibo", names=tuple((lang, "Mibo") for lang in LANGS)),
    Character("chatgpt", stem="GPT", old_name="ChatGPT",
              # What people call her, not a product's name with a suffix.
              names=(("en", "White Dragon"), ("zh", "白龙"), ("zh-TW", "白龍"), ("ja", "白竜"),
                     ("ko", "백룡"), ("es", "Dragona Blanca"), ("fr", "Dragonne Blanche"),
                     ("de", "Weiße Drachin"), ("pt-BR", "Dragoa Branca"), ("ru", "Белая Драконица"))),
    Character("deepseek", stem="DeepSeek", old_name="DeepSeek",
              names=(("en", "Big Fish"), ("zh", "大肥鱼"), ("zh-TW", "大肥魚"), ("ja", "おさかな"),
                     ("ko", "뚱보 물고기"), ("es", "Pez Gordo"), ("fr", "Gros Poisson"),
                     ("de", "Dicker Fisch"), ("pt-BR", "Peixão"), ("ru", "Большая Рыба"))),
    Character("claude", stem="Claude", old_name="Claude",
              names=(("en", "Milady"), ("zh", "大小姐"), ("zh-TW", "大小姐"), ("ja", "お嬢様"),
                     ("ko", "아가씨"), ("es", "Señorita"), ("fr", "Mademoiselle"),
                     ("de", "Mylady"), ("pt-BR", "Senhorita"), ("ru", "Барышня"))),
    Character(TEMPLATE),
)
PERSONA_KEYS = tuple(c.key for c in CHARACTERS)
# The local page's portrait sprites, <key>.webp.
PORTRAITS = Path(__file__).with_name("static") / "characters"


def character_for(key: str) -> Character:
    return CHARACTERS[PERSONA_KEYS.index(key)]


def portrait(key: str) -> Path | None:
    """The portrait sprite of character `key`, when it ships one."""
    path = PORTRAITS / f"{key}.webp"
    return path if key in PERSONA_KEYS and path.is_file() else None


def avatar_for(key: str) -> str:
    """PERSONA_AVATAR for a persona written as character `key`."""
    return key if portrait(key) else ""


# PERSONA_AVATAR for the picture the person chose, kept in the home as avatar.<ext>.
OWN_AVATAR = "own"
OWN_AVATAR_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
MAX_OWN_AVATAR = 2 * 1024 * 1024


def own_avatar_kind(data: bytes) -> str:
    """The extension a picture's bytes say it is (PNG, JPEG or WebP), or ""."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def own_avatar(home: Path) -> Path | None:
    """The home's own picture, when it holds one that is a plain file."""
    for kind in OWN_AVATAR_TYPES:
        path = home / f"avatar.{kind}"
        if path.is_file() and not path.is_symlink():
            return path
    return None


def write_own_avatar(home: Path, data: bytes) -> Path:
    """Keep `data` as the home's one picture, avatar.<ext>; a ValueError when it is not one."""
    kind = own_avatar_kind(data)
    if not kind:
        raise ValueError("avatar_type")
    target = home / f"avatar.{kind}"
    if target.is_symlink():
        raise ValueError("avatar_type")
    tmp = target.with_name(f"avatar.{secrets.token_hex(4)}.tmp")
    tmp.write_bytes(data)
    _replace(tmp, target)
    # One picture: one left by an earlier choice of another type goes.
    for other in OWN_AVATAR_TYPES:
        if other != kind:
            (home / f"avatar.{other}").unlink(missing_ok=True)
    return target


def kept_avatar(env_path: Path, key: str) -> str:
    """PERSONA_AVATAR after writing character `key`: a picture the person chose stays."""
    if env_get(env_path, "PERSONA_AVATAR") == OWN_AVATAR and own_avatar(env_path.parent):
        return OWN_AVATAR
    return avatar_for(key)


# The names the three fan-made ones had before, a product's name with a suffix: nobody chose those either,
# so a home still on one takes the character's name the next time it is applied.
PAST_NAMES = frozenset(c.stem + _NAME_SUFFIX.get(lang, "-chan")
                       for c in CHARACTERS if c.old_name for lang in LANGS)
OWN_NAMES = frozenset().union(PAST_NAMES, *(c.own_names for c in CHARACTERS))


def name_for(key: str, name: str, lang: str) -> str:
    """`name`, unless nobody chose it (the default, or another character's
    own): then character `key`'s own name, or the default when it has none.
    A name this character already has stays, so no home is renamed."""
    character = character_for(key)
    if name in character.own_names:
        return name
    if name == DEFAULT_NAMES[lang] or name in OWN_NAMES:
        return character.name_in(lang) or DEFAULT_NAMES[lang]
    return name

NAME_LINE = {"en": "Your name is {name}.", "zh": "你叫{name}。", "zh-TW": "你叫{name}。",
             "ja": "あなたの名前は{name}です。", "ko": "당신의 이름은 {name}입니다.",
             "es": "Te llamas {name}.", "fr": "Tu t'appelles {name}.",
             "de": "Du heißt {name}.", "pt-BR": "Seu nome é {name}.",
             "ru": "Тебя зовут {name}."}

def _normal(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def persona_source(key: str, lang: str) -> Path:
    if key == TEMPLATE:
        return homes.resource("data", f"persona.example.{lang}.txt")
    return homes.resource("data", "personas", lang, f"{key}.txt")


def render_persona(text: str, *, name: str, lang: str, admin_name: str = "",
                   admin_relationship: str = "") -> str:
    """A persona document ready for the model, rendered as the agent renders
    it, plus a name line when the text never names the character."""
    out = render_persona_template(_normal(text), bot_name=name, admin_name=admin_name,
                                  admin_relationship=admin_relationship, lang=lang)
    if "{bot_name}" not in text:
        out = NAME_LINE.get(lang, NAME_LINE["en"]).format(name=name) + "\n" + out
    return out + "\n"


def shipped_persona(text: str) -> tuple[str, str, str] | None:
    """(key, lang, name) when `text` is a shipped character as this setup
    writes it, or None when someone has edited it."""
    norm = _normal(text)
    for key, lang, raw, pattern in _shipped_shapes(str(homes.resource("data"))):
        if norm == raw:
            return (key, lang, "")
        found = pattern.fullmatch(norm)
        if found:
            return (key, lang, found.group(1))
    return None


@functools.lru_cache(maxsize=2)
def _shipped_shapes(data_dir: str) -> tuple:
    """Every shipped character under `data_dir` as (key, lang, its text, its
    text with a name slot). Read once: the local page asks on every poll."""
    sentinel = "\x00NAME\x00"
    shapes = []
    for lang in LANGS:
        for character in CHARACTERS:
            source = persona_source(character.key, lang)
            if not source.is_file():
                continue
            raw = source.read_text(encoding="utf-8")
            shape = _normal(render_persona(raw, name=sentinel, lang=lang))
            pattern = re.escape(shape).replace(re.escape(sentinel), r"(.+?)", 1)
            pattern = pattern.replace(re.escape(sentinel), r"\1")
            shapes.append((character.key, lang, _normal(raw), re.compile(pattern)))
    return tuple(shapes)


def persona_text(key: str, *, name: str, lang: str, admin_name: str = "",
                 admin_relationship: str = "") -> str:
    source = persona_source(key, lang)
    if not source.is_file():
        source = persona_source(key, "en")
    return render_persona(source.read_text(encoding="utf-8"), name=name, lang=lang,
                          admin_name=admin_name, admin_relationship=admin_relationship)


def default_character(origin: tuple[str, str, str] | None) -> str:
    """The menu default: the character persona.txt already is, else the first."""
    if origin and origin[0] in PERSONA_KEYS:
        return origin[0]
    return CHARACTERS[0].key


def write_persona(home: Path, key: str, *, name: str, lang: str, admin_name: str = "",
                  admin_relationship: str = "") -> bool:
    """Write persona.txt as `key` for this name; False when it already is that."""
    text = persona_text(key, name=name, lang=lang, admin_name=admin_name,
                        admin_relationship=admin_relationship)
    path = home / "persona.txt"
    if path.is_file() and _normal(path.read_text(encoding="utf-8")) == _normal(text):
        return False
    home.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    _replace(tmp, path)
    return True


def back_up_persona(home: Path) -> Path | None:
    """Copy a persona.txt someone wrote to persona.txt.bak and a dated copy
    before it is replaced; a shipped one needs no copy."""
    path = home / "persona.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.strip() or shipped_persona(text) is not None:
        return None
    kept = path.with_name("persona.txt.bak")
    atomic_write_text(kept, text)
    # The next replacement writes persona.txt.bak again: this one stays.
    return dated_copy(kept)


# ---------------------------------------------------------------------------
# Key probe
# ---------------------------------------------------------------------------

@dataclass
class Probe:
    url: str
    status: int          # 0 when nothing answered
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 200


def _is_loopback(url: str) -> bool:
    return (urllib.parse.urlsplit(url).hostname or "").lower() in ("localhost", "127.0.0.1", "::1")


def _post(url: str, api_key: str, payload: dict, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    # A loopback server (Ollama, a test server) must not go through a proxy.
    handlers = [urllib.request.ProxyHandler({})] if _is_loopback(url) else []
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, ""
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except OSError:
            body = ""
        return exc.code, " ".join(body.split())[:240]
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return 0, str(reason)[:240]


def probe_key(base_url: str, api_key: str, model: str, timeout: float = 30) -> Probe:
    """One tiny chat completion at the URL the agent will use."""
    url = completions_url(base_url)
    messages = [{"role": "user", "content": "Reply with OK."}]
    status, detail = _post(url, api_key, {"model": model, "max_tokens": 1,
                                          "messages": messages}, timeout)
    if status == 400 and "max_tokens" in detail:
        # Reasoning models refuse max_tokens; ask again without a limit.
        status, detail = _post(url, api_key, {"model": model, "messages": messages}, timeout)
    return Probe(url, status, detail)


# ---------------------------------------------------------------------------
# How to run mibo on this machine
# ---------------------------------------------------------------------------

@dataclass
class Launcher:
    """The interpreter that has mibo's dependencies, and how to spell
    its commands for the person reading the next steps."""
    python: str
    home: Path

    @property
    def in_checkout(self) -> bool:
        return homes.is_checkout()

    def _home_args(self) -> list[str]:
        # A home other than the one a bare command finds needs saying.
        usual = homes.CHECKOUT if self.in_checkout else homes.default_home()
        if os.environ.get("AGENT_HOME", "").strip() or _resolved(self.home) != _resolved(usual):
            return ["--home", str(self.home)]
        return []

    def argv(self, sub: str) -> list[str]:
        return [self.python, "-m", "mibo", *self._home_args(), sub]

    def cwd(self) -> str | None:
        # `-m mibo` finds the package from a checkout's root.
        return str(homes.CHECKOUT) if self.in_checkout else None

    def shown(self, sub: str) -> list[str]:
        """The lines to type, ready to copy."""
        extra = " ".join(_quote(a) for a in self._home_args())
        tail = f"{extra} {sub}" if extra else sub
        if self.in_checkout:
            python = Path(self.python)
            try:
                python = python.relative_to(homes.CHECKOUT)
            except ValueError:
                pass
            return [f"cd {_quote(str(homes.CHECKOUT))}",
                    f"{_program(str(python))} -m mibo {tail}"]
        if shutil.which("mibo"):
            return [f"mibo {tail}"]
        launcher = _started_by_launcher()
        if launcher:
            return [f"{_program(launcher)} {tail}"]
        return [f"{_program(self.python)} -m mibo {tail}"]


def _started_by_launcher() -> str | None:
    """The `mibo` script that started this process, when it was one (a venv
    run by its full path without being activated)."""
    started = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if started is None or started.name.lower() not in ("mibo", "mibo.exe"):
        return None
    beside = Path(sys.executable).parent
    for candidate in (started, started.with_name("mibo.exe"),
                      beside / "mibo.exe", beside / "mibo"):
        if candidate.is_file():
            return str(candidate)
    return None


def command(sub: str, home: Path | None = None) -> str:
    """`sub` as this install spells it, on one line, for a hint inside a sentence."""
    return Launcher(python=sys.executable, home=home or homes.find_home()).shown(sub)[-1]


def _resolved(path: Path) -> Path:
    return Path(path).expanduser().resolve()


def _quote(text: str) -> str:
    return f'"{text}"' if " " in text else text


def _program(path: str) -> str:
    """`path` as the first word of a command; PowerShell needs `&` to run a quoted path."""
    if " " in path and os.name == "nt" and "PROMPT" not in os.environ:
        return f'& "{path}"'
    return _quote(path)

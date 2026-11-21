"""Where the person is, what they can reach and what they read, in one table.

Each fact is a row in the runtime database's `situation` table (db.py), kept
per origin (user > observed > os > default): the highest one present is the value, and
inference never writes over what the person set. When an observation that
decided a value goes away (a VPN makes YouTube answer, so the mainland
signature is gone), the value stays and the row asks the person instead.

The rest of the package knows only five effects: `gate` (never fetched, never
shown), `supply` (a log-weight in the source draw), `priors` (m0, q0 on the
taste model), `context` (coarse buckets) and `notes` (lines the model reads).

Reachability comes from real transport outcomes. `recording_transport`
records each request's host: any HTTP answer, whatever its status, counts as
reachable; only DNS, connect, TLS and reset failures and connect timeouts
count against it. A host is down after two such failures at least five
minutes apart, and up again on one answer. Each host is a row in the `hosts`
table.
"""
from __future__ import annotations

import asyncio
import contextvars
import ctypes
import functools
import json
import locale
import re
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import httpx

from . import db

ORIGINS = ("default", "os", "observed", "user")
# feed_model is the person's switch for the taste model (off: the feed's old point sum ranks).
NAMES = ("region", "home", "readable", "unreadable", "vpn", "accounts", "metered", "feed_model")
DEFAULTS = {"region": "", "home": "", "readable": [], "unreadable": [], "vpn": False, "accounts": [],
            "metered": False, "feed_model": True}
# Down after this many counted failures in a row, counted at most once per FAIL_GAP_S.
DOWN_AFTER = 2
FAIL_GAP_S = 300
# A down host or blocked source is tried again this long after its last failure (every round with the VPN switch on).
RETRY_S = 6 * 3600
# A successful answer is written down at most this often per host.
OK_WRITE_S = 60
# The first start checks these, twice, PROBE_AGAIN_S apart.
PROBE_HOSTS = ("youtube.com", "www.google.com", "www.reddit.com", "www.bilibili.com", "en.wikipedia.org")
PROBE_TIMEOUT_S = 6
PROBE_AGAIN_S = 300
# Opened or liked things in one language before it counts as one the person reads.
OPENS_FOR_LANG = 3
# Supply terms: a source local to the current region, to the home region, to another one; a lean's scale;
# a source or walk site of a platform the person connected.
HERE, HOME, ELSEWHERE, LEAN, ACCOUNT = 1.0, 0.5, -1.0, 0.7, 0.5
UNREAD_LANG = (-2.0, 2.0)

# Transport errors that say the host could not be reached; a slow, refusing or moved site is not one.
COUNTED = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError)
# Chromium's words for the same, from a walk's navigation.
_NAV_FAILED = re.compile(r"net::ERR_(?:CONNECTION_[A-Z_]+|NAME_NOT_RESOLVED|TIMED_OUT|ADDRESS_UNREACHABLE)")
# Names that stand for one platform: a player or short link counts as the site.
_SAME = {"youtube-nocookie.com": "youtube.com", "youtu.be": "youtube.com", "b23.tv": "bilibili.com"}
_SECOND_LEVEL = {"com", "net", "org", "co", "ne", "or", "ac", "go", "gov", "edu"}
# The person's accounts that say which catalogue sources and walk sites are theirs.
PLATFORMS = {"youtube": (("youtube-",), "YouTube"), "bilibili": (("bili-", "bilibili-"), "Bilibili"),
             "zhihu": (("zhihu-",), ""), "weibo": (("weibo-",), ""), "github": (("gh-", "github-"), "")}
# Time zones that name one country (IANA, and Windows ids); "China Standard Time" covers Hong Kong too, so it is not here.
_ZONES = {"Asia/Shanghai": "CN", "Asia/Tokyo": "JP", "Asia/Seoul": "KR", "Asia/Taipei": "TW",
          "Asia/Hong_Kong": "HK", "Europe/Moscow": "RU", "America/Sao_Paulo": "BR", "Europe/Madrid": "ES",
          "Europe/Paris": "FR", "Europe/Berlin": "DE", "Europe/London": "GB", "Asia/Singapore": "SG",
          "Tokyo Standard Time": "JP", "Korea Standard Time": "KR", "Taipei Standard Time": "TW",
          "Russian Standard Time": "RU", "E. South America Standard Time": "BR"}
_SOUTH = {"AU", "NZ", "BR", "AR", "CL", "ZA", "UY"}
_PLACES = {"CN": "mainland China", "TW": "Taiwan", "HK": "Hong Kong", "JP": "Japan", "KR": "South Korea",
           "RU": "Russia", "BR": "Brazil", "ES": "Spain", "FR": "France", "DE": "Germany", "US": "the United States",
           "GB": "the United Kingdom", "SG": "Singapore", "AU": "Australia", "CA": "Canada"}
_LANG_NAMES = {"en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "ru": "Russian",
               "pt": "Portuguese", "es": "Spanish", "fr": "French", "de": "German"}
# Codes a source may carry for a language the person reads.
_VARIANTS = {"zh": ("zh-TW", "zh-Hant", "zh-HK", "zh-Hans", "zh-CN"), "pt": ("pt-BR", "pt-PT")}
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ]")
_HAN = re.compile(r"[一-鿿]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_LATIN = re.compile(r"[A-Za-z]")

# What the requests of one source's fetch did, for the round to tell blocked from failed.
_tally: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("situation_tally", default=None)


# ------------------------------------------------------------------- the table --

HOST_FIELDS = ("fails", "fail_at", "ok_at", "wall_at", "mode")


def _hosts(found) -> dict[str, dict]:
    return {site: dict(zip(HOST_FIELDS, values)) for site, *values in found}


def _host(store: db.Db, site: str) -> Optional[dict]:
    return _hosts(store.read("SELECT * FROM hosts WHERE site = ?", (site,))).get(site)


def _all_hosts(runtime: Path) -> dict[str, dict]:
    return _hosts(db.at(runtime).read("SELECT * FROM hosts ORDER BY site"))


def _factor(runtime: Path, name: str) -> Any:
    found = db.at(runtime).read("SELECT value FROM situation WHERE name = ?", (name,))
    return json.loads(found[0][0]) if found else None


def _edit(runtime: Path, edit: Callable[[dict], None]) -> dict:
    """Read, edit and write the factors as one transaction: {"factors": {name: row, and "probed": when the first
    start's check ran}, "hosts": read only}; only the rows `edit` changed are written."""
    with db.at(runtime).write() as conn:
        before = {name: value for name, value in conn.execute("SELECT name, value FROM situation")}
        state = {"factors": {name: json.loads(value) for name, value in before.items()},
                 "hosts": _hosts(conn.execute("SELECT * FROM hosts").fetchall())}
        edit(state)
        rows = {name: json.dumps(row, ensure_ascii=False, sort_keys=True) for name, row in state["factors"].items()}
        conn.executemany("INSERT OR REPLACE INTO situation VALUES (?,?)",
                         [(name, value) for name, value in rows.items() if before.get(name) != value])
        return state


def _edit_host(runtime: Path, site: str, edit: Callable[[dict], Optional[bool]]) -> None:
    """One host's row edited in one transaction; `edit` returning False writes nothing. It is read first outside
    one, so a request that changes nothing takes no write lock."""
    store = db.at(runtime)
    if edit(_host(store, site) or _blank_host()) is False:
        return
    with store.write() as conn:
        row = _host(store, site) or _blank_host()
        if edit(row) is not False:
            conn.execute("INSERT OR REPLACE INTO hosts VALUES (?,?,?,?,?,?)", (site, *(row[f] for f in HOST_FIELDS)))


def _blank_host() -> dict:
    return {"fails": 0, "fail_at": 0.0, "ok_at": 0.0, "wall_at": 0.0, "mode": ""}


def _top(row: Optional[dict]) -> tuple[str, Any]:
    """The origin that decides a row, and its value."""
    by = (row or {}).get("by") or {}
    for origin in reversed(ORIGINS):
        if origin in by:
            return origin, by[origin]
    return "default", None


def value(runtime: Path, name: str):
    """The current value of one factor, or a host's override ("host:<host>": "always", "never" or "")."""
    if name.startswith("host:"):
        return (_host(db.at(runtime), site_of(name[5:])) or _blank_host())["mode"]
    origin, found = _top(_factor(runtime, name))
    return DEFAULTS[name] if origin == "default" else found


def _clean(name: str, raw):
    if name in ("region", "home"):
        code = str(raw or "").strip().upper()
        if code and not re.fullmatch(r"[A-Z]{2}", code):
            raise ValueError("situation_invalid")
        return code
    if name in ("readable", "unreadable", "accounts"):
        if not isinstance(raw, list) or len(raw) > 40:
            raise ValueError("situation_invalid")
        words = {primary(w) if name != "accounts" else str(w) for w in raw if isinstance(w, str)}
        return sorted(w for w in words if w)
    if not isinstance(raw, bool):
        raise ValueError("situation_invalid")
    return raw


def set_value(runtime: Path, name: str, value) -> None:
    """The person's own value for a factor (None takes it back), or a host's override."""
    if name.startswith("host:"):
        site = site_of(name[5:])
        if not site or value not in ("always", "never", "", None):
            raise ValueError("situation_invalid")

        def mode(row: dict) -> None:
            row["mode"] = value or ""

        _edit_host(runtime, site, mode)
        return
    if name not in NAMES:
        raise ValueError("situation_invalid")
    cleaned = None if value is None else _clean(name, value)

    def put(state: dict) -> None:
        row = state["factors"].setdefault(name, {"by": {}})
        row.pop("ask", None)
        if cleaned is None:
            row["by"].pop("user", None)
        else:
            row["by"]["user"] = cleaned
        row["at"] = int(time.time())

    _edit(runtime, put)


def _observe(state: dict, name: str, origin: str, found, now: float) -> None:
    """One inference for a row. A value replaces that origin's; None means the observation is gone,
    and when it was the one deciding the row, the row asks before it changes."""
    row = state["factors"].setdefault(name, {"by": {}})
    by = row["by"]
    if found is not None:
        if by.get(origin) != found:
            by[origin], row["at"] = found, int(now)
        if _top(row)[0] == origin:
            row.pop("ask", None)
        return
    if origin not in by:
        return
    if _top(row)[0] == origin:
        below = {o: v for o, v in by.items() if o != origin}
        fallback = _top({"by": below})[1]
        if fallback != by[origin]:
            row["ask"] = DEFAULTS[name] if fallback is None else fallback
            return
    del by[origin]


# ---------------------------------------------------------------- hosts and gates --

def site_of(address: str) -> str:
    """The platform a host or address belongs to: "www.youtube.com" and "youtu.be" are both "youtube.com"."""
    address = str(address or "").strip().lower()
    host = (urlsplit(address).hostname or "") if "://" in address else address.split("/")[0].split(":")[0]
    labels = [label for label in host.strip(".").split(".") if label]
    if len(labels) < 2 or labels[-1].isdigit():
        return host
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 2
    site = ".".join(labels[-keep:])
    return _SAME.get(site, site)


def _up(row: Optional[dict]) -> bool:
    if not row:
        return True
    if row["mode"]:
        return row["mode"] == "always"
    return row["fails"] < DOWN_AFTER


def retry_s(runtime: Path) -> float:
    """How long a down host or blocked source waits before it is tried again."""
    return 0.0 if value(runtime, "vpn") else RETRY_S


def record(runtime: Path, host: str, ok: bool, now: Optional[float] = None) -> None:
    """One request's outcome for its host: True for any HTTP answer, False for a counted transport failure."""
    site, now = site_of(host), time.time() if now is None else now
    if not site:
        return

    def edit(row: dict) -> Optional[bool]:
        if ok:
            if not row["fails"] and now - row["ok_at"] < OK_WRITE_S:
                return False
            row["fails"], row["ok_at"] = 0, now
        else:
            if now - row["fail_at"] < FAIL_GAP_S:
                return False
            row["fails"], row["fail_at"] = row["fails"] + 1, now
        return None

    _edit_host(runtime, site, edit)


def record_wall(runtime: Path, host: str, wall: bool, now: Optional[float] = None) -> None:
    """A walk's list showed a login or verification wall to visitors (or did not)."""
    site, now = site_of(host), time.time() if now is None else now

    def edit(row: dict) -> Optional[bool]:
        if bool(row["wall_at"]) == wall:
            return False
        row["wall_at"] = now if wall else 0.0
        return None

    _edit_host(runtime, site, edit)


def navigation_failed(error: BaseException) -> bool:
    """Whether a browser navigation error says the host could not be reached."""
    return bool(_NAV_FAILED.search(str(error)))


def gate(runtime: Path, key: Optional[str] = None, host: Optional[str] = None) -> bool:
    """True when a source key may be read (it is not blocked, or is due a retry) and a host's things may be
    shown (it is not down). A host the record has never seen is allowed."""
    if host is not None and not _up(_host(db.at(runtime), site_of(host))):
        return False
    if key is not None:
        from .sources import weights
        return weights.open_to(weights.row(runtime, key), time.time(), retry_s(runtime))
    return True


def walkable(runtime: Path, host: str, now: Optional[float] = None) -> bool:
    """Whether a walk may go to `host` now: it is up or due a retry, and showed no wall in the last RETRY_S."""
    now = time.time() if now is None else now
    row = _host(db.at(runtime), site_of(host))
    if not row:
        return True
    if row["mode"]:
        return row["mode"] == "always"
    if row["wall_at"] and now - row["wall_at"] < RETRY_S:
        return False
    return _up(row) or now - row["fail_at"] >= retry_s(runtime)


def down_hosts(runtime: Path) -> list[str]:
    return [site for site, row in _all_hosts(runtime).items() if not _up(row)]


# --------------------------------------------------------------- the transport --

class _Recording(httpx.AsyncHTTPTransport):
    """An httpx transport that records each request's host as reachable or not."""

    def __init__(self, runtime: Path, **options):
        super().__init__(**options)
        self.runtime = Path(runtime)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host, counts = request.url.host, _tally.get()
        try:
            response = await super().handle_async_request(request)
        except COUNTED:
            if counts is not None:
                counts["fail"] += 1
            await asyncio.to_thread(record, self.runtime, host, False)
            raise
        if counts is not None:
            counts["ok"] += 1
        await asyncio.to_thread(record, self.runtime, host, True)
        return response


def recording_transport(runtime: Path, proxy: Optional[str] = None) -> httpx.AsyncHTTPTransport:
    return _Recording(runtime, proxy=proxy)


def client_options(runtime: Path) -> dict:
    """An httpx client's transport and mounts that record, and still go through the system's proxy as
    httpx itself would (a transport passed in otherwise turns that off)."""
    found = urllib.request.getproxies()
    mounts = {f"{scheme}://": recording_transport(runtime, url if "://" in url else "http://" + url)
              for scheme in ("http", "https", "all") if (url := found.get(scheme))}
    return {"transport": recording_transport(runtime), "mounts": mounts}


def tally() -> dict:
    """Start counting what the requests made from here on in this task do: {"ok": n, "fail": n}."""
    counts = {"ok": 0, "fail": 0}
    _tally.set(counts)
    return counts


async def _check(runtime: Path, hosts: list[str]) -> None:
    async def one(http: httpx.AsyncClient, host: str) -> None:
        try:
            async with http.stream("GET", f"https://{host}/"):
                pass
        except Exception:
            # Counted failures were recorded by the transport; anything else says nothing about reach.
            pass

    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S, follow_redirects=False,
                                 **client_options(runtime)) as http:
        await asyncio.gather(*(one(http, h) for h in hosts))


async def probe(runtime: Path, lang: str = "") -> None:
    """The first start's check of PROBE_HOSTS, run twice PROBE_AGAIN_S apart, so the gate and the
    region signature exist on day one."""
    if _factor(runtime, "probed"):
        return
    await _check(runtime, list(PROBE_HOSTS))
    await asyncio.to_thread(infer, runtime, lang)
    await asyncio.sleep(PROBE_AGAIN_S)
    await _check(runtime, list(PROBE_HOSTS))

    def done(state: dict) -> None:
        state["factors"]["probed"] = time.time()

    await asyncio.to_thread(_edit, runtime, done)
    await asyncio.to_thread(infer, runtime, lang)


async def retry_down(runtime: Path, now: Optional[float] = None) -> None:
    """Each round: the down hosts due a retry (RETRY_S after their last failure, every round with the VPN
    switch on) tried again, so one no source or walk asks for, such as a first-start probe host, comes
    back too. A host the person set to always or never is left alone."""
    now = time.time() if now is None else now
    wait = retry_s(runtime)
    named = {site_of(host): host for host in PROBE_HOSTS}
    due = [named.get(site, site) for site, row in _all_hosts(runtime).items()
           if not row["mode"] and row["fails"] >= DOWN_AFTER and now - row["fail_at"] >= wait]
    if due:
        await _check(runtime, due)


# ------------------------------------------------------------------- inference --

def primary(code: str) -> str:
    """A language code's language: "zh-Hans-CN" and "zh-TW" are "zh", "pt-BR" is "pt"."""
    return str(code or "").strip().replace("_", "-").split("-")[0].lower()


def _windows_languages() -> list[str]:
    kernel = ctypes.windll.kernel32
    count, size = ctypes.c_ulong(), ctypes.c_ulong(0)
    kernel.GetUserPreferredUILanguages(0x8, ctypes.byref(count), None, ctypes.byref(size))
    buffer = ctypes.create_unicode_buffer(size.value)
    kernel.GetUserPreferredUILanguages(0x8, ctypes.byref(count), buffer, ctypes.byref(size))
    return [code for code in buffer[:size.value].split("\0") if code]


def _defaults(key: str) -> str:
    try:
        return subprocess.run(["defaults", "read", "-g", key], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


@functools.cache
def os_languages() -> frozenset[str]:
    """The languages the system's own settings name, read once per run."""
    if sys.platform == "win32":
        codes = _windows_languages()
    elif sys.platform == "darwin":
        codes = re.findall(r'"?([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*)"?', _defaults("AppleLanguages"))
    else:
        codes = [locale.getlocale()[0] or ""]
    return frozenset(primary(c) for c in codes if primary(c) not in ("", "c", "posix"))


def os_region() -> str:
    """The country the system is set to, as two letters, or ""."""
    if sys.platform == "win32":
        buffer = ctypes.create_unicode_buffer(16)
        if ctypes.windll.kernel32.GetUserDefaultGeoName(buffer, 16):
            code = buffer.value.upper()
            return code if re.fullmatch(r"[A-Z]{2}", code) else ""
        return ""
    named = _defaults("AppleLocale").strip() if sys.platform == "darwin" else locale.getlocale()[0] or ""
    found = re.search(r"_([A-Z]{2})\b", named)
    return found.group(1) if found else ""


def _zone() -> str:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation") as key:
                return str(winreg.QueryValueEx(key, "TimeZoneKeyName")[0])
        except OSError:
            return ""
    try:
        return str(Path("/etc/localtime").resolve()).split("zoneinfo/", 1)[-1]
    except OSError:
        return ""


def _metered() -> Optional[bool]:
    """Whether Windows says the connection costs by use; None elsewhere or when it does not say."""
    if sys.platform != "win32":
        return None
    script = ("[void][Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,"
              "ContentType=WindowsRuntime];$p=[Windows.Networking.Connectivity.NetworkInformation]::"
              "GetInternetConnectionProfile();if($p){$p.GetConnectionCost().NetworkCostType}")
    try:
        answer = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return answer in ("Fixed", "Variable") if answer else None


def script_lang(text: str) -> str:
    """The language a title's script says, where it says one: kana, hangul, Han, Cyrillic, plain Latin."""
    if _KANA.search(text):
        return "ja"
    if _HANGUL.search(text):
        return "ko"
    if _HAN.search(text):
        return "zh"
    if _CYRILLIC.search(text):
        return "ru"
    if _LATIN.search(text) and text.isascii():
        return "en"
    return ""


def _opened_languages(runtime: Path) -> set[str]:
    """Languages of at least OPENS_FOR_LANG things the person opened or liked."""
    from .library import Library
    from .panels import feed
    from .sources import catalog
    wanted = {e[1] for e in feed.events(runtime) if e[2] in ("open", "like", "play")}
    if not wanted:
        return set()
    sources = catalog.all_sources([])
    counts: Counter = Counter()
    for item in Library(runtime).items():
        if item.id in wanted:
            source = sources.get(item.source)
            counts[primary(source.lang) if source and source.lang else script_lang(item.title)] += 1
    return {lang for lang, n in counts.items() if lang and n >= OPENS_FOR_LANG}


def _accounts(runtime: Path) -> list[str]:
    """The person's connected accounts on platforms some sources or walk sites belong to."""
    found = []
    for key in PLATFORMS:
        try:
            state = json.loads((Path(runtime) / "accounts" / f"{key}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(state, dict) and any(state.get(f) for f in ("connected", "channels", "login", "account", "user")):
            found.append(key)
    return found


def _signature(hosts: dict) -> Optional[str]:
    """"CN" when YouTube and Google are down while Bilibili answers, the mainland's pattern; else None."""
    def known_down(site: str) -> bool:
        row = hosts.get(site)
        return bool(row) and row["fails"] >= DOWN_AFTER

    bilibili = hosts.get("bilibili.com")
    if known_down("youtube.com") and known_down("google.com") and bilibili and bilibili["ok_at"] and not bilibili["fails"]:
        return "CN"
    return None


def infer(runtime: Path, lang: str = "", now: Optional[float] = None) -> dict:
    """Every factor inferred again, locally; run at start and at the beginning of each round."""
    now = time.time() if now is None else now
    system = os_region()
    zone = _ZONES.get(_zone(), "")
    readable = {primary(lang)} | os_languages() | _opened_languages(runtime)
    accounts = _accounts(runtime)
    metered = _metered()

    def edit(state: dict) -> None:
        _observe(state, "region", "os", system or zone or None, now)
        _observe(state, "region", "observed", _signature(state["hosts"]), now)
        current = _top(state["factors"]["region"])[1] or ""
        _observe(state, "home", "os", system if system and system != current else "", now)
        _observe(state, "readable", "observed", sorted(r for r in readable if r), now)
        _observe(state, "accounts", "observed", accounts, now)
        if metered is not None:
            _observe(state, "metered", "os", metered, now)

    return _edit(runtime, edit)


# ------------------------------------------------------------- the five effects --

def expand(codes) -> set[str]:
    """Languages with the codes a source may write them as ("zh-TW" for "zh")."""
    codes = {primary(c) for c in codes}
    return codes | {v for c in codes for v in _VARIANTS.get(c, ())}


def readable(runtime: Path) -> set[str]:
    """The language codes the person reads, with the codes a source may write them as."""
    found = value(runtime, "readable") or sorted(os_languages() or {"en"})
    return expand(set(map(primary, found)) - set(value(runtime, "unreadable")))


def default_langs(runtime: Path, lang: str) -> set[str]:
    """The languages whose default sources count for the person, as the catalogue writes defaults: each
    one they read once, and Chinese in the script they use ("zh-Hant" for a zh-TW page or Taiwan/Hong Kong)."""
    found = {primary(c) for c in readable(runtime) | {lang}}
    if "zh" in found and (lang in ("zh-TW", "zh-Hant", "zh-HK") or value(runtime, "region") in ("TW", "HK", "MO")):
        found = found - {"zh"} | {"zh-Hant"}
    return found


def unreadable(runtime: Path) -> set[str]:
    """The languages the person said they cannot read: those sources are never drawn."""
    return expand(value(runtime, "unreadable"))


def supplier(runtime: Path) -> Callable[[Any], float]:
    """`supply` for many sources at once: the table and the profile read once."""
    from . import profile
    region, home = value(runtime, "region"), value(runtime, "home")
    lean = profile.load(runtime)["source_lean"]
    prefixes = tuple(p for key in value(runtime, "accounts") if key in PLATFORMS for p in PLATFORMS[key][0])

    def supply_of(source) -> float:
        term = 0.0
        if source.region and (region or home):
            term += HERE if source.region == region else HOME if source.region == home else ELSEWHERE
        term += LEAN * max(-1.0, min(1.0, lean.get(source.key, 0.0)))
        if prefixes and source.key.startswith(prefixes):
            term += ACCOUNT
        return term

    return supply_of


def supply(runtime: Path, source) -> float:
    """The log-weight the person's situation adds to a source in the draw."""
    return supplier(runtime)(source)


def walk_prior(runtime: Path, site: str, region: str = "") -> float:
    """What the situation adds to a walk site whose platform is of `region` ("" for the whole world's): +1
    for one of the person's region, or a world platform outside mainland China; and ACCOUNT for a platform
    the person connected."""
    here = value(runtime, "region")
    term = 1.0 if (region == here if region else here != "CN") else 0.0
    names = {PLATFORMS[k][1] for k in value(runtime, "accounts") if k in PLATFORMS}
    return term + (ACCOUNT if site in names else 0.0)


def priors(runtime: Path) -> dict[str, tuple[float, float]]:
    """(m0, q0) the situation puts on taste-model features."""
    return {"unread_lang": UNREAD_LANG}


def _hour(now: float) -> str:
    hour = datetime.fromtimestamp(now).hour
    return "night" if hour < 6 else "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"


def context(runtime: Path, now: float) -> list[str]:
    """Coarse buckets for now: hour of day, weekday or weekend, and a metered connection."""
    day = "weekend" if datetime.fromtimestamp(now).weekday() >= 5 else "weekday"
    return [f"hour:{_hour(now)}", f"day:{day}"] + (["metered"] if value(runtime, "metered") else [])


def _season(month: int, region: str) -> str:
    north = ("winter", "winter", "spring", "spring", "spring", "summer", "summer", "summer", "autumn", "autumn",
             "autumn", "winter")[month - 1]
    flip = {"winter": "summer", "summer": "winter", "spring": "autumn", "autumn": "spring"}
    return flip[north] if region in _SOUTH else north


def place(code: str) -> str:
    return _PLACES.get(code, code)


def notes(runtime: Path, now: float) -> str:
    """Plain lines on the person's situation for the model's prompts, today's line first."""
    region = value(runtime, "region")
    moment = datetime.fromtimestamp(now)
    where = f", in {place(region)}" if region else ""
    lines = [f"Today: {moment.strftime('%A')} {moment.day} {moment.strftime('%B %Y')}, {_hour(now)}, "
             f"{_season(moment.month, region)}{where}."]
    reads = sorted({primary(c) for c in readable(runtime)})
    lines.append("Reads: " + ", ".join(_LANG_NAMES.get(c, c) for c in reads) + ".")
    down = down_hosts(runtime)
    if down:
        lines.append("Out of reach from here right now: " + ", ".join(down) + ".")
    accounts = value(runtime, "accounts")
    if accounts:
        lines.append("Uses accounts on: " + ", ".join(accounts) + ".")
    if value(runtime, "metered"):
        lines.append("On a metered connection.")
    return "\n".join(lines)


def view(runtime: Path, now: Optional[float] = None) -> dict:
    """What the page shows and edits: each factor with its origin and any question, and the hosts."""
    now = time.time() if now is None else now
    factors = {}
    for name in NAMES:
        row = _factor(runtime, name)
        origin, found = _top(row)
        factors[name] = {"value": DEFAULTS[name] if origin == "default" else found, "origin": origin,
                         "ask": (row or {}).get("ask")}
    factors["readable"]["value"] = sorted({primary(c) for c in readable(runtime)})
    hosts = [{"host": site, "state": "up" if _up(row) else "down", "mode": row["mode"],
              "wall": bool(row["wall_at"] and now - row["wall_at"] < RETRY_S),
              "ok_at": int(row["ok_at"]), "fail_at": int(row["fail_at"])}
             for site, row in _all_hosts(runtime).items()]
    known = {h["host"]: h["state"] for h in hosts}
    return {"factors": factors, "hosts": hosts, "probed": bool(_factor(runtime, "probed")),
            "platforms": {site_of(p): known.get(site_of(p), "unknown") for p in PROBE_HOSTS}}

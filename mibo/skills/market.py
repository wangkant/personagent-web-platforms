"""The skill market: a catalog of public skills, each entry pointing at where the skill lives, never a copy of it.

The catalog is one JSON file (data/market/skills.json, shipped as a snapshot and refreshed at most once a day from
CATALOG_URL through the guarded fetch). It is data from the network: an entry that does not hold the fixed shape,
or points anywhere but a public https git host, is dropped, and nothing in it is read as an instruction.

Installing an entry fetches the skill's files at the entry's commit one by one, from its own host or, where that does
not answer, a public mirror of it (reach.py), checks each against the sha256 the entry lists for it (the list makes
up the entry's folder hash), scans what arrived, and writes it through the store's add path with the entry recorded,
so an update can be told. Nothing in it is run. An entry whose licence does not
allow it, or whose scan finds a high risk, is never installed from here; a repository with no stated licence is
installed only when the person agreed to that on the page.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import time
import zipfile
from functools import cache
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import store
from .. import reach
from ..storage import atomic_write_text

logger = logging.getLogger("mibo.skills")

# Where the app refreshes the catalog from: the present repository, which is to be renamed.
CATALOG_URL = "https://raw.githubusercontent.com/wangkant/mibo/main/data/market/skills.json"
REFRESH_S = 24 * 3600
MAX_CATALOG = 16 * 1024 * 1024
MAX_PREVIEW = 512 * 1024
# The catalog's shape: 2 lists each entry's files with their sha256.
VERSION = 2
RISKS = ("none", "low", "high")
# Licences that allow use and redistribution (share-alike ones included), as tools/skillmarket/build.py reads them.
OPEN = frozenset({"MIT", "MIT-0", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD", "Unlicense",
                  "CC0-1.0", "CC-BY-4.0", "BSL-1.0", "Zlib", "CC-BY-SA-4.0", "GPL-2.0", "GPL-3.0", "AGPL-3.0",
                  "LGPL-2.1", "LGPL-3.0", "MPL-2.0"})
FITS = ("ready", "partial", "no")
# Public git hosts an entry may point at, with where a file at a commit can be read: the host's own raw address
# first, then for GitHub the public mirrors that serve the same bytes (jsDelivr's two networks, ghfast.top).
HOSTS = {
    "github.com": lambda repo, sha, path: [f"https://raw.githubusercontent.com/{repo}/{sha}/{path}",
                                           f"https://cdn.jsdelivr.net/gh/{repo}@{sha}/{path}",
                                           f"https://fastly.jsdelivr.net/gh/{repo}@{sha}/{path}",
                                           f"https://ghfast.top/https://raw.githubusercontent.com/{repo}/{sha}/{path}"],
    "gitlab.com": lambda repo, sha, path: [f"https://gitlab.com/{repo}/-/raw/{sha}/{path}"],
    "codeberg.org": lambda repo, sha, path: [f"https://codeberg.org/{repo}/raw/commit/{sha}/{path}"],
    "gitee.com": lambda repo, sha, path: [f"https://gitee.com/{repo}/raw/{sha}/{path}"],
}
_REPO = re.compile(r"^[\w.-]+(?:/[\w.-]+){1,4}$")
_ID = re.compile(r"^[\w.-]{1,80}/[\w.-]{1,80}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_PATH = re.compile(r"^[\w.@+ -]+(?:/[\w.@+ -]+)*$")
_UA = {"User-Agent": "Mibo"}
_lock = asyncio.Lock()


# ------------------------------------------------------------------- hashing

def folder_hash(files: dict[str, bytes]) -> str:
    """The hash a catalog entry records for a skill's folder: sha256 over one line per file, sorted by the file's
    path inside the folder (POSIX, UTF-8), each line `path NUL sha256-of-its-bytes-in-hex LF`."""
    total = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.encode("utf-8")):
        total.update(path.encode("utf-8") + b"\0" + hashlib.sha256(files[path]).hexdigest().encode() + b"\n")
    return total.hexdigest()


def listed_hash(listed: dict[str, str]) -> str:
    """folder_hash from each file's own sha256, as an entry lists them."""
    total = hashlib.sha256()
    for path in sorted(listed, key=lambda p: p.encode("utf-8")):
        total.update(path.encode("utf-8") + b"\0" + listed[path].encode() + b"\n")
    return total.hexdigest()


# ---------------------------------------------------------------------- scan

# What a skill's files may hold that matters before it is installed: (code, high, pattern). A skill's text is
# guidance the model reads, and its scripts run only through the command gate; these are what would turn either
# against the person.
_SCRIPT = re.compile(r"(?<!\.d)\.(py|js|mjs|cjs|ts|sh|bash|zsh|ps1|psm1|bat|cmd|rb|pl|php|lua)$", re.I)
_HOSTS = (r"webhook\.site|requestbin|pipedream\.net|ngrok(?:-free)?\.(?:io|app)|burpcollaborator|interact\.sh|\boast\."
          r"|discord(?:app)?\.com/api/webhooks|api\.telegram\.org/bot|hooks\.slack\.com/services|transfer\.sh")
_CHECKS = [(code, high, re.compile(p, re.I)) for code, high, p in (
    ("model_against_person", True,
     r"ignore (?:all |any )?(?:the )?(?:previous|prior|above|earlier|preceding|system) (?:instructions|messages|rules"
     r"|prompts?)|disregard (?:all |any )?(?:the |your )?(?:previous|prior|above|system) (?:instructions|rules|prompt)"
     r"|(?:do not|don't|never) (?:tell|inform|notify|alert|show) the (?:user|human|person|owner)"
     r"(?=\s*(?:about\b|anything\b|what\b|that you\b|this\b|it\b|[.,;:!)]|$))"
     r"|without (?:telling|informing|notifying) the (?:user|human|person|owner)"
     r"|keep (?:this|it) (?:secret|hidden) from the (?:user|human|person)"
     r"|pretend (?:that )?(?:you are|to be) (?:the user|a human|the system|an? (?:admin|administrator|developer))"
     r"|pretend (?:that )?(?:the user|they) (?:has|have) (?:approved|agreed|confirmed)"
     r"|(?:turn|switch|shut) off (?:the |all |any )?(?:confirmation|permission|approval|safety) (?:cards?|prompts?|checks?"
     r"|dialogs?)|(?:approve|answer|accept|press|click) (?:the |any |every )?(?:cards?|permission prompts?|confirmations?)"
     r" (?:yourself|for (?:them|the user|the person)|on (?:their|the user's|the person's) behalf)"
     r"|忽略(?:之前|以上|上面|先前|所有|系统)的?(?:指令|指示|说明|规则|提示)|不要(?:告诉|通知|告知)用户"
     r"|不要让用户(?:知道|发现|看到|察觉|注意到)|瞒着用户|对用户隐瞒|假装(?:你是用户|用户已|是系统)"
     r"|关闭(?:确认|权限|安全)?(?:卡片|提示|确认)|替用户(?:确认|点击|批准|同意)"),
    ("sends_data", True,
     _HOSTS +
     r"|(?:send|upload|post|forward) (?:all |the |their |the user'?s? |the person'?s? )?(?:credentials|cookies"
     r"|tokens|api keys|passwords|browser history|env(?:ironment)? variables|\.env(?: files?)?|ssh keys?|private keys?)"
     r" to\b|(?:send|upload|post|forward|copy) (?:(?:all|every|the|their|the user'?s?|the person'?s?) ){0,2}"
     r"(?:files|documents|photos)\b[^.\n]{0,40}?\bto (?:https?://|my server|our server|this (?:url|address|endpoint"
     r"|server|webhook))|(?:上传|发送|发|传)到\s*https?://"
     r"|(?:用户|对方|他们)的(?:密码|凭据|密钥|令牌|cookies?|token)[^。\n]{0,12}(?:发送|上传|发)(?:到|给)"),
    ("encoded_run", True,
     r"(?:eval|exec)\s*\(\s*(?:atob|base64\.b64decode|bytes\.fromhex|Buffer\.from|codecs\.decode)"
     r"|FromBase64String[^\n]*(?:iex|Invoke-Expression)|base64 (?:-d|--decode)[^\n|]*\|\s*(?:ba|z)?sh\b"
     r"|marshal\.loads|-EncodedCommand\b|powershell[^\n]* -enc\s"),
    ("download_run", True,
     r"(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b|(?:iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^\n|]*"
     r"\|\s*(?:iex|Invoke-Expression)\b|Invoke-Expression\s*\(?\s*\(?\s*(?:New-Object|iwr|irm|Invoke-WebRequest)"
     r"|(?:urlopen|requests\.get)\([^\n]*\)[^\n]*\b(?:exec|eval)\("),
    ("credentials", True,
     r"(?-i:Login Data|Local State)|cookies\.sqlite|Network\.get(?:All)?Cookies|Storage\.getCookies"
     r"|User Data[/\\]Default[/\\](?:Network[/\\])?Cookies|browser[_-]?cookie3|pycookiecheat|chrome-cookies-secure"
     r"|CredRead|cmdkey /list|wallet\.dat"),
    ("system_settings", True,
     r"\breg(?:\.exe)? add\b|Set-ItemProperty[^\n]*HK(?:LM|CU)|CurrentVersion\\+Run\b|schtasks(?:\.exe)? /create"
     r"|\bcrontab -|launchctl (?:load|bootstrap)|systemctl (?:enable|disable)|Set-MpPreference|netsh (?:advfirewall"
     r"|firewall)|\bbcdedit\b|Set-ExecutionPolicy\s+(?:Unrestricted|Bypass)"),
    ("encoded_blob", False, r"[A-Za-z0-9+/]{600,}={0,2}"),
    ("installs_packages", False,
     r"\b(?:pip3? install|npm (?:i|install)\b|npx -y\b|pnpm add|yarn add|brew install|apt(?:-get)? install"
     r"|winget install|choco install|cargo install|go install|sudo\b)"),
)]
_HOST = re.compile(_HOSTS, re.I)
# A skill that warns the model about injected text quotes it ("ignore previous instructions"): an example to refuse,
# not an instruction to the model.
_QUOTED = re.compile(r"[\"'“‘「『`(]\s*$")
_WARNS = re.compile(r"inject|(?:not|never) instructions|as data|flag it|malicious|disguis|steer you|directive|inert"
                    r"|target the model|伪装|注入|不是指令|视为数据|一律", re.I)
# Prose that forbids or faults what it names ("Never send credentials to an arbitrary URL", "Do not email them
# without telling the owner"), or a counter-example under a "✗ Bad" heading, says the opposite of the match.
_NEGATED = re.compile(r"(?<!-)\b(?:never|not|no|\w+n't|cannot|avoid|only|instead of|rather than|causes?|refuse)\b"
                      r"(?!\s+(?:hesitate|forget|fail|wait|need to ask))|不要|不得|禁止|切勿|绝不|不会", re.I)
_BAD = re.compile(r"✗|❌|\bBad\b|\bDon't:|错误示例|反例")
_GOOD = re.compile(r"✓|✅|\bGood\b|正确示例")
# A secret's path counts where the same sentence or line reads, copies or sends it, not where it is only named
# (a prerequisite, a list of files to leave out).
_SECRET_PATHS = re.compile(r"\.ssh[/\\](?:id_|authorized_keys)|\bid_(?:rsa|ed25519)\b|\.aws[/\\]credentials"
                           r"|find-generic-password|\.git-credentials", re.I)
_TAKES = re.compile(r"\b(?:read|reads|open|cat|type|copy|copies|cp|upload|send|load|readFileSync|readFile|Get-Content"
                    r"|copyFile|copyfile|copy2)\b|读取|复制|上传|发送", re.I)
# What prose may name as a vendor's own install steps: a shell installer fetched over https, a service enabled.
# The command still runs only through its card, so there it counts as installing software; fetched over plain
# http or from a bare address, it stays a download run.
_UNSAFE_FETCH = re.compile(r"http://|\b\d{1,3}(?:\.\d{1,3}){3}\b")
_PROSE_INSTALL = ("download_run", "system_settings")


def _sentence(text: str, start: int, end: int) -> tuple[str, str]:
    """The sentence or line around a match: (the part before it, all of it)."""
    begin = max(text.rfind(c, 0, start) for c in ".!?。！？\n|") + 1
    stops = [i for i in (text.find(c, end) for c in ".!?。！？\n") if i >= 0]
    return text[begin:start], text[begin:min(stops) if stops else len(text)]


def _found(code: str, pattern: re.Pattern, text: str, prose: bool, installs: set) -> bool:
    """Whether `text` holds what `pattern` looks for, read as the code's rules say; a vendor's install step in prose
    is put in `installs` instead."""
    for m in pattern.finditer(text):
        if prose and code in ("model_against_person", "sends_data", "download_run"):
            before = text[max(0, m.start() - 400):m.start()]
            bad = _BAD.search(before)
            if _NEGATED.search(_sentence(text, m.start(), m.end())[0]) or (bad and not _GOOD.search(before[bad.end():])):
                continue
        if code == "model_against_person" and _QUOTED.search(text[max(0, m.start() - 3):m.start()]) \
                and _WARNS.search(text[max(0, m.start() - 300):m.end() + 300]):
            continue
        # An exfiltration host named in a readme (an ngrok tunnel for local testing, a webhook example) is
        # documentation; in a script it is where data would go.
        if prose and code == "sends_data" and _HOST.fullmatch(m.group(0)):
            continue
        if prose and code in _PROSE_INSTALL and (code == "system_settings" or not _UNSAFE_FETCH.search(m.group(0))):
            installs.add(code)
            continue
        return True
    return False


def _takes_secret(text: str) -> bool:
    for m in _SECRET_PATHS.finditer(text):
        around = _sentence(text, m.start(), m.end())[1]
        if not around.lstrip().startswith(("#", "//", "*", "-- ")) and not _NEGATED.search(around) \
                and _TAKES.search(around):
            return True
    return False


_NETWORK = re.compile(r"requests\.(?:get|post)|urllib\.request|\bfetch\(|axios|http\.request|https?\.get\(|\bcurl |"
                      r"\bwget |Invoke-WebRequest|Invoke-RestMethod|httpx\.", re.I)
_DATA_URI = re.compile(r"data:[\w/+.-]+;base64,[A-Za-z0-9+/=]+")


def scan(files: dict[str, bytes]) -> dict:
    """The risk of a skill's files and why: high for anything in _CHECKS marked so, low for scripts, packages
    installed, the network or a long encoded blob, none otherwise. Notes are `code` or `code:path`."""
    notes: dict[str, str] = {}
    scripts = sorted(p for p in files if _SCRIPT.search(p))
    if scripts:
        notes["scripts"] = scripts[0]
    for path in sorted(files):
        raw = files[path]
        if b"\0" in raw[:4096] or len(raw) > 4 * 1024 * 1024:
            continue
        text = raw.decode("utf-8", "replace")
        # Pictures inlined as data: addresses are not hidden code.
        plain = _DATA_URI.sub("", text)
        prose, installs = path not in scripts, set()
        for code, _, pattern in _CHECKS:
            if code not in notes and _found(code, pattern, plain if code == "encoded_blob" else text, prose, installs):
                notes[code] = path
        if "credentials" not in notes and _takes_secret(text):
            notes["credentials"] = path
        # A vendor's install steps in prose are noted as installing software.
        if installs and not {"installs_packages", *_PROSE_INSTALL} & notes.keys():
            notes["installs_packages"] = path
        if path in scripts and "network" not in notes and _NETWORK.search(text):
            notes["network"] = path
    high = {code for code, is_high, _ in _CHECKS if is_high}
    risk = "high" if high & notes.keys() else "low" if notes else "none"
    order = [c for c, _, _ in _CHECKS] + ["scripts", "network"]
    return {"risk": risk, "notes": [f"{c}:{notes[c]}" for c in sorted(notes, key=order.index)]}


# ------------------------------------------------------------------- catalog

def _text(value, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) else ""


def _pair(value, limit: int) -> dict:
    value = value if isinstance(value, dict) else {}
    return {"zh": _text(value.get("zh", ""), limit), "en": _text(value.get("en", ""), limit)}


def _https(url, host: str = "") -> str:
    url = str(url or "") if isinstance(url, str) else ""
    m = re.match(r"^https://([a-z0-9.-]+)(?:/[^\s\"'<>]*)?$", url, re.I)
    if not m or (host and m.group(1).lower().removeprefix("www.") != host):
        return ""
    return url[:500]


def _plain_member(path: str) -> bool:
    """Whether a file an entry lists lands as a plain file inside the skill's folder; one that would climb out
    costs its entry, not the whole catalog."""
    try:
        return store._member_path(zipfile.ZipInfo(path))[1]
    except ValueError:
        return False


def entry(raw) -> dict | None:
    """One catalog entry in the fixed shape, its strings clipped, or None when it does not hold that shape or
    points anywhere but a public https git host."""
    if not isinstance(raw, dict):
        return None
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    licence = raw.get("licence") if isinstance(raw.get("licence"), dict) else {}
    found = raw.get("scan") if isinstance(raw.get("scan"), dict) else {}
    needs = raw.get("needs") if isinstance(raw.get("needs"), dict) else {}
    popular = raw.get("popularity") if isinstance(raw.get("popularity"), dict) else {}
    listed = raw.get("files") if isinstance(raw.get("files"), dict) else {}
    ident, name = str(raw.get("id") or ""), str(raw.get("name") or "")
    repo, path, sha = str(source.get("repo") or ""), str(source.get("path") or "").strip("/"), str(
        source.get("commit") or "")
    url = str(source.get("url") or "")
    # The page a person opens must be the repository's own, on the host the archive comes from.
    link = url.lower().removeprefix("https://").removeprefix("www.") if url.startswith("https://") else ""
    host = link.split("/", 1)[0]
    if (not _ID.match(ident) or not store.NAME.match(name) or len(name) > store.MAX_NAME or host not in HOSTS
            or not _REPO.match(repo) or ".." in repo.split("/") or not _https(url, host)
            or not (link == f"{host}/{repo.lower()}" or link.startswith(f"{host}/{repo.lower()}/"))
            or (path and (not _PATH.match(path) or ".." in path.split("/"))) or not _SHA.match(sha)
            or not _HASH.match(str(raw.get("hash") or "")) or found.get("risk") not in RISKS
            or raw.get("fit") not in FITS or not 0 < len(listed) <= store.MAX_MEMBERS
            or not all(isinstance(v, str) and _HASH.match(v) and _plain_member(k) for k, v in listed.items())
            or listed_hash(listed) != raw["hash"]):
        return None
    places = layout()
    category = raw.get("category") if raw.get("category") in places else ""
    sub = raw.get("sub") if raw.get("sub") in places.get(category, ()) else ""
    spdx = _text(licence.get("spdx", ""), 60) or "NONE"
    count = lambda v: v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None
    return {
        "id": ident, "name": name, "title": _pair(raw.get("title"), 60), "about": _pair(raw.get("about"), 300),
        "publisher": _text(raw.get("publisher", ""), 80) or repo.split("/")[0],
        "source": {"host": host, "repo": repo, "path": path, "commit": sha, "url": _https(url, host)},
        "licence": {"spdx": spdx, "url": _https(licence.get("url")),
                    "installable": licence.get("installable") is True, "note": _text(licence.get("note", ""), 300)},
        "category": category, "sub": sub,
        "tags": [_text(t, 30) for t in (raw.get("tags") if isinstance(raw.get("tags"), list) else [])[:12]
                 if _text(t, 30)],
        "needs": {"scripts": needs.get("scripts") is True,
                  "runtimes": [_text(t, 20) for t in (needs.get("runtimes") or [])[:6] if _text(t, 20)]
                  if isinstance(needs.get("runtimes"), list) else [],
                  "tools": [_text(t, 40) for t in (needs.get("tools") or [])[:12] if _text(t, 40)]
                  if isinstance(needs.get("tools"), list) else []},
        "fit": raw["fit"], "fit_note": _text(raw.get("fit_note", ""), 300),
        "popularity": {"stars": count(popular.get("stars")), "installs": count(popular.get("installs")),
                       "as_of": _text(popular.get("as_of", ""), 30)},
        "hash": raw["hash"], "files": dict(listed),
        "scan": {"risk": found["risk"], "notes": [_text(n, 300) for n in (found.get("notes") or [])[:12]
                                                  if _text(n, 300)] if isinstance(found.get("notes"), list) else []},
        "updated": _text(raw.get("updated", ""), 30),
    }


def _catalog(data) -> dict | None:
    if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("skills"), list):
        return None
    entries, seen = [], set()
    for raw in data["skills"][:5000]:
        one = entry(raw)
        if one and one["id"] not in seen:
            seen.add(one["id"])
            entries.append(one)
    sources = [{"id": _text(s.get("id", ""), 80), "name": _text(s.get("name", ""), 80), "url": _https(s.get("url")),
                "note": _text(s.get("note", ""), 300)}
               for s in (data.get("sources") if isinstance(data.get("sources"), list) else []) if isinstance(s, dict)]
    return {"version": VERSION, "built_at": _text(data.get("built_at", ""), 40), "sources": sources[:200],
            "skills": entries}


def snapshot_path() -> Path:
    from ..home import resource
    return resource("data", "market", "skills.json")


def _cache_path(home: Path) -> Path:
    return store._records_path(home).parent / "market" / "skills.json"


_loaded: dict[tuple, dict] = {}


def _read(path: Path) -> dict | None:
    try:
        info = path.stat()
    except OSError:
        return None
    key = (str(path), info.st_mtime_ns, info.st_size)
    if key not in _loaded:
        try:
            _loaded.clear()
            _loaded[key] = _catalog(json.loads(path.read_text(encoding="utf-8"))) or {}
        except (OSError, ValueError):
            _loaded[key] = {}
    return _loaded[key] or None


def catalog(home: Path) -> tuple[dict, str]:
    """(the catalog, "live" or "snapshot" or "none"): the refreshed one when it reads and is not older than the
    snapshot, else the snapshot shipped with the app."""
    live, shipped = _read(_cache_path(home)), _read(snapshot_path())
    if live and (not shipped or live["built_at"] >= shipped["built_at"]):
        return live, "live"
    if shipped:
        return shipped, "snapshot"
    return {"version": VERSION, "built_at": "", "sources": [], "skills": []}, "none"


def _refresh_state(home: Path) -> dict:
    try:
        data = json.loads((_cache_path(home).parent / "refresh.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def refresh(home: Path, force: bool = False) -> bool:
    """Fetches the catalog from CATALOG_URL at most once a day; True when a new one was kept."""
    from ..ingestion import safe_fetch_url
    state = _refresh_state(home)
    if not force and time.time() - float(state.get("tried") or 0) < REFRESH_S:
        return False
    place = _cache_path(home)
    place.parent.mkdir(parents=True, exist_ok=True)
    # The catalog carries the hashes everything else is checked against, so it comes from its own host only.
    result = None
    if await reach.answers(reach.runtime_of(home), urlsplit(CATALOG_URL).hostname):
        result = await safe_fetch_url(CATALOG_URL, timeout=30, max_wire_bytes=MAX_CATALOG,
                                      max_decoded_bytes=MAX_CATALOG, headers=_UA, max_redirects=3)
    ok = False
    if result is not None:
        try:
            ok = _catalog(json.loads(result.content.decode("utf-8"))) is not None
        except ValueError:
            ok = False
        if ok:
            atomic_write_text(place, result.content.decode("utf-8"))
    atomic_write_text(place.parent / "refresh.json", json.dumps({"tried": time.time(), "ok": ok}))
    if not ok:
        logger.info("[Skills] the market catalog was not refreshed")
    return ok


def by_id(home: Path, ident: str) -> dict | None:
    return next((e for e in catalog(home)[0]["skills"] if e["id"] == ident), None)


# ------------------------------------------------------------------ installed

def installed(home: Path) -> dict[str, dict]:
    """Entries installed from the market: id -> {folder, commit, hash, at}, for folders still there."""
    rows = store.records(home).get("sources") or {}
    there = store.skills_dir(home)
    return {row["market"]["id"]: {"folder": folder, **row["market"]} for folder, row in rows.items()
            if isinstance(row, dict) and isinstance(row.get("market"), dict) and (there / folder).is_dir()}


def can_install(one: dict, unlicensed_ok: bool) -> str:
    """"" when the entry may be installed from here, else the reason's code."""
    if one["scan"]["risk"] == "high":
        return "market_high_risk"
    # The flag is taken only with a licence that allows use and redistribution.
    if one["licence"]["installable"] and one["licence"]["spdx"] in OPEN:
        return ""
    if one["licence"]["spdx"].upper() == "NONE":
        return "" if unlicensed_ok else "market_unlicensed"
    return "market_not_installable"


def view(home: Path) -> dict:
    """What the page shows: the catalog, how it was got, and per entry whether it is installed or has an update."""
    data, came = catalog(home)
    have = installed(home)
    names = {s.name for s in store.all_skills(home)}
    state = _refresh_state(home)
    skills = []
    for one in data["skills"]:
        mine = have.get(one["id"])
        skills.append({**{k: v for k, v in one.items() if k != "files"}, "installed": bool(mine), "folder": mine["folder"] if mine else "",
                       "installed_commit": mine["commit"] if mine else "",
                       "update": bool(mine) and (mine["commit"], mine["hash"]) != (one["source"]["commit"], one["hash"]),
                       "name_taken": not mine and one["name"] in names,
                       # Why it may not be installed from here ("" when it may, after a word on a missing licence).
                       "blocked": can_install(one, True)})
    return {"built_at": data["built_at"], "from": came, "refresh_failed": state.get("ok") is False,
            "sources": data["sources"], "categories": layout(), "skills": skills}


@cache
def layout() -> dict[str, list[str]]:
    """The market's categories in the page's order, each with its places inside (data/market/categories.json);
    skills and connectors share them, and the locales name them."""
    from ..home import resource
    return json.loads(resource("data", "market", "categories.json").read_text(encoding="utf-8"))["categories"]


# ------------------------------------------------------------------- fetching

async def _get(url: str, limit: int, timeout: float = 60):
    from ..ingestion import safe_fetch_url
    return await safe_fetch_url(url, timeout=timeout, max_wire_bytes=limit, max_decoded_bytes=limit, headers=_UA,
                                max_redirects=5)


def zip_of(name: str, files: dict[str, bytes]) -> bytes:
    """A zip holding `files` in one folder `name`, for the store's add path."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in files.items():
            z.writestr(f"{name}/{path}", data)
    return out.getvalue()


def _sources(src: dict, path: str) -> list[str]:
    return HOSTS[src["host"]](src["repo"], src["commit"], quote(f"{src['path']}/{path}" if src["path"] else path))


async def _checked(home: Path, src: dict, path: str, sha: str, limit: int, timeout: float) -> bytes:
    """One file of the entry's folder from the first host that answers and serves the bytes its sha256 names;
    ValueError when none does."""
    wrong = False
    for url in await reach.order(reach.runtime_of(home), _sources(src, path)):
        got = await _get(url, limit, timeout)
        if got is None:
            continue
        if hashlib.sha256(got.content).hexdigest() == sha:
            return got.content
        wrong = True
    raise ValueError("market_hash_mismatch" if wrong else "market_fetch_failed")


async def fetch(home: Path, one: dict) -> dict[str, bytes]:
    """The entry's folder at its commit, file by file, each checked against the entry; ValueError when one cannot be
    fetched or does not match."""
    src, gate = one["source"], asyncio.Semaphore(4)

    async def take(path: str, sha: str) -> tuple[str, bytes]:
        async with gate:
            return path, await _checked(home, src, path, sha, store.MAX_MEMBER, 60)

    files = dict(await asyncio.gather(*(take(p, h) for p, h in one["files"].items())))
    if store.SKILL_FILE not in files:
        raise ValueError("skill_none_found")
    if sum(map(len, files.values())) > store.MAX_UNPACKED:
        raise ValueError("skill_too_large")
    return files


async def install(home: Path, ident: str, unlicensed_ok: bool = False) -> str:
    """Installs or updates one entry; returns its folder name. ValueError with a code when it may not be or did not
    arrive whole."""
    one = by_id(home, ident)
    if one is None:
        raise ValueError("market_gone")
    # Updating one the person installed keeps their word on a missing licence.
    why = can_install(one, unlicensed_ok or ident in installed(home))
    if why:
        raise ValueError(why)
    async with _lock:
        files = await fetch(home, one)
        # What arrived is scanned again; the entry's own result is not trusted for this.
        if scan(files)["risk"] == "high":
            raise ValueError("market_high_risk")
        mine = installed(home).get(ident)
        folder = mine["folder"] if mine else one["name"]
        added = await asyncio.to_thread(
            store.install_zip, home, zip_of(folder, files), "market", one["source"]["url"], "", one["source"]["commit"],
            {"id": ident, "commit": one["source"]["commit"], "hash": one["hash"], "at": time.time()})
        # The catalog's Chinese title and about are the skill's own: no model call to make them.
        skill = next(s for s in store.all_skills(home) if s.folder.name == added[0])
        store.keep_titles(home, skill, {"title_zh": one["title"]["zh"], "description_zh": one["about"]["zh"]})
    logger.info("[Skills] %s installed from the market at %s", ident, one["source"]["commit"][:12])
    return added[0]


def remove(home: Path, ident: str) -> None:
    mine = installed(home).get(ident)
    if mine is None:
        raise ValueError("skill_gone")
    store.remove(home, mine["folder"])


# The page's detail view: the SKILL.md at the reviewed commit, and whether the folder changed since, kept a while.
_seen: dict[str, tuple[float, object]] = {}
SEEN_S = 6 * 3600


async def preview(home: Path, one: dict) -> str | None:
    key = f"preview:{one['id']}:{one['source']['commit']}"
    if key in _seen:
        return _seen[key][1]
    try:
        got = await _checked(home, one["source"], store.SKILL_FILE, one["files"][store.SKILL_FILE], MAX_PREVIEW, 30)
    except ValueError:
        return None
    text = got.decode("utf-8", "replace")
    _seen[key] = (time.time(), text)
    return text


async def changed(home: Path, one: dict) -> bool | None:
    """Whether the folder changed upstream after the reviewed commit; None when it cannot be told (another host,
    the network, GitHub's hourly limit). Only GitHub's own API tells, so a network without it gets None at once."""
    src = one["source"]
    if src["host"] != "github.com" or not await reach.answers(reach.runtime_of(home), "api.github.com"):
        return None
    key = f"changed:{one['id']}:{src['commit']}"
    hit = _seen.get(key)
    if hit and time.time() - hit[0] < SEEN_S:
        return hit[1]
    from urllib.parse import quote
    url = f"https://api.github.com/repos/{src['repo']}/commits?per_page=1" + (
        f"&path={quote(src['path'])}" if src["path"] else "")
    result = await _get(url, 256 * 1024, 20)
    if result is None:
        return None
    try:
        rows = json.loads(result.content)
        sha = rows[0]["sha"]
        date = str(rows[0]["commit"]["committer"]["date"])
    except (ValueError, LookupError, TypeError):
        return None
    # The entry's commit may be the repository's head when it was read, later than the folder's own last commit.
    moved = sha != src["commit"] and bool(one["updated"]) and date[:len(one["updated"])] > one["updated"]
    _seen[key] = (time.time(), moved)
    return moved


async def mark(home: Path, owner: str) -> bytes | None:
    """A GitHub publisher's own picture, kept on disk once fetched."""
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", owner):
        return None
    place = _cache_path(home).parent / "marks" / f"{owner.lower()}.png"
    try:
        return place.read_bytes()
    except OSError:
        pass
    # The page keeps a plain icon until a picture arrives, so a network without GitHub only keeps the icon.
    if not await reach.answers(reach.runtime_of(home), "github.com"):
        return None
    result = await _get(f"https://github.com/{owner}.png?size=96", 512 * 1024, 20)
    if result is None or not result.content.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF")):
        return None
    place.parent.mkdir(parents=True, exist_ok=True)
    place.write_bytes(result.content)
    return result.content


# ---------------------------------------------------------- other hosts' skills

# The folders other agents keep a person's skills in, under their profile.
HOST_FOLDERS = (
    ("Claude Code", (".claude", "skills")),
    ("Codex", (".codex", "skills")),
    ("Agent Skills", (".agents", "skills")),
    ("Cursor", (".cursor", "skills")),
    ("Gemini CLI", (".gemini", "skills")),
    ("GitHub Copilot", (".copilot", "skills")),
    ("OpenCode", (".config", "opencode", "skills")),
    ("OpenCode", (".config", "opencode", "skill")),
    ("Windsurf", (".codeium", "windsurf", "skills")),
)


def profile() -> Path:
    return Path.home()


def other_hosts() -> list[dict]:
    """The skills in other agents' folders in the person's profile, by name; read only when the person asks."""
    found, seen = [], set()
    for host, parts in HOST_FOLDERS:
        root = profile().joinpath(*parts)
        for folder in store._folders(root):
            skill, _ = store.read(folder)
            key = str(folder.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            found.append({"host": host, "path": str(folder), "name": skill.name, "description": skill.description,
                          "problems": skill.problems})
    return found


def import_folders(home: Path, paths: list[str]) -> list[str]:
    """Copies the chosen ones of other_hosts() in through the store's add path; the originals are left as they are."""
    listed = {row["path"]: row for row in other_hosts() if not row["problems"]}
    added = []
    for path in paths:
        row = listed.get(path)
        if row is None:
            raise ValueError("skill_gone")
        folder = Path(path)
        files: dict[str, bytes] = {}
        size = 0
        for item in sorted(folder.rglob("*")):
            rel = item.relative_to(folder)
            if any(p.startswith(".") for p in rel.parts) or item.is_symlink() or not item.is_file():
                continue
            size += item.stat().st_size
            if len(files) >= store.MAX_MEMBERS or size > store.MAX_UNPACKED:
                raise ValueError("skill_too_large")
            files[rel.as_posix()] = item.read_bytes()
        added += store.install_zip(home, zip_of(folder.name, files), "import", f"{row['host']}: {folder}"[:300])
    return added

"""The connector market: a short list of MCP servers chosen for the app, the official MCP Registry searched
beyond it, one press to install, pinned versions with their updates, and servers brought over from other apps.

Entries are in the registry's own server.json shape (`packages` to run on this computer, `remotes` to reach),
the shipped list (data/market/servers.json) adding a `listing` of Chinese and English words, a category and
the publisher's GitHub account. Installing builds the mcpServers entry the rest of the app already runs
(mcp_tools.py, mcp_hub.py): `npx -y pkg@version` or `uvx pkg==version`, or an address with its headers.
Every value typed into its form is kept in the credential store, an address's secret part included; a local
program still starts only once the person allows its exact command, and a remote one that wants a sign-in
says so on its row. The version is pinned in the arguments; npm or PyPI is asked at most once a day for the
latest, and an update is a press that rewrites the pin (and so asks to start again).

Registry entries, other apps' files and ModelScope's answer are data from elsewhere: shapes are checked,
strings clipped, nothing is followed as an instruction. Other apps' files are read only on the person's press
and are never changed. Requests carry no name of this app.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import mcp_tools as mt
from . import reach, secretstore
from .storage import atomic_write_text

# The registry's frozen API (v0.1), and where each answer says whether a version is active, deprecated or deleted.
REGISTRY = "https://registry.modelcontextprotocol.io/v0.1/servers"
OFFICIAL = "io.modelcontextprotocol.registry/official"
MODELSCOPE = "https://www.modelscope.cn/api/v1/mcp/services/operational"
LATEST = {"npm": "https://registry.npmjs.org/{}/latest", "pypi": "https://pypi.org/pypi/{}/json"}
COMMANDS = {"npm": "npx", "pypi": "uvx"}
RUNTIMES = {"npm": "node", "pypi": "uv"}
#: Seconds between asks for a package's latest version, and a registry search kept.
LATEST_S = 24 * 3600
SEARCH_S = 600
MAX_BODY = 4 * 1024 * 1024
TOKEN_KEY = "MIBO_MCP_MODELSCOPE_TOKEN"
_UA = {"User-Agent": "mcp-client/1.0", "Accept": "application/json"}
_ID = re.compile(r"^[A-Za-z0-9][\w.-]{0,100}/[\w.-]{1,100}$")
_VAR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]{0,63})\}")
_PACKAGE = {"npm": re.compile(r"^(?:@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*$", re.I),
            "pypi": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")}
_VERSION = re.compile(r"^[0-9][0-9A-Za-z.+_-]{0,63}$")
_searched: dict[str, tuple[float, list]] = {}


# ------------------------------------------------------------------- entries

def _text(value, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _pair(value, limit: int) -> dict:
    value = value if isinstance(value, dict) else {}
    return {"zh": _text(value.get("zh"), limit), "en": _text(value.get("en"), limit)}


def _https(url) -> str:
    url = url if isinstance(url, str) else ""
    return url[:500] if re.match(r"^https://[a-z0-9.-]+(?:[/?][^\s\"'<>]*)?$", url, re.I) else ""


def _input(ident: str, name: str, raw: dict, labels: dict) -> dict:
    """One value a way asks for: what it is called, how the form says it, whether it must be filled and kept secret."""
    label = labels.get(name) if isinstance(labels.get(name), dict) else {}
    return {"id": ident, "name": _text(name, 64), "required": raw.get("isRequired") is True,
            "secret": raw.get("isSecret") is True, "default": _text(raw.get("default"), 300),
            "label": {"zh": _text(label.get("zh"), 200), "en": _text(label.get("en") or raw.get("description"), 200)}}


def _remote(raw: dict, labels: dict) -> dict | None:
    if raw.get("type") not in ("streamable-http", "sse") or not _https(raw.get("url")):
        return None
    variables = raw.get("variables") if isinstance(raw.get("variables"), dict) else {}
    url = raw["url"]
    inputs = [_input(f"url:{name}", name, variables.get(name) or {}, labels) for name in _VAR.findall(url)]
    headers = []
    for header in (raw.get("headers") if isinstance(raw.get("headers"), list) else [])[:16]:
        if not isinstance(header, dict) or not mt.HEADER_NAME_RE.fullmatch(str(header.get("name") or "")):
            continue
        value = header.get("value") if isinstance(header.get("value"), str) else None
        own = header.get("variables") if isinstance(header.get("variables"), dict) else {}
        if value is None:
            inputs.append(_input(f"header:{header['name']}", header["name"], header, labels))
        else:
            inputs += [_input(f"url:{name}", name, own.get(name) or {}, labels) for name in _VAR.findall(value)]
        headers.append({"name": header["name"], "value": value})
    return {"kind": "remote", "transport": raw["type"], "url": url, "where": _VAR.sub(r"<\1>", url),
            "headers": headers, "inputs": inputs, "needs": "", "signin": False}


def _package(raw: dict, labels: dict) -> dict | None:
    kind = raw.get("registryType")
    ident, version = str(raw.get("identifier") or ""), str(raw.get("version") or "")
    transport = raw.get("transport") if isinstance(raw.get("transport"), dict) else {}
    if kind not in COMMANDS or transport.get("type", "stdio") != "stdio" or not _PACKAGE[kind].match(ident) \
            or not _VERSION.match(version):
        return None
    inputs, args = [], []
    for n, arg in enumerate((raw.get("packageArguments") if isinstance(raw.get("packageArguments"), list) else [])[:16]):
        if not isinstance(arg, dict):
            continue
        flag = str(arg.get("name") or "") if arg.get("type") == "named" else ""
        if isinstance(arg.get("value"), str):
            args += [flag, arg["value"]] if flag else [arg["value"]]
        elif arg.get("isRequired") is True:
            hint = _text(arg.get("valueHint") or flag.lstrip("-") or f"value{n}", 64)
            inputs.append(_input(f"arg:{n}", hint, arg, labels))
            args += [flag, {"input": f"arg:{n}"}] if flag else [{"input": f"arg:{n}"}]
    for env in (raw.get("environmentVariables") if isinstance(raw.get("environmentVariables"), list) else [])[:32]:
        if isinstance(env, dict) and mt.ENV_NAME_RE.fullmatch(str(env.get("name") or "")):
            inputs.append(_input(f"env:{env['name']}", env["name"], env, labels))
    pinned = f"{ident}@{version}" if kind == "npm" else f"{ident}=={version}"
    shown = [COMMANDS[kind], *(["-y"] if kind == "npm" else []), pinned,
             *(a if isinstance(a, str) else f"<{next(i['name'] for i in inputs if i['id'] == a['input'])}>" for a in args)]
    return {"kind": kind, "transport": "stdio", "package": ident, "version": version, "args": args,
            "where": " ".join(shown), "inputs": inputs, "needs": RUNTIMES[kind], "signin": False}


def _publisher(ident: str, owner: str) -> str:
    """Who publishes an entry, from its GitHub account or its reversed domain (com.notion/mcp -> notion.com)."""
    if owner:
        return owner
    space = ident.split("/")[0].split(".")
    if space[:2] == ["io", "github"] and len(space) > 2:
        return space[2]
    return ".".join(reversed(space))


def entry(item, reviewed: bool = False) -> dict | None:
    """One market entry from a registry item ({"server": server.json, "listing": …}), or None when it has no way
    this app can run and no sync of its own."""
    server = item.get("server") if isinstance(item, dict) and isinstance(item.get("server"), dict) else None
    if server is None:
        return None
    listing = item.get("listing") if isinstance(item.get("listing"), dict) else {}
    ident = str(server.get("name") or "")
    if not _ID.match(ident):
        return None
    labels = listing.get("labels") if isinstance(listing.get("labels"), dict) else {}
    ways = [w for w in (_remote(r, labels) for r in (server.get("remotes") or [])[:4] if isinstance(r, dict)) if w]
    ways += [w for w in (_package(p, labels) for p in (server.get("packages") or [])[:6] if isinstance(p, dict)) if w]
    sync = listing.get("sync") if listing.get("sync") == "modelscope" else ""
    if not ways and not sync:
        return None
    for way in ways:
        way["signin"] = way["kind"] == "remote" and listing.get("signin") is True
    repo = server.get("repository") if isinstance(server.get("repository"), dict) else {}
    repo_url = _https(repo.get("url"))
    owner = _text(listing.get("owner"), 40) or (repo_url.split("/")[3] if repo_url.startswith("https://github.com/")
                                               and repo_url.count("/") >= 4 else "")
    title, about = _pair(listing.get("title"), 60), _pair(listing.get("about"), 300)
    title["en"] = title["en"] or _text(server.get("title"), 60) or ident.split("/")[-1]
    about["en"] = about["en"] or _text(server.get("description"), 300)
    name = _text(listing.get("name"), 40) or re.sub(r"[^A-Za-z0-9_.-]", "-", ident.split("/")[-1])[:40].strip("-.")
    tags = listing.get("tags") if isinstance(listing.get("tags"), list) else []
    return {"id": ident, "name": name or "server", "title": title, "about": about,
            "publisher": _publisher(ident, owner), "owner": owner, "repo": repo_url,
            "category": _text(listing.get("category"), 30), "sub": _text(listing.get("sub"), 30), "licence": _text(listing.get("licence"), 40),
            "tags": [_text(t, 30) for t in tags[:12] if _text(t, 30)],
            "match": [_text(t, 30).lower() for t in (listing.get("match") or [])[:6] if _text(t, 30)],
            "get_key": _https(listing.get("get_key")), "sync": sync, "reviewed": reviewed,
            "version": _text(server.get("version"), 40), "ways": ways}


def snapshot_path() -> Path:
    from .home import resource
    return resource("data", "market", "servers.json")


_shipped: dict[tuple, dict] = {}


def shipped() -> dict:
    """The list shipped with the app, read once per change of the file."""
    path = snapshot_path()
    info = path.stat()
    key = (info.st_mtime_ns, info.st_size)
    if key not in _shipped:
        data = json.loads(path.read_text(encoding="utf-8"))
        _shipped.clear()
        _shipped[key] = {"built_at": _text(data.get("built_at"), 40),
                         "servers": [e for e in (entry(raw, True) for raw in data.get("servers") or []) if e]}
    return _shipped[key]


# ------------------------------------------------------------------- versions

def _pin(spec: dict) -> tuple[int, str, str, str] | None:
    """(the argument's index, npm or pypi, the package, its pinned version or "") of a server started by npx
    or uvx; None otherwise."""
    command = re.sub(r"\.(cmd|exe)$", "", Path(str(spec.get("command") or "")).name.lower())
    kind = {"npx": "npm", "uvx": "pypi"}.get(command)
    args = spec.get("args") or []
    if kind == "pypi" and "--from" in args[:-1]:
        at = args.index("--from") + 1
    else:
        at = next((i for i, a in enumerate(args) if not a.startswith("-")), -1)
    if not kind or at < 0:
        return None
    word = args[at]
    if kind == "npm":
        scope = "@" if word.startswith("@") else ""
        package, _, version = word[len(scope):].partition("@")
        package = scope + package
    else:
        package, _, version = word.partition("==") if "==" in word else word.partition("@")
    return (at, kind, package, version) if _PACKAGE[kind].match(package) else None


def pinned(spec: dict) -> tuple[str, str, str] | None:
    """(npm or pypi, the package, its pinned version or "") of a server started by npx or uvx; None otherwise."""
    pin = _pin(spec)
    return pin[1:] if pin else None


def repin(spec: dict, version: str) -> dict:
    """The spec with its package pinned at `version`."""
    at, kind, package, _ = _pin(spec)
    args = list(spec["args"])
    args[at] = f"{package}@{version}" if kind == "npm" else f"{package}=={version}"
    return {**spec, "args": args}


def _latest_path(home: Path) -> Path:
    return mt.desktop_dir(home) / "mcp-latest.json"


def _latest(home: Path) -> dict:
    try:
        data = json.loads(_latest_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def _get(url: str, headers: dict | None = None, limit: int = MAX_BODY):
    from .ingestion import safe_fetch_url
    return await safe_fetch_url(url, timeout=20, max_wire_bytes=limit, max_decoded_bytes=limit,
                                headers={**_UA, **(headers or {})}, max_redirects=3)


async def refresh_latest(home: Path, force: bool = False) -> None:
    """Asks npm or PyPI for the latest version of each pinned package, each at most once a day."""
    known = _latest(home)
    pins = [pinned(spec) for spec in mt.load_config(home)["mcpServers"].values()]
    asked = {(pin[0], pin[1]) for pin in pins if pin and pin[2]}
    changed = False
    for kind, package in asked:
        key = f"{kind}:{package}"
        if not force and time.time() - float((known.get(key) or {}).get("at") or 0) < LATEST_S:
            continue
        # A version from a mirror would be unchecked, so only npm's or PyPI's own answer counts; asked again later.
        if not await reach.answers(reach.runtime_of(home), urlsplit(LATEST[kind]).hostname):
            continue
        result = await _get(LATEST[kind].format(quote(package, safe="@") if kind == "npm" else package))
        version = ""
        if result is not None:
            try:
                data = json.loads(result.content)
                version = str(data["version"] if kind == "npm" else data["info"]["version"])
            except (ValueError, LookupError, TypeError):
                version = ""
        known[key] = {"version": version if _VERSION.match(version) else "", "at": time.time()}
        changed = True
    if changed:
        mt.desktop_dir(home).mkdir(parents=True, exist_ok=True)
        atomic_write_text(_latest_path(home), json.dumps(known))


def annotate(view: dict, home: Path) -> dict:
    """Settings' rows with what the market knows: the pinned version, the latest one when it differs, and the
    entry a row was installed from."""
    config = mt.load_config(home)["mcpServers"]
    known = _latest(home)
    entries = shipped()["servers"]
    for row in view["servers"]:
        spec = config.get(row["name"]) or {}
        pin = pinned(spec)
        latest = (known.get(f"{pin[0]}:{pin[1]}") or {}).get("version", "") if pin else ""
        row["version"] = pin[2] if pin else ""
        row["unpinned"] = bool(pin) and not pin[2]
        row["latest"] = latest if pin and pin[2] and latest and latest != pin[2] else ""
        row["market"] = next((e["id"] for e in entries if _installed_as(e, spec)), "")
    view["updates"] = sum(1 for row in view["servers"] if row["latest"])
    return view


# --------------------------------------------------------------------- market

def _bare(url: str) -> str:
    """An address without its query or a trailing slash, as two entries are told apart."""
    parts = urlsplit(_VAR.sub("", str(url or "")))
    return f"{parts.netloc.lower()}{parts.path.rstrip('/')}"


def _installed_as(one: dict, spec: dict) -> bool:
    pin = pinned(spec)
    for way in one["ways"]:
        if way["kind"] == "remote" and spec.get("url") and _bare(spec["url"]) == _bare(way["url"]):
            return True
        if way["kind"] != "remote" and pin and pin[:2] == (way["kind"], way["package"]):
            return True
    return False


def _with_installed(one: dict, config: dict) -> dict:
    mine = next((name for name, spec in config.items() if _installed_as(one, spec)), "")
    if one["sync"]:
        mine = next((name for name in config if name.startswith("ms-")), "")
    return {**one, "installed": mine}


def view(home: Path) -> dict:
    """What the market shows: the shipped list, each entry with the server it is installed as."""
    from .skills.market import layout
    data, config = shipped(), mt.load_config(home)["mcpServers"]
    return {"built_at": data["built_at"], "categories": layout(),
            "servers": [_with_installed(one, config) for one in data["servers"]],
            "synced": bool(secretstore.get(home, TOKEN_KEY))}


def _active(item) -> bool:
    """Whether a registry answer's version is active: one its publisher deprecated or deleted is not offered."""
    meta = item.get("_meta") if isinstance(item, dict) and isinstance(item.get("_meta"), dict) else {}
    return isinstance(meta.get(OFFICIAL), dict) and meta[OFFICIAL].get("status") == "active"


async def search(home: Path, query: str) -> list[dict]:
    """The official registry's active entries whose names hold `query`, latest versions only, without those
    shipped."""
    query = " ".join(query.split())[:60]
    # The registry answers a search of fewer than three characters with an error.
    if len(query) < 3 or not query.isascii():
        return []
    hit = _searched.get(query.lower())
    if hit is None or time.time() - hit[0] > SEARCH_S:
        # The registry has no mirror: a network without it is told at once rather than after a long wait.
        if not await reach.answers(reach.runtime_of(home), urlsplit(REGISTRY).hostname):
            raise mt.ToolsError("market_search_failed")
        result = await _get(f"{REGISTRY}?search={quote(query)}&version=latest&limit=40")
        if result is None:
            raise mt.ToolsError("market_search_failed")
        try:
            rows = json.loads(result.content).get("servers") or []
        except (ValueError, AttributeError):
            raise mt.ToolsError("market_search_failed") from None
        hit = _searched[query.lower()] = (time.time(), [e for e in (entry(r) for r in rows[:40] if _active(r)) if e])
    have = {e["id"] for e in shipped()["servers"]}
    config = mt.load_config(home)["mcpServers"]
    return [_with_installed(one, config) for one in hit[1] if one["id"] not in have][:30]


async def by_id(ident: str) -> dict:
    """A shipped entry, or the registry's latest version of one while it is active; the page's copy is never
    trusted."""
    one = next((e for e in shipped()["servers"] if e["id"] == ident), None)
    if one is None and _ID.match(ident):
        result = await _get(f"{REGISTRY}/{quote(ident, safe='')}/versions/latest")
        try:
            item = json.loads(result.content) if result is not None else None
        except ValueError:
            item = None
        one = entry(item) if _active(item) else None
    if one is None:
        raise mt.ToolsError("market_gone")
    return one


def _free_name(name: str, config: dict) -> str:
    base = mt._server_name(re.sub(r"[^A-Za-z0-9_.-]", "-", name).strip("-.")[:36] or "server")
    taken = {n.lower() for n in config}
    out, n = base, 2
    while out.lower() in taken:
        out, n = f"{base}-{n}", n + 1
    return out


def build(one: dict, index: int, values: dict, name: str) -> tuple[dict, dict]:
    """({name: spec} for mcp.json, the address's secret values to keep): the way chosen, filled from the form.
    Values of the environment and headers are kept by mcp_tools.stash on writing; a secret part of the
    address is kept here, the address holding its ${MIBO_MCP_…} reference."""
    if not 0 <= index < len(one["ways"]):
        raise mt.ToolsError("market_gone")
    way = one["ways"][index]
    given = {k: v.strip() for k, v in values.items() if isinstance(k, str) and isinstance(v, str) and v.strip()}
    rows = {row["id"]: row for row in way["inputs"]}
    for row in rows.values():
        if row["default"]:
            given.setdefault(row["id"], row["default"])
        if row["required"] and not given.get(row["id"]):
            raise mt.ToolsError("tools_required")
    kept: dict = {}

    def var(secret_ok: bool):
        def one_var(m: re.Match) -> str:
            row, value = rows.get(f"url:{m.group(1)}"), given.get(f"url:{m.group(1)}", "")
            if secret_ok and row and row["secret"] and value:
                key = mt.secret_key(name, "env", m.group(1))
                kept[key] = value
                return "${" + key + "}"
            return quote(value, safe="") if secret_ok else value
        return one_var

    if way["kind"] == "remote":
        spec = {"url": _VAR.sub(var(True), way["url"]), "type": "sse" if way["transport"] == "sse" else "http"}
        headers = {}
        for header in way["headers"]:
            template = header["value"]
            if template is None:
                value = given.get(f"header:{header['name']}", "")
            else:
                # A header whose values were all left empty is left out ("Bearer " alone says nothing).
                names = _VAR.findall(template)
                value = _VAR.sub(var(False), template) if not names or any(given.get(f"url:{n}") for n in names) else ""
            if value:
                headers[header["name"]] = value
        spec["headers"] = headers
    else:
        args = [a if isinstance(a, str) else given[a["input"]] for a in way["args"] if isinstance(a, str) or a["input"] in given]
        pin = f"{way['package']}@{way['version']}" if way["kind"] == "npm" else f"{way['package']}=={way['version']}"
        spec = {"command": COMMANDS[way["kind"]], "args": [*(["-y"] if way["kind"] == "npm" else []), pin, *args],
                "env": {row["name"]: given[row["id"]] for row in rows.values() if row["id"].startswith("env:") and row["id"] in given}}
    return {name: mt.normalize(spec)}, kept


async def install(home: Path, ident: str, index: int, values: dict, keep) -> str:
    """Adds one entry as a server; returns its name. `keep` writes values to the credential store."""
    one = await by_id(ident)
    config = mt.load_config(home)["mcpServers"]
    name = _free_name(one["name"], config)
    servers, kept = build(one, index, values, name)
    if kept:
        await asyncio.to_thread(keep, kept)
    await asyncio.to_thread(mt.write_servers, home, servers, keep)
    return name


def update(home: Path, server: str, keep) -> None:
    """Moves a pinned server to the latest version npm or PyPI named."""
    spec = mt.load_config(home)["mcpServers"].get(server)
    pin = pinned(spec) if spec else None
    latest = (_latest(home).get(f"{pin[0]}:{pin[1]}") or {}).get("version") if pin else ""
    if not latest:
        raise mt.ToolsError("tools_unknown_server")
    mt.write_servers(home, {server: repin(spec, latest)}, keep, replace=True)


# -------------------------------------------------------------- other apps

def places() -> list[tuple[str, Path]]:
    """Where Claude Desktop, Cursor, VS Code and Cherry Studio keep their MCP servers for this account."""
    home = Path.home()
    if sys.platform == "win32":
        roaming, local = Path(os.environ["APPDATA"]), Path(os.environ["LOCALAPPDATA"])
        store = sorted(local.glob("Packages/Claude_*/LocalCache/Roaming/Claude/claude_desktop_config.json"))
    else:
        roaming = home / "Library" / "Application Support" if sys.platform == "darwin" else home / ".config"
        store = []
    return [("Claude", roaming / "Claude" / "claude_desktop_config.json"), *(("Claude", p) for p in store),
            ("Cursor", home / ".cursor" / "mcp.json"),
            ("VS Code", roaming / "Code" / "User" / "mcp.json"),
            ("VS Code", roaming / "Code - Insiders" / "User" / "mcp.json"),
            ("Cherry Studio", roaming / "CherryStudio" / "Data" / "cherrystudio.sqlite")]


def _cherry(path: Path) -> dict:
    """Cherry Studio's servers from its database, read only, in the mcpServers shape."""
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT name, type, base_url, command, args, env, headers, is_active, install_source "
                          "FROM mcp_server").fetchall()
    def load(text):
        return json.loads(text) if text else None

    out = {}
    for name, kind, url, command, args, env, headers, active, source in rows:
        if kind == "inMemory" or source == "builtin":
            continue
        if command and kind in (None, "stdio"):
            spec = {"command": command, "args": load(args) or [], "env": load(env) or {}}
        else:
            spec = {"url": url, "headers": load(headers) or {}, "type": "sse" if kind == "sse" else "http"}
        out[str(name)] = {**spec, "disabled": not active}
    return out


def _servers_in(path: Path) -> dict:
    if path.suffix == ".sqlite":
        return _cherry(path)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    found = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else data.get("servers")
    return found if isinstance(found, dict) else {}


_ASKED = re.compile(r"\$\{(?:input|env|config|command):[^}]*\}|\$\{workspaceFolder[^}]*\}")


def _as_ours(server: str, raw: dict) -> tuple[dict, str]:
    """(the spec, "" or why it cannot come over). A value another app fills in itself (VS Code's ${input:…},
    ${env:…}) becomes one this app asks for in Settings."""
    raw = {k: v for k, v in raw.items() if k != "disabled"}
    for kind in ("env", "headers"):
        if isinstance(raw.get(kind), dict):
            raw[kind] = {k: ("${" + mt.secret_key(server, kind, k) + "}" if isinstance(v, str) and _ASKED.search(v) else v)
                         for k, v in raw[kind].items()}
    if _ASKED.search(json.dumps([raw.get("command"), raw.get("args"), raw.get("url")])):
        return {}, "variables"
    try:
        return mt.normalize(raw), ""
    except mt.ToolsError as e:
        return {}, str(e)


def other_apps(home: Path) -> list[dict]:
    """The servers kept by other apps on this account, by name and what they run; their values never leave here."""
    config = mt.load_config(home)["mcpServers"]
    found = []
    for app, path in places():
        if not path.is_file():
            continue
        try:
            servers = _servers_in(path)
        except (OSError, ValueError, sqlite3.Error):
            found.append({"key": "", "app": app, "path": str(path), "problem": "unreadable"})
            continue
        for n, (original, raw) in enumerate(list(servers.items())[:mt.MAX_SERVERS]):
            if not isinstance(raw, dict):
                continue
            name = _free_name(str(original), {}) if str(original).strip() else "server"
            spec, problem = _as_ours(name, raw)
            same = next((have for have, mine in config.items() if spec and
                         {k: v for k, v in mine.items() if k not in ("env", "headers", "disabled")} ==
                         {k: v for k, v in spec.items() if k not in ("env", "headers")}), "")
            found.append({"key": f"{app}|{path}|{n}", "app": app, "path": str(path), "original": str(original)[:80],
                          "name": name, "kind": "program" if raw.get("command") else "address",
                          "where": mt.command_line(spec) if spec.get("command") else _masked(spec.get("url", "")),
                          "values": sorted({*(spec.get("env") or {}), *(spec.get("headers") or {})}),
                          "have": same, "taken": not same and name.lower() in {c.lower() for c in config},
                          "off": raw.get("disabled") is True, "problem": problem})
    return found


def _masked(url: str) -> str:
    head, _, query = url.partition("?")
    return head + ("?" + "&".join(p.split("=", 1)[0] + "=…" for p in query.split("&")) if query else "")


def import_from(home: Path, keys: list[str], keep) -> list[str]:
    """Copies the chosen servers in, read again from their files: values to the credential store, a name taken
    here given a free one. The other apps' files are left as they are."""
    listed = {row["key"]: row for row in other_apps(home) if row["key"] and not row["problem"] and not row["have"]}
    chosen = [listed[k] for k in keys if k in listed]
    if not chosen:
        raise mt.ToolsError("tools_unknown_server")
    config = dict(mt.load_config(home)["mcpServers"])
    servers = {}
    for row in chosen:
        app, path, n = row["key"].split("|")
        raw = list(_servers_in(Path(path)).values())[int(n)]
        name = _free_name(row["name"], {**config, **servers})
        spec, _ = _as_ours(name, raw)
        servers[name] = {**spec, "disabled": raw.get("disabled") is True}
    mt.write_servers(home, servers, keep)
    return list(servers)


# ------------------------------------------------------------------- ModelScope

def _hidden(name: str, url: str) -> tuple[str, dict]:
    """A hosted service's address, whose path names the person's own service: the path kept as a secret, the
    address holding its reference."""
    parts = urlsplit(url)
    key = mt.secret_key(name, "env", "PATH")
    rest = parts.path.lstrip("/") + (f"?{parts.query}" if parts.query else "")
    return f"https://{parts.netloc}/${{{key}}}", {key: rest}


async def sync_modelscope(home: Path, token: str, keep) -> list[str]:
    """Adds (or brings up to date) the services the person turned on at ModelScope, with the access token kept
    in the credential store for the next sync."""
    token = token.strip() or await asyncio.to_thread(secretstore.get, home, TOKEN_KEY)
    if not token:
        raise mt.ToolsError("tools_required")
    result = await _get(MODELSCOPE, {"Authorization": f"Bearer {token}"})
    try:
        rows = json.loads(result.content)["Data"]["Result"] if result is not None else None
    except (ValueError, LookupError, TypeError):
        rows = None
    if not isinstance(rows, list):
        raise mt.ToolsError("market_sync_failed")
    await asyncio.to_thread(keep, {TOKEN_KEY: token})
    servers, kept = {}, {}
    for row in rows[:mt.MAX_SERVERS]:
        urls = row.get("operational_urls") if isinstance(row, dict) else None
        url = _https(urls[0].get("url")) if isinstance(urls, list) and urls and isinstance(urls[0], dict) else ""
        if not url:
            continue
        name = mt._server_name(("ms-" + re.sub(r"[^A-Za-z0-9_.-]", "-", str(row.get("name") or row.get("id") or "")))[:40]
                               .strip("-.") or "ms-server")
        hidden, value = _hidden(name, url)
        kept.update(value)
        servers[name] = mt.normalize({"url": hidden, "type": "sse" if urlsplit(url).path.rstrip("/").endswith("/sse")
                                      else "http"})
    if kept:
        await asyncio.to_thread(keep, kept)
    if servers:
        await asyncio.to_thread(mt.write_servers, home, servers, keep, replace=True)
    return list(servers)

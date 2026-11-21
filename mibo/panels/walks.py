"""Settings › 外出 (static/welcome/setup/walks.js): whether the character goes out on its own, how often,
the sites it keeps off or favours, and the record of its walks. The time of the day's first walk (ROAM_AT) is
read from the home's settings only.

GET /api/walks is all of it; POST /api/walks changes one part: {on}, {walks} (written to the home's settings and
applied to the walk loop at once), {site, steer} ("avoid", "favour" or "" on the host's card,
web/sites.py), or {search, key}: the web search API the person brought ("brave" or "tavily", tried once
before it is kept in the system's credential store; "" removes it). The key is never sent back. Each walk
writes one entry to the record when it is back (roam.Roam.walk), whatever it kept.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Request

from .. import search, secretstore, settingsfile
from ..config_env import env_bool, env_int
from ..storage import atomic_write_text
from ..web import sites

ERRORS: frozenset[str] = frozenset({"walks_invalid", "walks_site", "walks_search_key"})
KEY_MAX = 400
FILE = Path("library") / "walks.json"
# Walks the record keeps, newest last.
KEEP = 40
WALKS_MAX = 6
_lock = threading.Lock()


def _load(runtime: Path) -> list:
    try:
        saved = json.loads((Path(runtime) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return saved if isinstance(saved, list) else []


def record(runtime: Path, row: dict) -> None:
    """One walk into the record, the oldest dropped past KEEP."""
    with _lock:
        rows = (_load(runtime) + [row])[-KEEP:]
        path = Path(runtime) / FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(rows, ensure_ascii=False))


def site_key(text: str) -> str:
    """The host key of what the person typed (an address or a bare name such as zhihu.com); "" for neither."""
    text = text.strip().lower()
    host = urlsplit(text if "://" in text else f"https://{text}").hostname or ""
    return sites.key_of(host) if "." in host and len(host) <= 253 else ""


def mount(app, workspace, body) -> None:
    def walker():
        return getattr(workspace.character.agent, "roam", None)

    def view() -> dict:
        values = workspace.config()
        runtime = workspace._runtime()
        roam, now = walker(), time.time()
        steered = sites.steered(runtime)
        names = {key: seed.get("name") or key for key, seed in sites.SEEDS.items() if seed.get("walk", True)}
        return {
            "on": env_bool("ROAM_ENABLED", True, env=values),
            "walks": env_int("ROAM_WALKS_A_DAY", 3, minimum=0, maximum=WALKS_MAX, env=values),
            # Why no walk of its own may leave now ("" when one may), when the next one may, and today's count.
            "why": roam._held(now) if roam else "",
            "where": roam.library.outing().site if roam else "",
            "next": roam.next_walk(now) if roam else None,
            "today": roam._walks_today(now) if roam else 0,
            "avoid": sorted(k for k, how in steered.items() if how == "avoid"),
            "favour": sorted(k for k, how in steered.items() if how == "favour"),
            "hosts": [{"key": key, "name": name} for key, name in names.items()],
            "record": list(reversed(_load(runtime))),
            "search": search.search_api(workspace.home)[0],
        }

    def change(payload: dict) -> dict:
        roam = walker()
        if "search" in payload:
            api, key = payload["search"], payload.get("key")
            secretstore.put(workspace.home, search.SEARCH_KEY, json.dumps({"api": api, "key": key}) if api else "")
            return view()
        if "site" in payload:
            key, how = site_key(str(payload.get("site") or "")), payload.get("steer")
            if not key:
                raise ValueError("walks_site")
            if how not in ("avoid", "favour", ""):
                raise ValueError("walks_invalid")
            sites.steer(workspace._runtime(), key, how)
            return view()
        on, walks = payload.get("on"), payload.get("walks")
        if on is not None and not isinstance(on, bool) or walks is not None and (
                isinstance(walks, bool) or not isinstance(walks, int) or not 0 <= walks <= WALKS_MAX):
            raise ValueError("walks_invalid")
        changes = {**({"ROAM_ENABLED": "true" if on else "false"} if on is not None else {}),
                   **({"ROAM_WALKS_A_DAY": str(walks)} if walks is not None else {})}
        with workspace._lock:
            settingsfile.write(workspace.home, changes)
        if roam:
            # The walk loop reads its settings as it goes: the change holds from its next look.
            roam.settings.roam_enabled = on if on is not None else roam.settings.roam_enabled
            roam.settings.roam_walks_a_day = walks if walks is not None else roam.settings.roam_walks_a_day
        return view()

    @app.get("/api/walks")
    async def walks_view():
        return await asyncio.to_thread(view)

    @app.post("/api/walks")
    async def walks_change(request: Request):
        payload = await body(request)
        if payload.get("search") is not None:
            api, key = payload["search"], payload.get("key")
            if not isinstance(api, str) or api and (api not in search.SEARCH_APIS or not isinstance(key, str)
                                                    or not key.strip() or len(key) > KEY_MAX):
                raise ValueError("walks_invalid")
            payload["key"] = key.strip() if api else ""
            # A key the service turns down is not kept: the person sees it at once, not at the next search.
            if api and not await search.key_taken(api, payload["key"]):
                raise ValueError("walks_search_key")
        shown = await asyncio.to_thread(change, payload)
        if walker():
            walker().changed()
        return shown

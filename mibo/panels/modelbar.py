"""The composer's model control and Settings' model page: what the saved model can
do (abilities.py), the thinking level, a quick switch to another model of the same
service, and what the calls spent with the account's balance (spend.py)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import Request

from .. import abilities, drafting, spend
from .. import setup_wizard as sw
from ..endpoints import chat_completions_url, model_problem
from . import models as listing

logger = logging.getLogger("agent")

ERRORS: frozenset[str] = frozenset({"invalid_effort", "invalid_limit", "invalid_told"})
TOLD = ("near", "reached", "confirmed")
# The checks running now, by (url, model); kept so a task is not collected mid-run.
_checks: dict[tuple[str, str], asyncio.Task] = {}


def _runtime(workspace) -> Path:
    return drafting.usage_path(workspace.home, workspace.config()).parent


def _short(model: str) -> str:
    """The name as the composer shows it: without the vendor's prefix."""
    name = model.rsplit("/", 1)[-1].removesuffix(":free")
    return model if name in ("", "free", "auto") else name


def _view(rec: dict | None) -> dict | None:
    if rec is None:
        return None
    return {**{n: rec.get(n) or "" for n in abilities.NAMES}, "at": rec.get("at"), "error": rec.get("error") or "",
            "efforts": abilities.efforts(rec)}


def _start_check(workspace, url: str, model: str) -> asyncio.Task:
    slot = (url, model)
    task = _checks.get(slot)
    if task is None or task.done():
        task = asyncio.create_task(abilities.check(workspace.home, workspace.config()))
        task.add_done_callback(lambda t: t.cancelled() or t.exception() and logger.warning(
            "[Agent] ability check failed: %s", type(t.exception()).__name__))
        _checks[slot] = task
    return task


def check_at_start(workspace) -> None:
    """At the app's start: the saved model measured once when nothing is kept for it (`state` starts it)."""
    state(workspace)


async def measured(url: str, model: str, within: float) -> None:
    """Back when the model's check under way has ended, or after `within` seconds; at once when none runs."""
    task = _checks.get((url, model))
    for _ in range(int(within / 0.25)):
        if task is None or task.done():
            return
        await asyncio.sleep(0.25)


def state(workspace) -> dict:
    values = workspace.config()
    base, model = values.get("LLM_BASE_URL", "").strip(), values.get("LLM_MODEL", "").strip()
    if not (base and model and values.get("LLM_API_KEY")):
        return {"configured": False}
    url = chat_completions_url(base)
    runtime = _runtime(workspace)
    rec = abilities.record(runtime, url, model)
    task = _checks.get((url, model))
    if rec is None and task is None:
        # Saved or switched elsewhere, or a first start with nothing kept for it: measured once.
        task = _start_check(workspace, url, model)
    known = sw.provider_for_base(base)
    return {"configured": True, "model": model, "short": _short(model), "baseUrl": base,
            "provider": known.key if known else "other", "abilities": _view(rec),
            "checking": bool(task and not task.done()), "effort": abilities.effort(runtime),
            "console": spend.console_of(base)}


async def model_list(workspace) -> dict:
    """The service's models: recently used first, then its /models listing, else the preset's own."""
    values = workspace.config()
    base, model = values.get("LLM_BASE_URL", "").strip(), values.get("LLM_MODEL", "").strip()
    recent = [m for m in abilities.recent(_runtime(workspace), base) if m != model]
    found = (await listing.listing(workspace)).get("models")
    known = sw.provider_for_base(base)
    names = [m["id"] for m in found] if found else ([known.model] if known and known.model else [])
    free = {m["id"] for m in found or [] if m.get("free")}
    return {"current": model, "recent": recent[:5], "models": [m for m in names if m != model],
            "free": sorted(free), "listed": bool(found)}


def switch(workspace, model: str) -> None:
    """Save another model of the saved service, as Settings saves one: tried first,
    then written to .env and the character service restarted to read it."""
    from ..welcome import _expands
    model = str(model or "").strip()
    if not model or len(model) > 256 or _expands(model):
        raise ValueError("model_required")
    with workspace._lock:
        values = workspace.config()
        base, key = values.get("LLM_BASE_URL", "").strip(), values.get("LLM_API_KEY", "")
        if not base or not key:
            raise ValueError("key_required")
        try:
            probe = sw.probe_key(base, key, model, timeout=20)
        except Exception:
            raise ValueError("model_unreachable") from None
        if not probe.ok:
            # Never return an upstream response body: it may echo a key.
            problem = model_problem(probe.status, probe.detail) if probe.status else ""
            raise ValueError({"key": "key_rejected", "limited": "model_limited", "no_balance": "model_no_balance",
                              "not_found": "model_not_found"}.get(problem, "model_unreachable"))
        sw.write_env(workspace.env, {"LLM_MODEL": model})
        abilities.add_recent(_runtime(workspace), base, model)
        workspace.character.reload()


def mount(app, workspace, body) -> None:
    @app.get("/api/modelbar")
    async def read():
        return state(workspace)

    @app.get("/api/modelbar/models")
    async def models():
        return await model_list(workspace)

    @app.post("/api/modelbar/effort")
    async def effort(request: Request):
        payload = await body(request)
        await asyncio.to_thread(abilities.set_effort, _runtime(workspace), str(payload.get("level") or ""))
        return state(workspace)

    @app.post("/api/modelbar/model")
    async def choose(request: Request):
        payload = await body(request)
        await asyncio.to_thread(switch, workspace, payload.get("model"))
        return state(workspace)

    @app.post("/api/modelbar/check")
    async def check(request: Request):
        await body(request)
        values = workspace.config()
        if not values.get("LLM_API_KEY"):
            raise ValueError("key_required")
        url, model = chat_completions_url(values.get("LLM_BASE_URL", "")), values.get("LLM_MODEL", "").strip()
        task = _checks.get((url, model))
        if task is not None and task.done():
            _checks.pop((url, model))
        await asyncio.shield(_start_check(workspace, url, model))
        return state(workspace)

    @app.get("/api/usage")
    async def usage(fresh: int = 0):
        return await spend.view(workspace.home, workspace.config(), fresh=bool(fresh))

    @app.post("/api/usage/limit")
    async def limit(request: Request):
        payload = await body(request)
        await asyncio.to_thread(spend.set_limit, _runtime(workspace), payload.get("limit"))
        return await spend.view(workspace.home, workspace.config())

    @app.post("/api/usage/told")
    async def told(request: Request):
        payload = await body(request)
        what = str(payload.get("what") or "")
        if what not in TOLD:
            raise ValueError("invalid_told")
        await asyncio.to_thread(spend.tell, _runtime(workspace), what)
        return {"ok": True}

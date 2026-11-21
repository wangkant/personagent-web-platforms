"""The tuning panel: the backup model and the models for other uses, as
Settings › Model shows them under 更多."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Request

from .. import abilities, context, drafting, settingsfile
from .. import setup_wizard as sw
from ..endpoints import chat_completions_url


@dataclass(frozen=True)
class Field:
    key: str
    kind: str  # model, url, secret, window


FIELDS: tuple[Field, ...] = (
    Field("LLM_FALLBACK_MODEL", "model"),
    Field("LLM_FALLBACK_BASE_URL", "url"),
    Field("LLM_FALLBACK_API_KEY", "secret"),
    Field("LLM_JUDGE_MODEL", "model"),
    Field("LLM_DM_MODEL", "model"),
    Field("CONTEXT_WINDOW", "window"),
)
BY_KEY = {f.key: f for f in FIELDS}
SECRETS = frozenset(f.key for f in FIELDS if f.kind == "secret")

ERRORS: frozenset[str] = frozenset(
    {f"tuning_invalid:{f.key}" for f in FIELDS}
    | {"tuning_unknown", "tuning_key_needed", "tuning_https", "tuning_fallback_rejected",
       "tuning_fallback_limited", "tuning_fallback_unreachable", "tuning_not_configured"})


def _bad(key: str) -> ValueError:
    return ValueError(f"tuning_invalid:{key}")


def _url(key: str, value) -> str:
    base = str(value or "").strip().rstrip("/")
    if not base:
        return ""
    parsed = urlsplit(base)
    if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username
            or parsed.password or len(base) > 512
            or any(c.isspace() for c in base)):
        raise _bad(key)
    if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("tuning_https")
    return base


def clean(field: Field, value) -> str:
    """`value` as it would be saved, or a ValueError naming the field."""
    if field.kind == "url":
        return _url(field.key, value)
    if field.kind == "window":
        text = str(value if value is not None else "").strip()
        if text and not (text.isdigit() and 4096 <= int(text) <= 100_000_000):
            raise _bad(field.key)
        return str(int(text)) if text else ""
    text = str(value if value is not None else "").strip()
    if (len(text) > (4096 if field.kind == "secret" else 256)
            or any(c.isspace() or not c.isprintable() for c in text)):
        raise _bad(field.key)
    return text


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower() if url else ""


class Tuning:
    def __init__(self, workspace):
        self.ws = workspace

    def view(self) -> dict:
        values = self.ws.config()
        return {"values": {f.key: values.get(f.key, "") for f in FIELDS if f.kind != "secret"},
                "secrets": {k: sw.mask_secret(values[k]) for k in SECRETS if values.get(k)},
                "llmBaseUrl": values.get("LLM_BASE_URL", ""), "contextWindow": self._window(values)}

    def _window(self, values: dict) -> dict:
        """The reply model's window when CONTEXT_WINDOW is left blank, and where it comes from (context.window)."""
        url = chat_completions_url(values.get("LLM_BASE_URL", "")) if values.get("LLM_BASE_URL") else ""
        model = values.get("LLM_DM_MODEL") or values.get("LLM_MODEL", "")
        runtime = drafting.usage_path(self.ws.home, values).parent
        tokens, source = context.window(abilities.record(runtime, url, model))
        return {"tokens": tokens, "from": source}

    def _fallback(self, old: dict, changes: dict, typed_key: str) -> None:
        """Keep the backup model's key with its address and check the model answers."""
        def now(key):
            return changes.get(key, old.get(key, ""))

        base, model = now("LLM_FALLBACK_BASE_URL"), now("LLM_FALLBACK_MODEL")
        base_moved = "LLM_FALLBACK_BASE_URL" in changes
        if typed_key:
            changes["LLM_FALLBACK_API_KEY"] = typed_key
        elif base_moved:
            # A key is never forwarded to an address it was not saved for.
            changes["LLM_FALLBACK_API_KEY"] = ""
        key = now("LLM_FALLBACK_API_KEY")
        primary = old.get("LLM_BASE_URL", "")
        if base and _host(base) != _host(primary) and not key:
            raise ValueError("tuning_key_needed")
        touched = {"LLM_FALLBACK_MODEL", "LLM_FALLBACK_BASE_URL", "LLM_FALLBACK_API_KEY"} & set(changes)
        if not model or not touched:
            return
        try:
            probe = sw.probe_key(base or primary, key or old.get("LLM_API_KEY", ""), model, timeout=20)
        except Exception:
            raise ValueError("tuning_fallback_unreachable") from None
        if not probe.ok:
            # Never return an upstream body: it may echo a key.
            raise ValueError("tuning_fallback_rejected" if probe.status in (401, 403)
                             else "tuning_fallback_limited" if probe.status == 429
                             else "tuning_fallback_unreachable")

    def save(self, payload: dict) -> dict:
        values = payload.get("values") or {}
        if not isinstance(values, dict) or any(k not in BY_KEY for k in values):
            raise ValueError("tuning_unknown")
        with self.ws._lock:
            old = self.ws.config()
            if not old.get("LLM_API_KEY"):
                raise ValueError("tuning_not_configured")
            changes: dict[str, str] = {}
            typed_key = ""
            for key, raw in values.items():
                field = BY_KEY[key]
                new = clean(field, raw)
                if field.kind == "secret":
                    typed_key = new
                elif old.get(key, "").strip() != new:
                    changes[key] = new
            if typed_key == old.get("LLM_FALLBACK_API_KEY", ""):
                typed_key = ""
            if typed_key or {"LLM_FALLBACK_MODEL", "LLM_FALLBACK_BASE_URL"} & set(changes):
                self._fallback(old, changes, typed_key)
            if not changes:
                return {**self.view(), "changed": False}
            settingsfile.write(self.ws.home, changes)
            self.ws.character.reload()
            return {**self.view(), "changed": True}


def mount(app, workspace, body) -> None:
    tuning = Tuning(workspace)

    @app.get("/api/tuning")
    async def tuning_view():
        return await asyncio.to_thread(tuning.view)

    @app.post("/api/tuning")
    async def tuning_save(request: Request):
        return await asyncio.to_thread(tuning.save, await body(request))

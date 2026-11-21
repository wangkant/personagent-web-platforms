"""What the saved model can do, measured rather than looked up by name.

A handful of tiny calls go through the app's own call path (llm.ModelCalls), so a
refusal is learned the usual way: one tool round with one small tool, a 16 px
picture, JSON mode with thinking off, and a short question with thinking on.
A model that takes pictures also points at one drawn picture (see/profile.py's
self-test), kept as `pointing`: how it works a window only a picture can work.
The answers and the refusals those calls taught (llm's quirks) are kept in
<runtime>/model-abilities.json, keyed by the chat URL and the model, so the
service process and the page read the same answers after a restart. Ordinary calls add what they measure to the
record (note_learned): the ratio of reported to estimated tokens and the window an overflow error stated (context.py).

    record(runtime, url, model)       the measured abilities, or None before a check
    can(runtime, url, model, name)    "yes", "no", "unstable", or "" when not measured;
                                      name is one of NAMES (tools, pictures, json, thinking)
    efforts(rec)                      the thinking levels the composer offers ([] for none)
    effort(runtime) / set_effort()    the level the person chose for replies (EFFORTS)
    reply_effort(runtime, url, model) (level, parameter) for a reply that should think, else None
    thinking_on(payload, url, level, parameter)
                                      spell thinking on for that parameter; returns the token budget
    await check(home, values)         run the calls, keep the result, return it

`url` is always the chat-completions URL (endpoints.chat_completions_url), as llm keys its quirks.
A missing ability means another route (the call path already has one for each refusal)
or one plain sentence on the Settings page; never a hang or an empty reply.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import struct
import time
import zlib
from pathlib import Path

from .endpoints import chat_completions_url
from .storage import FileLock, atomic_write_text

logger = logging.getLogger("agent")

FILE = "model-abilities.json"
NAMES = ("tools", "pictures", "json", "thinking")
# auto: standard wherever the model was measured to think; the others are the person's choice.
EFFORTS = ("auto", "off", "standard", "deep")
# Each level as an effort word and a thinking budget in tokens; "probe" is the check's own.
LEVELS = {"standard": ("medium", 4096), "deep": ("high", 12288), "probe": ("low", 1024)}
# The spellings a host may take for thinking on, as the check tries them.
PARAMS = ("reasoning_effort", "reasoning", "thinking", "enable_thinking")
RECENT_MAX = 8
# A problem a call reported counts for the reply that was waiting for this long.
PROBLEM_FRESH_S = 900

_TOOL = {"type": "function", "function": {
    "name": "get_time", "description": "Returns the current local time.",
    "parameters": {"type": "object", "properties": {}, "required": []}}}

_cache: dict[Path, tuple[float, dict]] = {}
# Per process: which runtime file's quirks were last folded into which caller.
_seeded: dict[int, float] = {}


def key_of(url: str, model: str) -> str:
    return f"{url} {model}"


def _path(runtime) -> Path:
    return Path(runtime) / FILE


def _read(runtime) -> dict:
    path = _path(runtime)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return {}
    kept = _cache.get(path)
    if kept and kept[0] == stamp:
        return kept[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    _cache[path] = (stamp, data)
    return data


def _change(runtime, edit) -> dict:
    """Read, edit and write the file under its lock; both processes write it."""
    path = _path(runtime)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path.with_suffix(".lock"), timeout=10):
        _cache.pop(path, None)
        data = dict(_read(runtime))
        edit(data)
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=1), fsync=False)
        _cache.pop(path, None)
    return data


def runtime_of(caller) -> Path | None:
    """The runtime folder of a model caller: beside the service's memory file,
    or the page's usage file."""
    memory = getattr(caller, "memory_file", None)
    if memory:
        return Path(memory).parent
    usage = getattr(caller, "_usage_path", None)
    return Path(usage).parent if usage else None


# ------------------------------------------------------------------ reading --

def record(runtime, url: str, model: str) -> dict | None:
    if runtime is None:
        return None
    rec = (_read(runtime).get("models") or {}).get(key_of(url, model))
    return rec if isinstance(rec, dict) else None


def can(runtime, url: str, model: str, name: str) -> str:
    rec = record(runtime, url, model)
    return str((rec or {}).get(name) or "")


def efforts(rec: dict | None) -> list[str]:
    """The levels the composer offers: none for a model that never reasons or
    takes no setting; no "off" for one that always reasons."""
    thinking = (rec or {}).get("thinking_detail") or {}
    if not thinking.get("reasons") or not thinking.get("param"):
        return []
    return [e for e in EFFORTS if e != "off" or thinking.get("off")]


def effort(runtime) -> str:
    value = _read(runtime).get("effort") if runtime is not None else ""
    return value if value in EFFORTS else "auto"


def set_effort(runtime, level: str) -> None:
    if level not in EFFORTS:
        raise ValueError("invalid_effort")
    _change(runtime, lambda data: data.__setitem__("effort", level))


def reply_effort(runtime, url: str, model: str):
    """(level, parameter) when replies think: the model was measured to think,
    at the person's level ("auto" is standard); None for thinking off."""
    if runtime is None:
        return None
    level = {"auto": "standard"}.get(effort(runtime), effort(runtime))
    rec = record(runtime, url, model)
    if level == "off" or (rec or {}).get("thinking") != "yes":
        return None
    param = ((rec or {}).get("thinking_detail") or {}).get("param") or ""
    return (level, param) if param in PARAMS else None


def recent(runtime, base: str) -> list[str]:
    rows = (_read(runtime).get("recent") or {}).get(base.rstrip("/")) or []
    return [str(m) for m in rows if isinstance(m, str)]


def add_recent(runtime, base: str, model: str) -> None:
    def edit(data):
        rows = dict(data.get("recent") or {})
        mine = [m for m in rows.get(base.rstrip("/"), []) if m != model]
        rows[base.rstrip("/")] = [model, *mine][:RECENT_MAX]
        data["recent"] = rows
    _change(runtime, edit)


# --------------------------------------------------- what calls write back --

def seed(caller, quirks: dict) -> None:
    """Fold the refusals kept on disk into a caller's quirks, once per change of
    the file; what the process learned itself stays."""
    runtime = runtime_of(caller)
    if runtime is None or caller.__dict__.get("_abilities_fresh"):
        return
    try:
        stamp = _path(runtime).stat().st_mtime_ns
    except OSError:
        return
    if _seeded.get(id(quirks)) == stamp:
        return
    _seeded[id(quirks)] = stamp
    for rec in (_read(runtime).get("models") or {}).values():
        if not isinstance(rec, dict):
            continue
        for field, value in rec.get("quirks") or []:
            quirks.setdefault((field, rec.get("url"), rec.get("model")), value)


def note_quirk(caller, url: str, model: str, field: str, value) -> None:
    """Keep a refusal learned during ordinary calls with the model's record."""
    runtime = runtime_of(caller)
    if runtime is None or caller.__dict__.get("_abilities_fresh") or not record(runtime, url, model):
        return

    def edit(data):
        rec = (data.get("models") or {}).get(key_of(url, model))
        if isinstance(rec, dict) and [field, value] not in rec.get("quirks", []):
            rec["quirks"] = [*rec.get("quirks", []), [field, value]]
    try:
        _change(runtime, edit)
    except OSError as e:
        logger.debug("[Agent] refusal not kept: %s", e)


def note_learned(caller, url: str, model: str, **found) -> None:
    """Keep what ordinary calls measured (context.py: `token_ratio`, `context_error`) with the model's record;
    a check measures afresh and drops it."""
    runtime = runtime_of(caller)
    if runtime is None or caller.__dict__.get("_abilities_fresh") or not record(runtime, url, model):
        return

    def edit(data):
        rec = (data.get("models") or {}).get(key_of(url, model))
        if isinstance(rec, dict):
            rec.update(found)
    try:
        _change(runtime, edit)
    except OSError as e:
        logger.debug("[Agent] measure not kept: %s", e)


def note_problem(caller, kind: str, url: str) -> None:
    """The class of a failed call (endpoints.PROBLEMS), for the page to name in the chat."""
    runtime = runtime_of(caller)
    if runtime is None:
        return
    try:
        _change(runtime, lambda data: data.__setitem__(
            "problem", {"kind": kind, "url": url, "at": time.time()}))
    except OSError as e:
        logger.debug("[Agent] problem not kept: %s", e)


def problem_since(runtime, since: float) -> str:
    seen = _read(runtime).get("problem") or {}
    at = seen.get("at") if isinstance(seen.get("at"), (int, float)) else 0
    return str(seen.get("kind") or "") if at >= since and time.time() - at < PROBLEM_FRESH_S else ""


def reasoning_of(message: dict) -> str:
    text = message.get("reasoning_content") or message.get("reasoning") or ""
    if not text and isinstance(message.get("content"), str):
        found = re.match(r"^\s*<think>(.*?)</think>", message["content"], re.S)
        text = found.group(1) if found else ""
    return str(text).strip()


def note_reasoning(text: str, seconds: float) -> None:
    """The reply's reasoning, kept with the turn for the page's activity line
    (dm.py puts it on the reply as `thinking`)."""
    from .turns import current_turn
    turn = current_turn.get()
    if turn is None or not text:
        return
    held = turn.thinking or {"s": 0, "text": ""}
    turn.thinking = {"s": round(held["s"] + seconds), "text": (held["text"] + "\n\n" + text).strip()[:8000]}


def thinking_on(payload: dict, url: str, level: str, param: str) -> int:
    """Ask for thinking at `level` in the spelling `param` the model took; any
    off switch goes. Returns the thinking budget, which the caller adds to max_tokens."""
    word, budget = LEVELS[level]
    for name in (*PARAMS, "thinking_budget"):
        payload.pop(name, None)
    if param == "reasoning_effort":
        payload["reasoning_effort"] = word
    elif param == "reasoning":
        payload["reasoning"] = {"effort": word}
    elif param == "enable_thinking":
        payload["enable_thinking"] = True
        payload["thinking_budget"] = budget
    elif param == "thinking":
        payload["thinking"] = {"type": "enabled"}
        # Claude's compatible endpoint wants a budget below max_tokens.
        if "anthropic.com" in url:
            payload["thinking"]["budget_tokens"] = budget
            payload.pop("temperature", None)
    return budget


# -------------------------------------------------------------- the check --

def _png() -> str:
    """A 16 px red square, as a data URL."""
    rows = b"".join(b"\x00" + b"\xdc\x14\x14" * 16 for _ in range(16))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(raw).decode()


_RED = re.compile(r"\bred\b|\brot\b|rouge|roj[oa]|rosso|vermelh|красн|红|紅|赤|빨|scarlet|crimson", re.I)
_BLIND = re.compile(r"can(?:no|')t see|cannot view|unable to (?:see|view)|no image|not able to see"
                    r"|无法(?:查看|看到|识别)|看不到|没有(?:看到)?图片", re.I)


def _reasoned(body: dict) -> bool:
    choice = ((body.get("choices") or [{}])[0]) or {}
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
    return bool(reasoning_of(choice.get("message") or {}) or (details.get("reasoning_tokens") or 0) > 0)


def _answer(body: dict) -> str:
    from .llm import answer_text
    return answer_text(((body.get("choices") or [{}])[0] or {}).get("message") or {})


async def _post(caller, url: str, key: str, payload: dict) -> dict | None:
    """One call through the app's path; the body of a 200, else None."""
    import openai
    try:
        body = await caller._post_chat(url, key, payload, timeout=45)
    except openai.APIStatusError as e:
        caller.__dict__.setdefault("_check_status", e.status_code)
        caller.__dict__.setdefault("_check_text", e.response.text[:400])
        return None
    except openai.APIError as e:
        logger.info("[Agent] ability check call failed: %s", type(e).__name__)
        return None
    return body if body.get("choices") else None


def _params_for(caller, url: str, model: str) -> tuple[str, ...]:
    """The spellings of thinking on to try, the host's own first."""
    from . import llm
    host = llm._host(url)
    if llm._on_host(host, llm._ENABLE_THINKING_HOSTS):
        return ("enable_thinking",)
    if "openrouter.ai" in url:
        return ("reasoning",)
    if llm._on_host(host, llm._THINKING_HOSTS) or caller._thinking_verdict(url, model):
        return ("thinking", "reasoning_effort")
    return ("reasoning_effort", "thinking")


# The fields a host's model list gives the context window in: OpenRouter, vLLM, Groq, others.
_CONTEXT_FIELDS = ("context_length", "max_model_len", "context_window", "max_context_length")


async def _context(url: str, key: str, model: str) -> int:
    """The model's context window in tokens as the host's own model list states it; 0 when it does not."""
    import openai

    from . import transport
    try:
        rows = [r.to_dict() for r in (await transport.client(url, key).models.list(timeout=20)).data]
    except openai.APIError:
        return 0
    row = next((r for r in rows if r.get("id") == model), {})
    found = [row.get(k) for k in _CONTEXT_FIELDS] + [(row.get("top_provider") or {}).get("context_length")]
    return next((int(v) for v in found if isinstance(v, int) and v > 0), 0)


async def _measure(caller, url: str, key: str, model: str) -> dict:
    quirks = caller._api_quirks
    out: dict = {}

    async def tool_round(said: str, **how) -> dict | None:
        """One round with one small tool, as the reply's rounds call them; None when the call failed."""
        try:
            return await caller._tool_round("", [{"role": "user", "content": said}], model, [_TOOL], timeout=45, **how)
        except Exception as e:
            logger.info("[Agent] ability check call failed: %s", type(e).__name__)
            return None

    message = await tool_round("Call get_time now. Do not answer in words.")
    called = lambda m: any((c.get("function") or {}).get("name") == "get_time"  # noqa: E731
                           for c in (m or {}).get("tool_calls") or [] if isinstance(c, dict))
    if ("tools", url, model) in quirks:
        out["tools"] = "no"
    elif called(message):
        out["tools"] = "yes"
    elif message is not None:
        again = await tool_round("Call get_time.", choice="required")
        out["tools"] = "unstable" if called(again) else ("no" if ("tools", url, model) in quirks else "unstable")
    else:
        out["tools"] = ""

    # Pictures: a 16 px red square.
    payload = {"model": model, "max_tokens": 60, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "What colour is this square? Answer with one word."},
        {"type": "image_url", "image_url": {"url": _png()}}]}]}
    caller._thinking_off(payload, url)
    body = await _post(caller, url, key, payload)
    if ("images", url, model) in quirks:
        out["pictures"] = "no"
    elif body is None:
        out["pictures"] = ""
    else:
        said = _answer(body)
        if _RED.search(said):
            out["pictures"] = "yes"
        elif _BLIND.search(said):
            # Took the picture and could not see it: the words alone from now on.
            caller._remember_refusal(url, model, "images")
            out["pictures"] = "no"
        elif not said:
            # No words at all (a model still thinking at 60 tokens says nothing): unknown, not blind.
            out["pictures"] = ""
        else:
            out["pictures"] = "unstable"
    if out["pictures"] in ("yes", "unstable"):
        from .see import profile

        async def ask(system: str, messages: list[dict]) -> str | None:
            payload = {"model": model, "max_tokens": 80,
                       "messages": [{"role": "system", "content": system}, *messages]}
            caller._thinking_off(payload, url)
            body = await _post(caller, url, key, payload)
            return None if body is None else _answer(body)
        out["pointing"] = await profile.selftest(ask, model)

    # JSON mode with thinking off, at about 60 tokens; its answer also shows whether thinking went off.
    payload = {"model": model, "max_tokens": 60, "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": "Answer with one JSON object and nothing else."},
                            {"role": "user", "content": 'Return {"ok": true} as JSON.'}]}
    caller._thinking_off(payload, url)
    body = await _post(caller, url, key, payload)
    quiet = None
    if body is not None:
        quiet = not _reasoned(body)
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", _answer(body).strip())
        try:
            parsed = isinstance(json.loads(text), dict)
        except ValueError:
            parsed = False
    if ("response_format", url, model) in quirks:
        out["json"] = "no"
    elif body is None:
        out["json"] = ""
    else:
        out["json"] = "yes" if parsed else "unstable"

    # Thinking on: a short question with each spelling the host may take, the first taken kept.
    reasons, param = quiet is False, ""
    for name in _params_for(caller, url, model):
        payload = {"model": model, "max_tokens": 300,
                   "messages": [{"role": "user", "content": "Is 391 a prime number? Answer yes or no."}]}
        payload["max_tokens"] += thinking_on(payload, url, "probe", name)
        body = await _post(caller, url, key, payload)
        if body is None:
            continue
        # Taken when the field survived the call (a refusal drops it) and the answer shows reasoning.
        if _reasoned(body):
            reasons = True
            if name in payload:
                param = name
                break
    out["thinking_detail"] = {"reasons": reasons, "off": quiet is not False, "param": param}
    out["thinking"] = "yes" if reasons else ("no" if quiet is not None else "")
    # The window as the host lists it, one source of the budget's (context.window).
    out["context"] = await _context(url, key, model)
    return out


async def check(home: Path, values: dict) -> dict:
    """Measure the saved model through the app's own call path and keep the
    result with the refusals it taught. Costs a few hundred tokens."""
    from . import drafting
    from .llm import purpose
    from .usage import UsageBook

    class Checker(drafting._Client):
        def _count_usage(self, model: str, body: dict) -> None:
            try:
                book = UsageBook(self._usage_path)
                book.add(model, body.get("usage") if isinstance(body, dict) else None, time.time(), "check")
                book.flush(time.time())
            except Exception:
                pass

    path = drafting.usage_path(Path(home), values)
    runtime = path.parent
    caller = Checker(values, path)
    caller.llm_timeout_s = 45.0
    # Measured afresh: nothing learned before is assumed.
    caller.__dict__["_abilities_fresh"] = True
    caller.__dict__["_api_quirks_map"] = {}
    url = chat_completions_url(caller.base_url)
    model = caller.model
    add_recent(runtime, caller.base_url, model)
    with purpose("check"):
        found = await _measure(caller, url, caller.api_key, model)
    learned = [[f, v] for (f, u, m), v in caller._api_quirks.items() if u == url and m == model]
    rec = {"url": url, "model": model, "at": time.time(), **found, "quirks": learned}
    if not any(found.get(n) for n in NAMES):
        # Nothing answered: kept as unmeasured, with why, so the page can say so and offer the check again.
        from .endpoints import model_problem
        rec["error"] = model_problem(caller.__dict__.get("_check_status"), caller.__dict__.get("_check_text", "")) or "unreachable"

    def edit(data):
        models = dict(data.get("models") or {})
        models[key_of(url, model)] = rec
        data["models"] = models
    _change(runtime, edit)
    logger.info("[Agent] model %s measured: %s", model,
                ", ".join(f"{n}={found.get(n) or '?'}" for n in NAMES))
    return rec

"""What the model calls spent, the account's balance, and the person's monthly limit.

    month_view(runtime, prices, now)   tokens and cost today and this month, split by GROUPS
    await prices_for(base, key)        {model: (input, output, cached input)} in USD per token,
                                       from the service's /models listing where it carries pricing
    await balance(values)              the balance or remaining quota from the service's own
                                       documented endpoint (BALANCE), or None where it has none
    limit_state(runtime, view)         the limit, how much of it is used, and whether the
                                       background work that spends unasked is paused
    paused_for(caller, purpose)        True for that background work while the limit is reached

The cost is the one the service reported per call (OpenRouter's usage.cost) or
tokens times the service's own listed price; with neither, tokens only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import openai

from . import transport, usage
from .endpoints import chat_completions_url
from .storage import FileLock, atomic_write_text

logger = logging.getLogger("agent")

FILE = "spend.json"
# The groups the page shows, and the call purposes (usage.PURPOSES) in each.
GROUPS = (("chat", ("reply", "gate", "search", "tools", "proactive")),
          ("roam", ("roam",)),
          ("memory", ("memory", "learning")),
          ("recommend", ("recommend",)),
          ("operate", ("operate",)),
          ("other", ("check", "persona", "other")))
# Spending without being asked: paused at the limit.
UNASKED = ("memory", "learning", "roam", "proactive")
NEAR = 0.8
BALANCE_EVERY_S = 180
PRICES_EVERY_S = 3600
STATE_EVERY_S = 30
TIMEOUT_S = 8

_balances: dict[tuple, tuple[float, dict | None]] = {}
_prices: dict[tuple, tuple[float, dict]] = {}
_states: dict[Path, tuple[float, bool]] = {}


def _group(label: str) -> str:
    label = usage.purpose_of(label)
    return next((g for g, labels in GROUPS if label in labels), "other")


def _read(runtime) -> dict:
    try:
        data = json.loads((Path(runtime) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def change(runtime, edit) -> dict:
    path = Path(runtime) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path.with_suffix(".lock"), timeout=10):
        data = _read(runtime)
        edit(data)
        atomic_write_text(path, json.dumps(data, ensure_ascii=False), fsync=False)
    _states.pop(Path(runtime), None)
    return data


def month_of(now: float) -> str:
    return time.strftime("%Y-%m", time.localtime(now))


# ------------------------------------------------------------------- usage --

def _row_cost(row: dict, price) -> float | None:
    """A model's day row in USD: as reported for every call, else at the listed price."""
    calls = usage._count(row.get("calls"))
    if calls and usage._count(row.get("costed")) >= calls:
        return float(usage._count(row.get("cost")))
    if price:
        cached = usage._count(row.get("cached_tokens"))
        fresh = max(0, usage._count(row.get("prompt_tokens")) - cached)
        return fresh * price[0] + cached * (price[2] if price[2] is not None else price[0]) \
            + usage._count(row.get("completion_tokens")) * price[1]
    return None


def _add(into: dict, row: dict, price) -> None:
    tokens = usage._count(row.get("prompt_tokens")) + usage._count(row.get("completion_tokens"))
    cost = _row_cost(row, price)
    into["tokens"] += tokens
    if tokens and cost is None:
        into["unpriced"] += tokens
    into["cost"] += cost or 0.0
    # Split by purpose: tokens as kept per purpose, the cost in proportion to them.
    split_in = row.get("in_by") if isinstance(row.get("in_by"), dict) else {}
    split_out = row.get("out_by") if isinstance(row.get("out_by"), dict) else {}
    named = 0
    for label in set(split_in) | set(split_out):
        n = usage._count(split_in.get(label)) + usage._count(split_out.get(label))
        named += n
        _put(into["by"], _group(label), n, (cost or 0.0) * n / tokens if tokens else 0.0)
    if tokens > named:
        # Tokens from before purposes kept tokens: under the purpose most of the calls had.
        calls = row.get("purposes") if isinstance(row.get("purposes"), dict) else {}
        label = max(calls, key=lambda k: usage._count(calls[k])) if calls else "other"
        rest = tokens - named
        _put(into["by"], _group(label), rest, (cost or 0.0) * rest / tokens)


def _put(by: dict, group: str, tokens: float, cost: float) -> None:
    slot = by.setdefault(group, {"tokens": 0, "cost": 0.0})
    slot["tokens"] += tokens
    slot["cost"] += cost


def _empty() -> dict:
    return {"tokens": 0, "cost": 0.0, "unpriced": 0, "by": {}}


def _out(total: dict) -> dict:
    priced = total["unpriced"] == 0
    return {"tokens": int(total["tokens"]), "cost": round(total["cost"], 6) if priced and total["tokens"] else
            (0.0 if not total["tokens"] else None),
            "by": [{"group": g, "tokens": int(total["by"][g]["tokens"]),
                    "cost": round(total["by"][g]["cost"], 6) if priced else None}
                   for g, _ in GROUPS if total["by"].get(g, {}).get("tokens")]}


def month_view(runtime, prices: dict | None = None, now: float | None = None) -> dict:
    """Today's and this month's tokens and cost, each split by group; a cost is None
    when any of its tokens has no known price."""
    now = time.time() if now is None else now
    runtime = Path(runtime)
    days = usage.merged(usage.load(runtime / usage.USAGE_FILE),
                        usage.load(runtime / usage.PAGE_USAGE_FILE))
    prices = prices if prices is not None else {m: tuple(p) for m, p in (_read(runtime).get("prices") or {}).items()}
    today, month = usage.day_of(now), month_of(now)
    totals = {"today": _empty(), "month": _empty()}
    for day, models in days.items():
        if not day.startswith(month):
            continue
        for model, row in models.items():
            price = prices.get(model)
            _add(totals["month"], row, price)
            if day == today:
                _add(totals["today"], row, price)
    return {"today": _out(totals["today"]), "month": _out(totals["month"]), "currency": "USD"}


async def prices_for(base: str, key: str) -> dict:
    """{model: (input, output, cached input or None)} per token, from /models where the
    listing carries prices (OpenRouter's shape); {} where it carries none."""
    slot = (base.rstrip("/"), key[-8:])
    kept = _prices.get(slot)
    if kept and time.monotonic() < kept[0]:
        return kept[1]
    found: dict = {}
    try:
        rows = [r.to_dict() for r in (await transport.client(chat_completions_url(base), key).models.list(
            timeout=TIMEOUT_S * 2)).data]
    except openai.APIError:
        rows = []
    for row in rows:
        pricing = row.get("pricing")
        if not isinstance(pricing, dict):
            continue
        try:
            entry = (float(pricing["prompt"]), float(pricing["completion"]),
                     float(pricing["input_cache_read"]) if pricing.get("input_cache_read") not in (None, "") else None)
        except (KeyError, TypeError, ValueError):
            continue
        if min(entry[0], entry[1]) >= 0:
            found[str(row.get("id"))] = entry
    _prices[slot] = (time.monotonic() + (PRICES_EVERY_S if rows is not None else 60), found)
    return found


def keep_prices(runtime, prices: dict, models: set) -> None:
    """The prices of the models used, for the limit's check in the service process."""
    used = {m: list(p) for m, p in prices.items() if m in models}
    if used and used != (_read(runtime).get("prices") or {}):
        change(runtime, lambda data: data.__setitem__("prices", used))


# ----------------------------------------------------------------- balance --

def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _openrouter(body: dict) -> dict | None:
    data = body.get("data") if isinstance(body.get("data"), dict) else None
    if data is None:
        return None
    free = data.get("free_model_daily_requests") if isinstance(data.get("free_model_daily_requests"), dict) else {}
    left = _num(data.get("limit_remaining"))
    return {"currency": "USD", "remaining": left, "limit": _num(data.get("limit")),
            "usedMonth": _num(data.get("usage_monthly")),
            "freeLeft": free.get("remaining") if isinstance(free.get("remaining"), int) else None,
            "freeLimit": free.get("limit") if isinstance(free.get("limit"), int) else None,
            "kind": "key" if left is not None else "usage"}


def _deepseek(body: dict) -> dict | None:
    infos = [i for i in body.get("balance_infos") or [] if isinstance(i, dict)]
    if not infos:
        return None
    first = next((i for i in infos if _num(i.get("total_balance"))), infos[0])
    return {"currency": str(first.get("currency") or "CNY"), "remaining": _num(first.get("total_balance")),
            "kind": "balance"}


def _siliconflow(body: dict, base: str) -> dict | None:
    data = body.get("data") if isinstance(body.get("data"), dict) else None
    if data is None:
        return None
    return {"currency": "USD" if ".com" in base else "CNY", "remaining": _num(data.get("totalBalance")),
            "kind": "balance"}


def _moonshot(body: dict, base: str) -> dict | None:
    data = body.get("data") if isinstance(body.get("data"), dict) else None
    if data is None:
        return None
    return {"currency": "USD" if "moonshot.ai" in base else "CNY",
            "remaining": _num(data.get("available_balance")), "kind": "balance"}


# Each service's own balance endpoint, as its documentation gives it: (path beside the base, reader).
# OpenRouter GET /api/v1/key; DeepSeek GET /user/balance; SiliconFlow GET /v1/user/info;
# Moonshot GET /v1/users/me/balance (platform.kimi.com and platform.kimi.ai alike).
BALANCE = {
    "openrouter": ("/key", lambda body, base: _openrouter(body)),
    "deepseek": ("/user/balance", lambda body, base: _deepseek(body)),
    "siliconflow": ("/user/info", _siliconflow),
    "moonshot": ("/users/me/balance", _moonshot),
}
# Where the account's spending is shown, for services whose console page for it is known.
CONSOLE = {
    "openrouter": "https://openrouter.ai/settings/credits",
    "deepseek": "https://platform.deepseek.com/usage",
    "openai": "https://platform.openai.com/usage",
}


def service_of(base: str):
    from . import setup_wizard as sw
    known = sw.provider_for_base(base)
    if known is None and "moonshot.ai" in base:
        known = sw.provider_named("moonshot")
    if known is None and "siliconflow.com" in base:
        known = sw.provider_named("siliconflow")
    return known


def console_of(base: str) -> str:
    known = service_of(base)
    if known is None:
        return ""
    url = CONSOLE.get(known.key) or known.key_url
    if known.key == "moonshot" and "moonshot.ai" in base:
        url = "https://platform.kimi.ai/console"
    return url


async def balance(values: dict, *, fresh: bool = False) -> dict | None:
    """The balance from the saved service's own endpoint, at most every BALANCE_EVERY_S;
    None where the service has no such endpoint or it did not answer."""
    base, key = values.get("LLM_BASE_URL", "").strip(), values.get("LLM_API_KEY", "")
    known = service_of(base)
    if not base or not key or known is None or known.key not in BALANCE:
        return None
    slot = (base, key[-8:])
    kept = _balances.get(slot)
    if kept and time.monotonic() - kept[0] < (60 if fresh else BALANCE_EVERY_S):
        return kept[1]
    path, read = BALANCE[known.key]
    # DeepSeek documents the path at its root, beside no /v1.
    root = base.rstrip("/")
    if known.key == "deepseek":
        root = root.removesuffix("/v1")
    found = None
    try:
        body = await transport.client(chat_completions_url(base), key).get(
            root + path, cast_to=object, options={"timeout": TIMEOUT_S})
        found = read(body, base)
    except (openai.APIError, AttributeError) as e:
        logger.info("[Agent] balance not read: %s", type(e).__name__)
    if found is not None:
        found["at"] = time.time()
    _balances[slot] = (time.monotonic(), found)
    return found


# ------------------------------------------------------------------- limit --

def limit_of(runtime) -> dict | None:
    """{"kind": "cost" (USD) or "tokens", "value"}, or None when the person set none."""
    value = _read(runtime).get("limit")
    if not isinstance(value, dict) or value.get("kind") not in ("cost", "tokens"):
        return None
    amount = _num(value.get("value"))
    return {"kind": value["kind"], "value": amount} if amount and amount > 0 else None


def set_limit(runtime, value) -> None:
    if value is None:
        change(runtime, lambda data: data.pop("limit", None))
        return
    if not isinstance(value, dict) or value.get("kind") not in ("cost", "tokens") \
            or not (_num(value.get("value")) or 0) > 0 or _num(value.get("value")) > 1e12:
        raise ValueError("invalid_limit")
    change(runtime, lambda data: data.__setitem__("limit", {"kind": value["kind"], "value": _num(value["value"])}))


def limit_state(runtime, view: dict | None = None, now: float | None = None) -> dict | None:
    """The limit with the share of it used this month: "ok", "near" (from 80 percent) or
    "reached"; and which notices were already shown this month."""
    limit = limit_of(runtime)
    if limit is None:
        return None
    now = time.time() if now is None else now
    view = view or month_view(runtime, now=now)
    used = view["month"]["cost"] if limit["kind"] == "cost" else view["month"]["tokens"]
    if used is None:
        # A money limit while some tokens have no price: the priced part is all that is known.
        used = 0.0
    share = used / limit["value"]
    told = _read(runtime).get("told") or {}
    month = month_of(now)
    return {**limit, "used": used, "share": round(share, 4),
            "state": "reached" if share >= 1 else "near" if share >= NEAR else "ok",
            "told": [k for k, v in told.items() if v == month]}


def tell(runtime, what: str, now: float | None = None) -> None:
    month = month_of(time.time() if now is None else now)

    def edit(data):
        told = dict(data.get("told") or {})
        told[what] = month
        data["told"] = told
    change(runtime, edit)


def paused(runtime) -> bool:
    runtime = Path(runtime)
    kept = _states.get(runtime)
    if kept and time.monotonic() - kept[0] < STATE_EVERY_S:
        return kept[1]
    state = limit_state(runtime)
    value = bool(state and state["state"] == "reached")
    _states[runtime] = (time.monotonic(), value)
    return value


def paused_for(caller, label: str) -> bool:
    """Before a model call: the work that spends unasked waits while the limit is reached.
    The caller answers as it does without a model ("" or None)."""
    from .abilities import runtime_of
    if usage.purpose_of(label) not in UNASKED:
        return False
    runtime = runtime_of(caller)
    if runtime is None or not paused(runtime):
        return False
    if not caller.__dict__.get("_paused_said"):
        caller.__dict__["_paused_said"] = True
        logger.info("[Agent] the monthly limit set in Settings is reached; walks, memory folds and "
                    "remarks of its own wait until next month or a higher limit")
    return True


async def view(home: Path, values: dict, *, fresh: bool = False) -> dict:
    """Everything the usage popover and the Settings section show."""
    from . import drafting
    runtime = drafting.usage_path(Path(home), values).parent
    base, key = values.get("LLM_BASE_URL", "").strip(), values.get("LLM_API_KEY", "")
    known = service_of(base)
    prices: dict = {}
    if base and key and not (known and not known.needs_key):
        prices = await prices_for(base, key)
    days = usage.merged(usage.load(runtime / usage.USAGE_FILE), usage.load(runtime / usage.PAGE_USAGE_FILE))
    used_models = {m for models in days.values() for m in models}
    if prices:
        await asyncio.to_thread(keep_prices, runtime, prices, used_models)
        prices = {m: p for m, p in prices.items() if m in used_models}
    else:
        prices = None
    month = month_view(runtime, prices)
    return {"usage": month, "balance": await balance(values, fresh=fresh),
            "balanceKnown": bool(known and known.key in BALANCE),
            "local": bool(known and not known.needs_key),
            "console": console_of(base), "service": known.key if known else "other",
            "limit": limit_state(runtime, month)}

"""What a model can do, its window and its price, from the bundled snapshot of the models.dev catalogue
(modelcatalog.json, MIT, refreshed per release by tools/modelcatalog.py; never fetched at run time).

    entry(url, model)        the catalogue's line for `model` at the service of `url` (a base or chat-completions
                             URL), found by the service's own address and the model id; None when it has none
    abilities(url, model)    that line as an ability record (abilities.py): the seed a first message works from,
                             which the app's own check then confirms and overrides where it differs
    toggles(line)            whether reasoning has an on/off switch
    lowest_effort(line)      the effort that switches reasoning off or lowest, for a model with efforts and no switch
    fixed_temperature(url, model)
                             True when the model takes no temperature (the service sets its own)
    price(url, model)        (input, output, cached input or None) in USD per token, or None

A line says what the model can do, not how a service spells a request for it: the spelling of thinking stays
measured (abilities.check) and learned from refusals (llm.py)."""
from __future__ import annotations

import functools
import json
from pathlib import Path
from urllib.parse import urlsplit

PATH = Path(__file__).with_name("modelcatalog.json")
# Effort values that turn reasoning off or to its least.
_OFF_EFFORTS = ("none", "minimal", "low")


def _address(url: str) -> tuple[str, str]:
    """(host with any port, base path) of a base or chat-completions URL."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "") + (f":{parts.port}" if parts.port else "")
    return host, parts.path.removesuffix("/chat/completions").rstrip("/")


@functools.cache
def _load(path: Path) -> list[tuple[str, str, dict]]:
    """(host, base path, models) per provider."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [(*_address(provider["api"]), provider["models"]) for provider in data["providers"].values()]


def entry(url: str, model: str) -> dict | None:
    host, path = _address(url)
    if not host or not model:
        return None
    # The provider at this exact address first (a coding plan shares its host with the pay-as-you-go one).
    for _, _, models in sorted((p for p in _load(PATH) if p[0] == host), key=lambda p: p[1] != path):
        line = models.get(model)
        if line is None:
            line = next((v for k, v in models.items() if k.lower() == model.lower()), None)
        if line is not None:
            return line
    return None


def toggles(line: dict) -> bool:
    return bool(line.get("reasoning")) and any(o.get("type") == "toggle" for o in line.get("reasoning_options") or [])


def lowest_effort(line: dict | None) -> str:
    if not line or not line.get("reasoning") or toggles(line):
        return ""
    values = next((o.get("values") or [] for o in line.get("reasoning_options") or [] if o.get("type") == "effort"), [])
    return values[0] if values and values[0] in _OFF_EFFORTS else ""


def abilities(url: str, model: str) -> dict | None:
    line = entry(url, model)
    if line is None:
        return None
    reasons = bool(line.get("reasoning"))
    return {"tools": "yes" if line.get("tool_call") else "no",
            "pictures": "yes" if line.get("images") else "no",
            # The catalogue states structured output; plain JSON mode without it is left to the check.
            "json": "yes" if line.get("structured_output") else "",
            "thinking": "yes" if reasons else "no",
            # The parameter that turns thinking on is the check's to find; until then replies do not think.
            "thinking_detail": {"reasons": reasons, "param": "",
                                "off": not reasons or toggles(line) or lowest_effort(line) in ("none", "minimal")},
            "context_catalogue": int(line.get("context") or 0)}


def fixed_temperature(url: str, model: str) -> bool:
    line = entry(url, model)
    return line is not None and line.get("temperature") is False


def price(url: str, model: str):
    cost = (entry(url, model) or {}).get("cost")
    if not cost or cost[0] is None or cost[1] is None:
        return None
    return cost[0] / 1e6, cost[1] / 1e6, (cost[2] / 1e6 if cost[2] is not None else None)

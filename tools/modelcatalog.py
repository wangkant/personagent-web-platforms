"""Refresh the bundled model catalogue (mibo/modelcatalog.json) from models.dev, by a maintainer before a release.

    python tools/modelcatalog.py                 download https://models.dev/api.json and write the snapshot
    python tools/modelcatalog.py --source FILE   trim an api.json already downloaded

models.dev (github.com/anomalyco/models.dev, MIT, Copyright (c) 2025 models.dev) lists what each model of each
service can do, its limits and its prices in USD per million tokens. The snapshot keeps the services whose address
is one the app's provider list in Settings offers (setup_wizard.PROVIDERS), and of each model only the fields the app
reads; it records the day, the size and the sha256 of the file it was trimmed from. The app never fetches it."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

# This checkout's mibo, not the one the environment has installed.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mibo import setup_wizard  # noqa: E402

SOURCE = "https://models.dev/api.json"
OUT = ROOT / "mibo" / "modelcatalog.json"
# The fields of a model the app reads (mibo/modelcatalog.py).
FIELDS = ("tool_call", "reasoning", "reasoning_options", "temperature", "structured_output")
COSTS = ("input", "output", "cache_read")
# The catalogue leaves out the address of the services its AI SDK packages carry themselves; these are those
# packages' own default base URLs.
SDK_BASES = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com/v1",
             "google": "https://generativelanguage.googleapis.com/v1beta", "xai": "https://api.x.ai/v1",
             "mistral": "https://api.mistral.ai/v1", "groq": "https://api.groq.com/openai/v1"}


def host(url: str) -> str:
    return (urlsplit(url or "").hostname or "").lower()


def trim_model(model: dict) -> dict:
    out = {k: model[k] for k in FIELDS if k in model}
    images = "image" in ((model.get("modalities") or {}).get("input") or [])
    out["images"] = images
    limit = model.get("limit") or {}
    out["context"] = int(limit.get("context") or 0)
    cost = model.get("cost") or {}
    if "input" in cost and "output" in cost:
        out["cost"] = [cost.get(k) for k in COSTS]
    return out


def trim(catalogue: dict) -> dict:
    """The providers the app's list reaches, each with its base URL and its models' kept fields."""
    wanted = {host(p.base_url) for p in setup_wizard.PROVIDERS if p.base_url and p.needs_key}
    providers = {}
    for pid, provider in sorted(catalogue.items()):
        api = provider.get("api") or SDK_BASES.get(pid, "")
        if host(api) not in wanted:
            continue
        providers[pid] = {"api": api, "models": {mid: trim_model(m) for mid, m in sorted(provider["models"].items())}}
    return providers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, help="an api.json already downloaded")
    args = parser.parse_args()
    if args.source:
        raw = args.source.read_bytes()
    else:
        with urllib.request.urlopen(urllib.request.Request(SOURCE, headers={"User-Agent": "mibo-build"}),
                                    timeout=60) as answer:
            raw = answer.read()
    providers = trim(json.loads(raw))
    snapshot = {"source": SOURCE, "licence": "MIT, Copyright (c) 2025 models.dev",
                "fetched": time.strftime("%Y-%m-%d"), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                "providers": providers}
    OUT.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8",
                   newline="\n")
    models = sum(len(p["models"]) for p in providers.values())
    print(f"{OUT.relative_to(ROOT)}: {len(providers)} providers, {models} models, {OUT.stat().st_size} bytes "
          f"from {len(raw)} (sha256 {snapshot['sha256'][:12]})")
    missing = [p.key for p in setup_wizard.PROVIDERS if p.base_url and p.needs_key
               and not any(host(v["api"]) == host(p.base_url) for v in providers.values())]
    if missing:
        print("not in the catalogue (the probe measures their models):", ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

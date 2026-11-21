"""Single anchor for on-disk locations.

ROOT is the deployment folder (home.find_home(): AGENT_HOME, a checkout, or
~/mibo), never the package directory. It holds settings.json, persona.txt and
stickers; mutable state goes under ``runtime_dir()``. Read-only seed data comes from
``seed_file()``: ``<ROOT>/data/...`` when present (a user's override, and how
a checkout reads its tracked data), else the copy shipped in the package.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

from .home import find_home, resource

ROOT = find_home()


def seed_file(*parts: str) -> Path:
    """A read-only file under data/: the home's own copy first, else the shipped one."""
    own = ROOT.joinpath("data", *parts)
    if own.exists():
        return own
    return resource("data", *parts)


def resolve_seed_lang_file(stem: str, ext: str, lang: str, root: Path | None = None) -> Path:
    """Resolve a read-only seed file, preferring the language suffix; `root`
    stands for ROOT when another process's home is asked about."""
    home = ROOT if root is None else Path(root)
    names = (f"{stem}.{lang}.{ext}", f"{stem}.{ext}")
    for base in (home / "data", resource("data")):
        for name in names:
            if (base / name).is_file():
                return base / name
    return home / "data" / names[-1]


def runtime_dir() -> Path:
    """Return the ignored directory used for learned runtime state."""
    return runtime_dir_for(ROOT, os.getenv("AGENT_RUNTIME_DIR", ""))


def runtime_dir_for(home: Path, configured: str = "") -> Path:
    """`runtime_dir` of a home whose AGENT_RUNTIME_DIR is `configured`."""
    configured = str(configured or "").strip()
    root = Path(home).resolve()
    path = Path(configured).expanduser() if configured else Path("runtime")
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"AGENT_RUNTIME_DIR must stay under AGENT_HOME ({root}): "
            f"{resolved}") from exc
    return resolved


def resolve_runtime_state_file(value: str | Path) -> Path:
    """Resolve a relative state path under the runtime dir, copying a legacy
    root-level file once. An absolute path is used as given."""
    path = Path(value)
    if path.is_absolute():
        return path
    base = runtime_dir().resolve()
    target = (base / path).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            f"relative runtime state path must stay under {base}: {value}"
        ) from exc
    return target


def resolve_runtime_lang_file(stem: str, ext: str, lang: str) -> Path:
    return runtime_dir() / f"{stem}.{lang}.{ext}"


def parse_jsonl(blob: bytes) -> list[dict]:
    """The object rows of a JSONL blob; blank and malformed lines are skipped."""
    rows: list[dict] = []
    # Decoded with "replace": one bad byte costs its row, not the whole file.
    for line in blob.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def read_jsonl(paths: Iterable[Path]) -> list[dict]:
    """Read valid object rows from multiple JSONL files, in path order."""
    rows: list[dict] = []
    for path in paths:
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        rows.extend(parse_jsonl(blob))
    return rows


def use_home(home: Path) -> None:
    """Make `home` the root every path here is resolved from. ROOT is bound at
    import, from AGENT_HOME or the checkout; the app names its home when it
    builds its character (character.py)."""
    global ROOT
    # agent.py binds its own ROOT, which locates the persona text and card.
    from . import agent as agent_module

    ROOT = agent_module.ROOT = Path(home)
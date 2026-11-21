"""Where a deployment lives, decided before any setting is read."""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
CHECKOUT = PACKAGE_DIR.parent


def is_checkout(path: Path = CHECKOUT) -> bool:
    """A source checkout carries pyproject.toml and the seed data beside the package."""
    return ((path / "pyproject.toml").is_file() and (path / "data").is_dir()
            and (path / "mibo").is_dir())


def resource(*parts: str) -> Path:
    """A file shipped with mibo: the tracked file in a checkout, else
    its copy under mibo/_bundled."""
    if is_checkout():
        return CHECKOUT.joinpath(*parts)
    return PACKAGE_DIR.joinpath("_bundled", *parts)


def default_home() -> Path:
    return Path.home() / "mibo"


def looks_like_home(path: Path) -> bool:
    # A stray data/ folder is not enough: that would claim most project folders.
    return is_checkout(path) or (
        any((path / name).is_file() for name in ("settings.json", ".env")) and (path / "persona.txt").is_file())


def find_home() -> Path:
    """AGENT_HOME, else this checkout, else a home in the cwd, else ~/mibo."""
    configured = os.getenv("AGENT_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if is_checkout():
        return CHECKOUT
    cwd = Path.cwd().resolve()
    if looks_like_home(cwd):
        return cwd
    return default_home()


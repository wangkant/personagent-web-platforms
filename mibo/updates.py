"""Whether a newer mibo is out. Asked at most once a day, in the
background, and the request carries only the version number. A new release
comes as a new installer on the release page."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from . import __version__
from .config_env import env_bool
from .storage import atomic_write_text

# The newest release on the release page, through GitHub's API.
LATEST_URL = "https://api.github.com/repos/wangkant/mibo/releases/latest"
RELEASE_URL = "https://github.com/wangkant/mibo/releases/tag/v{version}"
CHECK_EVERY_S = 24 * 3600
RETRY_AFTER_S = 3600
# Generous: through some proxies the TLS handshake alone takes seconds.
TIMEOUT_S = 15.0

_lock = threading.Lock()
_running: set[Path] = set()
# When each home last started a check, so an unwritable cache cannot turn
# the page's polling into a request every few seconds.
_attempted: dict[Path, float] = {}
_enabled: dict[tuple, bool] = {}


def _release(version: str) -> tuple[int, ...] | None:
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version or "")
    return tuple(int(n) for n in match.groups()) if match else None


def newer(latest: str, current: str = __version__) -> bool:
    """Only a plain X.Y.Z release counts, so a pre-release is never offered."""
    mine = _release(current)
    if mine is None or not re.fullmatch(r"\d+\.\d+\.\d+", latest or ""):
        return False
    return _release(latest) > mine


def enabled(home: Path) -> bool:
    from . import settingsfile

    try:
        stamp = settingsfile.path(home).stat().st_mtime_ns
    except OSError:
        stamp = None
    # Remembered per version of the settings, so a bad value warns once, not every poll.
    key = (str(home), stamp, os.environ.get("UPDATE_CHECK_ENABLED"))
    if key not in _enabled:
        values = settingsfile.values(home, keys=False) if stamp is not None else {}
        values.update(os.environ)  # the environment wins over the settings, as everywhere else
        _enabled[key] = env_bool("UPDATE_CHECK_ENABLED", True, env=values)
    return _enabled[key]


def _cache(home: Path) -> Path:
    return home / "runtime" / "desktop" / "update.json"


def _read(home: Path) -> dict:
    try:
        data = json.loads(_cache(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data


def stale(home: Path) -> bool:
    """The last answer is a day old, or an hour old after a failed attempt."""
    last = _read(home)
    checked, wait = last.get("checked"), RETRY_AFTER_S if last.get("failed") else CHECK_EVERY_S
    return checked is None or not 0 <= time.time() - checked < wait


def check(home: Path, timeout: float = TIMEOUT_S) -> str:
    """Ask the release page now and remember the answer; a failure keeps the last one."""
    import httpx

    latest = ""
    try:
        response = httpx.get(LATEST_URL, timeout=timeout, follow_redirects=True,
                             headers={"User-Agent": f"mibo/{__version__}"})
        response.raise_for_status()
        latest = str(response.json()["tag_name"]).removeprefix("v")
    except Exception:  # offline, blocked, or an answer of another shape
        pass
    failed = not latest
    latest = latest or str(_read(home).get("latest") or "")
    if home.is_dir():
        try:
            atomic_write_text(_cache(home), json.dumps(
                {"checked": time.time(), "latest": latest, "failed": failed}), fsync=False)
        except OSError:
            pass
    return latest


def refresh_soon(home: Path) -> None:
    """Ask in the background when the last answer is stale."""
    if not enabled(home) or not stale(home):
        return
    key = home.resolve()
    with _lock:
        now = time.time()
        if key in _running or 0 <= now - _attempted.get(key, 0) < RETRY_AFTER_S:
            return
        _running.add(key)
        _attempted[key] = now

    def run() -> None:
        try:
            check(home)
        finally:
            with _lock:
                _running.discard(key)

    threading.Thread(target=run, name="mibo-update-check", daemon=True).start()


def notice(home: Path, latest: str | None = None, *, always: bool = False) -> dict | None:
    """The newer release the last check found, and its release page.
    `always`: also with the daily check turned off, for someone who asked."""
    if not (always or enabled(home)):
        return None
    latest = str(_read(home).get("latest") or "") if latest is None else latest
    if not newer(latest):
        return None
    return {"version": latest, "url": RELEASE_URL.format(version=latest)}


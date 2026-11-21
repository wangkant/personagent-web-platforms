"""Secrets in the system's credential store through keyring: Windows Credential Manager, the macOS
keychain, the Secret Service on Linux. Nothing of them is written to a file of the home.

Each home has a service name of its own (Mibo/<hash of its path>), so two homes on one computer never
share a value. Windows keeps at most 2560 bytes (1280 characters) per entry, so a longer value, such as
an OAuth token, is kept in parts: the first entry says how many.

A system with no credential store at all (a Linux box with no Secret Service: a server, a minimal
desktop, CI) makes keyring raise NoKeyringError. There the values are kept in credentials.json in the
home, readable only by its user, as the GitHub CLI and Docker keep theirs without one, and Settings ›
本机 says that is where they are (kept_in). A store that is there but refuses (locked, a prompt
dismissed) still raises: nothing then goes to the file.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import keyring
from keyring.errors import NoKeyringError

from .storage import atomic_write_text

#: Characters per entry, inside Windows' limit.
PART = 1200
# Starts the first entry of a value kept in parts: \x01<count>\x01<first part>.
_PARTS = "\x01"
#: The home's file of values on a system with no credential store.
FILE = "credentials.json"


def service(home) -> str:
    path = str(Path(home).resolve()).lower()
    return "Mibo/" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]


def kept_in(home) -> str:
    """"store" where the system has a credential store, "file" where values go to credentials.json."""
    try:
        keyring.get_password(service(home), "")
    except NoKeyringError:
        return "file"
    return "store"


#: The stores the interface has a name for (locales: setup_device.stores).
NAMED = ("Windows", "macOS", "SecretService", "libsecret", "kwallet")


def store_name() -> str:
    """Which credential store keyring uses, as the interface names it: one of NAMED, else "other" (a chain of
    several, or one a plugin adds)."""
    name = type(keyring.get_keyring()).__module__.rsplit(".", 1)[-1]
    return name if name in NAMED else "other"


def get(home, name: str) -> str:
    """The value kept under `name`, or "" when there is none."""
    where = service(home)
    try:
        first = keyring.get_password(where, name) or ""
    except NoKeyringError:
        return _held(home).get(name, "")
    if not first.startswith(_PARTS):
        return first
    count, _, text = first[1:].partition(_PARTS)
    return text + "".join(keyring.get_password(where, f"{name}#{i}") or "" for i in range(1, int(count)))


def put(home, name: str, value: str) -> None:
    """Keeps `value` under `name`; an empty value removes it."""
    where = service(home)
    try:
        _remove(where, name)
    except NoKeyringError:
        _hold(home, name, value)
        return
    if not value:
        return
    parts = [value[i:i + PART] for i in range(0, len(value), PART)]
    if len(parts) == 1:
        keyring.set_password(where, name, value)
        return
    keyring.set_password(where, name, f"{_PARTS}{len(parts)}{_PARTS}{parts[0]}")
    for i, part in enumerate(parts[1:], 1):
        keyring.set_password(where, f"{name}#{i}", part)


def put_many(home, values: dict) -> None:
    for name, value in values.items():
        put(home, name, value)


def delete(home, name: str) -> None:
    put(home, name, "")


def _remove(where: str, name: str) -> None:
    first = keyring.get_password(where, name)
    if first is None:
        return
    count = int(first[1:].partition(_PARTS)[0]) if first.startswith(_PARTS) else 1
    for key in [name, *(f"{name}#{i}" for i in range(1, count))]:
        keyring.delete_password(where, key)


def _held(home) -> dict:
    try:
        return json.loads((Path(home) / FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _hold(home, name: str, value: str) -> None:
    """credentials.json with `name` set to `value` (removed when empty); gone once it holds nothing."""
    held = _held(home)
    held.pop(name, None)
    if value:
        held[name] = value
    path = Path(home) / FILE
    if held:
        atomic_write_text(path, json.dumps(held, ensure_ascii=False, indent=2) + "\n")
    else:
        path.unlink(missing_ok=True)

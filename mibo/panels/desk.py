"""The desk panel: start at sign-in, and the home as one archive: back it up,
restore it, or reset it.

A restore or a reset is done by the next start, before anything of it opens
the home's files: the page leaves the request in PENDING and the app starts
itself again (welcome.relaunch, apply_pending)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from fastapi import Request
from keyring.errors import KeyringError

from .. import __version__, autostart, db, secretstore, settingsfile
from .. import setup_wizard as sw
from ..storage import atomic_write_text

ERRORS: frozenset[str] = frozenset({
    "desk_unsupported", "desk_shortcut_failed", "desk_invalid_place", "desk_missing",
    "desk_open_failed", "desk_backup_failed", "desk_invalid_backup"})

# Where an archive may be written and found; nothing from a request is ever a path.
TARGETS = ("desktop", "documents", "backups")
# "desktop;documents" in place of the real folders, for tests and trial runs.
DIRS_ENV = "MIBO_USER_DIRS"
PREFIX = "mibo-backup-"
BACKUP_NAME = re.compile(r"mibo-backup-[0-9A-Za-z_-]{1,64}\.zip")
MANIFEST = "mibo-backup.json"
# What the credential store holds for the home (key_names), in an archive made with secrets; a restore puts it
# back there.
KEYS = "mibo-keys.json"
KEY_NAME = re.compile(r"[A-Za-z0-9_]{1,120}")
# Never in an archive: the archives themselves, the built-in browser's own storage (its sign-ins
# hold only on this computer) and the walks' throwaway profile.
LEFT_OUT = ("backups", "runtime/desktop/webview", "runtime/visitor")
# Nor what means something only to the app that is running: its link and token, locks, logs and
# half-written files; nor the values kept on a system with no credential store (KEYS carries the keys).
LEFT_OUT_NAMES = frozenset({"welcome.json", "__pycache__", secretstore.FILE})
LEFT_OUT_SUFFIXES = (".lock", ".tmp", ".part", ".pyc", ".log", "-wal", "-shm", "-journal")
# The runtime database, copied whole and consistent through SQLite's backup API, without the chat
# record, which a forget must reach in full (db.snapshot); a restore takes it back table by table.
DB = f"runtime/{db.FILE}"
# What the next start does ({"restore": path} or {"reset": true}), and what a restore came to.
PENDING = ("runtime", "desktop", "pending.json")
RESTORED = ("runtime", "desktop", "restored.json")
# What a reset leaves besides the archives: what the start doing it holds open.
HELD = frozenset({"runtime/desktop/welcome.lock", "runtime/desktop/app.log"})
MAX_MEMBERS = 50_000
MAX_UNPACKED = 4 * 1024 ** 3


def open_path(path: Path) -> None:
    """Show `path` in the system's file manager or default app."""
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _known_folder(kind: str) -> Path | None:
    override = os.environ.get(DIRS_ENV, "").strip()
    if override:
        parts = dict(zip(("desktop", "documents"), override.split(os.pathsep)))
        return Path(parts[kind]) if parts.get(kind) else None
    if os.name == "nt":
        import ctypes

        # CSIDL_DESKTOPDIRECTORY, CSIDL_PERSONAL: right even when OneDrive moved them.
        buffer = ctypes.create_unicode_buffer(260)
        csidl = {"desktop": 0x10, "documents": 0x05}[kind]
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer) == 0 and buffer.value:
            return Path(buffer.value)
        return None
    path = Path.home() / ("Desktop" if kind == "desktop" else "Documents")
    return path if path.is_dir() else None


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _left_out(member: str) -> bool:
    parts = member.split("/")
    return (any(member == p or member.startswith(p + "/") for p in LEFT_OUT)
            or bool(LEFT_OUT_NAMES & set(parts)) or parts[-1].endswith(LEFT_OUT_SUFFIXES))


def members(home: Path) -> list[tuple[Path, str]]:
    """(file on disk, name in the archive) for everything an archive of `home` holds."""
    found = []
    for folder, dirs, files in os.walk(home):
        base = Path(folder).relative_to(home).as_posix()
        prefix = "" if base == "." else base + "/"
        dirs[:] = [d for d in dirs if not _left_out(prefix + d)]
        for name in files:
            path = Path(folder) / name
            if not path.is_symlink() and not _left_out(prefix + name) and prefix + name != DB:
                found.append((path, prefix + name))
    return sorted(found, key=lambda pair: pair[1])


def key_names(home: Path) -> list[str]:
    """Every name the credential store holds for `home`: the model keys, the search API's key, and the values
    and sign-ins of the connectors in mcp.json."""
    from .. import mcp_market, mcp_tools, search

    servers = mcp_tools.load_config(home)["mcpServers"]
    refs = {found.group(1) for found in mcp_tools.REF_RE.finditer(json.dumps(servers))
            if found.group(1).startswith(mcp_tools.SECRET_PREFIX)}
    return [*settingsfile.KEYS, search.SEARCH_KEY, mcp_market.TOKEN_KEY, *sorted(refs),
            *(f"MIBO_OAUTH_{server}" for server in servers)]


def write(home: Path, folder: Path, secrets: bool, tag: str = "") -> Path:
    """One archive of `home` in `folder`; what the credential store holds for it only when `secrets`."""
    # Moves a .env, or a key typed into settings.json, where it belongs before anything is copied.
    settingsfile.values(home, keys=False)
    keys = {name: value for name in key_names(home) if secrets and (value := secretstore.get(home, name))}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path, n = folder / f"{PREFIX}{stamp}{tag}.zip", 1
    while path.exists():
        n += 1
        path = folder / f"{PREFIX}{stamp}{tag}-{n}.zip"
    partial = path.with_suffix(".zip.part")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST, json.dumps({"version": __version__, "created": int(time.time()),
                                                   "secrets": secrets}))
            if keys:
                archive.writestr(KEYS, json.dumps(keys))
            for source, member in members(home):
                try:
                    archive.write(source, member)
                except FileNotFoundError:
                    # Replaced mid-walk by an atomic write; nothing to copy.
                    continue
            copy = home / "runtime" / f"{db.FILE}.part"
            try:
                if db.snapshot(home / "runtime", copy):
                    archive.write(copy, DB)
            finally:
                copy.unlink(missing_ok=True)
        partial.replace(path)
    except (OSError, sqlite3.Error):
        # The folder itself may be what is wrong, so tidying up can fail too.
        with contextlib.suppress(OSError):
            partial.unlink()
        raise ValueError("desk_backup_failed") from None
    return path


def check(path: Path) -> tuple[list[zipfile.ZipInfo], dict]:
    """The members an archive puts back and its manifest; ValueError if it is not one of ours."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            manifest = json.loads(archive.read(MANIFEST))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        raise ValueError("desk_invalid_backup") from None
    if (not isinstance(manifest, dict) or len(infos) > MAX_MEMBERS
            or sum(i.file_size for i in infos) > MAX_UNPACKED):
        raise ValueError("desk_invalid_backup")
    plan = []
    for info in infos:
        name = info.filename
        if info.is_dir() or name in (MANIFEST, KEYS):
            continue
        if ("\\" in name or name.startswith("/") or ":" in name
                or any(p in ("", ".", "..") for p in name.split("/")) or _left_out(name)):
            raise ValueError("desk_invalid_backup")
        plan.append(info)
    return plan, manifest


def _unpack(home: Path, path: Path, plan: list[zipfile.ZipInfo]) -> None:
    """The home as the archive has it, and the keys it carries back in the credential store. Keys an archive
    without them does not carry stay as they are."""
    for source, _ in members(home):
        source.unlink()
    for folder, _, _ in os.walk(home, topdown=False):
        if folder != str(home) and not _left_out(Path(folder).relative_to(home).as_posix()):
            # Only the ones the archive's members left empty go.
            with contextlib.suppress(OSError):
                Path(folder).rmdir()
    archived = home / "runtime" / f"{db.FILE}.part"
    with zipfile.ZipFile(path) as archive:
        for info in plan:
            target = archived if info.filename == DB else home.joinpath(*info.filename.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)
    try:
        db.take_back(home / "runtime", archived if archived.is_file() else None)
    finally:
        archived.unlink(missing_ok=True)
    with zipfile.ZipFile(path) as archive:
        if KEYS in archive.namelist():
            secretstore.put_many(home, {name: str(value) for name, value in json.loads(archive.read(KEYS)).items()
                                        if KEY_NAME.fullmatch(name)})


def _restore(home: Path, path: Path) -> str:
    """Put the archive back, the present kept in backups/ first; '' or what went wrong."""
    try:
        plan, manifest = check(path)
        # With the present keys when the archive's would replace them.
        safety = write(home, home / "backups", manifest.get("secrets") is True, "-before-restore")
    except ValueError as error:
        return str(error)
    try:
        _unpack(home, path, plan)
    except (OSError, sqlite3.Error, zipfile.BadZipFile, KeyringError):
        try:
            _unpack(home, safety, check(safety)[0])
        except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile, KeyringError):
            return "desk_restore_failed"
        return "desk_restore_undone"
    return ""


def _reset(home: Path) -> None:
    """Everything of the home but its archives, as before the first run, its keys in the credential store too."""
    db.close(home / "runtime")
    for name in key_names(home):
        secretstore.delete(home, name)
    for folder, _, files in os.walk(home, topdown=False):
        here = Path(folder)
        if here.relative_to(home).parts[:1] == ("backups",):
            continue
        for name in files:
            if (here / name).relative_to(home).as_posix() not in HELD:
                (here / name).unlink()
        if here != home:
            # A folder still holding what this start keeps open stays.
            with contextlib.suppress(OSError):
                here.rmdir()


def apply_pending(home: Path) -> None:
    """The restore or reset the page asked for, before this start opens anything of the home."""
    pending = home.joinpath(*PENDING)
    asked = sw._read_json(pending)
    if not asked:
        return
    pending.unlink()
    if asked.get("reset"):
        _reset(home)
    elif isinstance(asked.get("restore"), str):
        error = _restore(home, Path(asked["restore"]))
        atomic_write_text(home.joinpath(*RESTORED), json.dumps({"error": error}))


class Desk:
    def __init__(self, workspace):
        self.workspace = workspace
        self.home: Path = workspace.home

    # ---- start at sign-in

    def shortcuts(self) -> dict:
        if not autostart.supported():
            return {"login_supported": False, "login": "off"}
        try:
            return {"login_supported": True, "login": autostart.status(self.home)}
        except (RuntimeError, ValueError):
            raise ValueError("desk_shortcut_failed") from None

    def set_login(self, on: bool) -> dict:
        if not autostart.supported():
            raise ValueError("desk_unsupported")
        if autostart.set_login(self.home, on):
            raise ValueError("desk_shortcut_failed")
        # The person chose: the first start's default no longer applies.
        autostart.remember(self.home)
        return self.shortcuts()

    # ---- the archive

    def target(self, name: str) -> Path | None:
        if name not in TARGETS:
            raise ValueError("desk_invalid_place")
        return self.home / "backups" if name == "backups" else _known_folder(name)

    def _folder(self, name: str) -> Path:
        folder = self.target(name)
        if folder is None:
            raise ValueError("desk_missing")
        return folder

    def data(self) -> dict:
        restored = self.home.joinpath(*RESTORED)
        told = sw._read_json(restored)
        restored.unlink(missing_ok=True)
        return {"targets": [{"id": name, "path": str(path)} for name in TARGETS
                            if (path := self.target(name)) is not None],
                "backups": self.backups(), "restored": told or None, "settings": self.settings()}

    def settings(self) -> dict:
        """Where the settings and the keys are kept, and the names in settings.json that change nothing."""
        kept = secretstore.kept_in(self.home)
        return {"file": str(settingsfile.path(self.home)), "keys": kept,
                "store": secretstore.store_name() if kept == "store" else str(self.home / secretstore.FILE),
                **settingsfile.problems(self.home)}

    def backups(self) -> list[dict]:
        found = []
        for where in TARGETS:
            folder = self.target(where)
            if folder is None or not folder.is_dir():
                continue
            for path in folder.glob(f"{PREFIX}*.zip"):
                if BACKUP_NAME.fullmatch(path.name) and path.is_file():
                    found.append({"place": where, "name": path.name, "size": _size(path),
                                  "when": int(path.stat().st_mtime)})
        return sorted(found, key=lambda item: -item["when"])[:20]

    def backup(self, to: str, secrets: bool) -> dict:
        path = write(self.home, self._folder(to), secrets)
        return {"path": str(path), "name": path.name, "size": _size(path), "backups": self.backups()}

    def open(self, place: str) -> dict:
        folder = self._folder(place)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            open_path(folder)
        except OSError:
            raise ValueError("desk_open_failed") from None
        return {"ok": True}

    def _ask(self, request: dict) -> None:
        atomic_write_text(self.home.joinpath(*PENDING), json.dumps(request))

    def restore(self, place: str, name: str) -> dict:
        folder = self.target(place) if place in TARGETS else None
        if folder is None or not BACKUP_NAME.fullmatch(name):
            raise ValueError("desk_invalid_backup")
        path = folder / name
        if not path.is_file():
            raise ValueError("desk_missing")
        check(path)
        self._ask({"restore": str(path)})
        return {"restarting": True}

    def reset(self, backup_to: str, secrets: bool) -> dict:
        saved = str(write(self.home, self._folder(backup_to), secrets)) if backup_to else ""
        self._ask({"reset": True})
        return {"restarting": True, "path": saved}


def mount(app, workspace, body) -> None:
    desk = Desk(workspace)

    async def start_again() -> None:
        # As Quit does, with a new start waiting to take the home over (welcome.relaunch).
        await asyncio.to_thread(app.state.relaunch)
        await asyncio.to_thread(workspace.close)
        app.state.shutdown()

    @app.get("/api/desk/shortcuts")
    async def shortcuts():
        return await asyncio.to_thread(desk.shortcuts)

    @app.post("/api/desk/shortcut")
    async def set_shortcut(request: Request):
        payload = await body(request)
        return await asyncio.to_thread(desk.set_login, payload.get("on") is True)

    @app.get("/api/desk/data")
    async def data():
        return await asyncio.to_thread(desk.data)

    @app.post("/api/desk/backup")
    async def backup(request: Request):
        payload = await body(request)
        return await asyncio.to_thread(desk.backup, str(payload.get("to") or ""), payload.get("secrets") is True)

    @app.post("/api/desk/open")
    async def open_place(request: Request):
        payload = await body(request)
        return await asyncio.to_thread(desk.open, str(payload.get("place") or ""))

    @app.post("/api/desk/restore")
    async def restore(request: Request):
        payload = await body(request)
        done = await asyncio.to_thread(desk.restore, str(payload.get("place") or ""), str(payload.get("name") or ""))
        await start_again()
        return done

    @app.post("/api/desk/reset")
    async def reset(request: Request):
        payload = await body(request)
        done = await asyncio.to_thread(desk.reset, str(payload.get("backupTo") or ""),
                                       payload.get("secrets") is True)
        await start_again()
        return done

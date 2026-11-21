"""Electron fetched and started from Python, from a fresh home: what a person's first window start does on this
system, through the app's own code (mibo/host/launch.py and runtimes.py).

    python tools/xplat/electron_check.py --home <fresh folder> [--out <folder>]
    python tools/xplat/electron_check.py --home <the same folder> --offline [--out <folder>]

The first form runs:

    fetch      the pinned Electron is downloaded, checked against its sha256 and unpacked into the home
    tamper     the same download with one byte changed is refused with "Error: the download did not match its
               checksum", and nothing is unpacked
    start      Electron is started with the shell, handed the port and the token on stdin, says hello, opens a view
    home       its profile (the browser pane's partition included) is in the home, and nothing was written in the
               per-user folders Electron names after itself or the shell (new entries elsewhere there, such as the
               system's shared certificate store and font cache, are listed)
    quit       it quits when the app asks and its process ends

The second form is run with the network turned off around it:

    offline    the network does not answer here, so the check proves something
    cache      the kept Electron is used, with no address past this computer asked for
    start, quit as above

The network is turned off the same way in .github/workflows/xplat.yml (a network namespace on Linux and in WSL, a
firewall rule on Windows, a pf rule on macOS). It opens a window, so it runs only on a runner or a virtual screen,
never on someone's own desktop. Results go to <out>/electron.json (electron-offline.json); the last lines say what
passed and what it measured. Exit 0 when every check passed, 1 otherwise.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import platform
import shutil
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from mibo import ingestion, runtimes  # noqa: E402
from mibo.host import launch  # noqa: E402


def size_mb(folder: Path) -> float:
    return round(sum(p.stat().st_size for p in folder.rglob("*") if p.is_file() and not p.is_symlink()) / 2**20, 1)


# The names Electron gives its own per-user folders when nothing says otherwise: the shell's package name, and
# Electron's.
APP_NAMES = {"mibo-shell", "Electron"}


def per_user_folders() -> dict[str, float]:
    """What sits in the system's per-user folders for apps, one level down, with the time of the newest write inside
    the ones named after the shell or Electron."""
    user = Path.home()
    if sys.platform == "win32":
        places = [Path(os.environ["APPDATA"]), Path(os.environ["LOCALAPPDATA"])]
    elif sys.platform == "darwin":
        places = [user / "Library" / name for name in ("Application Support", "Caches", "Logs", "Preferences",
                                                       "Saved Application State", "HTTPStorages", "WebKit")]
    else:
        places = [Path(os.environ.get("XDG_CONFIG_HOME") or user / ".config"),
                  Path(os.environ.get("XDG_CACHE_HOME") or user / ".cache"), user / ".local" / "share"]
    places.append(user)
    found = {}
    for entry in (entry for place in places if place.is_dir() for entry in place.iterdir()):
        inside = [entry, *entry.rglob("*")] if entry.name in APP_NAMES and entry.is_dir() else [entry]
        found[str(entry)] = max(p.lstat().st_mtime for p in inside)
    return found


def network_answers() -> list[str]:
    """Which of a public address and a public name answer from here, before anything is guarded."""
    answered = []
    for host in ("1.1.1.1", "github.com"):
        try:
            socket.create_connection((host, 443), timeout=5).close()
            answered.append(host)
        except OSError:
            pass
    return answered


def guard_network(asked: list[str]) -> None:
    """From here on this process may reach only this computer: anything else is noted in `asked` and refused."""
    from asyncio import proactor_events

    def local(host) -> bool:
        host = host.decode() if isinstance(host, bytes) else str(host)
        return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")

    def refuse(host) -> None:
        asked.append(str(host))
        raise OSError(f"the check refused {host}")

    real_connect, real_lookup = socket.socket.connect, socket.getaddrinfo

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not local(address[0]):
            refuse(address[0])
        return real_connect(sock, address)

    def lookup(host, *rest, **named):
        if host is not None and not local(host):
            refuse(host)
        return real_lookup(host, *rest, **named)

    socket.socket.connect, socket.getaddrinfo = connect, lookup
    # Windows' loop connects through the I/O completion port, not socket.connect.
    loop_connect = proactor_events.BaseProactorEventLoop.sock_connect

    def sock_connect(loop, sock, address):
        if not local(address[0]):
            refuse(address[0])
        return loop_connect(loop, sock, address)
    proactor_events.BaseProactorEventLoop.sock_connect = sock_connect


class Check:
    def __init__(self, out: Path):
        self.out = out
        self.results: dict = {"system": f"{platform.system()} {platform.release()} {platform.machine()}",
                              "python": platform.python_version(), "checks": {}, "measures": {}}

    def record(self, name: str, ok: bool, *steps: str) -> bool:
        self.results["checks"][name] = {"ok": bool(ok), "steps": list(steps)}
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {'; '.join(steps)}", flush=True)
        return ok


def start_and_quit(check: Check, home: Path) -> None:
    """Starts Electron with the shell, opens one view, then asks it to quit."""
    measures = check.results["measures"]
    bridge = launch.Bridge()
    began = time.monotonic()
    try:
        shell = launch.start(launch.command(launch.program(home)), bridge, home, home / "Downloads")
    except launch.LaunchError as error:
        check.record("start", False, str(error))
        log = launch.places(home)["log"]
        print(log.read_text(encoding="utf-8", errors="replace")[-3000:] if log.is_file() else "no log", flush=True)
        bridge.close()
        return
    measures["hello_s"] = round(time.monotonic() - began, 2)

    async def view() -> str:
        tab = await asyncio.to_thread(bridge.tab)
        await tab.goto("data:text/html,<title>Mibo</title><p>ready")
        title = await tab.evaluate("() => document.title")
        await tab.close()
        return title

    try:
        title = asyncio.run(view())
        metrics = bridge.call("metrics")
        measures["versions"] = {"electron": metrics["electron"], "chrome": metrics["chrome"]}
        measures["processes"] = len(metrics["processes"])
        check.record("start", title == "Mibo", f"hello after {measures['hello_s']} s",
                     f"Electron {metrics['electron']} (Chrome {metrics['chrome']}), {len(metrics['processes'])}"
                     f" processes, a view read '{title}'")
    except Exception as error:
        check.record("start", False, f"{type(error).__name__}: {error}"[:400])
    asked = time.monotonic()
    try:
        bridge.call("quit", 5)
    except Exception as error:
        print(f"quit: {type(error).__name__}: {error}", flush=True)
    try:
        code = shell.wait(20)
    except Exception:
        shell.kill()
        code = None
    measures["quit_s"] = round(time.monotonic() - asked, 2)
    bridge.close()
    check.record("quit", code == 0, f"the process ended with {code} after {measures['quit_s']} s")


def first_start(check: Check, home: Path) -> None:
    measures = check.results["measures"]
    if launch.program(home).exists():
        check.record("fetch", False, f"{home} already holds Electron: give a fresh home")
        return
    before, began = per_user_folders(), time.time()
    build = runtimes._build("electron")
    kept: dict = {}
    real_fetch = ingestion.safe_fetch_url

    async def keeping(url, **named):
        got = await real_fetch(url, **named)
        if got is not None:
            kept.update(url=url, got=got)
        return got

    ingestion.safe_fetch_url = keeping
    began = time.monotonic()
    try:
        program = asyncio.run(launch.fetch(home))
        error = ""
    except launch.LaunchError as failed:
        program, error = None, str(failed)
    ingestion.safe_fetch_url = real_fetch
    measures["fetch_s"] = round(time.monotonic() - began, 1)
    measures["zip_mb"] = round(build[0][2] / 2**20, 1)
    if program is None:
        check.record("fetch", False, error)
        return
    measures["unpacked_mb"] = size_mb(runtimes.folder(home, "electron"))
    check.record("fetch", program.is_file() and os.access(program, os.X_OK),
                 f"{kept['url'].rsplit('/', 1)[1]}: {measures['zip_mb']} MB from {kept['url'].split('/')[2]}"
                 f" in {measures['fetch_s']} s,"
                 f" {measures['unpacked_mb']} MB unpacked", f"program {program.relative_to(home)}")

    # The same download with one byte changed, into another fresh home.
    tampered = bytearray(kept["got"].content)
    tampered[len(tampered) // 2] ^= 0xFF
    other = home.with_name(home.name + "-tampered")
    shutil.rmtree(other, ignore_errors=True)

    async def changed(url, **named):
        return ingestion.SafeFetchResult(url=url, status_code=200, headers={}, content=bytes(tampered))

    ingestion.safe_fetch_url = changed
    try:
        asyncio.run(launch.fetch(other))
        said = "no error"
    except launch.LaunchError as failed:
        said = str(failed)
    ingestion.safe_fetch_url = real_fetch
    left = sorted(p.name for p in (other / "runtime" / "bin").iterdir()) if (other / "runtime" / "bin").is_dir() else []
    check.record("tamper", said == "Error: the download did not match its checksum" and not left,
                 f"said: {said}", f"left in the home: {left or 'nothing'}")
    shutil.rmtree(other, ignore_errors=True)

    start_and_quit(check, home)
    data = launch.places(home)["data"]
    partition = data / "Partitions" / "mibo-browse"
    after = per_user_folders()
    written = sorted(e for e, t in after.items() if Path(e).name in APP_NAMES and (e not in before or t > began))
    other = sorted(set(after) - set(before) - set(written))
    check.record("home", partition.is_dir() and not written,
                 f"profile {data.relative_to(home)} ({size_mb(data)} MB), pane partition there: {partition.is_dir()}",
                 f"written in Electron's own per-user folders: {written or 'nothing'}",
                 f"other new entries in the per-user folders (the system's shared stores): {other or 'none'}")


def offline_start(check: Check, home: Path) -> None:
    answered = network_answers()
    check.record("offline", not answered, f"answered here: {answered or 'nothing'}")
    asked: list[str] = []
    guard_network(asked)
    began = time.monotonic()
    try:
        program = asyncio.run(launch.fetch(home))
        said = f"program {program.relative_to(home)}"
    except launch.LaunchError as failed:
        program, said = None, str(failed)
    check.results["measures"]["cache_s"] = round(time.monotonic() - began, 3)
    check.record("cache", program is not None and program.is_file() and not asked,
                 f"{said} in {check.results['measures']['cache_s']} s", f"addresses asked for: {asked or 'none'}")
    if program is not None:
        start_and_quit(check, home)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    home = Path(args.home).resolve()
    home.mkdir(parents=True, exist_ok=True)
    out = Path(args.out or home).resolve()
    out.mkdir(parents=True, exist_ok=True)
    check = Check(out)
    (offline_start if args.offline else first_start)(check, home)
    name = "electron-offline.json" if args.offline else "electron.json"
    (out / name).write_text(json.dumps(check.results, ensure_ascii=False, indent=1), encoding="utf-8")
    failed = [n for n, c in check.results["checks"].items() if not c["ok"]]
    wanted = ["offline", "cache", "start", "quit"] if args.offline else ["fetch", "tamper", "start", "home", "quit"]
    failed += [n for n in wanted if n not in check.results["checks"]]
    print(f"\nmeasures: {json.dumps(check.results['measures'], ensure_ascii=False)}")
    print(f"kept in {out / name}")
    print(f"electron_check{' --offline' if args.offline else ''}: {len(wanted) - len(set(failed))}/{len(wanted)} passed"
          + (f"; failed or not run: {', '.join(dict.fromkeys(failed))}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    raise SystemExit(main(sys.argv[1:]))

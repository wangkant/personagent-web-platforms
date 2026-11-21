"""Starts the Electron shell (mibo/electron) for the app's window.

Electron is fetched into the home on the first start and checked against its release's sha256 (runtimes.ELECTRON);
an installed build starts the Electron it carries instead. The app listens on 127.0.0.1 (Bridge) and Electron
connects to it: it gets the port, a token made for this start and its folders as one line on its stdin, which is then
closed, and answers with {"hello": token}. The token is never written to disk or argv, Electron opens no port of its
own, and a connection without the token, or from a web page (which always sends its Origin), is refused.

    bridge = Bridge()
    shell = start(command(await fetch(home)), bridge, home, downloads)
    bridge.call("quit")
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

from .. import reach, runtimes
from .etab import ElectronTab

SHELL = Path(__file__).resolve().parents[1] / "electron"
# runtimes.install's codes, as the one sentence a failed fetch is told in.
FAILED = {"runtime_hash_mismatch": "Error: the download did not match its checksum",
          "runtime_fetch_failed": "Error: Electron could not be downloaded",
          "runtime_write_failed": "Error: Electron could not be saved in the home"}


class LaunchError(RuntimeError):
    """Electron could not be fetched or started; the message is one sentence."""


def program(home: Path) -> Path:
    """Electron's program: the one an installed build carries, else the one fetched into the home."""
    if getattr(sys, "frozen", False):
        # electron-builder's layout, with the Python app as extraResources: <app>/resources/mibo/mibo(.exe) beside
        # <app>/Mibo(.exe), and on macOS Mibo.app/Contents/Resources/mibo/mibo beside Contents/MacOS/Mibo.
        app = Path(sys.executable).resolve().parents[2]
        return {"win32": app / "Mibo.exe", "darwin": app / "MacOS" / "Mibo"}.get(sys.platform, app / "mibo")
    folder = runtimes.folder(home, "electron")
    return {"win32": folder / "electron.exe",
            "darwin": folder / "Electron.app" / "Contents" / "MacOS" / "Electron"}.get(sys.platform,
                                                                                      folder / "electron")


async def fetch(home: Path) -> Path:
    """Electron's program, fetched, checked and unpacked into the home the first time; LaunchError when it could not
    be. Later starts use what the home keeps and ask the network nothing."""
    if not program(home).is_file():
        try:
            await runtimes.install(home, "electron")
        except ValueError as error:
            raise LaunchError(FAILED[str(error)]) from None
    return program(home)


def command(executable: Path) -> list[str]:
    """What starts the shell: a fetched Electron is given the shell's folder; an installed one carries it."""
    return [str(executable)] if getattr(sys, "frozen", False) else [str(executable), str(SHELL)]


def places(home: Path) -> dict[str, Path]:
    """Electron's folders in the home: its profile (the browser pane's sign-ins and cache), the downloads waiting for
    their card's answer, and its log. Nothing of it is kept anywhere else on the computer."""
    desktop = reach.runtime_of(home) / "desktop"
    return {"data": desktop / "electron", "held": desktop / "held", "log": desktop / "electron.log"}


class Bridge:
    """The app's side of the connection: a websocket on 127.0.0.1 that takes one Electron, the one holding the token.
    `call` waits for an answer; events go to the handlers `listen` and `on` gave."""

    def __init__(self) -> None:
        from websockets.sync.server import serve
        self.token = secrets.token_urlsafe(32)
        self.ws = None
        self.hello = threading.Event()
        self.lock = threading.Lock()
        self.count = 0
        self.waiting: dict[int, list] = {}
        self.handlers: dict[tuple[str, str], list] = {}
        self.server = serve(self._serve, "127.0.0.1", 0, process_request=self._no_pages, max_size=None)
        self.port = self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _no_pages(self, connection, request):
        # A web page always sends its origin; Electron's main process sends none.
        if request.headers.get("Origin"):
            return connection.respond(403, "Forbidden\n")
        return None

    def _serve(self, ws) -> None:
        from websockets.exceptions import ConnectionClosed
        try:
            first = json.loads(ws.recv(timeout=10))
        except (TimeoutError, ConnectionClosed, ValueError):
            return
        if self.ws is not None or not isinstance(first, dict) \
                or not hmac.compare_digest(str(first.get("hello", "")), self.token):
            ws.close(code=1008)
            return
        self.ws = ws
        self.hello.set()
        try:
            for raw in ws:
                self._dispatch(json.loads(raw))
        except ConnectionClosed:
            # Electron quit.
            pass

    def _dispatch(self, message: dict) -> None:
        if "id" in message:
            waiter = self.waiting.pop(message["id"], None)
            if waiter:
                waiter[1] = message
                waiter[0].set()
            return
        key = (message.get("tab", ""), message["method"]) if message["event"] == "cdp" else ("", message["event"])
        for handler in list(self.handlers.get(key, [])):
            if message["event"] == "cdp":
                handler(message.get("params") or {}, message.get("session", ""))
            else:
                handler(message)

    def call(self, op: str, timeout: float = 30.0, **fields) -> dict:
        with self.lock:
            self.count += 1
            ident = self.count
            waiter = self.waiting[ident] = [threading.Event(), None]
            self.ws.send(json.dumps({"id": ident, "op": op, **fields}))
        if not waiter[0].wait(timeout):
            self.waiting.pop(ident, None)
            raise RuntimeError(f"{op} had no answer in {timeout:.0f} s")
        if "error" in waiter[1]:
            raise RuntimeError(waiter[1]["error"])
        return waiter[1]["result"]

    def cdp(self, tab: str, method: str, params: dict, session: str = "") -> dict:
        return self.call("cdp", tab=tab, method=method, params=params, session=session)

    def listen(self, tab: str, event: str, handler) -> None:
        self.handlers.setdefault((tab, event), []).append(handler)

    def on(self, event: str, handler) -> None:
        self.handlers.setdefault(("", event), []).append(handler)

    def tab(self, **options) -> ElectronTab:
        return ElectronTab(self, self.call("tab.create", **options)["tab"])

    def close(self) -> None:
        self.server.shutdown()


def start(command: list[str], bridge: Bridge, home: Path, downloads: Path, wait_s: float = 60.0) -> subprocess.Popen:
    """Starts Electron, hands it the bridge's port and token and its folders on stdin, and waits for its hello;
    LaunchError when it ends first or stays silent past `wait_s`."""
    where = places(home)
    where["log"].parent.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if key != "ELECTRON_RUN_AS_NODE"}
    with where["log"].open("ab") as log:
        shell = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, env=env)
    line = {"port": bridge.port, "token": bridge.token, "data": str(where["data"]), "held": str(where["held"]),
            "downloads": str(downloads)}
    shell.stdin.write(json.dumps(line).encode() + b"\n")
    shell.stdin.close()
    deadline = time.monotonic() + wait_s
    while not bridge.hello.wait(0.1):
        if shell.poll() is not None:
            raise LaunchError(f"Error: Electron ended before it connected (see {where['log']})")
        if time.monotonic() > deadline:
            shell.kill()
            raise LaunchError(f"Error: Electron did not connect within {wait_s:.0f} s (see {where['log']})")
    return shell

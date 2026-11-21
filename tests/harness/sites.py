"""Stand-in sites on 127.0.0.1: tools/opsite (the operating benchmark's pages, its /api/state, /__check?task=,
/__reset and POST log) and tests/roam_standin.py's self-describing pages, walls, paywall, download, script-drawn
list, search and redirects, served together by one server, plus pages a test writes itself. Every request is
logged with its method and, for a POST, its body. A second Site on another port serves cross-site and frame
cases; roam_standin.Recorder stands for an internal address under the name localhost.

The app's fetches refuse loopback, as they must. `reachable(monkeypatch, *sites)` lets these sites' own
addresses through and nothing else, so every other internal address is still refused. `PublicWeb` goes the
other way: the app's own check runs whole, on names a test makes answer with public addresses, and the
connection to such an address lands on the site."""
from __future__ import annotations

import importlib.util
import io
import ipaddress
import json
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from httpcore._backends.auto import AutoBackend

import roam_standin
from mibo import ingestion

# How long a stand-in that holds its asker (a tarpit, a held lookup) waits before it gives up on its own, so an
# app that waits on it for good fails its test rather than hanging the run.
HOLD_S = 20

OPSITE = Path(__file__).resolve().parents[2] / "tools" / "opsite"


def _opsite():
    """tools/opsite/serve.py as a module, loaded once; its log file is set per site."""
    if "opsite_serve" not in sys.modules:
        spec = importlib.util.spec_from_file_location("opsite_serve", OPSITE / "serve.py")
        module = importlib.util.module_from_spec(spec)
        argv, sys.argv = sys.argv, [str(OPSITE / "serve.py")]
        try:
            spec.loader.exec_module(module)
        finally:
            sys.argv = argv
        sys.modules["opsite_serve"] = module
    return sys.modules["opsite_serve"]


class Site(roam_standin.Site):
    """`url`, `log` (["GET /og", "POST /api/send {...}"]), `state()` (opsite's; GET /__check?task=T01 judges a
    task on it), `put(path, body)` to serve a page of the test's own, `tarpit(path)` for one that never finishes,
    `events` (["start /slow", "hang-up /slow"], in the order they happened) and `reset()` between tests."""

    def __init__(self, folder: Path) -> None:
        self.serve = _opsite()
        self.serve.LOG = folder / "opsite.log"
        self.log: list[str] = []
        self.written: dict[str, tuple] = {}
        self.tarpits: dict[str, Tarpit] = {}
        self.events: list[str] = []
        super().__init__()

    def reset(self) -> None:
        for pit in self.tarpits.values():
            pit.released.set()
        self.log.clear()
        self.written.clear()
        self.tarpits.clear()
        self.events.clear()
        self.loads.clear()
        self.serve.STATE = self.serve.fresh()

    def close(self) -> None:
        for pit in self.tarpits.values():
            pit.released.set()
        super().close()

    def state(self) -> dict:
        return json.loads(json.dumps(self.serve.STATE))

    def put(self, path: str, body: str | bytes, kind: str = "text/html; charset=utf-8", *, status: int = 200,
            headers: dict | None = None, sized: bool = True, before=None) -> str:
        """Serve `body` at `path` with this type, status and headers; not `sized`, it is sent with no
        Content-Length and ends at the connection's close. `before()` runs first, in the request's own thread (a
        barrier, a wait). Its full address."""
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.written[path] = (data, kind, status, dict(headers or {}), sized, before)
        return self.url + path

    def tarpit(self, path: str) -> Tarpit:
        """A page at `path` that sends its headers and then a byte at a time, never finishing, until the asker
        hangs up, the test ends or HOLD_S pass."""
        self.tarpits[path] = Tarpit()
        return self.tarpits[path]

    def _handler(self):
        site = self

        class Handler(site.serve.Handler):
            def log_message(self, format, *args) -> None:
                if self.command == "GET":
                    site.log.append(f"GET {self.path}")

            def do_GET(self) -> None:
                url = urlsplit(self.path)
                if url.path in site.tarpits:
                    return self._trickle(url.path, site.tarpits[url.path])
                if url.path in site.written:
                    site.loads.append(url.path)
                    data, kind, status, headers, sized, before = site.written[url.path]
                    if before:
                        before()
                    return self._send(status, kind, data, headers, sized)
                if url.path == "/cover.png":
                    site.loads.append(url.path)
                    return self._send(200, "image/png", site.cover)
                if url.path == "/api/hot":
                    rows = [{"id": ident, "title": title} for ident, title in site.videos.items()]
                    return self._send(200, "application/json", json.dumps({"rows": rows}).encode())
                status, kind, body, headers = site.page(url.path, parse_qs(url.query))
                if status != 404:
                    site.loads.append(url.path)
                    return self._send(status, kind, body.encode("utf-8"), headers)
                super().do_GET()

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.log.append(f"POST {self.path} {raw.decode('utf-8', 'replace')}".rstrip())
                # opsite reads the body again.
                self.rfile = io.BytesIO(raw)
                super().do_POST()

            def _send(self, status: int, kind: str, body: bytes, headers: dict | None = None,
                      sized: bool = True) -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                if sized:
                    self.send_header("Content-Length", str(len(body)))
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                try:
                    self.end_headers()
                    self.wfile.write(body)
                except ConnectionError:
                    # An asker that gave up (past its cap, or out of time) has hung up.
                    pass

            def _trickle(self, path: str, pit: Tarpit) -> None:
                site.events.append(f"start {path}")
                pit.started.set()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                ends = time.monotonic() + HOLD_S
                try:
                    while not pit.released.wait(0.02) and time.monotonic() < ends:
                        self.wfile.write(b" ")
                except ConnectionError:
                    site.events.append(f"hang-up {path}")
                    pit.hung_up.set()

        return Handler


class Tarpit:
    """`started` when it is asked for; `hung_up` when the asker closed the connection while it was still
    answering."""

    def __init__(self) -> None:
        self.started, self.hung_up, self.released = threading.Event(), threading.Event(), threading.Event()


class PublicWeb:
    """The public internet as the app's guarded fetch meets it, on this computer: `name(host, *answers)` makes
    lookups of `host` give those answers, one per lookup and the last repeated, each an address or a tuple of
    addresses given together; a connection to a public address among them reaches the site on 127.0.0.1 at the
    port the URL names. An internal address is never routed: the app's own check has to refuse it. `hold(host)`
    keeps a lookup of `host` waiting until `release()`. `lookups` and `connections` record what the app did; a
    name it was not given is left to the suite's guard, which refuses it."""

    def __init__(self, monkeypatch, site: Site) -> None:
        self.site = site
        self.answers: dict[str, list] = {}
        self.held: dict[str, threading.Event] = {}
        self.public: set[str] = set()
        self.lookups: list[str] = []
        self.connections: list[tuple[str, int]] = []
        lookup, connect = socket.getaddrinfo, AutoBackend.connect_tcp
        web = self

        def getaddrinfo(host, port, *rest, **named):
            name = host.decode() if isinstance(host, bytes) else str(host)
            if name not in web.answers and name not in web.held:
                return lookup(host, port, *rest, **named)
            web.lookups.append(name)
            if name in web.held:
                web.held[name].wait(HOLD_S)
            given = web.answers.get(name) or ["93.184.216.34"]
            answer = given.pop(0) if len(given) > 1 else given[0]
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, int(port or 0), 0, 0))
                    if ipaddress.ip_address(address).version == 6
                    else (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, int(port or 0)))
                    for address in (answer if isinstance(answer, tuple) else (answer,))]

        async def connect_tcp(backend, host, port, *rest, **named):
            web.connections.append((str(host), port))
            return await connect(backend, "127.0.0.1" if str(host) in web.public else host, port, *rest, **named)

        monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
        monkeypatch.setattr(AutoBackend, "connect_tcp", connect_tcp)

    def name(self, host: str, *answers) -> str:
        """Lookups of `host` give `answers`; its base address at the site's port."""
        self.answers[host] = list(answers)
        every = [a for answer in answers for a in (answer if isinstance(answer, tuple) else (answer,))]
        self.public |= {a for a in every if ipaddress.ip_address(a).is_global}
        return f"http://{host}:{urlsplit(self.site.url).port}"

    def hold(self, host: str) -> str:
        """A lookup of `host` that does not return while the test runs; its base address."""
        self.held[host] = threading.Event()
        return f"http://{host}:{urlsplit(self.site.url).port}"

    def release(self) -> None:
        for event in self.held.values():
            event.set()


def reachable(monkeypatch, *sites: Site) -> None:
    """The app's fetches may reach these sites (their exact host and port); every other internal address is
    still refused by the app's own check."""
    real = ingestion._resolve_public_target
    allowed = {urlsplit(site.url).netloc for site in sites}

    async def resolve(url: str):
        parts = urlsplit(url)
        if parts.netloc in allowed:
            return parts.hostname, "127.0.0.1", parts.port
        return await real(url)

    monkeypatch.setattr(ingestion, "_resolve_public_target", resolve)

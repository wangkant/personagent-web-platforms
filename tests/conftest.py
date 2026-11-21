"""What every test shares: a home of its own, no network past this computer, no real credential store
or window, the small model catalogue in data/, the markers, and coroutine tests."""
from __future__ import annotations

import asyncio
import inspect
import ipaddress
import json
import os
import socket
from asyncio import proactor_events
from pathlib import Path

import pytest

from mibo import db as _db
from mibo import home as _home
from mibo import modelcatalog as _modelcatalog
from mibo import paths as _paths
from mibo.shell import run as _shell_run

SUITE = Path(__file__).resolve().parent
# The shared harness's fixtures (model, app, turn) for every folder.
pytest_plugins = ("harness.fixtures",)
# This system's name, read before any test stands in another.
SYSTEM = os.name
LOOPBACK_NAMES = {"localhost"}


class NetworkBlocked(RuntimeError):
    """A test reached for an address past this computer."""


@pytest.fixture(autouse=True)
def _one_home_at_a_time(tmp_path_factory: pytest.TempPathFactory):
    """Each test gets a home of its own in the temporary folder: AGENT_HOME, paths.ROOT and the
    runtime folder point there, so nothing reaches the checkout's runtime/. A page a test starts takes
    its home's .env into the environment (character.py); environment and roots are put back."""
    environment, from_file, root = dict(os.environ), set(_home.FROM_ENV_FILE), _paths.ROOT
    own = tmp_path_factory.mktemp("home")
    os.environ["AGENT_HOME"] = str(own)
    os.environ.pop("AGENT_RUNTIME_DIR", None)
    _paths.use_home(own)
    yield own
    # Every database a test opened is closed with it: a run of a thousand homes would hold three files open for each.
    for opened in list(_db._open):
        _db.close(Path(opened).parent)
    for key in set(os.environ) - set(environment):
        del os.environ[key]
    os.environ.update(environment)
    _home.FROM_ENV_FILE.clear()
    _home.FROM_ENV_FILE.update(from_file)
    # A test may still be standing in another system's name here (its monkeypatch is put back later), and
    # before Python 3.12 a path cannot be made under a foreign name.
    with pytest.MonkeyPatch.context() as real:
        real.setattr(os, "name", SYSTEM)
        _paths.use_home(root)


@pytest.fixture(autouse=True)
def _small_model_catalogue(monkeypatch):
    """Tests read the small model catalogue in data/, not the snapshot a release refreshes."""
    monkeypatch.setattr(_modelcatalog, "PATH", SUITE / "data" / "modelcatalog.json")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    return True


def _name(host) -> str:
    return host.decode("ascii", "replace") if isinstance(host, bytes) else str(host)


def _loopback(host) -> bool:
    host = _name(host)
    if _is_ip(host):
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    return host.lower().rstrip(".") in LOOPBACK_NAMES


# Addresses past loopback that code under test asked for and was refused, for the run's summary.
REFUSED: list[str] = []


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch: pytest.MonkeyPatch):
    """No test reaches past this computer: connecting to, or looking up, anything but loopback raises
    NetworkBlocked. A test that meets it fails; code that swallows it is named in the run's summary."""

    def refuse(where) -> None:
        REFUSED.append(_name(where[0] if isinstance(where, tuple) else where))
        raise NetworkBlocked(f"a test reached the network: {_name(where)}")

    def check(sock, address) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address[0]):
            refuse(address)

    def guard(real):
        def connect(sock, address):
            check(sock, address)
            return real(sock, address)
        return connect

    def guard_loop(real):
        def sock_connect(loop, sock, address):
            check(sock, address)
            return real(loop, sock, address)
        return sock_connect

    real_lookup = socket.getaddrinfo

    def lookup(host, *rest, **named):
        # Turning an address written as numbers into itself asks no server.
        if host is not None and not _loopback(host) and not _is_ip(_name(host)):
            refuse(host)
        return real_lookup(host, *rest, **named)

    monkeypatch.setattr(socket.socket, "connect", guard(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(socket.socket.connect_ex))
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    # Windows' asyncio loop connects through the I/O completion port, not socket.connect.
    monkeypatch.setattr(proactor_events.BaseProactorEventLoop, "sock_connect",
                        guard_loop(proactor_events.BaseProactorEventLoop.sock_connect))


def pytest_terminal_summary(terminalreporter) -> None:
    """One line naming the addresses code under test asked for and was refused."""
    if REFUSED:
        counts = {host: REFUSED.count(host) for host in dict.fromkeys(REFUSED)}
        terminalreporter.write_line("refused past loopback: " + ", ".join(
            f"{host} x{count}" for host, count in sorted(counts.items(), key=lambda kv: -kv[1])))


@pytest.fixture(autouse=True)
def _no_update_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test asks PyPI; tests/test_updates.py turns the check back on."""
    monkeypatch.setenv("UPDATE_CHECK_ENABLED", "false")


@pytest.fixture(autouse=True)
def _no_walks(monkeypatch: pytest.MonkeyPatch) -> None:
    """No service a test starts goes out to real sites; tests/parts/finds/test_walks.py turns walks on."""
    monkeypatch.setenv("ROAM_ENABLED", "false")


@pytest.fixture(autouse=True)
def _no_credential_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test writes to this computer's credential store (secretstore.py): a dictionary stands in."""
    import keyring
    held: dict = {}
    monkeypatch.setattr(keyring, "get_password", lambda service, name: held.get((service, name)))
    monkeypatch.setattr(keyring, "set_password", lambda service, name, value: held.__setitem__((service, name), value))
    monkeypatch.setattr(keyring, "delete_password", lambda service, name: held.pop((service, name)))


@pytest.fixture(autouse=True)
def _same_on_every_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """English output whatever the system language, no waiting on the disk (no test checks that a write
    was flushed), and not as an administrator, whoever runs the suite: the app starts no local program
    of a connector while it is elevated, and CI's Windows runner is."""
    monkeypatch.setenv("AGENT_LANG", "en")
    monkeypatch.setattr(os, "fsync", lambda fd: None)
    monkeypatch.setattr(_shell_run, "elevated", lambda: False)
    # A browser test draws no window, not even a minimized one, on the desk of whoever runs the suite.
    monkeypatch.setenv("PERSONA_BROWSER_HEADLESS", "1")


@pytest.fixture
def tmp(tmp_path: Path) -> Path:
    """The per-test scratch directory the suites take as `tmp`."""
    return tmp_path


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Everything under floors/ and every test floors/manifest.json names carries the `floor` marker."""
    manifest = json.loads((SUITE / "floors" / "manifest.json").read_text(encoding="utf-8"))
    named = {node for nodes in manifest["floors"].values() for node in nodes}
    for item in items:
        if item.nodeid.startswith("tests/floors/") or item.nodeid.split("[")[0] in named:
            item.add_marker(pytest.mark.floor)


def pytest_runtest_setup(item: pytest.Item) -> None:
    """A test that drives a real window, pointer or keys runs only in the sandbox on CI."""
    if item.get_closest_marker("window") and not (
            os.environ.get("CI") == "true" and os.environ.get("MIBO_SANDBOX") == "1"):
        pytest.fail("window tests run only in the sandbox (CI=true and MIBO_SANDBOX=1)", pytrace=False)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item):
    """With MIBO_REQUIRE_BROWSER=1 (CI), a browser test that skips fails instead, so coverage is not lost."""
    report = yield
    if report.skipped and item.get_closest_marker("browser") and os.environ.get("MIBO_REQUIRE_BROWSER") == "1":
        # A skip's report is (file, line, reason).
        report.outcome = "failed"
        report.longrepr = f"MIBO_REQUIRE_BROWSER=1, and this test skipped: {report.longrepr[2]}"
    return report


def pytest_pyfunc_call(pyfuncitem: pytest.Function):
    """Run an `async def` test on its own event loop, so pytest stays the only test dependency."""
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    arguments = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
    }
    asyncio.run(pyfuncitem.obj(**arguments))
    return True

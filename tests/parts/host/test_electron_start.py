"""Electron for the app's window, fetched into the home and started from Python (mibo/host/launch.py with
runtimes.py): what is kept, what is refused, and the contract on Electron's stdin and the local bridge. Downloads
are stood in by archives made here, and Electron by a small Python program that speaks the bridge's first lines."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import stat
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

from mibo import ingestion, reach, runtimes
from mibo.host import launch

SYSTEMS = [(system, machine) for system in ("windows", "darwin", "linux") for machine in ("x64", "arm64")]
SHELL_PACKAGE = Path(launch.SHELL)


def archive(home: Path, link: bool = False) -> bytes:
    """An Electron download as this system's zip holds it: the program with its x bits beside the release's version
    file, and on request a link like the ones a macOS framework keeps."""
    inside = launch.program(home).relative_to(runtimes.folder(home, "electron")).as_posix()
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as made:
        program = zipfile.ZipInfo(inside)
        program.external_attr = (stat.S_IFREG | 0o755) << 16
        made.writestr(program, b"electron")
        made.writestr("version", runtimes.ELECTRON["version"])
        if link:
            current = zipfile.ZipInfo("Framework.framework/Versions/Current")
            current.external_attr = (stat.S_IFLNK | 0o777) << 16
            made.writestr(current, "A")
            made.writestr("Framework.framework/Versions/A/Framework", b"framework")
    return data.getvalue()


@pytest.fixture
def download(monkeypatch: pytest.MonkeyPatch):
    """Electron's release stood in on every system: `serve(published, sent)` makes the release's sha256 that of
    `published` and the bytes that arrive `sent`. The fetches made are listed."""
    fetched: list[str] = []

    def serve(published: bytes, sent: bytes | None = None) -> list[str]:
        url = "https://github.com/electron/electron/releases/download/v0/electron.zip"
        build = [(url, hashlib.sha256(published).hexdigest(), len(published))]
        monkeypatch.setitem(runtimes.ELECTRON, "builds", {key: build for key in SYSTEMS})

        async def fetch(url: str, **_named):
            fetched.append(url)
            return ingestion.SafeFetchResult(url=url, status_code=200, headers={},
                                             content=published if sent is None else sent)

        async def every(_runtime, urls):
            return urls

        monkeypatch.setattr(ingestion, "safe_fetch_url", fetch)
        monkeypatch.setattr(reach, "order", every)
        return fetched

    return serve


def test_a_download_that_does_not_match_its_checksum_is_refused_and_nothing_is_unpacked(tmp_path, download):
    good = archive(tmp_path)
    download(good, sent=good[:-1] + bytes([good[-1] ^ 0xFF]))
    with pytest.raises(launch.LaunchError) as refused:
        asyncio.run(launch.fetch(tmp_path))
    assert str(refused.value) == "Error: the download did not match its checksum"
    assert not (tmp_path / "runtime" / "bin").exists()


def test_electron_is_kept_in_the_home_and_a_second_start_asks_the_network_nothing(tmp_path, download):
    fetched = download(archive(tmp_path))
    first = asyncio.run(launch.fetch(tmp_path))
    assert first.is_file() and first.is_relative_to(tmp_path)
    assert asyncio.run(launch.fetch(tmp_path)) == first
    assert len(fetched) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="Windows' Electron has no links, and making one needs a privilege")
def test_electron_is_unpacked_with_its_program_bits_and_its_frameworks_links(tmp_path, download):
    download(archive(tmp_path, link=True))
    program = asyncio.run(launch.fetch(tmp_path))
    assert os.access(program, os.X_OK)
    current = runtimes.folder(tmp_path, "electron") / "Framework.framework" / "Versions" / "Current"
    assert current.is_symlink() and os.readlink(current) == "A"
    assert (current / "Framework").read_bytes() == b"framework"


def test_electron_is_not_offered_as_an_mcp_runtime_or_put_on_a_servers_path(tmp_path, download):
    download(archive(tmp_path))
    asyncio.run(launch.fetch(tmp_path))
    assert "electron" not in runtimes.RELEASES
    assert runtimes.env(tmp_path, {"PATH": "x"}) == {"PATH": "x"}


def test_the_electron_fetched_is_the_one_the_shell_pins_for_every_system():
    package = json.loads((SHELL_PACKAGE / "package.json").read_text(encoding="utf-8"))
    locked = json.loads((SHELL_PACKAGE / "package-lock.json").read_text(encoding="utf-8"))
    version = runtimes.ELECTRON["version"]
    assert package["devDependencies"]["electron"] == version
    assert locked["packages"]["node_modules/electron"]["version"] == version
    assert sorted(runtimes.ELECTRON["builds"]) == sorted(SYSTEMS)
    for builds in runtimes.ELECTRON["builds"].values():
        assert all(f"/v{version}/electron-v{version}-" in url and len(sha256) == 64 for url, sha256, _ in builds)


FAKE_SHELL = textwrap.dedent("""
    import json, os, sys
    from websockets.sync.client import connect
    line = json.loads(sys.stdin.readline())
    with open(os.environ["FAKE_SEEN"], "w") as seen:
        json.dump({"argv": sys.argv, "line": line}, seen)
    if os.environ.get("FAKE_END"):
        sys.exit(3)
    with connect(f"ws://127.0.0.1:{line['port']}") as ws:
        ws.send(json.dumps({"hello": line["token"]}))
        for raw in ws:
            request = json.loads(raw)
            ws.send(json.dumps({"id": request["id"], "result": {}}))
            if request["op"] == "quit":
                break
""")


@pytest.fixture
def shell(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """A stand-in Electron: it notes its arguments and its stdin line, then says hello and answers until quit."""
    program = tmp_path / "fake_electron.py"
    program.write_text(FAKE_SHELL, encoding="utf-8")
    monkeypatch.setenv("FAKE_SEEN", str(tmp_path / "seen.json"))
    bridge = launch.Bridge()
    yield bridge, [sys.executable, str(program)], tmp_path / "seen.json"
    bridge.close()


def test_the_token_reaches_electron_on_its_stdin_and_never_its_arguments_or_disk(tmp_path, shell):
    bridge, command, seen = shell
    home = tmp_path / "home"
    process = launch.start(command, bridge, home, tmp_path / "Downloads", wait_s=30)
    assert bridge.hello.is_set()
    bridge.call("quit", 10)
    assert process.wait(10) == 0
    noted = json.loads(seen.read_text(encoding="utf-8"))
    assert noted["line"]["token"] == bridge.token
    assert bridge.token not in " ".join(noted["argv"])
    assert all(bridge.token.encode() not in path.read_bytes() for path in home.rglob("*") if path.is_file())


def test_electron_keeps_its_profile_and_held_downloads_in_the_home(tmp_path, shell):
    bridge, command, seen = shell
    home = tmp_path / "home"
    process = launch.start(command, bridge, home, tmp_path / "Downloads", wait_s=30)
    bridge.call("quit", 10)
    process.wait(10)
    line = json.loads(seen.read_text(encoding="utf-8"))["line"]
    assert Path(line["data"]).is_relative_to(home) and Path(line["held"]).is_relative_to(home)
    assert line["downloads"] == str(tmp_path / "Downloads")


def test_electron_that_ends_before_its_hello_is_told_in_one_sentence(tmp_path, shell, monkeypatch):
    bridge, command, _ = shell
    monkeypatch.setenv("FAKE_END", "1")
    with pytest.raises(launch.LaunchError) as ended:
        launch.start(command, bridge, tmp_path / "home", tmp_path / "Downloads", wait_s=30)
    assert str(ended.value).startswith("Error: Electron ended before it connected")
    assert not bridge.hello.is_set()


def test_the_bridge_refuses_a_wrong_token_and_any_web_page():
    bridge = launch.Bridge()
    try:
        with connect(f"ws://127.0.0.1:{bridge.port}") as ws:
            ws.send(json.dumps({"hello": "not-the-token"}))
            with pytest.raises(ConnectionClosed):
                ws.recv(timeout=5)
        with pytest.raises(InvalidStatus) as refused:
            connect(f"ws://127.0.0.1:{bridge.port}", origin="http://127.0.0.1:8000")
        assert refused.value.response.status_code == 403
        assert not bridge.hello.is_set()
    finally:
        bridge.close()


def test_an_installed_build_starts_the_electron_it_carries(tmp_path, monkeypatch):
    app = tmp_path / ("Mibo.app/Contents" if sys.platform == "darwin" else "Mibo")
    python = app / ("Resources" if sys.platform == "darwin" else "resources") / "mibo" / "mibo"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(python))
    carried = launch.program(tmp_path / "home")
    app = app.resolve()
    assert carried == {"win32": app / "Mibo.exe", "darwin": app / "MacOS" / "Mibo"}.get(sys.platform, app / "mibo")
    assert launch.command(carried) == [str(carried)]

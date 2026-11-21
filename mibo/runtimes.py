"""Node.js and uv for the MCP servers people start with npx or uvx, fetched on the person's press into the home.

As Cherry Studio does it: the installer carries neither. A press on a server's row in Settings fetches the vendor's
own release archive for this computer (nodejs.org; astral-sh/uv's GitHub releases) through the guarded fetch,
checks it against the sha256 the vendor published for that release (written below), and unpacks it under
home/runtime/bin/<name>. Where the vendor does not answer, the same release comes from a public mirror (npmmirror's
copy of nodejs.org; the wheel astral-sh publishes on PyPI, from PyPI or its Tsinghua and Aliyun mirrors), checked
against the sha256 written for it, so a mirror never changes what is unpacked. Local servers start with those
folders first on PATH, and what the runtimes fetch in turn (npm packages, uv's cache and the Python it may fetch
for uvx) is kept beside them, so nothing lands elsewhere on the computer and removing the home removes all of it.

Electron for the app's window comes the same way (ELECTRON, started by host/launch.py): fetched on the window's first
start from Electron's GitHub release, or npmmirror's copy of it, and checked against the release's SHASUMS256.txt. It
is not one of the runtimes an MCP server is offered.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
import platform
import shutil
import stat
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from . import reach


def _node(file: str, sha256: str, size: int) -> list[tuple[str, str, int]]:
    return [(f"{base}/v26.13.1/{file}", sha256, size)
            for base in ("https://nodejs.org/dist", "https://registry.npmmirror.com/-/binary/node")]


def _uv(file: str, sha256: str, size: int, wheel: str, wheel_sha256: str, wheel_size: int) -> list[tuple[str, str, int]]:
    """GitHub's release archive, then the same release's wheel as PyPI publishes it, from PyPI and its mirrors."""
    return [(f"https://github.com/astral-sh/uv/releases/download/0.13.9/{file}", sha256, size)] + [
        (f"{base}/packages/{wheel}", wheel_sha256, wheel_size)
        for base in ("https://files.pythonhosted.org", "https://pypi.tuna.tsinghua.edu.cn",
                     "https://mirrors.aliyun.com/pypi")]


def _electron(file: str, sha256: str, size: int) -> list[tuple[str, str, int]]:
    return [(f"{base}/v44.11.5/{file}", sha256, size)
            for base in ("https://github.com/electron/electron/releases/download",
                         "https://registry.npmmirror.com/-/binary/electron")]


# name -> what the page calls it, the release, and per (system, machine) where its archive is, each with the
# sha256 and size in bytes its publisher gives.
RELEASES = {
    "node": {"title": "Node.js", "version": "26.13.1", "source": "nodejs.org", "builds": {
        ("windows", "x64"): _node("node-v26.13.1-win-x64.zip",
                            "e2edda75bfb089654705cf65a13b58933bfd5f57148e8ebda916f58a2474f1f7", 41166246),
        ("windows", "arm64"): _node("node-v26.13.1-win-arm64.zip",
                            "181ebe431b90787113a9e4fc80d38ce86d37888a353a0386de44d2872e48cb97", 37036542),
        ("darwin", "arm64"): _node("node-v26.13.1-darwin-arm64.tar.xz",
                            "af6202f5370a3a5209e85dc543758dfd9f44c8726626a236730cd603a67a8559", 30638176),
        ("darwin", "x64"): _node("node-v26.13.1-darwin-x64.tar.xz",
                            "2693310b2820f9350d29524f21344e1c623875bab773457d55b2aceb9670716f", 32743416),
        ("linux", "x64"): _node("node-v26.13.1-linux-x64.tar.xz",
                            "5c063e46bab0fbc1c17cc445d7ff129cbdcd0471be6ed83b6ab864f8143bc864", 34093052),
        ("linux", "arm64"): _node("node-v26.13.1-linux-arm64.tar.xz",
                            "89e1853138db51e8b82d3e98ba9c0d3f2956a28e5e55ea1b7e12d25aae92d5dd", 33053848),
    }},
    "uv": {"title": "uv", "version": "0.13.9", "source": "github.com/astral-sh/uv", "builds": {
        ("windows", "x64"): _uv("uv-x86_64-pc-windows-msvc.zip",
                            "edd61f7f5728a56cc62455386661860bdfc7bab65872f39b0c25bdcc1d8e1beb", 15567875,
                            "dd/ef/ceeabf3631117f7354787d9fb1ffe943899e59ed4b887a696ed547e47e76/uv-0.13.9-py3-none-win_amd64.whl",
                            "6a44b2ed8111d2f60d2b92627c5dccda3f7ed7570b18ce115e569cd68c28246b", 15662317),
        ("windows", "arm64"): _uv("uv-aarch64-pc-windows-msvc.zip",
                            "72eed595983a4d7d22d79f21065b0f4479cc7e5c7a89104bb410c8f47559a878", 15747415,
                            "da/d1/d97a5db1f27e077e4d6329223f6b3f719c02c97e4092043f13e628558d05/uv-0.13.9-py3-none-win_arm64.whl",
                            "eeb92051c2123b9730c46b04cc843cfa4bc7d7b0aec56df431a2215ef4cb0273", 15841844),
        ("darwin", "arm64"): _uv("uv-aarch64-apple-darwin.tar.gz",
                            "5aeca67f2283f75235de40d0bd9c0a5bbc5fce23b6d48b3bf9fa1b2db08382ca", 14554974,
                            "f3/a7/a8d7b074798c7fb3b26b37584b8fe2e29df2ccdc24ce7902caab2a53edbf/uv-0.13.9-py3-none-macosx_11_0_arm64.whl",
                            "66c512266d633bef48384a1f53003f41f2a8e7b36fe6e7fbfd3e0fd52d96fa63", 14660755),
        ("darwin", "x64"): _uv("uv-x86_64-apple-darwin.tar.gz",
                            "617a3211261b0fc739562f0071aa34e9739797c510208ede8e2977c30903d041", 17362924,
                            "1e/db/08e9ef4879bd667e6d7b4705e594e5bd3e4585f10fc63cb16b9fa2fac63e/uv-0.13.9-py3-none-macosx_10_12_x86_64.whl",
                            "c16ed11e013557536b3e60065f1b912f6407bd6518e041b961e23ef9e6b4fbf5", 17529974),
        ("linux", "x64"): _uv("uv-x86_64-unknown-linux-gnu.tar.gz",
                            "a7fbaff2b9a271d85c1bdb540941090fd1ce011797b4abeb48e4dcd39c2471d5", 17068176,
                            "3d/aa/26d64642d808de4a652579ef950d7cd2d37f080a8babf9c67c6b75fc4c3f/uv-0.13.9-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
                            "d7f459fe4fd247d830351c75686d3996ace3c6f3f728eb0e85a30b7ada0cef19", 17606939),
        ("linux", "arm64"): _uv("uv-aarch64-unknown-linux-gnu.tar.gz",
                            "0797542018e541a9288855df555c8077eed06f7a47d206362030480e8ecc9f3d", 16120245,
                            "57/15/94b6cd62ac3ff6550288328e8035288875197cbc183f51554b266a69877b/uv-0.13.9-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.musllinux_1_1_aarch64.whl",
                            "5435c5ce14a56431208ca0996416d310f1598e8c2c8ba707688985a16bb82dbe", 18026789),
    }},
}
# The Electron the app's window runs on (the version mibo/electron/package.json pins), as RELEASES lists a runtime.
ELECTRON = {"title": "Electron", "version": "44.11.5", "source": "github.com/electron/electron", "builds": {
    ("windows", "x64"): _electron("electron-v44.11.5-win32-x64.zip",
                                  "2938040ee81382bb8a99954425a316fee17f23d55f5be0ef413275f7e793c8af", 158322092),
    ("windows", "arm64"): _electron("electron-v44.11.5-win32-arm64.zip",
                                    "b761722597942aed068be74768fdbb1137b0cafce6c91e9b31e454c82ffa6a39", 156552141),
    ("darwin", "arm64"): _electron("electron-v44.11.5-darwin-arm64.zip",
                                   "0bed9868a3074ec80af72d15c022a17e74aec52bd7057d207f33d48a6bf0f43b", 130889686),
    ("darwin", "x64"): _electron("electron-v44.11.5-darwin-x64.zip",
                                 "c26253fb081f758a29412e51f21b00f6f79170e279dbb614bde428ed73d6be8a", 134496461),
    ("linux", "x64"): _electron("electron-v44.11.5-linux-x64.zip",
                                "765b846228245704d7796aaa8621b753cc5bbb9aec8d7ccd32c5fc268aa0348a", 123245398),
    ("linux", "arm64"): _electron("electron-v44.11.5-linux-arm64.zip",
                                  "0bf637f2cecbd000a158d25b956b7982e30fd720a7537084f024a1a367f6cb11", 124264009),
}}
# The commands that need each runtime.
COMMANDS = {"npx": "node", "npm": "node", "node": "node", "uvx": "uv", "uv": "uv"}
# The program whose presence says a runtime is unpacked.
_MAIN = {"node": "node", "uv": "uv"}


def _build(name: str):
    system = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    machine = "arm64" if machine in ("arm64", "aarch64") else "x64" if machine in ("amd64", "x86_64") else machine
    return (ELECTRON if name == "electron" else RELEASES[name])["builds"].get((system, machine))


def folder(home: Path, name: str) -> Path:
    return Path(home) / "runtime" / "bin" / name


def _path_dir(home: Path, name: str) -> Path:
    """The folder of a runtime that goes on PATH: Node's own programs are in bin/ outside Windows."""
    base = folder(home, name)
    return base / "bin" if name == "node" and os.name != "nt" else base


def ready(home: Path, name: str) -> bool:
    return bool(shutil.which(_MAIN[name], path=str(_path_dir(home, name))))


def needed(command: str) -> str:
    """The runtime a bare command such as npx or uvx comes with, or ""."""
    path = Path(command)
    if path.parent.name:
        return ""
    return COMMANDS.get(path.stem.lower() if path.suffix.lower() in (".cmd", ".exe", "") else "", "")


def offer(name: str) -> dict | None:
    """What the page says before a download: the release, its size and where it comes from; None when there is
    no build for this computer."""
    build = _build(name)
    if build is None:
        return None
    release = RELEASES[name]
    return {"title": release["title"], "version": release["version"], "source": release["source"],
            "size": build[0][2]}


def env(home: Path, own: dict | None) -> dict:
    """A local server's own `env` with the runtimes Mibo fetched first on PATH and their downloads kept in the
    home; unchanged when there are none."""
    out = dict(own or {})
    have = [name for name in RELEASES if ready(home, name)]
    if not have:
        return out
    out["PATH"] = os.pathsep.join([str(_path_dir(home, name)) for name in have] + [out.get("PATH") or os.environ["PATH"]])
    cache = Path(home) / "runtime" / "cache"
    if "uv" in have:
        out.setdefault("UV_CACHE_DIR", str(cache / "uv"))
        out.setdefault("UV_TOOL_DIR", str(cache / "uv-tools"))
        out.setdefault("UV_PYTHON_INSTALL_DIR", str(cache / "python"))
    if "node" in have:
        out.setdefault("npm_config_cache", str(cache / "npm"))
    return out


async def reach_env(home: Path, command: str) -> dict:
    """Where npx or uvx fetch packages, and uv a Python, when the vendor's own host does not answer here: the
    well-known public mirrors (npmmirror; Tsinghua or Aliyun's PyPI; npmmirror's copy of the Python builds uv
    checks against the hashes it carries). {} for other commands, or where the vendors answer."""
    runtime, need, out = reach.runtime_of(home), needed(command), {}
    if need == "node" and not await reach.answers(runtime, "registry.npmjs.org") \
            and await reach.answers(runtime, "registry.npmmirror.com"):
        out["npm_config_registry"] = "https://registry.npmmirror.com"
    if need == "uv":
        if not all([await reach.answers(runtime, "pypi.org"), await reach.answers(runtime, "files.pythonhosted.org")]):
            index = await reach.first(runtime, ["pypi.tuna.tsinghua.edu.cn", "mirrors.aliyun.com"])
            if index:
                out["UV_DEFAULT_INDEX"] = {"pypi.tuna.tsinghua.edu.cn": "https://pypi.tuna.tsinghua.edu.cn/simple",
                                           "mirrors.aliyun.com": "https://mirrors.aliyun.com/pypi/simple"}[index]
        if not await reach.answers(runtime, "releases.astral.sh") \
                and await reach.answers(runtime, "registry.npmmirror.com"):
            out["UV_PYTHON_INSTALL_MIRROR"] = "https://registry.npmmirror.com/-/binary/python-build-standalone"
    return out


def _unpack(data: bytes, url: str, into: Path) -> None:
    """The archive into `into`, without its one top folder when it has one."""
    if url.endswith(".whl"):
        # uv's wheel holds its programs as the wheel's scripts.
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if info.filename.split("/")[-2:-1] == ["scripts"]:
                    target = into / PurePosixPath(info.filename).name
                    target.write_bytes(archive.read(info))
                    target.chmod(0o755)
        return
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                # The mode the archive keeps: Electron's programs carry their x bits, and its macOS app its
                # frameworks' links (Versions/Current), which a plain extract would write as small files.
                mode = info.external_attr >> 16
                target = into / info.filename
                if stat.S_ISLNK(mode):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.symlink_to(archive.read(info).decode())
                    continue
                archive.extract(info, into)
                if mode & 0o111 and not info.is_dir():
                    target.chmod(mode & 0o777)
    else:
        # The archive is the one whose sha256 is written above, so its own paths are taken as they are.
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            archive.extractall(into)
    inside = list(into.iterdir())
    if len(inside) == 1 and inside[0].is_dir():
        top = inside[0]
        for item in list(top.iterdir()):
            item.rename(into / item.name)
        top.rmdir()


async def install(home: Path, name: str) -> None:
    """Fetches, checks and unpacks one runtime; ValueError with the page's code when it could not."""
    from .ingestion import safe_fetch_url
    where = {url: (sha256, size) for url, sha256, size in _build(name)}
    got, wrong = None, False
    for url in await reach.order(reach.runtime_of(home), list(where)):
        sha256, size = where[url]
        got = await safe_fetch_url(url, timeout=60, max_wire_bytes=size + 1024, max_decoded_bytes=size + 1024,
                                   headers={"User-Agent": "Mibo", "Accept-Encoding": "identity"}, max_redirects=5)
        if got is not None and hashlib.sha256(got.content).hexdigest() == sha256:
            break
        wrong, got = wrong or got is not None, None
    if got is None:
        raise ValueError("runtime_hash_mismatch" if wrong else "runtime_fetch_failed")
    target = folder(home, name)
    part = target.with_name(f".{name}-part")

    def write() -> None:
        shutil.rmtree(part, ignore_errors=True)
        part.mkdir(parents=True)
        _unpack(got.content, url, part)
        shutil.rmtree(target, ignore_errors=True)
        part.rename(target)

    try:
        await asyncio.to_thread(write)
    except OSError:
        raise ValueError("runtime_write_failed") from None

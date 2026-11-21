"""An icon on the desktop and in the Start menu that opens the window with no
terminal behind it, which the desk panel makes. Windows only."""
from __future__ import annotations

import base64
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import window

WINDOWS = os.name == "nt"
LINK = "Mibo.lnk"
# Where each kind of link lives; Startup is the one that runs at sign-in.
SPECIAL = {"desktop": "Desktop", "programs": "Programs", "startup": "Startup"}
ICON = ("desktop", "programs")
LOGIN = ("startup",)
# "desktop;programs;startup" in place of the real folders, for tests and trial runs.
DIRS_ENV = "MIBO_SHORTCUT_DIRS"


def _quoted(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _override() -> dict[str, Path] | None:
    raw = os.environ.get(DIRS_ENV, "").strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(os.pathsep)]
    if len(parts) != len(SPECIAL) or not all(parts):
        # Never fall back to the real folders when someone meant to redirect them.
        raise ValueError(f"{DIRS_ENV} needs {len(SPECIAL)} folders")
    return {kind: Path(p) for kind, p in zip(SPECIAL, parts)}


def _where(kinds: tuple[str, ...]) -> str:
    override = _override()
    if override:
        return "@(" + ", ".join(_quoted(override[k]) for k in kinds) + ")"
    return "@(" + ", ".join(f"[Environment]::GetFolderPath('{SPECIAL[k]}')" for k in kinds) + ")"


def target(home: Path) -> tuple[Path, str]:
    """What the icon runs: this install's Python without a console, on `home`;
    the installer's build is a program of its own, already without one."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable), f'start --home "{home}"'
    python = Path(sys.executable)
    quiet = python.with_name("pythonw.exe")
    return (quiet if quiet.is_file() else python), f'-m mibo start --home "{home}"'


def _arguments(base: str, kind: str) -> str:
    # At sign-in it stays behind its icon, when closing is set to keep it running.
    return base + " --background" if kind == "startup" else base


# WScript.Shell saves and reads only a path the system code page can spell, so
# a link under a profile outside it goes through Public (or Windows\Temp). A
# file put there directly is open to every local account, and a link moved on
# keeps that, so it goes in a new folder that only this user can open, with
# that access set when the folder is created.
_PRIVATE_FOLDER = "\n".join([
    "function Private-Folder {",
    "  $base = $env:PUBLIC; if (-not $base) { $base = Join-Path $env:SystemRoot 'Temp' }",
    "  $acl = New-Object Security.AccessControl.DirectorySecurity",
    "  $acl.SetAccessRuleProtection($true, $false)",
    "  $me = [Security.Principal.WindowsIdentity]::GetCurrent().User",
    "  $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule("
    "$me, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')))",
    "  $path = Join-Path $base ('mibo-' + [Guid]::NewGuid().ToString('N'))",
    "  [IO.Directory]::CreateDirectory($path, $acl).FullName",
    "}"])


def script(home: Path, *, remove: bool = False, folders: list[Path] | None = None,
           kinds: tuple[str, ...] = ICON) -> str:
    """The PowerShell that writes the links, or takes them away."""
    where = "@(" + ", ".join(map(_quoted, folders)) + ")" if folders else _where(kinds)
    if remove:
        # A link that is not there is already removed; one that stays is a failure.
        return (f"foreach ($folder in {where}) {{ if (-not $folder) {{ continue }}; "
                f"$path = Join-Path $folder {_quoted(LINK)}; "
                f"if (Test-Path -LiteralPath $path) {{ Remove-Item -LiteralPath $path -Force -ErrorAction Stop }} }}")
    program, arguments = target(home)
    if kinds == LOGIN:
        arguments = _arguments(arguments, "startup")
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$shell = New-Object -ComObject WScript.Shell",
        "function Save-Link($path) {",
        "  $link = $shell.CreateShortcut($path)",
        f"  $link.TargetPath = {_quoted(program)}",
        f"  $link.Arguments = {_quoted(arguments)}",
        f"  $link.WorkingDirectory = {_quoted(home)}",
        f"  $link.IconLocation = {_quoted(window.icon())}",
        "  $link.Description = 'Mibo'",
        "  $link.Save()",
        "}",
        _PRIVATE_FOLDER,
        f"foreach ($folder in {where}) {{",
        f"  $final = Join-Path $folder {_quoted(LINK)}",
        "  try { Save-Link $final } catch {",
        "    $staging = Private-Folder; $staged = Join-Path $staging 'Mibo.lnk'",
        "    try { Save-Link $staged; Move-Item -LiteralPath $staged -Destination $final -Force }",
        "    finally { Remove-Item -LiteralPath $staging -Recurse -Force }",
        "  }",
        "}"])


def _powershell(source: str) -> tuple[str, str]:
    """Run it; (what it printed, what went wrong or '')."""
    # Encoded: a home or a user name outside ASCII survives any console code page.
    source = "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" + source
    encoded = base64.b64encode(source.encode("utf-16-le")).decode("ascii")
    try:
        done = subprocess.run(
            ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "", str(error)
    out = done.stdout.decode("utf-8", errors="replace")
    if done.returncode == 0:
        return out, ""
    # An encoded command reports its errors as CLIXML; the first is the reason.
    said = re.search(r'<S S="Error">(.*?)</S>', done.stderr.decode("utf-8", errors="replace"), re.S)
    reason = re.sub(r"_x[0-9A-Fa-f]{4}_", " ", said.group(1)).strip() if said else ""
    return out, html.unescape(reason) or f"exit {done.returncode}"


def run(source: str) -> str:
    """Run it; the answer is what went wrong, or ''."""
    return _powershell(source)[1]


def status(home: Path) -> dict[str, str]:
    """Each link kind: "on" (opens this install on `home`), "other" (opens
    something else) or "off". RuntimeError when Windows cannot be asked."""
    pairs = ", ".join(f"@({_quoted(kind)}, {_where((kind,))}[0])" for kind in SPECIAL)
    source = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$shell = New-Object -ComObject WScript.Shell",
        "$found = @()",
        _PRIVATE_FOLDER,
        f"foreach ($pair in @({pairs})) {{",
        "  if (-not $pair[1]) { continue }",
        f"  $path = Join-Path $pair[1] {_quoted(LINK)}",
        "  if (Test-Path -LiteralPath $path) {",
        # Read a copy where the code page can spell the path.
        "    $read = $path; $staging = $null",
        "    if ($path -match '[^ -~]') {",
        "      $staging = Private-Folder; $read = Join-Path $staging 'Mibo.lnk'",
        "      Copy-Item -LiteralPath $path -Destination $read -Force",
        "    }",
        "    try {",
        "      $link = $shell.CreateShortcut($read)",
        "      $found += [pscustomobject]@{kind = $pair[0]; target = $link.TargetPath; arguments = $link.Arguments}",
        "    } finally { if ($staging) { Remove-Item -LiteralPath $staging -Recurse -Force } }",
        "  }",
        "}",
        "ConvertTo-Json -InputObject @($found) -Compress"])
    out, failed = _powershell(source)
    if failed:
        raise RuntimeError(failed)
    try:
        found = json.loads(out.strip() or "[]")
    except ValueError:
        raise RuntimeError("unreadable answer") from None
    program, arguments = target(home)
    result = dict.fromkeys(SPECIAL, "off")
    for item in found if isinstance(found, list) else [found]:
        kind = str(item.get("kind") or "") if isinstance(item, dict) else ""
        if kind in result:
            same = (os.path.normcase(str(item.get("target") or "")) == os.path.normcase(str(program))
                    and str(item.get("arguments") or "") == _arguments(arguments, kind))
            result[kind] = "on" if same else "other"
    return result

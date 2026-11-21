"""Run without a console (pythonw, a packaged shell), the app starts console programs with no window of their own:
every process start in the process passes through one place (consoles.hush), the app's and its libraries', and no
start in mibo/ goes around it."""
from __future__ import annotations

import ast
import ctypes
import subprocess
import sys
import textwrap
import types
from pathlib import Path

from mibo import consoles

PACKAGE = Path(consoles.__file__).resolve().parent
NO_WINDOW, NEW_CONSOLE = 0x08000000, 0x00000010
# Starts that do not pass through CreateProcess in _winapi: the C runtime's own (os.system, os.spawn*, os.exec*)
# and Windows' called directly.
AROUND = ("os.system", "os.spawn", "os.exec", "os.posix_spawn", "CreateProcessW", "CreateProcessA",
          "WinExec")


def _stand_in(monkeypatch, window: int = 0) -> list:
    """A stand-in _winapi whose CreateProcess records the flags each start reaches Windows with, in a process whose
    console window is `window` (0: none)."""
    started = []
    fake = types.ModuleType("_winapi")
    fake.CreateProcess = lambda application, command, pa, ta, inherit, flags, env, cwd, startup: started.append(
        (command, flags))
    monkeypatch.setitem(sys.modules, "_winapi", fake)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=types.SimpleNamespace(
        GetConsoleWindow=lambda: window)), raising=False)
    monkeypatch.setattr(consoles, "NO_WINDOW", NO_WINDOW)
    monkeypatch.setattr(consoles, "OWN", NEW_CONSOLE | 0x00000008)
    return started


def test_after_hush_every_start_has_no_console_window_unless_it_asked_for_a_console(monkeypatch) -> None:
    started = _stand_in(monkeypatch)
    consoles.hush()
    create = sys.modules["_winapi"].CreateProcess
    create(None, "powershell -Command Get-Date", None, None, False, 0, None, None, None)
    create(None, "node driver.js", None, None, False, 0x00000200, None, None, None)
    create(None, "cmd /k", None, None, False, NEW_CONSOLE, None, None, None)
    assert started == [("powershell -Command Get-Date", NO_WINDOW), ("node driver.js", NO_WINDOW | 0x00000200),
                       ("cmd /k", NEW_CONSOLE)]


def test_in_a_terminal_what_it_starts_shares_the_terminals_window(monkeypatch) -> None:
    started = _stand_in(monkeypatch, window=0x1234)
    consoles.hush()
    sys.modules["_winapi"].CreateProcess(None, "powershell", None, None, False, 0, None, None, None)
    assert started == [("powershell", 0)]


def test_the_package_hushes_itself_when_it_is_imported_without_a_console(tmp_path) -> None:
    """In a fresh interpreter with no console window, as pythonw starts it, importing mibo is enough."""
    out = tmp_path / "flags.txt"
    script = textwrap.dedent(f"""
        import ctypes, subprocess, sys, types
        subprocess.CREATE_NO_WINDOW, subprocess.CREATE_NEW_CONSOLE, subprocess.DETACHED_PROCESS = {NO_WINDOW}, 16, 8
        ctypes.windll = types.SimpleNamespace(kernel32=types.SimpleNamespace(GetConsoleWindow=lambda: 0))
        fake = types.ModuleType("_winapi")
        seen = []
        fake.CreateProcess = lambda *args: seen.append(args[5])
        sys.modules["_winapi"] = fake
        import mibo
        fake.CreateProcess(None, "powershell", None, None, False, 0, None, None, None)
        open({str(out)!r}, "w").write(str(seen))
    """)
    subprocess.run([sys.executable, "-c", script], cwd=PACKAGE.parent, check=True, timeout=60)
    assert out.read_text() == f"[{NO_WINDOW}]"


def test_no_start_in_the_package_goes_around_createprocess_or_sets_the_flag_itself() -> None:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        where = path.relative_to(PACKAGE.parent).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            if any(name == a or name.startswith(a) or name.endswith("." + a) for a in AROUND):
                found.append(f"{where}:{node.lineno} {name}")
            flags = next((k.value for k in node.keywords if k.arg == "creationflags"), None)
            if flags is not None and ("CREATE_NO_WINDOW" in ast.unparse(flags) or str(NO_WINDOW) in ast.unparse(flags)):
                found.append(f"{where}:{node.lineno} sets CREATE_NO_WINDOW itself")
    assert found == [], "start processes with subprocess or asyncio, which consoles.hush covers"

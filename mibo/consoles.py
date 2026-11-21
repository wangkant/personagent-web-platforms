"""No console window for any process the app starts while it runs without a console of its own.

Installed, the app runs as a windowless process (pythonw, a packaged shell). A console program it starts
(PowerShell, cmd, node, python) then gets a new console, and that console opens a black window. Asking for the
window hidden (SW_HIDE, as playwright's driver and the standard library's platform module do) is not enough where
Windows Terminal takes over new consoles; CREATE_NO_WINDOW is. With it the program still has a console, so what it
prints comes back as before, and a program with windows of its own is not changed by it.

Every process a Python program starts on Windows, the app's and its libraries' alike, is created by
_winapi.CreateProcess, so the flag is added there, once, as the package is imported (mibo/__init__.py). A process
with a console window of its own (started in a terminal) is left alone: what it starts shares that window. On other
systems there is nothing to do.
"""
from __future__ import annotations

import subprocess

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# A start that asks for a console of its own, or for none at all, keeps what it asked for.
OWN = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)


def hush() -> None:
    """Without a console window of its own, every process this one starts from now on gets CREATE_NO_WINDOW,
    unless it asked for a console of its own."""
    if not NO_WINDOW:
        return
    import ctypes
    if ctypes.windll.kernel32.GetConsoleWindow():
        return
    import _winapi
    create = _winapi.CreateProcess

    def quiet(application, command, process_attrs, thread_attrs, inherit, flags, *rest):
        return create(application, command, process_attrs, thread_attrs, inherit,
                      flags if flags & OWN else flags | NO_WINDOW, *rest)

    _winapi.CreateProcess = quiet

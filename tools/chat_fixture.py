"""The chat fixture (tools/chatfixture, a chat window on the pinned Electron), started and read the way the app reads
another program's window.

    python tools/chat_fixture.py --home <folder> [--variant named|bare|both] [--corner] [--record <file>]

Fetches the pinned Electron into the home (mibo/host/launch.py) when it is not there, starts the fixture with each
variant, reads its window once through the program layer (mibo/desk/uia.View, which wakes a Chromium-drawn window
first) and prints how many named controls it found, whether the avatar of the program's own agent was among them,
and the look itself. The window is closed at once. With --corner it shows small in a corner and never takes the
focus, so it can run on someone's own desktop; it presses nothing and types nothing.

--record also reads every state the errand passes through (each chat shown, the fixture opened on it) with the
window's marked picture, into one JSON file that tools/chatfixture_errand.py replays without a window.

Windows only. Exit 0 when every variant read its controls by name, 1 otherwise; the last lines say what was found.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURE = ROOT / "tools" / "chatfixture"
TITLE = "Mibo test fixture"
CHATS = ["Daily notes", "Travel plan", "Code review", "Reading list"]
MAIN = "main"


def start(home: Path, variant: str, log: Path, corner: bool = True, chat: str = "", popen=subprocess.Popen):
    """The fixture running with `variant` (and `chat` shown), and its window: (process, window handle). `popen`
    starts it (the sandbox starts it as its standard user)."""
    from mibo.desk import win32
    from mibo.host import launch
    electron = asyncio.run(launch.fetch(home))
    profile = Path(home) / "chatfixture-profile"
    cmd = [str(electron), str(FIXTURE), f"--variant={variant}", f"--log={log}", f"--profile={profile}"]
    cmd += ([f"--chat={chat}"] if chat else []) + (["--corner"] if corner else [])
    env = {k: v for k, v in os.environ.items() if k != "ELECTRON_RUN_AS_NODE"}
    low = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    proc = popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=low)
    end = time.monotonic() + 30
    while time.monotonic() < end:
        hwnd = next((h for h in win32.top_windows() if win32.pid_of(h) == proc.pid and win32.title_of(h) == TITLE
                     and win32.shown(h)), 0)
        if hwnd:
            return proc, hwnd
        time.sleep(0.1)
    stop(proc)
    raise RuntimeError("the fixture showed no window")


def stop(proc) -> None:
    """The fixture's main process ended; Electron's own processes go with it."""
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


def read(hwnd: int, pid: int, picture: bool = False) -> dict:
    """One look of the program layer at the window: the look, its named controls and its nameless ones, and with
    `picture` the window's picture with each id marked (base64 JPEG)."""
    from mibo.desk import uia
    view = uia.View(hwnd, pid)
    started = time.monotonic()
    state = view.look()
    named = sorted({n.name for n in view.nodes.values() if n.name})
    out = {"seconds": round(time.monotonic() - started, 2), "named": view.named, "controls": view.interactive,
           "names": named, "chats": [c for c in CHATS if c in named],
           # The avatar: by its name, or as a nameless control said to be round with the program's name under it.
           "agent": "Main agent" in named or any("round" in line and '"Lumen" under it' in line
                                                 for line in view.unnamed),
           "state": state, "unnamed": view.unnamed, "drawn": view.drawn,
           "nodes": {str(i): [n.role, n.name] for i, n in view.nodes.items()}}
    if picture:
        out["picture"] = view.picture()[0]
    return out


def record(home: Path, out: Path) -> None:
    """Every state of both variants, read off the window with nothing pressed: {variant: {chat: look}}."""
    looks: dict = {}
    for variant in ("named", "bare"):
        for chat in [*CHATS, MAIN]:
            proc, hwnd = start(home, variant, home / "record.log", True, chat)
            try:
                looks.setdefault(variant, {})[chat] = read(hwnd, proc.pid, picture=True)
            finally:
                stop(proc)
    out.write_text(json.dumps(looks, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--variant", default="both", choices=["named", "bare", "both"])
    parser.add_argument("--corner", action="store_true")
    parser.add_argument("--record", default="")
    args = parser.parse_args()
    home = Path(args.home)
    home.mkdir(parents=True, exist_ok=True)
    if args.record:
        record(home, Path(args.record))
        print(f"recorded {args.record}")
        return 0
    found = {}
    for variant in (["named", "bare"] if args.variant == "both" else [args.variant]):
        proc, hwnd = start(home, variant, home / f"chatfixture-{variant}.log", args.corner)
        try:
            out = read(hwnd, proc.pid)
        finally:
            stop(proc)
        print(out["state"])
        found[variant] = {k: v for k, v in out.items() if k in ("seconds", "named", "controls", "chats", "agent")}
    ok = all(len(v["chats"]) == len(CHATS) and v["agent"] for v in found.values())
    for variant, v in found.items():
        print(f"{variant}: {v['named']} named controls, {v['controls']} controls, chats {len(v['chats'])}/4, "
              f"agent {'found' if v['agent'] else 'not found'}, read in {v['seconds']}s")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

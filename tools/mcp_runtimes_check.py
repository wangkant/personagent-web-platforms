"""What a person without Node.js or uv meets when they add a local MCP server, measured on a scratch home.

    python tools/mcp_runtimes_check.py --home F:/scratch/home --port 59323 [--install]

It starts the app with no window on a computer that, as far as the app can see, has neither (PATH holds only the
system folders, the user folder is one inside the home), adds `uvx mcp-server-time` and
`npx -y @modelcontextprotocol/server-memory`, allows both to start once, and prints each row as Settings receives
it. With --install it then presses the row's download for each runtime it names and prints how long the download
took, its size on disk, and how long until both servers were connected. The app is stopped at the end.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

SERVERS = {"mcpServers": {"time": {"command": "uvx", "args": ["mcp-server-time"]},
                          "memory": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]}}}


def call(base: str, token: str, path: str, body: dict | None = None, timeout: float = 600) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{base}/api/{path}", data=data, method="POST" if data else "GET",
                                     headers={"Content-Type": "application/json", "x-mibo-setup-token": token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as answer:
            return json.loads(answer.read().decode())
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode()[:300]}


def rows(view: dict) -> dict:
    return {r["name"]: {"state": r["state"], "error": r["error"], "need": r.get("need", ""),
                        "tools": len(r["tools"])} for r in view["servers"]}


def wait_rows(base: str, token: str, done, limit: float) -> tuple[dict, float]:
    began = time.monotonic()
    view = call(base, token, "mcp")
    while not done(view) and time.monotonic() - began < limit:
        time.sleep(1)
        view = call(base, token, "mcp")
    return view, round(time.monotonic() - began, 1)


def size_of(folder: Path) -> int:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) if folder.exists() else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--install", action="store_true")
    a = parser.parse_args()
    home = Path(a.home).resolve()
    user = home / "user"
    user.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mibo import settingsfile
    if not settingsfile.path(home).exists():
        settingsfile.write(home, {"UPDATE_CHECK_ENABLED": "false", "ROAM_ENABLED": "false", "AGENT_LANG": "zh",
                                  "PERSONA_NAME": "Mibo", "PAGE_PORT": a.port})
    if os.name == "nt":
        system = os.environ["SYSTEMROOT"]
        path = os.pathsep.join([system + r"\System32", system, system + r"\System32\Wbem"])
    else:
        path = "/usr/bin:/bin"
    env = {**os.environ, "AGENT_HOME": str(home), "USERPROFILE": str(user), "HOME": str(user),
           "APPDATA": str(user / "AppData" / "Roaming"), "LOCALAPPDATA": str(user / "AppData" / "Local"),
           "PATH": path}
    app = subprocess.Popen([sys.executable, "-c", "import sys; from mibo import welcome; "
                            f"sys.exit(welcome.main(['--no-browser', '--port', '{a.port}']))"],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report: dict = {}
    try:
        link = home / "runtime" / "desktop" / "welcome.json"
        for _ in range(240):
            if link.exists() and app.poll() is None:
                try:
                    url = json.loads(link.read_text(encoding="utf-8"))["url"]
                    break
                except (ValueError, KeyError):
                    pass
            time.sleep(0.5)
        parts = urlsplit(url)
        base, token = f"{parts.scheme}://{parts.netloc}", parts.fragment.partition("token=")[2]
        call(base, token, "mcp", {"add": json.dumps(SERVERS)})
        for name in SERVERS["mcpServers"]:
            call(base, token, "mcp", {"start": name, "answer": "once"})
        view, took = wait_rows(base, token, lambda v: all(r["state"] in ("failed", "connected")
                                                          for r in v["servers"]), 120)
        report["before"] = {"rows": rows(view), "seconds": took, "runtimes": view.get("runtimes")}
        if a.install:
            for need in sorted({r["need"] for r in rows(view).values() if r["need"]}):
                began = time.monotonic()
                call(base, token, "mcp", {"runtime": need})
                done, _ = wait_rows(base, token, lambda v, n=need: v["runtimes"][n]["state"] != "fetching", 600)
                report[need] = {"seconds": round(time.monotonic() - began, 1), **done["runtimes"][need],
                                "bytes_on_disk": size_of(home / "runtime" / "bin" / need)}
            view, took = wait_rows(base, token, lambda v: all(r["state"] == "connected" for r in v["servers"]), 300)
            report["after"] = {"rows": rows(view), "seconds_to_connected": took}
    finally:
        try:
            call(base, token, "exit", {}, timeout=30)
        except Exception:
            pass
        try:
            app.wait(30)
        except subprocess.TimeoutExpired:
            app.kill()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Smoke-test an installed `mibo` command: it answers --version with the version it was built as.

The app itself (its page, a saved key, a chat turn, a clean exit) is run by tools/welcome_smoke.py, which
takes its stand-in model (FakeModel) and its clean environment (_env) from here.

    python tools/package_smoke.py --mibo .venv/bin/mibo --version 1.0.0
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler

REPLY = "hey there"
# Settings a developer's shell may carry that would override the test home's settings.
_SCRUBBED_PREFIXES = ("LLM_", "AGENT_", "PERSONA_", "ADMIN_", "EMBEDDING_", "VISION_", "TAVILY_", "BENCH_", "EVAL_")


class FakeModel(BaseHTTPRequestHandler):
    """An OpenAI-compatible server that answers every call with REPLY, so nothing leaves this machine."""
    calls: list[str] = []

    def log_message(self, *args) -> None:
        pass

    def _send(self, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self.calls.append(self.path)
        self._send({"object": "list", "data": [{"id": "fake-model", "object": "model"}]})

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.calls.append(self.path)
        if self.path.endswith("/embeddings"):
            self._send({"data": [{"embedding": [0.1] * 8, "index": 0}]})
            return
        self._send({"choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": REPLY}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}})


def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(_SCRUBBED_PREFIXES)}
    env["PYTHONIOENCODING"] = "utf-8"
    env["UPDATE_CHECK_ENABLED"] = "false"  # offline: no question about a newer release either
    return env


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--mibo", default=shutil.which("mibo") or "",
                   help="the installed command (default: mibo on PATH)")
    p.add_argument("--version", dest="expect_version", default="",
                   help="the version `mibo --version` must print")
    args = p.parse_args(argv)
    if not args.mibo:
        p.error("no mibo command found; pass --mibo")
    cmd = [shutil.which(args.mibo) or args.mibo, "--version"]
    print("$", " ".join(cmd), flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                            env=_env())
    said = result.stdout.strip()
    print("  " + (said + result.stderr.strip())[:2000])
    ok = result.returncode == 0 and said.startswith(f"mibo {args.expect_version}".strip())
    print("smoke:", "all steps passed" if ok else "FAILED version")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""Which tool the reply model reaches for first, for the everyday requests about pages and programs, with nothing
carried out: the app runs windowless on a scratch home (tools/everyday.py's headless run), every model call goes
through a pass-through that writes down the first round's calls of each turn and hands the app a plain answer in
their place, so no page opens, no program starts and nothing is changed.

    python tools/firstcall.py <scratch home> --places <folder> --out <folder> [--port 59923] [--budget 0.03]

The home is one tools/everyday.py prepare made. results.json in <folder> holds each request's first calls and
whether they went to a wrong surface (everyday.surface_of against the request's own wrong surfaces); the last
line printed sums them."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import everyday  # noqa: E402

# The requests about a page or a program that need no earlier step of their own.
ASKED = ("E02", "E04", "E06", "E17", "E18", "E30", "E31")
PLAIN = "(first call written down; nothing was done)"


class FirstCall(everyday.Meter):
    """The meter's accounting, with each answer that calls tools handed to the app as a plain one once noted."""

    def serve(self, port: int) -> None:
        meter = self

        class Pass(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                path = self.path[3:] if meter.upstream.endswith("/v1") and self.path.startswith("/v1") else self.path
                headers = {k: v for k, v in self.headers.items() if k.lower() in ("authorization", "content-type")}
                began = time.time()
                status, data = 429, json.dumps({"error": {"message": "budget spent"}}).encode()
                if meter.spent < meter.budget:
                    try:
                        with urllib.request.urlopen(urllib.request.Request(meter.upstream + path, data=body,
                                                                           headers=headers), timeout=300) as answer:
                            status, data = answer.status, answer.read()
                    except urllib.error.HTTPError as error:
                        status, data = error.code, error.read()
                    meter.note(began, status, body, data)
                got = json.loads(data) if status == 200 else {}
                if (got.get("choices") or [{}])[0].get("message", {}).get("tool_calls"):
                    got["choices"][0]["message"] = {"role": "assistant", "content": PLAIN}
                    got["choices"][0]["finish_reason"] = "stop"
                    data = json.dumps(got).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", port), Pass)
        threading.Thread(target=server.serve_forever, daemon=True).start()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("home")
    parser.add_argument("--places", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=59923)
    parser.add_argument("--budget", type=float, default=0.03)
    args = parser.parse_args(argv)
    from mibo import settingsfile
    home, out = Path(args.home).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    upstream = settingsfile.values(home, keys=False).get("LLM_BASE_URL") or ""
    env_file = settingsfile.path(home)
    kept = env_file.read_text(encoding="utf-8")
    meter = FirstCall(upstream, out / "model.jsonl", args.budget)
    meter.serve(args.port)
    settingsfile.write(home, {"LLM_BASE_URL": f"http://127.0.0.1:{args.port}/v1"})
    wanted = [r for r in everyday.REQUESTS if r["id"] in ASKED]
    rows: list[dict] = []

    def steps(d) -> None:
        for request in wanted:
            seen = len(meter.rows)
            everyday.app_api(home, "chat", {"text": request["say"]}, timeout=300)
            first = next((r for r in meter.rows[seen:] if r["tools"]), {})
            called = first.get("called") or []
            wrong = [name for name, sent in called if everyday.surface_of(name, sent) in request["wrong"]]
            rows.append({"id": request["id"], "say": request["say"], "called": called, "wrong": wrong})
            d.log(json.dumps(rows[-1], ensure_ascii=False))

    box: dict = {}
    everyday.stand_ins(Path(args.places).resolve(), box, windows=False)
    try:
        everyday.headless(home, steps, out)
    finally:
        env_file.write_text(kept, encoding="utf-8")
    (out / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    calls = sum(len(r["called"]) for r in rows)
    print(f"firstcall: {len(rows)} requests, {calls} first calls, {sum(len(r['wrong']) for r in rows)} on a wrong "
          f"surface, {sum(1 for r in rows if not r['called'])} answered with no call, ${meter.spent:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

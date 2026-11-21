"""The main chat's context budget and count (mibo/context.py), measured.

    python tools/context_check.py room
    python tools/context_check.py record <port> <upstream base URL file> <out.jsonl> [--budget 0.06]
    python tools/context_check.py count <out.jsonl>...

`room` sends one tool round and one plain reply to a stand-in model on this computer for windows of 16k, 64k and
none stated, thinking off and on, and prints the max_tokens each sent. `record` is a pass-through for a home's
LLM_BASE_URL that keeps every chat request with the usage its answer reported (never the Authorization header) and
refuses calls once the costs the answers report pass --budget dollars. `count` reads those records and prints the
estimator's error against the reported prompt_tokens on every reply request (the requests with tools): the whole
request and the change from the request before, before calibration (the first ratio) and after (the ratio learned
from the requests before it, as the app learns it). The change's error is also given over the whole request, which is
what the budget's safety margin (W/32) has to hold."""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mibo import abilities, context  # noqa: E402
from mibo.endpoints import chat_completions_url  # noqa: E402
from mibo.llm import FROM_BUDGET, ModelCalls, purpose  # noqa: E402


def _serve(handler) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def room() -> list[dict]:
    sent: list[dict] = []

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            sent.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            data = json.dumps({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}],
                               "usage": {"prompt_tokens": 100, "completion_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = _serve(Stub)
    base = f"http://127.0.0.1:{server.server_address[1]}/v1"
    url = chat_completions_url(base)

    class Caller(ModelCalls):
        def __init__(self, runtime: Path):
            self.base_url, self.api_key, self.model = base, "k", "standin"
            self.llm_dm_model = self.llm_fallback_model = "standin"
            self.llm_fallback_base_url = self.llm_fallback_api_key = ""
            self.llm_fallback_thinking = False
            self.llm_timeout_s, self.api_max_retries = 30.0, 0
            self.llm_rate_limit_cooldown_s = self.llm_fallback_duration_s = 60
            self._http_pool, self._unreachable_until, self._fallback_until = {}, {}, {}
            self.memory_file = runtime / "memory.json"

    rows = []
    with tempfile.TemporaryDirectory() as scratch:
        for window in (16384, 65536, 0):
            for thinks in (False, True):
                runtime = Path(tempfile.mkdtemp(dir=scratch))
                rec = {"url": url, "model": "standin", "tools": "yes", "context": window,
                       "thinking": "yes" if thinks else "no",
                       "thinking_detail": {"reasons": thinks, "off": True, "param": "reasoning_effort" if thinks else ""}}
                (runtime / abilities.FILE).write_text(json.dumps({"models": {abilities.key_of(url, "standin"): rec}}))
                caller = Caller(runtime)
                sent.clear()
                with purpose("reply"):
                    await caller._tool_round("system", [{"role": "user", "content": "你好"}], "standin",
                                             [abilities._TOOL], timeout=30)
                    await caller._call_llm("system", [{"role": "user", "content": "你好"}], "standin",
                                           max_tokens=FROM_BUDGET)
                rows.append({"window": window or "unknown", "thinking": thinks,
                             "tool_round": sent[0]["max_tokens"], "plain": sent[1]["max_tokens"]})
    server.shutdown()
    return rows


def record(port: int, upstream_file: Path, out: Path, budget: float) -> None:
    upstream = upstream_file.read_text(encoding="utf-8").strip().rstrip("/")
    spent = [0.0]
    lock = threading.Lock()

    class Pass(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _relay(self, method: str) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if method == "POST" else None
            if method == "POST" and spent[0] >= budget:
                status, data = 429, json.dumps({"error": {"message": "budget spent"}}).encode()
            else:
                path = self.path[3:] if upstream.endswith("/v1") and self.path.startswith("/v1") else self.path
                headers = {k: v for k, v in self.headers.items() if k.lower() in ("authorization", "content-type")}
                try:
                    with urllib.request.urlopen(urllib.request.Request(upstream + path, data=body, headers=headers,
                                                                       method=method), timeout=300) as answer:
                        status, data = answer.status, answer.read()
                except urllib.error.HTTPError as error:
                    status, data = error.code, error.read()
                if method == "POST":
                    try:
                        sent, got = json.loads(body), json.loads(data)
                    except ValueError:
                        sent, got = {}, {}
                    usage = (got.get("usage") or {}) if isinstance(got, dict) else {}
                    with lock:
                        spent[0] += float(usage.get("cost") or 0)
                        with out.open("a", encoding="utf-8") as kept:
                            kept.write(json.dumps({"status": status, "body": sent, "usage": usage,
                                                   "spent": round(spent[0], 6)}, ensure_ascii=False) + "\n")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            self._relay("POST")

        def do_GET(self):
            self._relay("GET")

    ThreadingHTTPServer(("127.0.0.1", port), Pass).serve_forever()


def count(files: list[Path]) -> dict:
    replies = []
    for file in files:
        for line in file.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            # A reply round: tools and the character's system prompt (the model check's tool round has none).
            if (row["status"] == 200 and row["body"].get("tools") and row["body"]["messages"][0]["role"] == "system"
                    and (row["usage"] or {}).get("prompt_tokens")):
                replies.append((context.estimate(row["body"]), row["usage"]["prompt_tokens"], row["body"]["messages"]))
    whole_before, whole_after, delta_before, delta_after, delta_of_whole, in_turn = [], [], [], [], [], []
    r = None
    for n, (guess, reported, messages) in enumerate(replies):
        whole_before.append(abs(context.RATIO_FIRST * guess - reported) / reported)
        if r is not None:
            whole_after.append(abs(r * guess - reported) / reported)
        if n:
            last_guess, last_reported, last_messages = replies[n - 1]
            change = reported - last_reported
            delta_before.append(abs(context.RATIO_FIRST * (guess - last_guess) - change) / abs(change))
            if r is not None:
                delta_after.append(abs(r * (guess - last_guess) - change) / abs(change))
                delta_of_whole.append(abs(r * (guess - last_guess) - change) / reported)
                if messages[:len(last_messages)] == last_messages:
                    # A later round of the same turn: the request before it, with the round's call and results.
                    in_turn.append(abs(r * (guess - last_guess) - change) / reported)
        seen = reported / guess
        r = seen if r is None else (1 - context.RATIO_WEIGHT) * r + context.RATIO_WEIGHT * seen

    def summary(errors: list[float]) -> dict:
        return {"n": len(errors), "median": round(statistics.median(errors), 4), "max": round(max(errors), 4)}
    return {"requests": len(replies), "prompt_tokens": [p for _, p, _ in replies], "ratio_at_end": round(r, 3),
            "whole_before": summary(whole_before), "whole_after": summary(whole_after),
            "delta_before": summary(delta_before), "delta_after": summary(delta_after),
            "delta_after_over_whole": summary(delta_of_whole), "in_turn_delta_after_over_whole": summary(in_turn)}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="what", required=True)
    sub.add_parser("room")
    rec = sub.add_parser("record")
    rec.add_argument("port", type=int)
    rec.add_argument("upstream", type=Path)
    rec.add_argument("out", type=Path)
    rec.add_argument("--budget", type=float, default=0.06)
    cnt = sub.add_parser("count")
    cnt.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args()
    if args.what == "room":
        print(json.dumps(asyncio.run(room()), indent=1))
    elif args.what == "record":
        record(args.port, args.upstream, args.out, args.budget)
    else:
        print(json.dumps(count(args.files), indent=1))


if __name__ == "__main__":
    main()

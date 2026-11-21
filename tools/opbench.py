"""The operating benchmark: each task of tools/opsite/tasks.py sent through the chat, as a person would type it,
in the real app window, with the home's own model. The character works the page open in the app's browser; the
cards it raises are answered as the task says (yes, or no for the checkout); the site decides whether it was done,
and how each errand ended is read from the chat's own record of the reply (GET /api/chat/history). With the chat
beside the page and the page in its context the message is done in the page directly (tools/opcolumn.py makes
sure of that); away from the page the reply model chooses.

    python tools/opbench.py <scratch home> --out <folder> [--runs 3] [--tasks T01,T02] [--site-port 59630]
                            [--pictures]

The home must be a scratch folder (as tools/window_driver.py says): what the site taught it is forgotten before
each task, so every run starts as a stranger to the site. Model calls go through a small pass-through on this
computer that counts them; it keeps request bodies and answers in <out>/model.jsonl, never headers or the key.
The table lands in <out>/table.md and <out>/results.json: per task and run, success, model calls, input tokens,
seconds, cards shown and, when it failed, the kind of failure. The site, the counter and the window's stand-in
model take the site port and the two after it. `--pictures` says the home's model takes pictures; without it
the drawn page (T25) passes only when it is handed back with nothing sent."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "opsite"))
import tasks as bench  # noqa: E402

# How long one task may take, and the password the person types in R3 (a test value, never a real one).
TASK_S = 420
PASSWORD = "bench-Pw-4471"
# What a card must read back: the recipient and the text of what is sent, or the item a like or vote is for.
CARD_HAS = {"R1": ["Alice", "明天见"], "R4": ["Bob", "狗狗也会做梦吗"], "T01": ["周子涵", "周六上午十点见"],
            "T02": ["林小雨"], "T18": ["周末去哪儿"],
            "T03": ["今天天气真好"], "T04": ["Bob", "说得对"], "T11": ["8812"], "T12": ["Carol", "周日可以"],
            "T20": ["Merrell"], "T21": ["田中", "明日の会議"], "T22": ["喜欢猫和咖啡"], "T22x": ["喜欢猫和咖啡"],
            "M01": ["王芳", "周末一起爬山"], "M02": ["陈晨"], "M05": ["谢谢分享"]}


class Counter:
    """The pass-through to the model: every call's answer and usage, kept in order."""

    def __init__(self, upstream: str, log: Path):
        self.upstream, self.log, self.rows, self.lock = upstream.rstrip("/"), log, [], threading.Lock()

    def serve(self, port: int) -> int:
        counter = self

        class Pass(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                path = self.path[3:] if counter.upstream.endswith("/v1") and self.path.startswith("/v1") else self.path
                headers = {k: v for k, v in self.headers.items() if k.lower() in ("authorization", "content-type")}
                began = time.time()
                try:
                    with urllib.request.urlopen(urllib.request.Request(counter.upstream + path, data=body,
                                                                       headers=headers), timeout=300) as answer:
                        status, data = answer.status, answer.read()
                except urllib.error.HTTPError as error:
                    status, data = error.code, error.read()
                except Exception as error:
                    status, data = 502, json.dumps({"error": {"message": type(error).__name__}}).encode()
                try:
                    sent, got = json.loads(body), json.loads(data)
                except ValueError:
                    sent, got = {}, {}
                row = {"at": round(began, 1), "s": round(time.time() - began, 1), "status": status,
                       "model": sent.get("model"), "tools": bool(sent.get("tools")),
                       "json": bool(sent.get("response_format")), "messages": sent.get("messages"),
                       "answer": (got.get("choices") or [{}])[0].get("message") if isinstance(got, dict) else None,
                       "usage": got.get("usage") if isinstance(got, dict) else None}
                with counter.lock:
                    counter.rows.append(row)
                    with counter.log.open("a", encoding="utf-8") as out:
                        out.write(json.dumps(row, ensure_ascii=False) + "\n")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", port), Pass)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server.server_address[1]


def _get(url: str, data: dict | None = None, timeout: float = 30) -> dict:
    request = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                     method="GET" if data is None else "POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as answer:
        return json.loads(answer.read().decode())


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("home")
    parser.add_argument("--out", required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--tasks", default="")
    parser.add_argument("--site-port", type=int, default=59630)
    parser.add_argument("--model", default="", help="another model for these runs (LLM_MODEL)")
    parser.add_argument("--pictures", action="store_true", help="the model takes pictures")
    args = parser.parse_args(argv)
    home, out = Path(args.home).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    wanted = [t for t in bench.TASKS if not args.tasks or t["id"] in args.tasks.split(",")]

    from mibo import settingsfile
    # The agent takes its model from the home's settings: for the run it points at the counter, and is put back after.
    upstream = settingsfile.values(home, keys=False).get("LLM_BASE_URL") or ""
    env_file = settingsfile.path(home)
    kept = env_file.read_text(encoding="utf-8")
    if upstream.startswith(f"http://127.0.0.1:{args.site_port + 1}/"):
        # A run killed before it put the settings back: the counter would call itself.
        raise SystemExit("the home's LLM_BASE_URL still points at the counter of a run that was stopped; put it back")
    counter = Counter(upstream, out / "model.jsonl")
    through = {"LLM_BASE_URL": f"http://127.0.0.1:{counter.serve(args.site_port + 1)}/v1",
               **({"LLM_MODEL": args.model} if args.model else {})}
    settingsfile.write(home, through)
    site = f"http://127.0.0.1:{args.site_port}"
    server = subprocess.Popen([sys.executable, str(HERE / "opsite" / "serve.py"), str(args.site_port),
                               str(out / "site.log")])
    results: list[dict] = []
    try:
        import window_driver

        # The window's stand-in model (unused here, the home has its own) on a port next to the site's too.
        window_driver.ThreadingHTTPServer = lambda address, handler: ThreadingHTTPServer(
            (address[0], args.site_port + 2), handler)
        failed = window_driver.run(home, lambda d: _walk(d, home, site, wanted, args.runs, counter, out, results,
                                                         args.pictures), out=out, lang="zh")
    finally:
        server.terminate()
        env_file.write_text(kept, encoding="utf-8")
    _table(results, out)
    return failed


def _walk(d, home: Path, site: str, wanted: list[dict], runs: int, counter: Counter, out: Path,
          results: list[dict], pictures: bool) -> None:
    from mibo.web import sites

    def api(path: str, body: dict | None = None) -> dict:
        url = json.loads((home / "runtime" / "desktop" / "welcome.json").read_text(encoding="utf-8"))["url"]
        base, _, token = url.partition("#token=")
        request = urllib.request.Request(base.rstrip("/") + "/api/" + path, method="GET" if body is None else "POST",
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "x-mibo-setup-token": token})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=30) as answer:
            return json.loads(answer.read().decode())

    def pane(expression: str):
        answer = d.app.pane.cdp("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return (answer.get("result") or {}).get("value")

    def card_text() -> str:
        return d.js("(() => { const c = document.querySelector('.op-card:not(.answered)'); "
                    "return c ? c.innerText : ''; })()") or ""

    def ask(text: str) -> None:
        # Into the box of the chat on screen, which stands beside the page where the window is wide enough for both.
        d.js("window.paAsk(%s)" % json.dumps(text))
        time.sleep(1.2)
        d.click(".chat-send")

    def wait(task: dict, cards: list[str], presses: list[tuple[float, bool]]) -> None:
        began = time.time()
        time.sleep(2)
        while time.time() - began < TASK_S:
            text = card_text()
            # Every new card is answered, the same words again included (a card shown anew after a change).
            if text and text != "<<page is busy>>" and d.js("(() => { const c = document.querySelector("
                                                            "'.op-card:not(.answered)'); return !!c && !c.dataset.bench; })()"):
                cards.append(text)
                d.shot(f"{task['id']}-card")
                no = task.get("deny") and re.search(task["deny"], text)
                # When the press was made: an effect the site got before it went out without the card.
                presses.append((time.time(), not no))
                d.click(".op-card:not(.answered) .pa-btn-%s" % ("secondary" if no else "primary"))
                d.js("(() => { const c = document.querySelector('.op-card'); if (c) c.dataset.bench = '1'; })()")
                d.log(f"   card ({'no' if no else 'yes'}): {' / '.join(text.split())[:200]}")
            if not d.js("!!document.querySelector('.line.typing')"):
                return
            time.sleep(1)

    # The character lives in the page's own process: it is there once the page answers.
    api("look/toured", {})
    time.sleep(3)
    for n in range(1, runs + 1):
        for task in wanted:
            d.log(f"== {task['id']} run {n}: {task['kind']}")
            _get(site + "/__reset", {})
            sites.forget(home / "runtime", site + "/")
            # A stranger each time: what the chat remembers of the run before is forgotten, so an answer read off
            # a page cannot come from memory.
            try:
                api("chat/forget", {})
            except (urllib.error.URLError, OSError) as error:
                d.log(f"   chat/forget failed ({error}); the chat is only started anew")
                api("chat/reset", {})
            d.js("location.reload()")
            time.sleep(6)
            d.app.open_link(site + task["start"])
            time.sleep(4)
            mark_rows = len(counter.rows)
            began = time.time()
            cards: list[str] = []
            presses: list[tuple[float, bool]] = []
            ask(task["ask"])
            wait(task, cards, presses)
            if task.get("password"):
                # The person types their own password where the character left the focus, and says go on.
                focus = pane("document.activeElement && document.activeElement.id")
                d.log(f"   handed over with the focus on {focus!r}")
                if focus == task["password"]:
                    pane("document.getElementById(%s).value = %s" % (json.dumps(task["password"]), json.dumps(PASSWORD)))
                    ask("继续")
                    wait(task, cards, presses)
            seconds = round(time.time() - began)
            said = [m for m in api("chat/history").get("messages", []) if m.get("role") != "user"]
            reply = " ".join(m.get("text", "") for m in said[-4:])
            verdict = _get(site + f"/__check?task={task['id']}")
            # How each errand in the page ended, as the chat keeps it under the reply (forgotten before each task).
            outcomes = [t["status"] for m in said for t in m.get("trace") or [] if t.get("tool") == "operate"]
            rows = counter.rows[mark_rows:]
            row = {"task": task["id"], "kind": task["kind"], "run": n, "seconds": seconds, "cards": len(cards),
                   "calls": len(rows), "operate_calls": sum(1 for r in rows if r["json"] and not r["tools"]),
                   "in_tokens": sum(((r.get("usage") or {}).get("prompt_tokens") or 0) for r in rows),
                   "cost": round(sum(((r.get("usage") or {}).get("cost") or 0) for r in rows), 5),
                   "errors": sum(1 for r in rows if r["status"] != 200), "outcomes": outcomes,
                   "site": verdict, "reply": reply[-300:], "card_texts": [" / ".join(c.split())[:300] for c in cards],
                   "presses": [[round(at, 2), yes] for at, yes in presses]}
            row["ok"], row["failure"] = _judge(task, row, verdict, reply, cards, pictures)
            results.append(row)
            d.log(f"   {'PASS' if row['ok'] else 'FAIL ' + row['failure']}: {verdict['why'][:160]} | "
                  f"calls {row['calls']} in {row['in_tokens']} | {seconds} s | cards {len(cards)} | {outcomes}")
            d.log(f"   reply: {reply[-200:]!r}")
            (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
            if task.get("password"):
                pane("document.getElementById(%s) && (document.getElementById(%s).value = '')"
                     % (json.dumps(task["password"]), json.dumps(task["password"])))


def _judge(task: dict, row: dict, verdict: dict, reply: str, cards: list[str], pictures: bool) -> tuple[bool, str]:
    """Success is the site's verdict, the read-off answer in the reply, and the cards a correct run shows; the
    failure's kind comes from how the errand ended. A drawn page with a text-only model: handed back, nothing
    sent.

    Under automatic review (acting.DEFAULT) the app's reviewer may pass an act the person asked for with no card:
    effects with no card shown at all are that pass, and the card count and read-back are not asked of the run.
    Not where a card must come whatever the reviewer says (`hard`: a password form, a switch that saves a setting,
    acting.HARD) or where the person would say no (`deny`)."""
    reviewed = not cards and bool(verdict.get("effects")) and not task.get("hard") and not task.get("deny")
    # Each effect the site got needs a yes pressed before it: the k-th effect, k yeses before it came.
    yeses = sorted(at for at, yes in row.get("presses", []) if yes)
    for k, at in enumerate(sorted(verdict.get("effects", []))):
        if not reviewed and sum(1 for y in yeses if y < at) < k + 1:
            return False, "effect_before_card"
    if task.get("vision") and not pictures:
        sent = not verdict["why"].startswith("0 POSTs")
        if row["outcomes"] and row["outcomes"][-1] == "handover" and not sent:
            return True, ""
        return False, "no_tool" if not row["outcomes"] else ("extra_effect" if sent else row["outcomes"][-1])
    if not row["outcomes"]:
        return False, "no_tool" if not verdict["ok"] else "no_tool_but_done"
    if verdict["ok"] and not reviewed and len(cards) < task.get("cards", 0):
        return False, "no_card"
    # One card the person said yes to (or any, when none was meant to go) reads back all of it: words spread
    # over two cards (a name on one, the text on another) are not a card that showed what went.
    presses = row.get("presses", [])
    said_yes = [" ".join(c.split()) for c, (_, yes) in zip(cards, presses) if yes] or [" ".join(c.split()) for c in cards]
    if verdict["ok"] and not reviewed and CARD_HAS.get(task["id"]) and not any(
            all(w in c for w in CARD_HAS[task["id"]]) for c in said_yes):
        return False, "card_wrong"
    if verdict["ok"] and not all(w in reply for w in verdict.get("reply_has", [])):
        return False, "wrong_answer"
    if verdict["ok"]:
        return True, ""
    if re.search(r"unexpected POSTs|\b[2-9] POSTs", verdict["why"]):
        return False, "extra_effect"
    last = row["outcomes"][-1]
    if last in ("cap", "handover", "asked", "declined", "stopped", "paused", "failed"):
        return False, last
    return False, "done_but_not" if "0 POSTs" in verdict["why"] or "reads=" in verdict["why"] else "wrong_effect"


def _table(results: list[dict], out: Path) -> None:
    by_task: dict[str, list[dict]] = {}
    for row in results:
        by_task.setdefault(row["task"], []).append(row)
    lines = ["| task | kind | success | calls | input tokens | seconds | cards | failures |",
             "|---|---|---|---|---|---|---|---|"]
    cost = sum(r.get("cost") or 0 for r in results)
    for task, rows in by_task.items():
        avg = lambda key: round(sum(r[key] for r in rows) / len(rows))  # noqa: E731
        fails = ", ".join(r["failure"] for r in rows if not r["ok"])
        lines.append(f"| {task} | {rows[0]['kind']} | {sum(r['ok'] for r in rows)}/{len(rows)} | {avg('calls')} | "
                     f"{avg('in_tokens')} | {avg('seconds')} | {avg('cards')} | {fails} |")
    done = sum(r["ok"] for r in results)
    lines.append(f"\n{done}/{len(results)} runs passed; the model's own count of what they cost: ${cost:.4f}.")
    (out / "table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

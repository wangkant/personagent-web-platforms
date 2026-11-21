"""The chat battery on the real model, with no window: each turn of a turns file sent through the app's own page
API, the model calls counted by everyday.py's pass-through, and a few plain checks read from the replies.

    python tools/chatbench.py <home> <turns.jsonl> --out <folder> [--budget 0.10] [--meter-port 59903]
    python tools/chatbench.py table <folder>          the table again from a run's saved results

A turns file has one {"label", "chat", "text"} a line; "main" is the main chat, any other name a new plain chat.
The home's .env is used as it is (its LLM_BASE_URL is pointed at the pass-through for the run and put back)."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from everyday import PRICE, Meter, app_api, no_windows  # noqa: E402

CJK = re.compile(r"[一-鿿]")


def _has(text: str, *patterns: str) -> bool:
    return all(re.search(p, text) for p in patterns)


# What a reply must show, by label: the arithmetic and the language of the battery's turns.
CHECKS = {
    "c03": lambda r: _has(r, r"52", r"(1|一|2|两|二)\s*(天|day)"),
    "c08": lambda r: not CJK.search(r),
    "c09": lambda r: _has(r, r"52", r"336"),
    "c14": lambda r: _has(r, r"2028", r"2100"),
    "c15": lambda r: _has(r, r"52", r"(1|一|2|两|二)\s*(天|day)"),
    "s04": lambda r: not CJK.search(r),
}


def _turns(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("label") and r.get("text")]


def walk(home: Path, turns: list[dict], meter: Meter, out: Path) -> None:
    info = home / "runtime" / "desktop" / "welcome.json"
    results = []
    try:
        for _ in range(240):
            if info.exists() and json.loads(info.read_text(encoding="utf-8")).get("url"):
                break
            time.sleep(0.5)
        app_api(home, "look/toured", {})
        chats: dict[str, str] = {}
        for turn in turns:
            if turn["chat"] != "main" and turn["chat"] not in chats:
                chats[turn["chat"]] = app_api(home, "chats", {})["id"]
            seen = len(meter.rows)
            began = time.time()
            answer = app_api(home, "chat", {"text": turn["text"], "chat": chats.get(turn["chat"], "main")}, timeout=900)
            rows = meter.rows[seen:]
            entries = [m for m in answer.get("messages") or [] if m.get("role") != "user"]
            reply = str(answer.get("reply") or "")
            check = CHECKS.get(turn["label"])
            results.append({
                "label": turn["label"], "chat": turn["chat"], "seconds": round(time.time() - began, 1),
                "calls": sum(1 for r in rows if r["tools"]), "plain_calls": sum(1 for r in rows if not r["tools"]),
                "json_calls": sum(1 for r in rows if r["json"]),
                "tools_called": [name for r in rows for name, _ in r["called"]],
                "trace": [s.get("tool") for e in entries for s in e.get("trace") or []],
                "cost": round(sum(r["cost"] for r in rows), 5), "reply": reply,
                "check": None if check is None else bool(check(reply)), "http": answer.get("http")})
            (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        app_api(home, "exit", {})


def table(results: list[dict], out: Path, spent: float) -> str:
    lines = ["| label | chat | model calls | plain calls | JSON calls | tools | check | seconds | cost | reply |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        check = "" if r["check"] is None else ("pass" if r["check"] else "fail")
        reply = " ".join(r["reply"].split())[:80].replace("|", "/")
        lines.append(f"| {r['label']} | {r['chat'][:8]} | {r['calls']} | {r['plain_calls']} | {r['json_calls']} | "
                     f"{', '.join(r['tools_called'])} | {check} | {r['seconds']} | ${r['cost']:.4f} | {reply} |")
    checked = [r for r in results if r["check"] is not None]
    calls = sum(r["calls"] + r["plain_calls"] for r in results)
    lines.append("")
    lines.append(f"{len(results)} turns; {calls} model calls; checks {sum(r['check'] for r in checked)}/{len(checked)}; "
                 f"turns answered in one call {sum(r['calls'] + r['plain_calls'] == 1 for r in results)}; "
                 f"cost ${spent:.4f}.")
    text = "\n".join(lines) + "\n"
    (out / "table.md").write_text(text, encoding="utf-8")
    return text


def main(argv: list[str]) -> int:
    if argv[:1] == ["table"]:
        # The table again from a run's results, under the checks as they are now.
        out = Path(argv[1])
        results = json.loads((out / "results.json").read_text(encoding="utf-8"))
        for r in results:
            r["check"] = None if r["label"] not in CHECKS else bool(CHECKS[r["label"]](r["reply"]))
        print(table(results, out, sum(r["cost"] for r in results)))
        return 0
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("home")
    parser.add_argument("turns")
    parser.add_argument("--out", required=True)
    parser.add_argument("--budget", type=float, default=0.10)
    parser.add_argument("--price-in", type=float, default=PRICE[0])
    parser.add_argument("--price-out", type=float, default=PRICE[2])
    parser.add_argument("--meter-port", type=int, default=59903)
    args = parser.parse_args(argv)
    home, out = Path(args.home).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    from dotenv import dotenv_values
    env_file = home / ".env"
    kept = env_file.read_text(encoding="utf-8")
    upstream = dotenv_values(env_file).get("LLM_BASE_URL") or ""
    if upstream.startswith(f"http://127.0.0.1:{args.meter_port}/"):
        raise SystemExit("the home's LLM_BASE_URL still points at the pass-through of a run that was stopped; put it back")
    meter = Meter(upstream, out / "model.jsonl", args.budget, (args.price_in, args.price_in, args.price_out))
    meter.serve(args.meter_port)
    lines = [line for line in kept.splitlines() if line.partition("=")[0].strip() != "LLM_BASE_URL"]
    env_file.write_text("\n".join(lines + [f"LLM_BASE_URL=http://127.0.0.1:{args.meter_port}/v1"]) + "\n",
                        encoding="utf-8")
    os.environ["AGENT_HOME"] = str(home)
    no_windows()
    (home / "runtime" / "desktop" / "welcome.json").unlink(missing_ok=True)
    threading.Thread(target=walk, args=(home, _turns(Path(args.turns)), meter, out), daemon=True).start()
    try:
        from mibo import welcome
        port = dotenv_values(env_file).get("PAGE_PORT")
        welcome.main(["--no-browser", *(["--port", port] if port else [])])
    finally:
        env_file.write_text(kept, encoding="utf-8")
    results = json.loads((out / "results.json").read_text(encoding="utf-8")) if (out / "results.json").exists() else []
    print(table(results, out, meter.spent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

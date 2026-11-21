"""Tasks of tools/opsite worked by web/operate.run with a real model, in headless Edge with no window and no chat
model in front: how far a longer errand gets, in how many calls, tokens and seconds. Each card is answered yes (no
where the task's `deny` matches its button); the site's own verdict (tasks.check) decides, and a task that reads
something off also needs those words in the run's result.

    python tools/oploop.py --env <.env with LLM_API_KEY, LLM_BASE_URL> [--model deepseek/deepseek-v4-flash]
        [--tasks M05,M06] [--runs 1] [--tools yes|no] [--context N] [--cheapest] [--out <folder>] [--port 59665]
        [--budget 0.03] [--tree <folder holding mibo>] [--review]

The run is given the model as the app's check measured it (abilities.py): `--tools` is what that check found
(yes: it called the check's tool by itself), and the context window is read from the host's own model list, as
the check reads it, so the run's ceiling, page and answer room are operate.budget's for that model; `--context 0`
runs it as the least able model (a context window the host does not state). A task of tasks.REAL starts on a real
page a visitor reads signed out: it passes when its words are in the result and no card came. `--tree` runs another
copy of the code (a snapshot from before a change). One line a run; <out>/results.jsonl keeps them with each run's
steps. `--review` puts what the gate holds to the app's reviewer (review.decide on the same model) before any
card, as the app's automatic review does: each verdict is kept with the run, and `looks` counts the reviewer's calls
and the cards together. The key is never printed or written."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import io
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def one(browser, site: str, task: dict, model, able: dict, out: Path, operate, gate, tasks,
              reviewer=None) -> dict:
    from mibo import review
    from mibo.web.tabs import PlaywrightTab
    real = task["start"].startswith("http")
    if not real:
        urllib.request.urlopen(urllib.request.Request(site + "/__reset", data=b"{}"), timeout=10).read()
    context = await browser.new_context(viewport={"width": 1100, "height": 760}, locale="zh-CN")
    page = await context.new_page()
    tab = PlaywrightTab(page)
    await tab.goto(task["start"] if real else site + task["start"])
    cards: list[dict] = []
    calls: list[dict] = []

    async def ask(card: dict) -> dict:
        no = bool(task.get("deny")) and bool(re.search(task["deny"], str(card.get("button") or "")))
        cards.append({k: card.get(k, "") for k in ("kind", "button", "to", "text")} | {"yes": not no})
        return {"yes": not no, "never": False}

    async def ask_model(system: str, messages: list[dict]) -> str:
        return await model.ask(system, messages, calls)

    verdicts: list[dict] = []

    async def judge(action: dict):
        # The person's own message of this task, as acting.asked gives the app's reviewer.
        verdict = await review.decide("browser", [{"text": task["ask"], "at": time.time()}], action, ask=reviewer,
                                      attended=True, lang="zh", tally=review.Tally())
        verdicts.append({"kind": action.get("kind"), "decision": verdict.decision, "by": verdict.by,
                         "reason": verdict.reason})
        return verdict

    hands = operate.Hands(tab, ask=ask, allowed=gate.Allowed(out / "allow"), attended=lambda: False, lang="zh",
                          review=judge if reviewer is not None else None)
    began = time.monotonic()
    try:
        outcome = await asyncio.wait_for(operate.run(ask_model, hands, task["ask"], said=task["ask"], able=able), 600)
    except asyncio.TimeoutError:
        outcome = {"status": "timeout", "result": "", "steps": []}
    finally:
        await hands.close()
    seconds = time.monotonic() - began
    await context.close()
    if real:
        verdict = {"ok": not cards, "why": f"{len(cards)} cards", "reply_has": task["reply_has"]}
    else:
        state = json.loads(urllib.request.urlopen(site + "/api/state", timeout=10).read())
        verdict = tasks.check(task["id"], state)
    result = str(outcome.get("result") or "")
    read = all(re.sub(r"\s+", "", w) in re.sub(r"\s+", "", result) for w in verdict.get("reply_has", []))
    return {"task": task["id"], "ok": verdict["ok"] and read, "site": verdict["ok"], "read": read,
            "why": verdict["why"][:200], "status": outcome.get("status"), "result": result[:300],
            "calls": len(calls), "in": sum(c["in"] for c in calls), "out": sum(c["out"] for c in calls),
            "cost": round(sum(c["cost"] for c in calls), 6), "seconds": round(seconds, 1),
            "cards": cards, "verdicts": verdicts, "looks": len(cards) + len(verdicts),
            "steps": outcome.get("steps", [])}


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True)
    parser.add_argument("--model", default="deepseek/deepseek-v4-flash")
    parser.add_argument("--tasks", default="M05,M06")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--tools", choices=("yes", "no"), default="yes")
    parser.add_argument("--context", type=int, default=-1, help="the context window, tokens (default: the host's)")
    parser.add_argument("--cheapest", action="store_true", help="route each call to the host's cheapest provider")
    parser.add_argument("--tree", default=str(HERE.parent))
    parser.add_argument("--out", default="")
    parser.add_argument("--port", type=int, default=59665)
    parser.add_argument("--budget", type=float, default=0.03, help="dollars; no new run past it")
    parser.add_argument("--review", action="store_true", help="the app's reviewer before any card")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(args.tree).resolve()))
    from mibo import review
    from mibo.web import gate, operate
    run = load("miniwob_run", HERE / "bench" / "miniwob" / "run.py")
    tasks = load("opsite_tasks", HERE / "opsite" / "tasks.py")
    from mibo import abilities
    env = run.read_env(Path(args.env))
    able = {"tools": args.tools,
            "context": args.context if args.context >= 0 else await abilities._context(
                env["LLM_BASE_URL"].rstrip("/") + "/chat/completions", "", args.model)}
    model = run.Model(env["LLM_BASE_URL"], env["LLM_API_KEY"], args.model, args.budget)
    if args.cheapest:
        model.extra = {"provider": {"sort": "price"}}
    # The answer's room as search.py gives it for this model.
    model.answer = operate.budget(able)["answer"]
    usage: dict = {}
    reviewer = review.endpoint(env["LLM_BASE_URL"], env["LLM_API_KEY"], args.model, usage=usage) if args.review else None
    out = Path(args.out or tempfile.mkdtemp(prefix="oploop-")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    site = f"http://127.0.0.1:{args.port}"
    server = subprocess.Popen([sys.executable, "-B", str(HERE / "opsite" / "serve.py"), str(args.port),
                               str(out / "site.log")])
    rows = []
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(site + "/api/state", timeout=2).read()
                break
            except OSError:
                time.sleep(0.25)
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel="msedge", headless=True)
            for n in range(args.runs):
                for name in args.tasks.split(","):
                    if model.spent >= args.budget:
                        print(f"budget reached: {name} run {n + 1} not run", flush=True)
                        continue
                    spent = usage.get("cost", 0.0)
                    row = await one(browser, site, tasks.BY_ID[name], model, able, out, operate, gate, tasks, reviewer)
                    # The reviewer's calls count in what the run cost.
                    row["cost"] = round(row["cost"] + usage.get("cost", 0.0) - spent, 6)
                    model.spent += usage.get("cost", 0.0) - spent
                    rows.append(row)
                    with (out / "results.jsonl").open("a", encoding="utf-8") as f:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    print(f"{'OK ' if row['ok'] else 'no '} {name} {row['status']:<9} calls={row['calls']} "
                          f"cards={len(row['cards'])} reviews={len(row['verdicts'])} in={row['in']} {row['seconds']}s "
                          f"${row['cost']:.4f}  {row['why'][:80]!r}", flush=True)
            await browser.close()
    finally:
        server.terminate()
        await model.client.aclose()
    print(f"{sum(r['ok'] for r in rows)}/{len(rows)} ok; able {able}; spent ${model.spent:.4f}; kept in {out}")
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    raise SystemExit(asyncio.run(main(sys.argv[1:])))

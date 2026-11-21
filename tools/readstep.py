"""The errand's `read` step alone, on the stand-in pages of tools/opsite, with a real model, in headless Edge with no
window: for each page a question whose answer is known, how long the read took, how many model calls and input
tokens it used, how far it scrolled, and whether the answer holds the known words.

    python tools/readstep.py --env <.env with LLM_API_KEY, LLM_BASE_URL> [--model deepseek/deepseek-v4.1-flash]
        [--pages W5,W1,W2,W3] [--context N] [--tree <folder holding mibo>] [--out <folder>] [--port 59851]
        [--budget 0.05]

The page is looked at once first, as a run does before its first step (no model call), then the read is made the
way the tree's run makes it: `operate.read_page` where the tree has it, else the read action and `_extract` (the
reader before 2026-11-21). `--context 0` reads as the least able model (a window the host does not state); by
default the window is the host's, as the app's check reads it. `--tree` runs another copy of the code. One line a
page; <out>/results.jsonl keeps them. Exits 0 when every answer holds its known words. The key is never printed
or written."""
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
# The pages read, by their W task in opsite/tasks.py (whose question is asked).
PAGES = ("W5", "W1", "W2", "W3")
# Watches the furthest any scroller went while the read ran (each is put back after), in pixels.
WATCH_JS = """() => { window.__miboFar = 0; document.addEventListener('scroll', (e) => {
  const t = e.target === document ? document.scrollingElement : e.target;
  window.__miboFar = Math.max(window.__miboFar, t.scrollTop); }, true); }"""


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def one(browser, site: str, task: dict, model, able: dict, out: Path, operate, gate, tasks) -> dict:
    from mibo.web.tabs import PlaywrightTab
    urllib.request.urlopen(urllib.request.Request(site + "/__reset", data=b"{}"), timeout=10).read()
    context = await browser.new_context(viewport={"width": 1100, "height": 760}, locale="zh-CN")
    page = await context.new_page()
    tab = PlaywrightTab(page)
    await tab.goto(site + task["start"])
    calls: list[dict] = []

    async def ask(card: dict) -> dict:
        return {"yes": False, "never": False}

    async def ask_model(system: str, messages: list[dict]) -> str:
        return await model.ask(system, messages, calls)

    hands = operate.Hands(tab, ask=ask, allowed=gate.Allowed(out / "allow"), attended=lambda: False, lang="zh")
    limits = operate.budget(able)
    question = task["ask"]
    try:
        await hands.look(limits["page"])
        await page.evaluate(WATCH_JS)
        began = time.monotonic()
        if hasattr(operate, "read_page"):
            got = await operate.read_page(ask_model, hands, {"do": "read", "question": question}, question, able)
            answer, status = got["answer"], got["status"]
        else:
            result = await hands.act({"do": "read", "question": question})
            status = result.get("status")
            answer, _ = await operate._extract(ask_model, question, str(result.get("text") or ""), limits["page"])
        seconds = time.monotonic() - began
        far = await page.evaluate("() => window.__miboFar")
    finally:
        await hands.close()
    state = json.loads(urllib.request.urlopen(site + "/api/state", timeout=10).read())
    want = tasks.check(task["id"], state)["reply_has"]
    right = all(re.sub(r"\s+", "", w) in re.sub(r"\s+", "", answer) for w in want)
    await context.close()
    return {"page": task["id"], "right": right, "status": status, "answer": answer[:300], "want": want,
            "calls": len(calls), "in": sum(c["in"] for c in calls), "out": sum(c["out"] for c in calls),
            "cost": round(sum(c["cost"] for c in calls), 6), "seconds": round(seconds, 1), "scrolled_px": far}


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True)
    parser.add_argument("--model", default="deepseek/deepseek-v4.1-flash")
    parser.add_argument("--pages", default=",".join(PAGES))
    parser.add_argument("--context", type=int, default=-1, help="the context window, tokens (default: the host's)")
    parser.add_argument("--tree", default=str(HERE.parent))
    parser.add_argument("--out", default="")
    parser.add_argument("--port", type=int, default=59851)
    parser.add_argument("--budget", type=float, default=0.05, help="dollars; no new page past it")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(args.tree).resolve()))
    from mibo import abilities
    from mibo.web import gate, operate
    run = load("miniwob_run", HERE / "bench" / "miniwob" / "run.py")
    tasks = load("opsite_tasks", HERE / "opsite" / "tasks.py")
    env = run.read_env(Path(args.env))
    able = {"tools": "yes",
            "context": args.context if args.context >= 0 else await abilities._context(
                env["LLM_BASE_URL"].rstrip("/") + "/chat/completions", "", args.model)}
    model = run.Model(env["LLM_BASE_URL"], env["LLM_API_KEY"], args.model, args.budget)
    model.answer = operate.budget(able)["answer"]
    out = Path(args.out or tempfile.mkdtemp(prefix="readstep-")).resolve()
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
            for name in args.pages.split(","):
                if model.spent >= args.budget:
                    print(f"budget reached: {name} not read", flush=True)
                    continue
                row = await one(browser, site, tasks.BY_ID[name], model, able, out, operate, gate, tasks)
                rows.append(row)
                with (out / "results.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"{'right' if row['right'] else 'WRONG'} {name} {row['seconds']}s calls={row['calls']} "
                      f"in={row['in']} scrolled={row['scrolled_px']}px ${row['cost']:.4f}  {row['answer'][:90]!r}",
                      flush=True)
            await browser.close()
    finally:
        server.terminate()
        await model.client.aclose()
    print(f"{sum(r['right'] for r in rows)}/{len(rows)} right; able {able}; spent ${model.spent:.4f}; kept in {out}")
    return 0 if rows and all(r["right"] for r in rows) else 1


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    raise SystemExit(asyncio.run(main(sys.argv[1:])))

"""Real walks on a home, one after another, and what each brought: pages read, kept, how many of
those bear on the person's interests, and the model tokens and money the walk took.

    python tools/walkcheck.py --home <home> --walks 10 [--topics 美食,历史,天文] [--out walks.json]

The home is a folder with its own settings (a model, AGENT_LANG); the walks run in this process through
roam.Roam with the app's own agent, headless and signed out, as the service would walk them, only one
after another instead of hours apart. --topics are written as the first run's topics before the first
walk. "Bears on" is the feed's own topic matcher (feed.topic_matcher) over title, excerpt and site, so
it counts what the ranking can see, not what a reader would judge; the titles are printed for that.
The model key is read into this process only and never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--walks", type=int, default=10)
    parser.add_argument("--topics", default="")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    home = args.home.resolve()
    from mibo import settingsfile
    os.environ.update(settingsfile.values(home))
    os.environ["AGENT_HOME"] = str(home)
    asyncio.run(walks(home, args.walks, [t for t in args.topics.split(",") if t], args.out))


async def walks(home: Path, count: int, topics: list[str], out: Path | None) -> None:
    from mibo import roam, usage
    from mibo.agent import Agent
    from mibo.panels import feed
    from mibo.paths import runtime_dir
    from mibo.settings import AgentSettings

    calls: list[dict] = []
    add = usage.UsageBook.add

    def counted(self, model, used, now, purpose="other"):
        used = used if isinstance(used, dict) else {}
        calls.append({"purpose": usage.purpose_of(purpose), "in": used.get("prompt_tokens") or 0,
                      "out": used.get("completion_tokens") or 0, "cost": used.get("cost") or 0})
        add(self, model, used, now, purpose)
    usage.UsageBook.add = counted

    runtime = runtime_dir()
    if topics:
        feed.keep_topics(runtime, topics)
    agent = Agent(AgentSettings.from_env())
    walker = roam.Roam(agent, home=home, runtime=runtime)
    walker.settings.roam_site_pages_a_day = 60
    named = roam.interests(runtime)
    matchers = {t: feed.topic_matcher([t]) for t in named}
    read_counts: list[int] = []
    choose = walker._choose

    async def seen(read):
        read_counts.append(len(read))
        return await choose(read)
    walker._choose = seen
    rows = []
    try:
        for n in range(count):
            before, started = len(calls), time.time()
            ids = {i.id for i in walker.library.items()}
            words = []
            plan = walker._plan

            def planned():
                got = plan()
                words.append(got.topic or "(none)")
                return got
            walker._plan = planned
            kept = await walker.walk()
            walker._plan = plan
            new = [i for i in walker.library.items() if i.id not in ids]
            bearing = {t: sum(1 for i in new if m and m.search(" ".join((i.title, i.description[:300], i.site)).casefold()))
                       for t, m in matchers.items()}
            spent = calls[before:]
            row = {"walk": n + 1, "words": words[0] if words else "", "read": read_counts[-1] if read_counts else 0,
                   "kept": kept, "bearing": sum(1 for i in new if any(
                       m and m.search(" ".join((i.title, i.description[:300], i.site)).casefold())
                       for m in matchers.values())),
                   "by_interest": bearing, "seconds": round(time.time() - started),
                   "calls": {p: sum(1 for c in spent if c["purpose"] == p) for p in {c["purpose"] for c in spent}},
                   "tokens_in": sum(c["in"] for c in spent), "tokens_out": sum(c["out"] for c in spent),
                   "cost": round(sum(c["cost"] for c in spent), 6),
                   "kept_items": [{"title": i.title[:80], "site": i.site, "kind": i.kind} for i in new]}
            rows.append(row)
            print(json.dumps({k: v for k, v in row.items() if k != "kept_items"}, ensure_ascii=False), flush=True)
            for i in row["kept_items"]:
                print("    ", i["kind"], "|", i["site"], "|", i["title"], flush=True)
            if out:
                out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        await walker.visitor.stop()
    total = {k: sum(r[k] for r in rows) for k in ("read", "kept", "bearing", "tokens_in", "tokens_out", "cost")}
    print("TOTAL", json.dumps(total, ensure_ascii=False))


if __name__ == "__main__":
    main()

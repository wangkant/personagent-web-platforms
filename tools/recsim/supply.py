"""What a snapshot holds for each simulated person's interests, from the labels (the truth), so a
starved interest shows before any simulation runs; with two snapshots, side by side.

    python tools/recsim/supply.py --snapshot <dir> [--snapshot <dir>]

For every person and every interest they care for (weight 0.5 or more, and the one that starts on day
ten), the items whose labelled subjects give it 0.4 or more and that they can read (a person who reads
no English is counted only Chinese items), with the three sources that brought most of them. A walk's
find counts under "walk:<site>".
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from people import PEOPLE  # noqa: E402

STRONG, ON = 0.5, 0.4


def load(snapshot: Path) -> tuple[dict, dict]:
    items = {json.loads(line)["id"]: json.loads(line)
             for line in (snapshot / "items.jsonl").read_text(encoding="utf-8").splitlines() if line}
    return items, json.loads((snapshot / "labels.json").read_text(encoding="utf-8"))


def held(items: dict, labels: dict, subject: str, english: bool) -> Counter:
    found = Counter()
    for ident, label in labels.items():
        item = items.get(ident)
        if item and label["subjects"].get(subject, 0) >= ON and (english or label.get("lang") == "zh"):
            found[item.get("source") or "walk:" + item.get("site", "")] += 1
    return found


def topics(who) -> list[str]:
    """The first run's topics the person ticks, as sim.Arm.topics picks them."""
    from people import OFFERED
    if not who.picks:
        return []
    strong = sorted((s for s, w in who.interests.items() if w >= 0.5), key=lambda s: -who.interests[s])
    return list(dict.fromkeys(OFFERED[s] for s in strong if s in OFFERED))[:4] + list(who.typed)


def draws(snaps: list[tuple[dict, dict]], rounds: int) -> None:
    """For each person and interest: on-interest things a sources round brings, expected over `rounds` draws
    of weights.pick, with the topic prior as it was (TOPIC on a source's words) and as it is now (catalog.pulls),
    on a fresh install (empty library) and after the snapshots' items came in (the pulls learned from them).
    A source's on-interest share is read from the labels of what it brought in the snapshots."""
    import random
    import tempfile
    import time
    from dataclasses import fields
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from mibo import situation
    from mibo.library import Item, Library
    from mibo.panels import feed
    from mibo.sources import base, catalog, weights
    items, labels = {}, {}
    for got, said in snaps:
        items.update(got)
        labels.update(said)
    by_source: dict[str, list] = {}
    for ident, item in items.items():
        if item.get("source") and ident in labels:
            by_source.setdefault(item["source"], []).append(labels[ident])
    sources = catalog.all_sources([])
    pulls = catalog.pulls
    print(f"\nexpected on-interest things per sources round ({rounds} draws; old prior -> new, fresh install | "
          "after the snapshot came in)")
    for who in PEOPLE:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime"
            runtime.mkdir()
            feed.keep_topics(runtime, topics(who))
            situation.set_value(runtime, "readable", ["zh", "en"] if who.english else ["zh"])
            chance = {}
            for stage in ("fresh", "after"):
                if stage == "after":
                    library, known = Library(runtime), {f.name for f in fields(Item)}
                    now = int(time.time()) - 3600
                    for it in items.values():
                        library.add(Item(**{**{k: v for k, v in it.items() if k in known}, "seen_at": now}))
                for prior in ("old", "new"):
                    catalog.pulls = (lambda *a: None) if prior == "old" else pulls
                    rng = random.Random(1)
                    counts = Counter()
                    # Weighed once: the taste model has learned nothing here, so its draw only adds noise.
                    weighed = catalog.weighed(runtime, "zh", sources, rng)
                    for _ in range(rounds):
                        for row in weights.pick(weighed, rng):
                            counts[row["source"].key] += 1
                    chance[stage, prior] = {k: v / rounds for k, v in counts.items()}
            catalog.pulls = pulls
        wanted = [s for s, w in who.interests.items() if w >= STRONG] + ([who.new] if who.new else [])
        for subject in wanted:
            def expected(p: dict) -> float:
                total = 0.0
                for key, share_of in p.items():
                    rows = by_source.get(key)
                    if rows:
                        on = sum(1 for r in rows if r["subjects"].get(subject, 0) >= ON
                                 and (who.english or r.get("lang") == "zh"))
                        total += share_of * base.PER_SOURCE * on / len(rows)
                return total
            cells = [f"{expected(chance[s, 'old']):5.1f} -> {expected(chance[s, 'new']):5.1f}" for s in ("fresh", "after")]
            print(f"{who.name:6s} {subject:14s} {cells[0]} | {cells[1]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, action="append", required=True)
    parser.add_argument("--draws", type=int, default=0, help="also the expected draw, old prior against new")
    args = parser.parse_args()
    snaps = [load(s) for s in args.snapshot]
    if args.draws:
        draws(snaps, args.draws)
        return
    print("person interest " + " | ".join(f"snapshot {n + 1} (top sources)" for n in range(len(snaps))))
    for who in PEOPLE:
        wanted = [s for s, w in who.interests.items() if w >= STRONG] + ([who.new] if who.new else [])
        for subject in wanted:
            cells = []
            for items, labels in snaps:
                found = held(items, labels, subject, who.english)
                cells.append(f"{sum(found.values()):4d} ({', '.join(f'{k} {v}' for k, v in found.most_common(3))})")
            print(f"{who.name:6s} {subject:14s} " + " | ".join(cells))


if __name__ == "__main__":
    main()

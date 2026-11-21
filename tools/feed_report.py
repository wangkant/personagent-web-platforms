"""Replay a home's feed events and print, week by week, what the things
shown led to: how many were shown, and the open, like and quick-close rates,
overall, by format (text or video) and by surface (the pet's card, the Feed
list, the main chat, the first run's round). Run it before and after a change
to the model or the pacing to compare the two.

A thing counts once, in the week it was first shown, where it was first shown.
Opened means opened, played or read in the app; a quick close is a card closed
by the person within three seconds.

Usage:
    python tools/feed_report.py --home <dir> [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mibo import taste
from mibo.panels import feed


def week(at: float) -> str:
    day = datetime.fromtimestamp(at).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def report(events: list) -> dict:
    """{week: {group: {shown, open, like, quick, open_rate, like_rate, quick_rate}}}, groups being
    "all", "format:<text|video>" and "surface:<name>"."""
    weeks: dict[str, dict] = {}
    for h in taste.history(events).values():
        groups = ("all", "format:" + ("video" if h["kind"] == "video" else "text"), "surface:" + (h["s"] or "other"))
        for group in groups:
            row = weeks.setdefault(week(h["at"]), {}).setdefault(group, {"shown": 0, "open": 0, "like": 0,
                                                                          "quick": 0})
            row["shown"] += 1
            row["open"] += bool(h["open"] or h["play"] > 0)
            row["like"] += h["like"]
            row["quick"] += h["end"] == "quick"
    for groups in weeks.values():
        for row in groups.values():
            for k in ("open", "like", "quick"):
                row[k + "_rate"] = round(row[k] / row["shown"], 3)
    return dict(sorted(weeks.items()))


def _print(weeks: dict) -> None:
    if not weeks:
        print("no events yet")
        return
    print(f"{'week of':12}{'group':18}{'shown':>7}{'open':>8}{'like':>8}{'quick':>8}")
    for start, groups in weeks.items():
        order = sorted(groups, key=lambda g: (g != "all", g))
        for n, group in enumerate(order):
            row = groups[group]
            print(f"{start if n == 0 else '':12}{group:18}{row['shown']:>7}"
                  + "".join(f"{row[k + '_rate']:>8.1%}" for k in ("open", "like", "quick")))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python tools/feed_report.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--home", type=Path, required=True, help="the home whose runtime/mibo.db is read")
    parser.add_argument("--json", action="store_true", help="print the weeks as JSON")
    args = parser.parse_args(argv)
    events = sorted(feed.events(args.home / "runtime"), key=lambda e: e[0])
    weeks = report(events)
    if args.json:
        print(json.dumps(weeks, ensure_ascii=False, indent=1))
    else:
        _print(weeks)


if __name__ == "__main__":
    main()

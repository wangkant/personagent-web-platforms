"""How often "because you picked <topic>" is true, topic by topic, on a library.

    python tools/cardcheck/topics.py --runtime <runtime folder with mibo.db> [--labels labels.json]
                                     [--sample 60] [--seed 1] [--out sheet.txt]

For each topic the first run offers, as if it were the only one ticked: every thing in the library the
ranking would put under it (the taste model's snapshot names the topic, from the thing's own words) and
whether the card's chip says so too (recommend.Why, which asks more of the thing's words). With
`--labels` (tools/recsim/snapshot.py: a model's one to three subjects per thing) it counts how often
each claim agrees with the label. It prints `--sample` things of each kind, drawn at random over the
topics, with title and summary, for a person to read and mark. No model is called.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "recsim"))

from mibo import recommend, subjects, taste  # noqa: E402
from mibo.library import Library  # noqa: E402
from mibo.panels import feed  # noqa: E402
from subjects_check import TO  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--sample", type=int, default=60)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    labels = json.loads(args.labels.read_text(encoding="utf-8")) if args.labels else {}
    items = [(item, feed.shape(item, {})) for item in Library(args.runtime).items()]
    items = [(item, shaped) for item, shaped in items if shaped]
    now = max(item.seen_at for item, _ in items) + 60
    said, kept, out = [], [], []
    for topic in subjects.OFFERED:
        reader = taste.Reader(args.runtime, [topic], [])
        why = recommend.Why(args.runtime, [topic], [], now, "zh")
        rows = []
        for item, shaped in items:
            snap = reader.snapshot(item, shaped, now)
            if not snap["topics"] or snap["guess"]:
                continue
            chip = why.of(dict(shaped, reason_term="topic:" + topic), item)["why"]
            label = labels.get(item.id, {}).get("subjects")
            # The label's subjects are the simulator's thirty; the topic's own and finer ones are mapped onto them.
            right = None if label is None else bool({TO[k] for k in subjects.under(topic)} & set(label))
            rows.append((topic, item, shaped, bool(chip and chip["key"] == "topic:" + topic), right))
        said.extend(rows)
        kept.extend(row for row in rows if row[3])
        judged = [row for row in rows if row[4] is not None]
        chips = [row for row in judged if row[3]]
        out.append(f"{topic:12} the ranking names it for {len(rows):4}"
                   + (f" (label agrees {sum(r[4] for r in judged) / len(judged):.2f})" if judged else "")
                   + f"; the chip says so for {sum(r[3] for r in rows):4}"
                   + (f" (label agrees {sum(r[4] for r in chips) / len(chips):.2f})" if chips else ""))
    judged, chips = [r for r in said if r[4] is not None], [r for r in kept if r[4] is not None]
    out.append(f"all: the ranking {len(said)}" + (f" (label agrees {sum(r[4] for r in judged) / len(judged):.3f})" if judged else "")
               + f"; the chip {len(kept)}" + (f" (label agrees {sum(r[4] for r in chips) / len(chips):.3f})" if chips else ""))
    rng = random.Random(args.seed)
    for name, rows in (("THE CHIP SAYS 因为你选了", kept), ("THE RANKING NAMES IT, THE CHIP DOES NOT", [r for r in said if not r[3]])):
        out.append(f"\n===== {name}: {min(args.sample, len(rows))} of {len(rows)}")
        for n, (topic, item, shaped, _, right) in enumerate(rng.sample(rows, min(args.sample, len(rows))), 1):
            out.append(f"{n:2} [{taste._topic_name(topic, 'zh')}] {shaped['site']} | {shaped['title'][:110]}\n"
                       f"     {' '.join(shaped['text'].split())[:170]}\n     label: {'-' if right is None else 'agrees' if right else 'differs'}")
    text = "\n".join(out)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

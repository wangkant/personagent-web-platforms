"""The chip under a card against the ranking's own reason, on a recorded home, for a person to read.

    python tools/cardcheck/chips.py --runtime <a copy of a home's runtime> [--lang zh] [--per 6] [--out sheet.txt]

Goes back to each moment the recorded home brought something (a "seen" on the card or in the chat),
takes the candidates the chooser would have had then (recommend.candidates on the events up to that
moment; with `--per` above six, that many of the ranking's first unseen instead) and prints each thing once: its title, site and summary, the ranking's reason as the feed labels
it (taste.Ranker.reason), the chip the card shows (recommend.Why) and the topic "less of this topic" would
name. Whether a chip is true is for the reader to mark against the title and summary: no model is called.
The runtime is written to (the taste model refits): give it a copy.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mibo import recommend  # noqa: E402
from mibo.library import Library  # noqa: E402
from mibo.panels import feed  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--lang", default="zh")
    parser.add_argument("--per", type=int, default=recommend.CANDIDATES)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    events, topics = feed.events(args.runtime), feed.load_topics(args.runtime)
    library = Library(args.runtime)
    moments = [e[0] for e in events if e[2] == "seen" and len(e) > 5 and e[5].get("s") in ("card", "chat")]
    lines, named, kinds = [], set(), Counter()
    for at in moments:
        before = [e for e in events if e[0] < at]
        why = recommend.Why(args.runtime, topics, before, at, args.lang)
        rows = recommend.candidates(library, before, at, topics, set())
        if args.per > recommend.CANDIDATES:
            ranked = feed.ranked(library, before, at, topics, seed=int(at * 1000))
            rows = [row for row in ranked if not row[0]["seen"]][:args.per]
        for shaped, item in rows:
            if item.id in named:
                continue
            named.add(item.id)
            card = why.of(shaped, item)
            chip = (card["why"] or {}).get("text", "")
            kinds[(bool(shaped["reason_term"]), chip.split(" ")[0] if chip else "")] += 1
            lines.append(f"#{len(named)} [{shaped['kind']}] {shaped['site']} ({item.source or 'walk'}) | {shaped['title']}\n"
                         f"   summary: {' '.join(shaped['text'].split())[:220]}\n"
                         f"   ranking: {shaped['reason_term'] or '-'}\n"
                         f"   chip:    {chip or '-'}   | topic for a no: {(card['topic'] or {}).get('name', '-')}")
    lines.append(f"\n{len(moments)} moments, {len(named)} things; with a ranking reason {sum(n for (r, _), n in kinds.items() if r)}, "
                 f"with a chip {sum(n for (_, c), n in kinds.items() if c)}")
    lines.extend(f"   ranking reason {'yes' if r else 'no '} -> chip {c or '-'}: {n}" for (r, c), n in sorted(kinds.items(), key=str))
    text = "\n".join(lines)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

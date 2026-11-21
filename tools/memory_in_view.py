"""How much of a person store reaches the reply prompt.

Builds person.recall_block for the 20 recall probes of the memory acceptance
life (asked on 11-30 and 12-04) and counts, for each store given:
the active facts placed, the health / dislike / form facts placed (these
must never be missing), whether the talk made it in, and the block size.

    python tools/memory_in_view.py <person.json> [<person.json> ...]

A profile.json beside a person.json supplies its manner lines.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mibo import person, profile  # noqa: E402

TZ = timezone(timedelta(hours=-8))
# Estimated tokens (context.text_tokens): about what the memory block is left at a 64k window (prompt.py).
BUDGET = 1000
MUST = ("health", "dislike", "form")
# (day, hour, words): the probes in the order the life asks them.
PROBES = [((11, 30), 20, w) for w in (
    "我猫叫什么来着？", "周末吃啥好？给我推荐一下", "我现在住哪个区你还记得吗", "我考试是哪天来着？",
    "你知道我叫什么吗", "今晚想看个电影，有啥推荐", "我男朋友叫什么来着？", "我妈是哪里人你记得不",
    "我之前是不是挂过科？", "帮我想想奶茶点什么")] + [((12, 4), 9, "早呀")] + [((12, 4), 19, w) for w in (
    "我那个考试是哪天来着？", "我的申请DDL是什么时候", "我室友叫啥，我们现在关系咋样", "我生日快到了吧？还有几天",
    "我什么时候去成都来着", "我在学什么外语", "我养的狗叫什么", "我导师姓啥", "我最好的朋友叫什么")]


def measure(path: Path) -> dict:
    store = person.Person(path)
    manner = profile.manner_lines(path.parent) if (path.parent / "profile.json").exists() else []
    facts = store.facts()
    must = {f["id"] for f in facts if f.get("kind") in MUST}
    placed_n, must_n, talk_n, sizes, missed = [], [], 0, [], {}
    for (month, day), hour, words in PROBES:
        now = datetime(2026, month, day, hour, 0, tzinfo=TZ).timestamp()
        block, ids = person.recall_block(store, words, now, TZ, "zh", BUDGET, manner=manner)
        got = set(ids)
        placed_n.append(len(got & {f["id"] for f in facts}))
        must_n.append(len(got & must))
        talk_n += "What you two have talked about" in block
        sizes.append(len(block))
        for f in facts:
            if f["id"] in must and f["id"] not in got:
                missed.setdefault(f["text"], []).append(words)
    return {"store": str(path), "facts": len(facts), "must": len(must),
            "facts_placed_min": min(placed_n), "facts_placed_mean": round(sum(placed_n) / len(PROBES), 1),
            "must_placed": f"{sum(must_n)}/{len(must) * len(PROBES)}",
            "probes_missing_a_must": sum(n < len(must) for n in must_n),
            "talk_placed": f"{talk_n}/{len(PROBES)}", "block_chars_max": max(sizes),
            "must_missed": {t: len(w) for t, w in missed.items()}}


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(json.dumps(measure(Path(arg)), ensure_ascii=False))

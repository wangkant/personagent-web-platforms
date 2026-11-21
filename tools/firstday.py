"""A new person's first minutes on a fresh home, timed with the real network and model: the first run's
topics, its round of sample finds, and the first cards after it.

    python tools/firstday.py --home <fresh home> --port <page port> --topics 美食,历史,天文
                             [--after 60] [--cards 10] [--out first.json]

The home holds only its settings (a model, AGENT_LANG and PAGE_PORT in a free range). The app
starts headless (welcome.main --no-browser); --after seconds later (the time a person spends on the
language, character and model steps) the topics are saved as the first run saves them, the round of
sample finds is asked for (GET /api/feed/first) and answered by a stand-in person who wants what is on a
topic and skips the rest; then the first run finishes (look/toured, sources/refresh, start) and the pet's
card is asked for at once each time (GET /api/feed/next?now=true), as many times as --cards. A card or
find "bears on" the topics by the feed's own matcher (feed.topic_matcher); titles are printed to judge
by eye. The app is stopped through /api/exit at the end. Keys are never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class Page:
    def __init__(self, home: Path):
        url = json.loads((home / "runtime" / "desktop" / "welcome.json").read_text(encoding="utf-8"))["url"]
        parts = urlsplit(url)
        self.base, self.token = f"{parts.scheme}://{parts.netloc}/api/", parts.fragment.partition("token=")[2]
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path: str, body: dict | None = None, timeout: float = 120) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method="POST" if data else "GET",
                                         headers={"Content-Type": "application/json",
                                                  "x-mibo-setup-token": self.token})
        with self.opener.open(request, timeout=timeout) as answer:
            return json.loads(answer.read().decode() or "{}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--topics", required=True)
    parser.add_argument("--after", type=float, default=60)
    parser.add_argument("--cards", type=int, default=10)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    home = args.home.resolve()
    topics = [t for t in args.topics.split(",") if t]
    from mibo.panels import feed
    matcher = feed.topic_matcher(topics)

    def bears(row: dict) -> bool:
        return bool(matcher.search(" ".join((row.get("title") or "", row.get("text") or "",
                                              row.get("site") or "")).casefold()))

    env = dict(os.environ, AGENT_HOME=str(home), PYTHONIOENCODING="utf-8")
    log = open(home / "firstday.app.log", "w", encoding="utf-8")
    app = subprocess.Popen([sys.executable, "-c", "import sys; from mibo import welcome; "
                            f"sys.exit(welcome.main(['--no-browser','--port','{args.port}']))"],
                           cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    report: dict = {"topics": topics}
    started = time.time()
    try:
        page = None
        while page is None and time.time() - started < 60:
            time.sleep(1)
            try:
                page = Page(home)
                page.call("look")
            except (OSError, ValueError, KeyError):
                page = None
        report["page_up_s"] = round(time.time() - started, 1)
        time.sleep(max(0.0, args.after - (time.time() - started)))
        page.call("feed/interests", {"topics": topics, "lang": "zh"})
        asked = time.time()
        finds = page.call("feed/first?lang=zh")["items"]
        report["first_round"] = {"after_page_s": round(asked - started, 1), "waited_s": round(time.time() - asked, 1),
                                 "count": len(finds), "bearing": sum(map(bears, finds)),
                                 "items": [{"title": f["title"][:70], "site": f["site"], "kind": f["kind"],
                                            "source": f.get("source", ""), "bears": bears(f)} for f in finds]}
        print(json.dumps({k: v for k, v in report["first_round"].items() if k != "items"}, ensure_ascii=False))
        for n, find in enumerate(finds):
            page.call("feed/react", {"id": find["id"], "action": "seen", "surface": "first", "rank": n})
            page.call("feed/react", {"id": find["id"], "action": "like" if bears(find) else "skip",
                                     "surface": "first", "rank": n})
        page.call("look/toured", {})
        page.call("sources/refresh", {})
        page.call("start", {})
        finished = time.time()
        report["finished_after_page_s"] = round(finished - started, 1)
        cards = []
        while len(cards) < args.cards and time.time() - finished < 900:
            got = page.call("feed/next?now=true", timeout=180).get("item")
            if not got:
                time.sleep(5)
                continue
            page.call("feed/react", {"id": got["id"], "action": "seen", "surface": "card", "rank": 0})
            row = {"n": len(cards) + 1, "at_s": round(time.time() - finished, 1), "title": got["title"][:70],
                   "site": got["site"], "kind": got["kind"], "source": got.get("source", ""), "bears": bears(got),
                   "words": (got.get("words") or "")[:120]}
            cards.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            # The stand-in person opens what bears on their topics and closes the rest at once.
            page.call("feed/react", {"id": got["id"], "action": "end", "surface": "card",
                                     "end": "open" if row["bears"] else "close", "shown_s": 8 if row["bears"] else 2})
        report["cards"] = cards
        first_good = next((c for c in cards if c["bears"]), None)
        report["first_good_card_s"] = first_good["at_s"] if first_good else None
        report["cards_bearing"] = sum(c["bears"] for c in cards)
        print("first good card", report["first_good_card_s"], "s after the first run; bearing",
              report["cards_bearing"], "of", len(cards))
    finally:
        try:
            Page(home).call("exit", {}, timeout=30)
        except (OSError, ValueError, KeyError, urllib.error.URLError):
            pass
        try:
            app.wait(timeout=60)
        except subprocess.TimeoutExpired:
            app.kill()
        log.close()
        if args.out:
            args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

"""What the person waits for in the finds, measured the same way before and after a change to how they are kept: on
a home with a year of use, the time to open 推荐记录 (and its liked list), to react to a find, to draw the next find,
for the model to learn from the reactions, for the address bar and the history to answer, and the size on disk.

    python tools/finds_bench.py make <folder>             # a year of use, in the files a home kept before mibo.db
    python tools/finds_bench.py run <folder> <copy>       # measure on a fresh copy of that home

The year: 5,000 things in the library, 20,000 showings (8,000 events, the most the feed kept), 10,000 pages read in
the app's browser, 120 sources' state, 150 hosts. `run` copies the home to <copy>, opens it as the app does (a
home from before the database moves in then) and prints one JSON object. Runs offline: no model is called, and
nothing refits in the background while it measures. Run it with PYTHONPATH at another copy of the package to
measure that copy."""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import sqlite3
import statistics
import sys
import time
from pathlib import Path

DAY = 86400
ITEMS, SHOWINGS, EVENTS, BROWSED = 5000, 20_000, 8000, 10_000
SOURCES = ("hn-top", "hn-show", "lobsters-hot", "devto-top", "so-week", "ph-today", "juejin-hot", "oschina-news",
           "infoqcn-news", "ruanyf-weekly", "hf-papers", "hf-models", "openai-news", "deepmind-blog")
WORDS = ("model", "garden", "bread", "river", "python", "camera", "tea", "music", "city", "paper", "robot", "bike",
         "kernel", "painting", "recipe", "museum", "orbit", "piano", "forest", "compiler")


def _id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _title(rng: random.Random) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(4, 9))).capitalize()


def make(folder: Path) -> Path:
    """A home a person used for a year, as the app kept it before mibo.db."""
    rng = random.Random(7)
    home = folder / "home"
    runtime = home / "runtime"
    (runtime / "library").mkdir(parents=True, exist_ok=True)
    (runtime / "desktop").mkdir(parents=True, exist_ok=True)
    from mibo import settingsfile
    settingsfile.write(home, {"LLM_MODEL": "stub", "LLM_BASE_URL": "http://127.0.0.1:9/v1", "PERSONA_NAME": "Mibo",
                              "AGENT_LANG": "en", "ROAM_ENABLED": "false", "UPDATE_CHECK_ENABLED": "false"})
    now = time.time()
    items = []
    for n in range(ITEMS):
        site = f"site{n % 90}.example"
        url = f"https://{site}/post/{n}"
        long = rng.random() < 0.25
        item = {"url": url, "site": site, "kind": rng.choice(("article", "article", "post", "video")),
                "title": _title(rng), "author": "", "published": "", "length": rng.choice((None, 240, 900)),
                "description": " ".join(rng.choice(WORDS) for _ in range(40)),
                "text": " ".join(rng.choice(WORDS) for _ in range(500)) if long else "",
                "embed": "", "picture": f"{_id(url)}.jpg", "seen_at": int(now - (ITEMS - n) / ITEMS * 365 * DAY),
                "outing": "", "why": "", "fits": rng.random() < 0.1, "told": False,
                "state": "kept" if n % 17 == 0 else "dismissed" if n % 23 == 0 else "", "id": _id(url),
                "source": SOURCES[n % len(SOURCES)] if n % 5 else "", "love": rng.choice((-1, 1, 2)),
                "tags": ["tech"], "judged": True, "via": "browsed" if n % 10 == 0 else "", "wonder": ""}
        items.append(item)
    # A thing changed after it came is a second line until the next compaction, as the walks and reactions wrote.
    lines = [json.dumps(i, ensure_ascii=False) for i in items]
    lines += [json.dumps(dict(i, love=3), ensure_ascii=False) for i in items[-400:]]
    (runtime / "library" / "items.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    events, shown = [], []
    # The last week's 150 not shown yet: what the next find is drawn from.
    seen = items[-4650:-150]
    for k in range(SHOWINGS):
        item = seen[k * len(seen) // SHOWINGS]
        at = item["seen_at"] + 600 + k % 7
        shown.append({"at": at, "id": item["id"], "surface": rng.choice(("card", "list", "chat")), "rank": k % 20,
                      "six": k % 6, "s": 0.4, "lift": 0.1, "top": [["origin:src:hn-top", 0.31], ["subj:tech", 0.2]],
                      "slot": "model", "host": item["site"], "gated": False})
    for item in seen:
        snap = {"origin": "src:" + item["source"] if item["source"] else "site:" + item["site"], "kind": "page",
                "len": "mid", "chars": 2400, "lang": "en", "fresh": 0.5, "judged": True, "fits": False, "love": 1,
                "subj": ["tech"], "guess": False, "again": [0, 2], "topics": [], "avoid": [], "likes": [],
                "tags": ["tech"], "host": item["site"], "site": item["site"]}
        at = item["seen_at"] + 600
        events.append([at, item["id"], "seen", item["site"], "page",
                       {"f": snap, "r": 3, "s": rng.choice(("card", "list", "chat")), "p": "model",
                        "why": {"k": "new", "first_run": False, "empty": False}}])
        roll = rng.random()
        if roll < 0.35:
            events.append([at + 30, item["id"], "open", item["site"], "page", {"f": snap, "r": 3, "s": "list", "p": ""}])
        if roll < 0.08:
            events.append([at + 90, item["id"], "like", item["site"], "page", {"f": snap, "r": 3, "s": "list", "p": ""}])
        elif 0.8 < roll < 0.95:
            events.append([at + 20, item["id"], "skip", item["site"], "page", {"f": snap, "r": 3, "s": "card", "p": ""}])
    events.sort(key=lambda e: e[0])
    (runtime / "feed.json").write_text(json.dumps({"events": events[-EVENTS:], "topics": ["AI", "cooking"]},
                                                  ensure_ascii=False), encoding="utf-8")
    (runtime / "shown.jsonl").write_text("".join(json.dumps(r) + "\n" for r in shown), encoding="utf-8")

    pages = [f"https://read{n % 40}.example/page/{n}" for n in range(400)]
    rows = [{"url": pages[k % 400], "title": _title(rng), "host": f"read{k % 40}.example",
             "at": round(now - (BROWSED - k) / BROWSED * 365 * DAY, 1), "s": float(k % 120)} for k in range(BROWSED)]
    (runtime / "desktop" / "browsed.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    state = {f"src-{n}": {"reads": 300, "fails": 0, "items": 40, "points": 3.5, "hides": 0, "blocked": 0,
                          "tried": int(now)} for n in range(120)}
    (runtime / "sources_taste.json").write_text(json.dumps({"sources": state}, indent=1), encoding="utf-8")
    (runtime / "sources.json").write_text(json.dumps({
        "own": [{"key": f"own-{n}", "url": f"https://blog{n}.example/feed", "title": f"Blog {n}"} for n in range(3)],
        "last": {f"src-{n}": {"at": int(now), "count": 12, "error": ""} for n in range(120)}}, indent=1), encoding="utf-8")
    hosts = {f"host{n}.example": {"fails": 0, "fail_at": 0.0, "ok_at": now, "wall_at": 0.0, "mode": ""} for n in range(150)}
    (runtime / "situation.json").write_text(json.dumps({
        "factors": {"readable": {"by": {"observed": ["en", "zh"]}, "at": int(now)}, "region": {"by": {"os": "US"}}},
        "hosts": hosts, "probed": now, "retry_at": 0}, indent=1), encoding="utf-8")
    return home


def _size(runtime: Path) -> int:
    """Bytes the finds take on disk: every store but the pictures and the chat files the database had before."""
    names = {"feed.json", "shown.jsonl", "items.jsonl", "browsed.jsonl", "sources_taste.json", "sources.json",
             "situation.json", "mibo.db", "mibo.db-wal"}
    return sum(p.stat().st_size for p in runtime.rglob("*") if p.is_file() and p.name in names)


def _median_ms(times: list[float]) -> float:
    return round(statistics.median(times) * 1000, 1)


def run(fixture: Path, copy: Path) -> dict:
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(fixture / "home", copy)
    home, runtime = copy, copy / "runtime"
    os.environ["AGENT_HOME"] = str(home)
    out: dict = {"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version, "size_before_open": _size(runtime)}
    from fastapi.testclient import TestClient

    from mibo import taste, welcome

    # Nothing refits in the background while the clock runs: the fit is timed on its own below.
    taste.fit = lambda runtime, soon=False: None
    began = time.perf_counter()
    workspace = welcome.Workspace(home)
    out["open_home_ms"] = round((time.perf_counter() - began) * 1000, 1)
    client = TestClient(welcome.create_app(home, "bench", workspace=workspace), base_url="http://127.0.0.1")
    head = {welcome.TOKEN_HEADER: "bench"}

    def timed(method: str, path: str, **sent) -> tuple[float, dict]:
        began = time.perf_counter()
        answer = client.request(method, path, headers=head, **sent)
        took = time.perf_counter() - began
        assert answer.status_code == 200, (path, answer.status_code, answer.text[:300])
        return took, answer.json()

    first, page = timed("GET", "/api/feed?limit=20")
    out["record_first_open_ms"] = round(first * 1000, 1)
    out["record_open_ms"] = _median_ms([timed("GET", "/api/feed?limit=20")[0] for _ in range(5)])
    out["record_rows"] = len(page["items"])
    out["record_next_page_ms"] = _median_ms([timed("GET", f"/api/feed?limit=20&before={page['next']}")[0]
                                            for _ in range(3)])
    out["liked_open_ms"] = _median_ms([timed("GET", "/api/feed?limit=20&kept=true")[0] for _ in range(3)])
    ids = [card["id"] for card in page["items"]]
    out["react_open_ms"] = _median_ms([timed("POST", "/api/feed/react", json={
        "id": i, "action": "open", "surface": "list"})[0] for i in ids[:10]])
    out["react_like_ms"] = _median_ms([timed("POST", "/api/feed/react", json={
        "id": i, "action": "like", "surface": "list"})[0] for i in ids[10:20]])
    draws = []
    for _ in range(8):
        took, drawn = timed("GET", "/api/feed/next?now=true")
        draws.append(took)
        timed("POST", "/api/feed/react", json={"id": drawn["item"]["id"], "action": "seen", "surface": "card"})
    out["draw_next_ms"] = _median_ms(draws)
    began = time.perf_counter()
    taste.train(runtime)
    out["learn_ms"] = round((time.perf_counter() - began) * 1000, 1)
    out["suggest_ms"] = _median_ms([timed("GET", "/api/browser/suggest?q=read1")[0] for _ in range(5)])
    out["history_ms"] = _median_ms([timed("GET", "/api/browser/history")[0] for _ in range(5)])
    workspace.close()
    out["size_after"] = _size(runtime)
    return out


def main(argv: list[str]) -> int:
    if argv[:1] == ["make"]:
        print(make(Path(argv[1])))
    else:
        print(json.dumps(run(Path(argv[1]), Path(argv[2])), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

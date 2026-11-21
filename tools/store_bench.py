"""What the person sees of the chat stores, measured the same way before and after a change to them: a 300-message
chat reopened after a restart (how many messages the page shows), the page's first load, a turn's commit while the
chat record already holds 5,000 lines, and whether a forget leaves the forgotten words in any byte of the home.

    python tools/store_bench.py <empty folder> [--messages 300] [--log 5000]

Runs offline: the character's reply is stood in for; no model is called. Prints one JSON object. Run it with
PYTHONPATH at another copy of the package to measure that copy."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import statistics
import sys
import time
from pathlib import Path

SECRET = "zebrafinch"  # the words a forget must leave nowhere


def _home(folder: Path) -> Path:
    home = folder / "home"
    home.mkdir(parents=True, exist_ok=True)
    from mibo import settingsfile
    settingsfile.write(home, {"LLM_API_KEY": "bench", "LLM_MODEL": "stub", "LLM_BASE_URL": "http://127.0.0.1:9/v1",
                              "PERSONA_NAME": "Mibo", "AGENT_LANG": "en", "ROAM_ENABLED": "false",
                              "UPDATE_CHECK_ENABLED": "false"})
    os.environ["AGENT_HOME"] = str(home)
    return home


def _workspace(home: Path):
    from mibo import welcome

    workspace = welcome.Workspace(home)

    async def reply(conversation, parts, *, choices=(), run=None):
        said = " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
        return [{"type": "text", "text": f"I heard: {said}. " + "A reply of a usual length. " * 6,
                 "activity": [{"place": "web", "mark": "search", "line": "looked something up", "status": "done"}]}]

    workspace.character.reply = reply
    return workspace


def _log(runtime: Path, days: float = 30):
    """The chat record, however this copy of the package opens it."""
    from mibo import chatlog

    if hasattr(chatlog, "file_name"):
        return chatlog.ChatLog(runtime / "log", days)
    return chatlog.ChatLog(runtime, days)


def _row(seq: int, text: str, now: float) -> dict:
    mine = seq % 2 == 0
    return {"seq": seq, "t": now - 3600 + seq * 0.1, "user_id": "" if mine else "browser:bench",
            "name": "Mibo" if mine else "", "text": text, "self": mine}


def _bytes_holding(root: Path, word: str) -> list[str]:
    forms = {word.encode("utf-8"), json.dumps(word)[1:-1].encode("ascii")}
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and any(f in p.read_bytes() for f in forms))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("--messages", type=int, default=300)
    parser.add_argument("--log", type=int, default=5000)
    args = parser.parse_args(argv)
    home = _home(Path(args.folder))
    from mibo import welcome

    out: dict = {"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                 "package": str(Path(welcome.__file__).parent)}
    runtime = home / "runtime"

    # A chat of `messages` messages, sent one turn at a time through the page's own path.
    workspace = _workspace(home)
    turns = []
    for n in range(args.messages // 2):
        text = f"message {n}" + (f" about the {SECRET} I saw" if n == 3 else "")
        began = time.perf_counter()
        asyncio.run(workspace.chat(text))
        turns.append(time.perf_counter() - began)
    out["sent"] = args.messages
    out["turn_ms_last20_median"] = round(statistics.median(turns[-20:]) * 1000, 2)
    workspace.close()

    # A restart: the page opens the chat again.
    began = time.perf_counter()
    again = _workspace(home)
    opened = time.perf_counter() - began
    began = time.perf_counter()
    shown = again.history()
    out["first_load_ms"] = {"open": round(opened * 1000, 2), "history": round((time.perf_counter() - began) * 1000, 2)}
    # The same as the page asks for it, over HTTP: the answer's time and size.
    from fastapi.testclient import TestClient

    client = TestClient(welcome.create_app(home, "bench", workspace=again), base_url="http://127.0.0.1")
    client.get("/api/chat/history", headers={welcome.TOKEN_HEADER: "bench"})
    began = time.perf_counter()
    answer = client.get("/api/chat/history", headers={welcome.TOKEN_HEADER: "bench"})
    out["first_load_ms"]["http"] = round((time.perf_counter() - began) * 1000, 2)
    out["first_load_bytes"] = len(answer.content)
    out["shown_after_restart"] = len(shown["messages"])
    out["first_shown"] = shown["messages"][0]["text"] if shown["messages"] else ""

    # The chat record holding `log` lines, then a turn's commit: the first after a restart and a steady one.
    now = time.time()
    conv = "private:browser:bench"
    log = _log(runtime)
    for start in range(1, args.log + 1, 500):
        log.take(conv, [_row(s, f"line {s} " + ("said about the zebrafinch" if s == 7 else "words " * 10), now)
                        for s in range(start, min(start + 500, args.log + 1))])
    fresh = _log(runtime)
    began = time.perf_counter()
    fresh.take(conv, [_row(args.log + 1, "a new line", now)])
    first_take = time.perf_counter() - began
    steady = []
    for s in range(args.log + 2, args.log + 22):
        began = time.perf_counter()
        fresh.take(conv, [_row(s, "another new line", now)])
        steady.append(time.perf_counter() - began)
    out["log_commit_ms"] = {"first_after_restart": round(first_take * 1000, 2),
                            "steady_median": round(statistics.median(steady) * 1000, 2)}
    began = time.perf_counter()
    found = fresh.search(conv, "zebrafinch", lang="en")
    out["log_search_ms"] = round((time.perf_counter() - began) * 1000, 2)
    out["log_search_found"] = len(found)

    # A forget of the words, as the person's "forget the zebrafinch" reaches the record and the page's copy.
    out["holding_before_forget"] = _bytes_holding(home, SECRET)
    began = time.perf_counter()
    fresh.forget(conv, lambda row: SECRET in row["text"])
    again._scrub_chats({"words": [SECRET], "texts": [], "mark": "en"}, again._main)
    again._save_chat()
    out["forget_ms"] = round((time.perf_counter() - began) * 1000, 2)
    again.close()
    out["holding_after_forget"] = _bytes_holding(home, SECRET)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The errand «给主agent发一个hello» on the chat fixture (tools/chatfixture), through the app's own loop
(mibo/desk/operate.run): does hello reach the program's main agent, in each variant of the fixture?

    python tools/chatfixture_errand.py replay --looks <looks.json> [--home <home>] [--model stub|real]
                                              [--eyes none|look] [--runs N] [--out <file>]
    python tools/chatfixture_errand.py window --home <folder> [--model stub|real] [--out <file>]

replay runs no window: the fixture's looks were read off its real window beforehand (tools/chat_fixture.py
--record, read only, nothing pressed), and a stand-in program serves them, changing as the fixture does (the chat
shown follows a press on the avatar or a chat, the message box holds what was written, Send writes the message to
the log under the chat shown). The send is let through as the app's review would let a send the person asked for
pass. --eyes look sends the window's marked picture beside the lines, as the loop does for a model that takes
pictures.

window starts the fixture and works it through the real program layer (mibo/desk/hands.Hands, with its strip),
the send confirmed on a stand-in card: it needs a screen and moves windows, so it runs on the sandbox's runners,
never on someone's own desktop.

--model real calls the person's model (LLM_BASE_URL, LLM_API_KEY and LLM_MODEL from <home>'s settings) and counts what
each answer cost; stub is a scripted stand-in that presses what the look names or describes. The last lines give,
per variant, how many runs ended with hello in the main agent's chat and nowhere else. Exit 0 when every run did.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from mibo.desk import operate  # noqa: E402

TASK = "给主agent发一个hello"  # copy: voice
PROGRAM = "Lumen"
CHATS = ["Daily notes", "Travel plan", "Code review", "Reading list"]


# --- the models

class Real:
    """The person's model through its OpenAI-compatible service, as tool.use calls it for this loop."""

    def __init__(self, home: Path):
        from mibo import settingsfile
        env = settingsfile.values(home)
        self.url, self.key, self.model = env["LLM_BASE_URL"].rstrip("/"), env["LLM_API_KEY"], env["LLM_MODEL"]
        self.cost = 0.0
        self.calls = 0

    async def __call__(self, system: str, messages: list[dict]) -> str:
        body = {"model": self.model, "messages": [{"role": "system", "content": system}, *messages],
                "temperature": 0.2, "max_tokens": 900, "response_format": {"type": "json_object"},
                "reasoning": {"enabled": False}, "usage": {"include": True}}
        request = urllib.request.Request(self.url + "/chat/completions", json.dumps(body).encode(),
                                         {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        answer = await asyncio.to_thread(lambda: json.load(urllib.request.urlopen(request, timeout=120)))
        self.calls += 1
        self.cost += float((answer.get("usage") or {}).get("cost") or 0)
        return answer["choices"][0]["message"]["content"] or ""


async def stub(system: str, messages: list[dict]) -> str:
    """A scripted stand-in: opens the main agent's chat by the control named so, or by the nameless round one with
    the program's name under it; then writes hello and sends it; then says done."""
    content = messages[-1]["content"]
    text = content if isinstance(content, str) else " ".join(p.get("text", "") for p in content)
    window = text.rsplit("The window now:", 1)[-1]
    if "looked again: the field is empty" in text:
        return json.dumps({"actions": [{"do": "done", "success": True, "result": "hello 已发给主 agent"}]})
    if re.search(r"^Main agent$", window, re.M):
        return json.dumps({"note": "发送 hello", "actions": [{"do": "set", "name": "Message", "text": "hello"},
                                                           {"do": "press", "name": "Send"}]})
    hit = re.search(r'\[(\d+)\] button "Main agent"', window) or re.search(
        r'\[(\d+)\] \w+ \(no name: [^)]*round[^)]*"Lumen" under it', window)
    if hit:
        return json.dumps({"note": "打开主 agent", "actions": [{"do": "press", "id": int(hit.group(1))}]})
    return json.dumps({"actions": [{"do": "ask_person", "question": "没有找到主 agent"}]})  # copy: voice


# --- the stand-in program, from the recorded looks

class Replay:
    """The service's side of the loop (what operate.Remote offers), over a program that changes as the fixture
    does. `log` is what the fixture would have written: {"chat", "text"} per message sent."""

    def __init__(self, looks: dict):
        self.looks = looks
        self.chat = CHATS[0]
        self.box = ""
        self.history: dict[str, list[str]] = {}
        self.log: list[dict] = []
        self.pictures = 0

    def record(self) -> dict:
        return self.looks[self.chat]

    def state(self) -> str:
        lines = self.record()["state"].splitlines()
        at = next(i for i, line in enumerate(lines) if re.match(r'\[\d+\] textbox "Message"', line))
        if self.box:
            lines[at] = lines[at].replace('textbox "Message"', f'textbox "Message" = "{self.box}"', 1)
        return "\n".join(lines[:at] + self.history.get(self.chat, []) + lines[at:])

    async def begin(self, task, program, sight="", also=()):
        return {"status": "ok", "program": PROGRAM, "line": f"[ok] using {PROGRAM}"}

    async def look(self) -> dict:
        rec = self.record()
        return {"status": "ok", "state": self.state(), "controls": rec["controls"], "program": PROGRAM,
                "named": rec["named"], "drawn": rec["drawn"], "unnamed": rec["unnamed"], "others": []}

    def _post(self, op: str, timeout: float, **fields) -> dict:
        if op == "picture":
            self.pictures += 1
            return {"status": "ok", "picture": self.record()["picture"]}
        return {"status": "ok"}

    async def step(self, n, note):
        return None

    async def end(self, status, note):
        return None

    def _target(self, a: dict) -> tuple[str, str, str]:
        """(id, role, name) of the control an action names, or raises with the line hands.py gives."""
        nodes = self.record()["nodes"]
        name = " ".join(str(a.get("name") or "").split()).lower()
        ident = str(a.get("id") or "")
        if ident in nodes and (not name or nodes[ident][1].lower() in (name, "")):
            return ident, *nodes[ident]
        hits = [i for i, (_r, n) in nodes.items() if name and n.lower() == name]
        if len(hits) == 1:
            return hits[0], *nodes[hits[0]]
        if name and name in [line.strip().lower() for line in self.state().splitlines()]:
            raise LookupError(f"[failed] \"{a.get('name')}\" is text the window shows, not a control: there is "
                              "nothing to press or fill there. It is what the window shows now: when it is what you "
                              "meant to open, that is open; go on with the task")
        unnamed = self.record()["unnamed"]
        raise LookupError(f"[failed] no control is named \"{a.get('name') or ident}\" in the latest look; nothing was "
                          f"done. Use a name exactly as the look prints it"
                          + (". These controls have no name and may be it, by how they look and where they are: "
                             + "; ".join(unnamed[:4]) + "; name one by its id" if unnamed else ""))

    def _send(self, line: str) -> dict:
        self.log.append({"chat": self.chat, "text": self.box})
        self.history.setdefault(self.chat, []).append(self.box)
        self.box = ""
        return {"status": "ok", "proved": True, "outside": True,
                "line": f"{line} (send, passed by the app's review; done once); looked again: the field is empty"}

    def act(self, a: dict) -> dict:
        do = str(a.get("do") or "")
        if do in ("look", "wait", "scroll", "focus"):
            return {"status": "ok", "line": f"[ok] {do}", "proved": False}
        if do == "key":
            if str(a.get("key") or "").lower() == "enter" and self.box:
                return self._send("[ok] key Enter")
            return {"status": "ok", "line": f"[ok] key {a.get('key')} -> the window shows no change yet"}
        try:
            ident, role, name = self._target(a)
        except LookupError as e:
            return {"status": "failed", "line": str(e), "proved": False}
        what = f"[{ident}] {role}" + (f' "{name}"' if name else "")
        if do in ("set", "type", "append", "paste") and role == "textbox":
            self.box = (self.box + str(a.get("text") or "")) if do in ("append", "type") else str(a.get("text") or "")
            return {"status": "ok", "proved": True,
                    "line": f"[ok] {do} {what} = \"{a.get('text')}\", read back and it holds exactly that"}
        if do in ("press", "click", "invoke", "select", "double"):
            before = self.chat
            if role == "button" and name == "Send":
                if self.box:
                    return self._send(f"[ok] press {what} (pressed once)")
                return {"status": "ok", "line": f"[ok] press {what} (pressed once) -> the window shows no change yet"}
            if ident == "1":
                self.chat = "main"
            elif role == "listitem":
                self.chat = name
            if self.chat != before:
                was = "Lumen" if before == "main" else before
                now = "Lumen" if self.chat == "main" else self.chat
                return {"status": "ok", "line": f"[ok] press {what} (pressed once) -> \"{was}\" now reads \"{now}\""}
            return {"status": "ok", "line": f"[ok] press {what} (pressed once) -> the window shows no change yet"}
        return {"status": "failed", "line": f"[failed] {do} {what}: not a {do} control"}

    async def batch(self, actions: list[dict], look: bool = True) -> dict:
        results, why = [], ""
        for a in actions:
            if why:
                results.append({"status": "skipped", "line": f"[not done] {a.get('do')}: {why}"})
                continue
            r = self.act(a)
            results.append(r)
            if r["status"] != "ok":
                why = "the action before it did not go through"
        if not look and all(r.get("proved") for r in results):
            return {"status": "ok", "results": results}
        return {"status": "ok", "results": results, "look": await self.look()}


def verdict(log: list[dict]) -> bool:
    """hello reached the main agent's chat, and nothing went to another chat."""
    return any(e["chat"] == "main" and "hello" in e["text"].lower() for e in log) and all(
        e["chat"] == "main" for e in log)


def replay(args) -> int:
    looks = json.loads(Path(args.looks).read_text(encoding="utf-8"))
    model = Real(Path(args.home)) if args.model == "real" else None
    rows = []
    for variant in ("named", "bare"):
        for run in range(args.runs):
            program = Replay(looks[variant])
            began = time.monotonic()
            outcome = asyncio.run(operate.run(model or stub, program, TASK, PROGRAM, lang="zh",
                                              eyes="" if args.eyes == "none" else args.eyes))
            rows.append({"variant": variant, "run": run, "status": outcome["status"], "calls": outcome["calls"],
                         "log": program.log, "ok": verdict(program.log), "pictures": program.pictures,
                         "seconds": round(time.monotonic() - began, 1), "result": outcome["result"][:200],
                         "steps": [s[:240] for s in outcome["steps"]]})
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    return report(rows, args, model)


def window(args) -> int:
    """The fixture's real window, worked through Hands on its worker thread, as desk_manners works its programs."""
    import chat_fixture
    from concurrent.futures import ThreadPoolExecutor
    from mibo.desk import hands as hands_mod
    from mibo.desk import win32
    home = Path(args.home)
    model = Real(home) if args.model == "real" else None
    worker = ThreadPoolExecutor(1, thread_name_prefix="desk")
    rows = []
    for variant in ("named", "bare"):
        log = home / f"errand-{variant}.log"
        log.unlink(missing_ok=True)
        proc, hwnd = chat_fixture.start(home, variant, log, corner=False)
        cards: list[dict] = []
        try:
            def begin():
                win32.dpi_aware()
                h = hands_mod.Hands(home / "runtime" / "desktop", ask=lambda card: cards.append(card) or {"yes": True},
                                    who="Mibo", lang="zh")
                h.begin(TASK)
                assert h.use(hwnd)["status"] == "ok"
                return h
            hands = worker.submit(begin).result()

            class Local:
                async def begin(self, task, program, sight="", also=()):
                    return {"status": "ok", "program": PROGRAM, "line": f"[ok] using {PROGRAM}"}

                def _post(self, op, timeout, **fields):
                    return worker.submit(hands.picture).result() if op == "picture" else {"status": "ok"}

                def __getattr__(self, op):
                    async def call(*a):
                        return await asyncio.wrap_future(worker.submit(getattr(hands, op), *a))
                    return call
            outcome = asyncio.run(operate.run(model or stub, Local(), TASK, PROGRAM, lang="zh"))
            time.sleep(0.5)
            sent = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
            rows.append({"variant": variant, "status": outcome["status"], "calls": outcome["calls"], "log": sent,
                         "ok": verdict(sent), "cards": [c.get("kind") for c in cards],
                         "steps": [s[:240] for s in outcome["steps"]]})
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
        finally:
            worker.submit(hands.end, "done", "").result()
            chat_fixture.stop(proc)
    return report(rows, args, model)


def report(rows: list[dict], args, model) -> int:
    if args.out:
        Path(args.out).write_text(json.dumps({"rows": rows, "cost": model.cost if model else 0}, ensure_ascii=False,
                                             indent=1), encoding="utf-8")
    for variant in ("named", "bare"):
        mine = [r for r in rows if r["variant"] == variant]
        print(f"{variant}: hello reached the main agent in {sum(r['ok'] for r in mine)}/{len(mine)} runs; "
              f"statuses {[r['status'] for r in mine]}, model calls {[r['calls'] for r in mine]}")
    if model:
        print(f"cost ${model.cost:.4f} over {model.calls} calls")
    ok = all(r["ok"] for r in rows)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["replay", "window"])
    parser.add_argument("--looks", default="")
    parser.add_argument("--home", default="")
    parser.add_argument("--model", default="stub", choices=["stub", "real"])
    parser.add_argument("--eyes", default="none", choices=["none", "look"])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    return replay(args) if args.mode == "replay" else window(args)


if __name__ == "__main__":
    sys.exit(main())

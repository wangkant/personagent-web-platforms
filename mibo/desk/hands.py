"""One task in another program, asked for by the person in chat: the never list,
the look, every action through the gate, the card for what has an effect
outside, the strip, the stop and the log, put together.

`Hands` is the shape web/operate.py's loop drives (`begin`, `look`, `act`,
`step`, `end`, and the statuses it knows), plus `use` to take the program's
window. It lives in the app's process, where the card can be shown;
`ask(card)` shows the person a card and returns their answer, or None when
nobody answered. Nothing the model sends counts as consent: only that answer.
"""
from __future__ import annotations

import asyncio
import difflib
import os
import re
import secrets
import time
from pathlib import Path
from typing import Callable, Optional

from ..cardwait import WAIT_S
from ..web.pageops import clip, quote
from . import gate, grants as g, names, uia, win32
from .strip import Strip

# The ways to press a control, and the state "check" and "uncheck" bring a tick box to.
PRESSES = {"invoke", "click", "press", "toggle", "check", "uncheck", "select", "expand", "collapse"}
TICKS = {"check": True, "uncheck": False}
# Items of a file's right-click menu that change no file and start nothing (Rename only opens the name for
# editing; the new name asks); every other one asks on a card.
MENU_FREE = re.compile(r"copy|copy as path|properties|view|sort by|group by|refresh|show more options|rename|"
                       r"复制|复制文件地址|复制为路径|属性|查看|排序方式|分组依据|刷新|显示更多选项|重命名", re.I)
# What read only and a view tier still do; the keys a click tier still presses (they move, they type nothing).
LOOKS = {"look", "wait", "switch", "scroll", "focus"}
NAV_KEYS = re.compile(r"(?:shift\+)?(?:tab|up|down|left|right|pageup|pagedown|home|end|esc|escape)", re.I)
# A change that ends a reply's run of actions: what comes after was planned on a window that is no longer so.
MOVED = ("a window opened", "a window closed", "the window closed")
EDITING = "the name is still being edited, the file is not renamed yet: the key Enter renames it"
# How long a program is given to show what a confirmed action did, before the line says it does not show yet.
PROOF_S = 1.5
# In an action's line: the program's own state was read after it and agrees with what was asked.
PROVED = ("read back", "looked again: the field is empty", "now shows as", "no longer show")
# The common Save and Open dialogs: the file name box and the address band, in English and Chinese Windows.
FILE_NAME = re.compile(r"(?:file name|文件名)\s*(?:\(&?\w\))?\s*[:：]?", re.I)
ADDRESS = re.compile(r"(?:address|地址)\s*[:：]\s*(.+)", re.I)
OFF_TEXT = {"zh": "电脑操作未开启：请先在设置中开启“允许{who}操作电脑上的其他程序”。",
            "en": "Operating other programs is off: turn it on in Settings first."}


class Hands:
    def __init__(self, desktop: Path, *, ask: Callable[[dict], Optional[dict]], who: str = "Mibo",
                 lang: str = "zh", review: Optional[Callable[[dict], object]] = None):
        from ..acting import Modes
        self.grants = g.Grants(desktop)
        self.modes = Modes(desktop)
        # Auto mode's reviewer on one action (review.decide's Verdict), for what acting.decide sends it.
        self.review = review
        # The windows where this task typed into a one-line field: their buttons may send it.
        self.typed_tops: set[int] = set()
        self.ask = ask
        self.who = who
        self.lang = "zh" if lang.startswith("zh") else "en"
        self.task = ""
        self.target = None
        self.target_id = 0
        self.view: uia.View | None = None
        self.strip: Strip | None = None
        self.program: dict = {}
        # The programs of the task (at most three, panel.PARTS), the one in use among them: {"name", "key", "hwnd"}.
        self.parts: list[dict] = []
        self.sight = None
        self.sees = ""

    # --- the task

    def begin(self, task: str) -> dict:
        """Only from a task the person asked for in chat; never on a timer."""
        if self.modes.mode() == "off":
            return {"status": "off", "line": OFF_TEXT[self.lang].format(who=self.who)}
        self.end("replaced", "")
        self.task = " ".join(str(task).split())[:300]
        self.typed_tops = set()
        return {"status": "ok"}

    def held(self) -> str:
        if not self.task or self.modes.mode() == "off":
            return "stopped"
        if self.strip and self.strip.halted:
            return self.strip.halted
        return ""

    def step(self, n: int, note: str) -> None:
        if self.strip:
            self.strip.say(note)

    def end(self, status: str, note: str) -> None:
        if self.strip:
            self.strip.close()
        if self.view and self.task:
            self._log("end", "", status, note[:200])
        self.strip = self.view = self.sight = None
        self.program = {}
        self.parts = []
        self.task = "" if status != "replaced" else self.task

    # --- the program

    def use(self, hwnd: int, sight: str = "") -> dict:
        """Attach to a program's window, unless the program is never operated. `sight` names the model when it
        takes pictures, else ""."""
        self.sees = sight
        if not self.task:
            return {"status": "stopped", "line": "[stopped] no task asked for in chat"}
        prog, refused = self._grant(hwnd)
        if refused:
            return refused
        self._attach(hwnd, prog)
        return {"status": "ok", "sight": bool(self.sees), "program": prog["name"], "line": f"[ok] using {prog['name']}"}

    def also(self, hwnd: int) -> dict:
        """Another program the task moves to (read in one, typed or pasted in another), granted as `use` grants; the window
        in use stays, and the switch action goes to this one."""
        if not self.task:
            return {"status": "stopped", "line": "[stopped] no task asked for in chat"}
        prog, refused = self._grant(hwnd)
        if refused:
            return refused
        key = g.key_of(prog)
        if all(p["key"] != key for p in self.parts):
            self.parts.append({"name": prog["name"], "key": key, "hwnd": hwnd})
        return {"status": "ok", "program": prog["name"], "line": f"[ok] {prog['name']} is in this task too"}

    def _grant(self, hwnd: int) -> tuple[dict, dict | None]:
        """(the program of `hwnd`, None) when it may be used for the task, or ({}, the result that ends it): any
        program off the never lists, with no card to grant it."""
        prog = g.identify(g.program_pid(hwnd))
        why = g.never(prog, hwnd)
        if not why:
            # Cards, the strip, Settings and the log call it what Start does (文件资源管理器, not Windows Explorer).
            shown = names.start_name(prog["exe"])
            word = g.NEVER_WORDS.search(shown)
            if word:
                why = f"Start calls it {shown!r}, and {word.group()!r} is on the never list"
            elif shown:
                prog = {**prog, "name": shown}
        word = "" if why else self.grants.blocked_by(prog)
        if word:
            why = f"its name holds {word!r}, which is on the person's own never list"
        if why:
            self.grants.log({"do": "use", "program": prog.get("name", ""), "exe": prog.get("exe", ""),
                             "status": "refused", "line": why})
            return {}, {"status": "refused", "line": f"[refused] {prog.get('name') or 'this program'} is never "
                                                     f"operated: {why}"}
        return prog, None

    def _attach(self, hwnd: int, prog: dict) -> None:
        """The window this task's actions go to from now on, with the strip round it."""
        if win32.minimized(hwnd):
            # A program kept minimised (a chat program most of the day) comes back to its place, not to the front.
            win32.restore(hwnd)
            end = time.monotonic() + 2
            while win32.minimized(hwnd) and time.monotonic() < end:
                time.sleep(0.05)
        pid = g.program_pid(hwnd)
        base = Path(prog["exe"]).name.lower()
        self.view = uia.View(hwnd, pid, shared=base in g.SHARED, halted=self.held,
                             never=lambda h: g.window_never(base, h), busy=lambda: self.strip.busy())
        if self.strip:
            self.strip.close()
        self.sight = None
        self.strip = Strip(hwnd, prog["name"], who=self.who, lang=self.lang, windows=self.view.windows)
        self.program = prog
        key = g.key_of(prog)
        self.parts = [p for p in self.parts if p["key"] != key] + [{"name": prog["name"], "key": key, "hwnd": hwnd}]
        self._log("use", "", "ok", "")

    def _switch(self, asked: str) -> dict:
        """The switch action: another program of the task becomes the one in use, through its window when that
        is still a window of the same program (as a grant names it), else its front-most open window."""
        others = [p for p in self.parts if p["key"] != g.key_of(self.program)]
        listed = ", ".join(p["name"] for p in others) or "none"
        part = max(others, key=lambda p: names.score(asked, p["name"]), default=None)
        if part is None or names.score(asked, part["name"]) < names.FOUND:
            return {"status": "failed", "line": f"[failed] {asked[:40]!r} is not another program of this task "
                                                f"(they are: {listed})"}
        hwnd = part["hwnd"]
        if not (win32.alive(hwnd) and g.key_of(g.identify(g.program_pid(hwnd))) == part["key"]):
            hwnd = next((w.hwnd for w in names.open_windows()
                         if g.key_of(g.identify(g.program_pid(w.hwnd))) == part["key"]), 0)
        if not hwnd:
            return {"status": "failed", "line": f"[failed] no window of {part['name']} is open now"}
        prog = g.identify(g.program_pid(hwnd))
        why = g.never(prog, hwnd) or self.grants.blocked_by({**prog, "name": part["name"]})
        if why:
            return {"status": "refused", "line": f"[refused] that window of {part['name']} is never operated: {why}"}
        self._attach(hwnd, {**prog, "name": part["name"]})
        return {"status": "ok", "line": f"[ok] switch to {part['name']}: its window is the one read and acted in now"}

    # --- looking and acting

    def look(self) -> dict:
        why = self.held()
        if why:
            return {"status": why, "line": f"[{why}] {self.strip.why if self.strip else ''}".strip()}
        if not self.view:
            return {"status": "failed", "line": "[failed] no program in use: use one first"}
        try:
            state = self._read()
        except uia.Refused as e:
            return {"status": "failed", "line": str(e)}
        return {"status": "ok", "state": state, "controls": self.view.interactive, "program": self.program["name"],
                "named": self.view.named, "drawn": self.view.drawn, "unnamed": self.view.unnamed,
                "others": [p["name"] for p in self.parts if p["key"] != g.key_of(self.program)]}

    def _read(self) -> str:
        """A look; once more when the program's elements went away under the read (a menu closing, a list
        drawn again)."""
        try:
            return self.view.look()
        except uia.Refused:
            raise
        except Exception:
            time.sleep(0.3)
            try:
                return self.view.look()
            except uia.Refused:
                raise
            except Exception as e:
                raise uia.Refused(f"[failed] the window could not be read: {str(e).splitlines()[0][:120]}") from None

    def picture(self) -> dict:
        jpeg, marks = self.view.picture()
        return {"status": "ok", "picture": jpeg, "marks": marks}

    def batch(self, actions: list[dict], look: bool = True) -> dict:
        """The actions of one reply, in order, each checked as it runs. The first that does not go through,
        or that opens or closes a window, ends them: what follows was planned on a window that is no longer
        so, and is reported as not done, one line each, never dropped in silence. Then one look, so the next
        reply starts from the window as it is; none when the caller says every action reads itself back and
        that turns out so."""
        results: list[dict] = []
        why = ""
        for a in actions:
            if why:
                results.append({"status": "skipped", "line": f"[not done] {self._brief(a)}: {why}"})
                continue
            r = self.act(a)
            if r["status"] == "paused" and self._resumed():
                r = {"status": "skipped", "line": f"[not done] {self._brief(a)}: {r['line']}; then the person "
                                                  f"pressed Continue: plan again from the window below"}
            elif r["status"] == "paused":
                # Stopped or taken over while paused, or no Continue in time.
                r = {**r, "status": self.strip.halted, "line": f"[{self.strip.halted}] {self.strip.why}"}
            results.append(r)
            if r["status"] != "ok":
                why = "the action before it did not go through"
            elif a.get("do") == "switch":
                why = "it was planned on the window before the switch"
            elif any(word in r["line"] for word in MOVED):
                if self.view.unfolded():
                    # A menu or a pop-up the press unfolded: read, and what follows goes on by the names it
                    # shows (each still checked against the window, and asked on a card where it must be).
                    try:
                        self._read()
                        continue
                    except uia.Refused:
                        pass
                why = "a window opened or closed before it"
        if not look and all(r.get("proved") for r in results):
            return {"status": "ok", "results": results}
        return {"status": "ok", "results": results, "look": self.look()}

    def _resumed(self) -> bool:
        """While the strip is paused, wait for the person's Continue on it (or Stop, take over, the turn ending)
        as long as a card waits: True once they pressed Continue."""
        end = time.monotonic() + WAIT_S
        while self.strip.halted == "paused" and time.monotonic() < end:
            time.sleep(0.1)
        return not self.strip.halted

    @staticmethod
    def _brief(a: dict) -> str:
        return " ".join(str(v)[:40] for v in (a.get("do"), a.get("name") or a.get("id") or a.get("key") or "") if v)

    def act(self, action: dict) -> dict:
        """One action: {"status", "line"}, plus "kind" and "outside" for one the person confirmed, "target"
        (the control's kind and name) and "proved" when the program's own state was read back and agrees."""
        why = self.held()
        if why:
            return {"status": why, "line": f"[{why}] {self.strip.why if self.strip else ''}".strip()}
        if not self.view:
            return {"status": "failed", "line": "[failed] no program in use"}
        do = str(action.get("do") or "")
        self.target = None
        limit = self._limit(do, action)
        if limit:
            self._log(do, "", "refused", limit, named=str(action.get("name") or "")[:80])
            return {"status": "failed", "line": limit, "proved": False}
        if do not in ("look", "wait", "switch") and self.view.seen and self.view.moved():
            # A dialog that came up by itself, or a window that went: nothing is pressed or typed into a
            # window the model has not seen, and a key least of all (it would answer the dialog).
            try:
                self._read()
            except uia.Refused as e:
                return {"status": "failed", "line": str(e), "proved": False}
            line = ("[stale] a window of the program opened or closed since your look; nothing was done. The window "
                    "is read again below")
            self._log(do, "", "failed", line, named=str(action.get("name") or "")[:80])
            return {"status": "failed", "proved": False, "line": line}
        if self.strip:
            self.strip.own_until = time.monotonic() + 30
        try:
            result = self._act(do, action)
        except uia.Refused as e:
            line = str(e)
            status = line[1:line.index("]")] if line.startswith("[") and "]" in line else "refused"
            result = {"status": status if status in ("stopped", "paused", "handover") else (
                "refused" if status == "refused" else "failed"), "line": line}
        except (KeyError, ValueError, TypeError) as e:
            result = {"status": "failed", "line": f"[failed] {do}: {e}"}
        if result.get("status") == "ok" and result["line"].startswith("[failed]"):
            result["status"] = "failed"
        if self.strip:
            self.strip.own_until = time.monotonic() + 0.3
        if self.target is not None:
            result["target"] = {"role": self.target.role, "name": self.target.name}
        result["proved"] = result.get("status") == "ok" and any(word in result["line"] for word in PROVED) and (
            EDITING not in result["line"])
        self._log(do, self.view.describe(self.target_id) if self.target is not None else str(action.get("key") or ""),
                  result["status"], result["line"][:300], result.get("kind", ""),
                  text=str(action.get("text") or "")[:200], named=str(action.get("name") or "")[:80])
        return result

    def _limit(self, do: str, a: dict) -> str:
        """Why the person's mode or the program's tier keeps this action from being done ("" when it may be):
        read only and a view tier only look; a click tier presses but types nothing and presses no key that
        types."""
        if do in LOOKS:
            return ""
        tier = self.grants.tier(self.program)
        if self.modes.mode() == "read" or tier == "view":
            why = "the person set other programs to read only" if tier != "view" else (
                f"{self.program['name']} is set to be looked at only")
            return f"[read-only] {do}: {why}, so nothing was done; read and tell them, or ask_person"
        if tier == "click" and (do in ("set", "type", "append", "paste") or (
                do == "key" and not NAV_KEYS.fullmatch(str(a.get("key") or "").replace(" ", "")))):
            return (f"[click-only] {do}: {self.program['name']} takes presses but no typing (what is typed there can "
                    f"run as a command), so nothing was done; ask_person to type it")
        return ""

    def _find(self, a: dict):
        """The id of the control an action means. It is named by the name the look printed, by its id, or
        both: both must be one line of the look, a name must be one control's, and anything else is refused
        with what the look does hold, so nothing is pressed on a guess."""
        v = self.view
        ident = a.get("id")
        if isinstance(ident, str) and ident.isdigit():
            ident = int(ident)
        if isinstance(ident, str):
            return ident
        name = " ".join(str(a.get("name") or "").split())
        role = str(a.get("role") or "").lower()

        def same(n) -> bool:
            return " ".join(n.name.split()).lower() == name.lower()

        def shown(i: int) -> str:
            return v.describe(i) + (f" in {quote(clip(v.nodes[i].where, 30))}" if v.nodes[i].where else "")
        if isinstance(ident, int) and ident in v.nodes:
            # A control with no name is meant by its id: whatever words come with it can only describe it.
            if not name or same(v.nodes[ident]) or not v.nodes[ident].name:
                return ident
            others = [i for i, n in v.nodes.items() if same(n)]
            raise uia.Refused(
                f"[failed] {v.describe(ident)} is not named {quote(clip(name, 40))}"
                + (f"; that name is on {', '.join(shown(i) for i in others[:3])}" if others else
                   "; no control of the look has that name")
                + ". Nothing was done: give the name and the id of one line of the look")
        if not name:
            if ident is not None:
                raise uia.Refused(f"[failed] [{ident}] is not in the latest look; nothing was done")
            return None
        hits = [i for i, n in v.nodes.items() if same(n)]
        hits = [i for i in hits if v.nodes[i].role == role] or hits
        hits = [i for i in hits if v.nodes[i].role in uia.CONTROLS] or hits
        if len(hits) > 1 and a.get("do") in ("set", "append", "type"):
            # Text goes into the field of that name, when one field and something else share it.
            hits = [i for i in hits if v.nodes[i].role in ("textbox", "document")] or hits
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise uia.Refused(f"[failed] {len(hits)} controls are named {quote(clip(name, 40))}: "
                              f"{', '.join(shown(i) for i in hits[:6])}. Nothing was done: add the id of the one meant")
        if any(" ".join(w.split()).lower() == name.lower() for words in v.words.values() for w in words):
            raise uia.Refused(f"[failed] {quote(clip(name, 40))} is text the window shows, not a control: there is "
                              "nothing to press or fill there. It is what the window shows now: when it is what you "
                              "meant to open, that is open; go on with the task")
        names = sorted({n.name for n in v.nodes.values() if n.name})
        near = [n for n in names if name.lower() in n.lower() or n.lower() in name.lower()][:3] or \
            difflib.get_close_matches(name, names, 3, 0.6)
        raise uia.Refused(f"[failed] no control is named {quote(clip(name, 40))} in the latest look"
                          + (f" (nearest: {', '.join(quote(clip(n, 40)) for n in near)})" if near else "")
                          + "; nothing was done. Use a name exactly as the look prints it"
                          + (". These controls have no name and may be it, by how they look and where they are: "
                             + "; ".join(v.unnamed[:4]) + "; name one by its id" if v.unnamed else ""))

    def _act(self, do: str, a: dict) -> dict:
        v = self.view
        if do == "look":
            return {"status": "ok", "line": "[ok] look"}
        if do == "wait":
            return {"status": "ok", "line": v.wait(float(a.get("seconds") or 2), str(a.get("until") or "")[:80])}
        if do == "switch":
            return self._switch(" ".join(str(a.get("program") or a.get("name") or "").split()))
        ident = None if do == "key" else self._find(a)
        if isinstance(ident, int):
            # The element is read again before anything is done with it: still the control the look printed.
            ident = v.live(ident)
            self.target, self.target_id = v.nodes[ident], ident
        if do == "scroll":
            return {"status": "ok", "line": v.scroll(ident, a.get("direction") != "up")}
        if do == "focus":
            return {"status": "ok", "line": v.focus(ident)}
        if do in ("menu", "double"):
            if not isinstance(ident, int):
                return {"status": "failed", "line": f"[failed] {do}: name the control (its name in the look, or its id)"}
            node = v.node(ident)
            if not node.enabled:
                return {"status": "failed", "line": f"[failed] {v.describe(ident)} is disabled"}
            # A double click does what a press does (opens a file); a menu only unfolds, its items ask as presses.
            kind, why = self._press_kind(node, "invoke" if do == "double" else "expand")
            run = (lambda: v.double(ident)) if do == "double" else (lambda: v.menu(ident))
            if kind:
                return self._outside(kind, why, node, "", self._files(node, kind), run)
            return {"status": "ok", "line": run()}
        if do in PRESSES:
            if isinstance(ident, str):
                return self._grid_click(ident)
            if ident is None:
                return {"status": "failed", "line": f"[failed] {do}: name the control (its name in the look, or its id)"}
            node = v.node(ident)
            if not node.enabled:
                return {"status": "failed", "line": f"[failed] {v.describe(ident)} is disabled"}
            kind, why = self._press_kind(node, "toggle" if do in TICKS else "invoke" if do == "press" else do)
            option = str(a.get("option") or "")
            run = {"invoke": lambda: v.invoke(ident), "click": lambda: v.invoke(ident), "press": lambda: v.invoke(ident),
                   "toggle": lambda: v.toggle(ident), "check": lambda: v.toggle(ident, True),
                   "uncheck": lambda: v.toggle(ident, False), "select": lambda: v.select(ident, option),
                   "expand": lambda: v.expand(ident, True), "collapse": lambda: v.expand(ident, False)}[do]
            if kind:
                source = self._composer(node) if kind in ("send", "post", "submit") and not option else None
                return self._outside(kind, why, node, option or self._said(source), self._files(node, kind), run,
                                     source)
            return {"status": "ok", "line": run()}
        if do in ("set", "type", "append", "paste"):
            text = str(a.get("text") or "")
            if do == "paste" and not text:
                return {"status": "failed", "line": "[failed] paste: give the text to put in"}
            node = v.node(ident) if ident is not None else v.focused()
            if node is None:
                return {"status": "failed", "line": "[failed] nothing has the focus to type into"}
            if ident is None and do not in ("type", "paste"):
                return {"status": "failed", "line": f"[failed] {do}: name the field (its name in the look, or its id)"}
            if node.secret:
                where = v.describe(ident) if ident is not None else "the focused field"
                return {"status": "refused", "line": f"[refused] {where} is a password field: it is for the person "
                                                     f"to type"}
            if any(ch < " " and ch not in "\r\n" for ch in text):
                return {"status": "failed", "line": "[failed] tabs and control characters are not typed (a Tab moves "
                                                    "to another control); use the key action"}
            if "\n" in text and node.role == "textbox" and not (node.hwnd and win32.multiline(node.hwnd)):
                return {"status": "failed", "line": "[failed] a line break in a one-line field would press Enter; "
                                                    "type the text, then the key Enter"}
            if self._address_bar(node) and not gate.address_ok(text):
                return {"status": "refused", "line": "[refused] File Explorer's address bar runs what is typed; only "
                                                     "an existing folder goes there"}
            run = {"set": lambda: v.set_value(ident, text), "append": lambda: v.set_value(ident, text, True),
                   "type": lambda: v.type(ident, text), "paste": lambda: v.paste(ident, text)}[do]
            kind, why = gate.set_text(node, text, win32.title_of(node.top))
            if kind:
                return self._outside(kind, why, node, text, self._files(node, kind), run)
            line = run()
            if node.role == "textbox" and not (node.hwnd and win32.multiline(node.hwnd)) and line.startswith("[ok]"):
                # A one-line field of a form: its window's buttons may now send what was typed.
                self.typed_tops.add(node.top)
            if node.role == "textbox" and gate.in_shell_view(node) and line.startswith("[ok]"):
                # A file's name being edited is not the file's name yet.
                line += "; " + EDITING
            return {"status": "ok", "line": line}
        if do == "key":
            keys = str(a.get("key") or "")
            uia.parse_key(keys)
            # A key lands where the program's own focus is once it is in front: judge it there, not on
            # whatever another window has.
            v.forward()
            focused = v.focused()
            top = focused.top if focused else v.hwnd
            hit = self._access_key(keys, focused, top)
            if hit is None and focused is not None and focused.top in v.menus and keys.lower() in ("enter", "space"):
                hit = focused
            if hit is not None:
                # A key that presses a button (Alt+D for &Delete, Enter on a menu item) is judged as that press.
                kind, why = self._press_kind(hit, "invoke")
                if kind:
                    source = self._composer(hit) if kind in ("send", "post", "submit") else None
                    return self._outside(kind, why, hit, self._said(source), self._files(hit, kind),
                                         lambda: v.key(keys), source)
            kind, why = gate.key(keys, focused, win32.title_of(top), top == v.hwnd, v.texts(top))
            if kind == "submit" and self._address_bar(focused) and gate.address_ok(self._said(focused)):
                kind, why = "", "Enter in the address bar goes to an existing folder"
            if kind == "open" and focused is not None and self._is_folder(focused):
                kind, why = "", "Enter opens a folder in the same window"
            if kind:
                said = self._said(focused)
                # With the program not in front there is no focus to read: the items selected in its file view.
                files = self._files(focused, kind, said) if focused is not None else self._picked(v.hwnd)
                return self._outside(kind, why, focused, said, files, lambda: v.key(keys))
            return {"status": "ok", "line": v.key(keys)}
        return {"status": "failed", "line": f"[failed] no action {do[:20]!r}"}

    def _press_kind(self, node, do: str) -> tuple[str, str]:
        """(kind, why) of pressing `node` as the gate judges it. An item of a menu opened on a file acts on that
        file: unless it only copies, shows or unfolds, it asks on a card whatever its name."""
        v = self.view
        kind, why = gate.press(node, win32.title_of(node.top), node.top == v.hwnd, v.texts(node.top),
                               self._is_folder(node), do, self._base(), node.top in self.typed_tops)
        origin = v.menus.get(node.top)
        if kind == "press" and origin is not None and gate.in_shell_view(origin):
            # A file's own menu: the rule below decides by MENU_FREE.
            kind = ""
        name = re.sub(r"\s*\(&?\w\)|&|\.\.\.|…", "", node.name).strip()
        if not kind and origin is not None and gate.in_shell_view(origin) and do not in ("expand", "collapse") and (
                "expand" not in node.patterns and not MENU_FREE.fullmatch(name)):
            kind = "open" if gate.OPEN_WORDS.search(name) or re.search(r"run as|以管理员", name, re.I) else "file"
            why = f"{name!r} in the menu of {origin.name!r} acts on the file"
        return kind, why

    def _access_key(self, keys: str, focused, top: int):
        """The control of window `top` whose access key `keys` is: Alt+Y, or Y alone where the focus is not
        in a text field (a dialog takes it so)."""
        parts = [p.strip().lower() for p in keys.split("+")]
        if len(parts[-1]) != 1 or not parts[-1].isalnum() or parts[:-1] not in ([], ["alt"]):
            return None
        if not parts[:-1] and focused is not None and focused.role in ("textbox", "document"):
            return None
        want = f"alt+{parts[-1]}"
        A = uia.automation()
        for n in self.view.nodes.values():
            if n.top != top or n.role not in ("button", "menuitem", "checkbox", "radio", "link", "tab", "listitem"):
                continue
            try:
                held = str(n.el.GetCurrentPropertyValue(A.AutomationElement.AccessKeyProperty) or "")
            except Exception:
                continue
            if held.replace(" ", "").lower() == want:
                return n
        return None

    def _grid_click(self, ident: str) -> dict:
        """A point of the picture's grid: what UI Automation finds there decides as a press would."""
        v = self.view
        if ident not in v.grid:
            return {"status": "failed", "line": f"[failed] {ident} is not a point of the latest picture"}
        x, y = v.grid[ident]
        A = uia.automation()
        from ..see.act import _point_type
        Point = _point_type()
        el = A.AutomationElement.FromPoint(Point(x, y))
        if el is None or el.Current.ProcessId not in v.pids:
            return {"status": "refused", "line": f"[refused] {ident} is not over this program's window"}
        kind_name = el.Current.ControlType.ProgrammaticName.split(".")[-1]
        node = uia.Node(el=el, role=uia.ROLES.get(kind_name, "custom"), name=el.Current.Name, auto="",
                        cls=el.Current.ClassName, pid=el.Current.ProcessId, enabled=True, focused=False,
                        secret=el.Current.IsPassword, rect=(x, y, 0, 0), hwnd=0, patterns=[], readonly=False,
                        top=v.hwnd, parent=None, cells=[])
        kind, why = gate.press(node, win32.title_of(v.hwnd), True, "", None, "click", self._base())
        if kind:
            return self._outside(kind, why, node, "", [], lambda: v.invoke(ident))
        return {"status": "ok", "line": v.invoke(ident)}

    # --- an action with an effect outside

    def _composer(self, node):
        """The text box a Send or Post button sends from: the nearest field of its window that holds text
        (never a password field), or None."""
        x, y = node.center()
        boxes = [n for n in self.view.nodes.values() if n.top == node.top and n.role in ("textbox", "document")
                 and not n.secret and not n.readonly and n is not node and self._said(n)]
        return min(boxes, key=lambda n: abs(n.center()[1] - y) * 3 + abs(n.center()[0] - x), default=None)

    def _outside(self, kind: str, why: str, node, text: str, files: list[str], run: Callable[[], str],
                 source=None) -> dict:
        """The person's press on a card first, each time (in auto mode the reviewer may pass what acting.decide
        sends it); then the action exactly once; then a look again. `source` is the field the text comes from,
        when it is not the target itself."""
        from .. import acting
        v = self.view
        name = node.name if node is not None else ""
        before = (self._fingerprint(node), self._fingerprint(source))
        window = clip(win32.title_of(node.top if node else v.hwnd), 120)
        how = "confirmed by the person"
        answer = verdict = None
        if self.review is not None and acting.decide(self.modes.mode(), kind, "", "programs") == "review":
            verdict = self.review({"kind": kind, "where": f"{self.program['name']} — {window}",
                                   "target": files[:50] or ([name] if name else []), "text": text[:2000],
                                   "page_says": name})
            if verdict.decision == "refuse":
                return {"status": "declined", "kind": kind,
                        "line": f"[declined] {kind} ({clip(name, 40)}), nothing was done. {verdict.declined()}"
                                + ("; turned down three times in a row: stop and hand the task back" if verdict.stop
                                   else "")}
            if verdict.decision == "go":
                answer, how = {"yes": True}, verdict.line(self.lang)
        if answer is None:
            try:
                picture = base64_jpeg(v)
            except Exception:
                picture = ""
            card = {"id": secrets.token_urlsafe(8), "type": "desk_action", "kind": kind,
                    "program": self.program["name"], "exe": self.program["exe"], "window": window,
                    "target": f"{node.role} {quote(clip(name, 80))}" if node is not None else "",
                    "text": text[:2000], "files": files[:50], "why": why[:200], "task": self.task, "picture": picture,
                    # Why the reviewer did not pass it, in one line.
                    **({"review": verdict.line(self.lang)} if verdict is not None else {})}
            answer = self.ask(card)
        if answer is None:
            return {"status": "declined", "kind": kind,
                    "line": f"[declined] nobody answered the card for {kind} ({clip(name, 40)}); nothing was done"}
        if not answer.get("yes"):
            return {"status": "declined", "kind": kind,
                    "line": f"[declined] the person said no to {kind} ({clip(name, 40)}); nothing was done"}
        if self.held():
            return {"status": self.held(), "kind": kind, "line": f"[{self.held()}] nothing was done"}
        if (self._fingerprint(node), self._fingerprint(source)) != before:
            return {"status": "failed", "kind": kind,
                    "line": "[changed] the window changed since the card; nothing was done, look again"}
        line = run()
        if not line.startswith("[ok]"):
            return {"status": "failed", "kind": kind, "line": line}
        proof = self._proof(kind, source or node, text, files)
        return {"status": "ok", "kind": kind, "outside": True, "line": f"{line} ({kind}, {how}; done once){proof}"}

    def _fingerprint(self, node) -> tuple:
        """What the card showed of the target, read again: its name and, for a field, what it holds."""
        if node is None:
            return ()
        try:
            name = str(node.el.Current.Name)
            held = "" if node.secret else self._said(node)
            return name, held
        except Exception:
            return ("gone",)

    def _said(self, node) -> str:
        """What a field or document holds now (never a password field's), or ""."""
        A = uia.automation()
        if node is None or node.secret:
            return ""
        try:
            if node.el.GetCurrentPropertyValue(A.AutomationElement.IsValuePatternAvailableProperty):
                return str(node.el.GetCurrentPropertyValue(A.ValuePattern.ValueProperty) or "")[:2000]
            if node.el.GetCurrentPropertyValue(A.AutomationElement.IsTextPatternAvailableProperty):
                return str(node.el.GetCurrentPattern(A.TextPattern.Pattern).DocumentRange.GetText(2000))
        except Exception:
            return ""
        return ""

    def _proof(self, kind: str, node, text: str, files: list[str]) -> str:
        """Done is not proven by the press: look again. A file view shows the new name or the files gone; a
        field that sent its text is empty again. The program is given PROOF_S to show it, read until it does."""
        end = time.monotonic() + PROOF_S
        if kind in ("send", "submit") and node is not None and text:
            while self._said(node) and time.monotonic() < end:
                time.sleep(0.05)
            if not self._said(node):
                return "; looked again: the field is empty"
            return "; looked again: the field still holds the text, so look before saying it went, and never press again"
        while True:
            try:
                self.view.look()
            except uia.Refused as e:
                return f"; {e}"
            names = {n.name for n in self.view.nodes.values() if n.role in ("listitem", "row", "treeitem")}
            shown = (kind == "file" and files and text in names and files[0] not in names) or (
                kind == "delete" and files and names and not any(f in names for f in files))
            if shown or not ((kind == "file" and text) or kind == "delete") or not files or time.monotonic() >= end:
                break
            time.sleep(0.2)
        if kind == "file" and files and text:
            if text in names and files[0] not in names:
                return f"; looked again: {files[0]!r} now shows as {text!r}"
            return "; looked again: the new name does not show yet, so look before saying it is done, and never do it again"
        if kind == "delete" and files and names:
            gone = [f for f in files if f not in names]
            return (f"; looked again: {len(gone)} of {len(files)} no longer show" if gone else
                    "; looked again: still showing, so look before saying it is done, and never do it again")
        return "; looked again"

    # --- File Explorer and the file dialogs

    def _base(self) -> str:
        return Path(self.program.get("exe", "")).name.lower()

    def _address_bar(self, node) -> bool:
        return node is not None and self._base() == "explorer.exe" and node.role in (
            "textbox", "combobox") and not gate.in_shell_view(node) and not gate.SEARCH.search(
                f"{node.name} {node.auto}")

    def _is_folder(self, node) -> bool | None:
        """For an item of a file view, whether its Type column says folder; None when it has no such column."""
        if node.role not in ("listitem", "treeitem", "row") or not gate.in_shell_view(node):
            return None
        if node.role == "treeitem":
            return True
        kinds = [value for _name, value in node.cells]
        return win32.folder_type() in kinds if kinds else None

    def _files(self, node, kind: str, said: str = "") -> list[str]:
        """The files an action touches: the item renamed or opened, else the items selected in the file view
        of the same window (for Delete, Paste and the like)."""
        if node is None:
            return []
        # An item of a right-click menu acts on what the menu was opened on.
        node = self.view.menus.get(node.top, node)
        items = ("listitem", "row", "treeitem")
        if node.parent is not None and node.parent.role in items and gate.in_shell_view(node):
            return [node.parent.name]
        if node.role in items and gate.in_shell_view(node) and kind in ("open", "file"):
            return [node.name]
        if kind not in ("delete", "file", "open"):
            return []
        saved = self._dialog_file(node.top) if kind in ("file", "open") else ""
        if saved:
            return [saved]
        return self._picked(node.top) or ([node.name] if node.role in items and gate.in_shell_view(node) else [])

    def _dialog_file(self, top: int) -> str:
        """In a Save or Open dialog: the file its File name box names, in the folder its address shows."""
        mine = [n for n in self.view.nodes.values() if n.top == top]
        box = next((n for n in mine if n.role == "textbox" and FILE_NAME.fullmatch(n.name)), None)
        name = self._said(box).strip().strip('"') if box is not None else ""
        if not name:
            return ""
        A = uia.automation()
        bars = uia.element(top).FindAll(A.TreeScope.Descendants, A.PropertyCondition(
            A.AutomationElement.ControlTypeProperty, A.ControlType.ToolBar))
        folder = next((m.group(1).strip() for b in bars for m in [ADDRESS.match(uia.norm(b.Current.Name))] if m), "")
        return name if os.path.isabs(name) or not folder else os.path.join(folder, name)

    def _picked(self, top: int) -> list[str]:
        """The items selected in the file view of window `top`."""
        return [n.name for n in self.view.nodes.values() if n.top == top and n.role in ("listitem", "row", "treeitem")
                and gate.in_shell_view(n) and self._selected(n)]

    @staticmethod
    def _selected(n) -> bool:
        A = uia.automation()
        try:
            return bool(n.el.GetCurrentPropertyValue(A.SelectionItemPattern.IsSelectedProperty))
        except Exception:
            return False

    # --- the log

    def _log(self, do: str, target: str, status: str, line: str, kind: str = "", text: str = "",
             named: str = "") -> None:
        """One row of the log: the control pressed, and beside it the name the model gave and the look it
        was read in, so a press that went astray can be traced."""
        self.grants.log({"program": self.program.get("name", ""), "exe": self.program.get("exe", ""),
                         "window": clip(win32.title_of(self.view.hwnd), 80) if self.view else "",
                         "do": do, "target": target, "kind": kind, "status": status,
                         **({"named": named, "look": self.view.serial} if named and self.view else {}),
                         **({"text": text} if text and do in ("set", "type", "append", "paste") else {}), "line": line})

    # --- by sight, where the window has no structure to read

    def see(self, op: str, fields: dict) -> dict:
        """One look or one action by picture (see/): only for a model measured to take pictures, which `use` was
        told, once the person allowed the window's pictures (panel.Desk). Its own cards ask before pointing where
        nothing is named."""
        from ..see import act as see_act
        from ..see import profile, remote
        why = self.held()
        if why:
            return {"status": why, "line": f"[{why}] {self.strip.why if self.strip else ''}".strip()}
        if not self.view or not self.sees:
            return {"status": "refused", "line": "[refused] this window is not worked by picture"}
        if self.sight is None:
            self.sight = see_act.Sight(
                self._surface(), profile.profile(self.sees), task=self.task, lang=self.lang, who=self.who,
                held=self.held, review=self._by_sight,
                ask=lambda card: self.ask({"program": self.program["name"], "exe": self.program["exe"], **card}))
        result = asyncio.run(remote.serve(self.sight, op, fields))
        if result.get("status") == "paused" or (op == "see_look" and self.held() == "paused"):
            # The person used the computer: the run waits for their Continue on the strip, as a batch does.
            if self._resumed():
                result = asyncio.run(remote.serve(self.sight, op, fields)) if op == "see_look" else {
                    "status": "failed", "line": f"[not done] {result.get('line', '')}; then the person pressed "
                                                f"Continue: look again before acting"}
            else:
                result = {**result, "status": self.held() or "paused", "line": f"[{self.held()}] {self.strip.why}"}
        if op == "see_act":
            self._log("see", str((fields.get("action") or {}).get("target") or "")[:80], str(result.get("status")),
                      str(result.get("line") or "")[:300])
        return result

    async def _by_sight(self, kind: str, action: dict):
        """The reviewer on an act by picture that acting.decide sends it, off the picture loop's thread; None when
        the kind is the person's (a delete, paying, an account) or nobody reviews."""
        from .. import acting
        if self.review is None or acting.decide(self.modes.mode(), kind, "", "programs") != "review":
            return None
        return await asyncio.to_thread(self.review, {"kind": kind, **action,
                                                     "where": f"{self.program['name']} — {action.get('where', '')}"})

    def _surface(self):
        """The window in use as picture mode captures and works it."""
        from ..see import act as see_act
        return see_act.WindowSurface(self.view.hwnd, self.view.pids, program=self.program["name"], idle=self.view.idle)

    def sent_picture(self) -> str:
        """The picture a card about pictures shows: taken as the first one to the model service will be, by the same
        surface at the size the person's model gets it (password fields painted over), so the person sees what
        goes; "" when none may be taken now (another program covers the window)."""
        import base64
        import io
        from ..see import capture, profile
        try:
            frame = asyncio.run(self._surface().capture(profile.profile(self.sees).long_edge))
        except capture.Refused:
            return ""
        image = frame.image.copy()
        image.thumbnail((720, 720))
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=65)
        return base64.b64encode(buf.getvalue()).decode("ascii")


def base64_jpeg(view: uia.View) -> str:
    """The window's picture for the card, without the marks."""
    import base64
    import io
    image, _ = win32.capture(view.hwnd)
    image.thumbnail((720, 720))
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=60)
    return base64.b64encode(buf.getvalue()).decode("ascii")

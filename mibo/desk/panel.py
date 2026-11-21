"""The app page's side of operating other programs: where a task's steps run, the cards wait, and Settings'
section reads the one acting mode, the kept tiers, the never list and the log.

The character's turn runs in the service; its loop (operate.py) sends each step here (/api/programs/op). Here one
`Hands` does it on one worker thread (UI Automation, the input and the per-monitor scale belong to the thread
that started them). A card waits at /api/programs/card for the person's press, which only the app's own page can
give (the window's key), never the service and never words typed in the chat. A step runs only while the
person's own chat turn is under way in the app, and a card waits for them there (cardwait.py): nothing for a turn
nobody started, on a timer or in the background. The turn stopped in the chat stops the run and closes the strip.

Which program a name means is found the way Start finds one (names.py): a window of it already open, the
front-most first, or else its entry in Start, opened for the task with no card; never a path from the model. No name at all means the window that was in front before the person came to the app. A
program on the never list is refused by its name before any window is looked for.

A window only a picture can work sends no picture to the model service before the person's yes on a card at the
first picture of the task (this once, or always for that service, kept in see.json and taken back in Settings);
the yes covers clicking by sight in that window for the task, while typing and keys still ask each time.
"""
from __future__ import annotations

import asyncio
import os
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from types import SimpleNamespace

from ..cardwait import Card
from . import grants as g

LAUNCH_S = 15.0
TASK_MAX = 500
# Programs one task may move across (copy in one, paste in another).
PARTS = 3

# What a model writes for `program` when the person named none: the window in front of them.
FRONT = {"", "front", "front window", "the window in front", "current window", "this window", "popup", "pop-up",
         "this popup", "this pop-up", "unknown", "当前窗口", "这个窗口", "前台窗口", "弹窗", "这个弹窗", "未知"}  # copy: voice
# Never operated, whatever was granted, by what the person calls it: the reason as Settings and the reply say it.
NEVER = (
    (r"terminal|终端|命令行|命令提示符|command prompt|\bcmd\b|powershell|\bshell\b|bash|wsl|控制台|console",
     "终端和命令行：不代为输入命令","Terminals and shells: commands are not typed for you"),
    (r"设置|settings?|控制面板|control panel|注册表|regedit|registry|任务管理器|task ?manager|组策略|policy|"
     r"defender|security|安全|防火墙|firewall|服务|services|设备管理器|device manager|管理员|admin|uac",
     "系统和安全设置、注册表、任务管理器、管理员提示", "System and security settings, the registry, Task Manager, "
     "administrator prompts"),
    (r"password|密码|keepass|bitwarden|1password|lastpass|dashlane|credential|凭据|authenticator|身份验证器",
     "密码管理器和输入密码的对话框", "Password managers and dialogs that ask for a password"),
    (r"install|setup|安装|卸载|uninstall|store|应用商店|商店|update|更新", "安装程序、卸载和应用商店",
     "Installers, uninstallers and app stores"),
    (r"bank|银行|支付|alipay|支付宝|paypal|wallet|钱包|payment|付款|证券|股票|trading|broker|crypto|理财|网银",
     "银行、支付、证券和钱包程序", "Banking, payment, trading and wallet programs"),
    (r"claude|chatgpt|codex|copilot|cursor|windsurf|agent|智能体|mibo",
     "其他 AI 智能体和本应用的权限页面", "Other AI agents, and this app's own permission screens"),
)
ACTION_WORDS = {
    "zh": {"invoke": "按下“{t}”", "click": "点击“{t}”", "press": "按下“{t}”", "toggle": "切换“{t}”",
           "check": "勾选“{t}”", "uncheck": "取消勾选“{t}”", "select": "选中“{t}”",
           "expand": "展开“{t}”", "collapse": "收起“{t}”", "set": "在“{t}”中写入{n}", "type": "在“{t}”中输入{n}",
           "append": "在“{t}”末尾添加{n}",
           "scroll": "滚动{t}", "key": "按 {k}", "focus": "将焦点移到“{t}”", "wait": "等待窗口响应", "look": "查看窗口",
           "menu": "打开“{t}”的右键菜单", "double": "双击“{t}”", "paste": "在“{t}”中粘贴{n}",
           "switch": "切换到“{t}”"},
    "en": {"invoke": "Pressed “{t}”", "click": "Clicked “{t}”", "press": "Pressed “{t}”", "toggle": "Switched “{t}”",
           "check": "Ticked “{t}”", "uncheck": "Unticked “{t}”", "select": "Selected “{t}”",
           "expand": "Opened “{t}”", "collapse": "Closed “{t}”", "set": "Wrote {n} into “{t}”",
           "type": "Typed {n} into “{t}”", "append": "Added {n} at the end of “{t}”",
           "scroll": "Scrolled {t}", "key": "Pressed {k}", "focus": "Moved to “{t}”",
           "menu": "Opened the right-click menu of “{t}”", "double": "Double-clicked “{t}”",
           "paste": "Pasted {n} into “{t}”", "switch": "Switched to “{t}”",
           "wait": "Waited for the window", "look": "Looked at the window"},
}
STATUS_WORDS = {
    "zh": {"declined": "（已拒绝，未执行）", "refused": "（不允许，未执行）", "failed": "（未成功）",
           "stopped": "（已停止）", "paused": "（已暂停）", "handover": "（已交由你操作）", "outside": "（经你在卡片上确认，仅执行一次）",
           "skipped": "（未执行）"},
    "en": {"declined": " (you said no; not done)", "refused": " (not allowed; not done)", "failed": " (did not work)",
           "stopped": " (stopped)", "paused": " (paused)", "handover": " (handed to you)",
           "outside": " (confirmed by you on the card; done once)", "skipped": " (not done)"},
}


def _error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


# A never-list program named in the task itself ("用计算器打开注册表"): names of programs only, not words that
# ordinary text holds.
NEVER_IN_TASK = re.compile(
    r"注册表|regedit|命令提示符|(?<![a-z])cmd(?![a-z])|powershell|windows terminal|终端|任务管理器|taskmgr|task manager|"
    r"控制面板|control panel|组策略|gpedit|系统设置|windows ?设置|keepass|bitwarden|1password|lastpass|密码管理器|"
    r"password manager|网银|支付宝|alipay|paypal", re.I)


def never_named(program: str) -> tuple[str, str] | None:
    """(zh, en) why a program named so is never operated, or None."""
    from .names import SEVERAL
    name = " ".join(program.lower().split())
    for pattern, zh, en in NEVER:
        if re.search(pattern, name, re.I):
            return zh, en
    if g.NEVER_WORDS.search(name) or any(f"{part.strip().removesuffix('.exe')}.exe" in g.NEVER_EXE
                                          for part in SEVERAL.split(name)):
        return "在永不操作的清单上", "It is on the never list"
    return None


def said(action: dict, node, status: str, outside: bool, lang: str) -> str:
    """One step in the person's words, for the step log under the reply."""
    do = str(action.get("do") or "")
    words = ACTION_WORDS[lang]
    text = str(action.get("text") or "")
    lines = text.count("\n") + 1 if text else 0
    count = ((f"{lines} 行" if lines > 1 else f"{len(text)} 个字") if lang == "zh"
             else (f"{lines} lines" if lines > 1 else f"{len(text)} characters"))
    name = (node.name if node is not None and node.name else (node.role if node is not None else "")) or str(
        action.get("program") or action.get("name") or "") or ("当前位置" if lang == "zh" else "where the caret is")
    if do == "scroll":
        name = ("向上" if action.get("direction") == "up" else "向下") if lang == "zh" else (
            "up" if action.get("direction") == "up" else "down")
    line = words.get(do, do).format(t=name[:40], n=count, k=str(action.get("key") or "")[:20],
                                    s=action.get("seconds") or 1)
    if do == "select" and action.get("option"):
        line += f"：{action['option']}" if lang == "zh" else f": {action['option']}"
    tail = STATUS_WORDS[lang]["outside"] if outside else STATUS_WORDS[lang].get(status, "")
    return line + tail


def worded(action: dict, result: dict) -> dict:
    """An action's result with the step in the person's words, for the step log under the reply."""
    target = result.get("target")
    node = SimpleNamespace(**target) if target else None
    result["said"] = {lang: said(action, node, result.get("status", ""), bool(result.get("outside")), lang)
                      for lang in ("zh", "en")}
    return result


def serve(hands, op: str, payload: dict) -> dict:
    """One op of the loop on the Hands, on the thread that owns them."""
    if op == "look":
        return hands.look()
    if op == "picture":
        return hands.picture()
    if op == "act":
        action = payload.get("action") or {}
        return worded(action, hands.act(action))
    if op == "batch":
        actions = [a for a in payload.get("actions") or [] if isinstance(a, dict)][:40]
        out = hands.batch(actions, payload.get("look") is not False)
        for action, result in zip(actions, out["results"]):
            worded(action, result)
        return out
    if op in ("see_look", "see_act"):
        return hands.see(op, payload)
    if op == "step":
        hands.step(int(payload.get("n") or 0), str(payload.get("note") or "")[:80])
        return {"status": "ok"}
    if op == "end":
        hands.end(str(payload.get("status") or "")[:20], str(payload.get("note") or "")[:200])
        return {"status": "ok"}
    raise ValueError("generic")


class Desk:
    """One task at a time: its Hands on the worker thread, the card waiting for the person, the turn it is for."""

    def __init__(self, app, workspace):
        self.app, self.workspace = app, workspace
        self.desktop = Path(workspace.home) / "runtime" / "desktop"
        self.grants = g.Grants(self.desktop)
        self.worker = ThreadPoolExecutor(1, thread_name_prefix="desk")
        self.hands = None
        self.lang = "zh"
        self.conversation = ""
        # The card waiting for the person, and the event loop it waits on (the worker thread asks through it).
        self.cards = Card(workspace, lambda: getattr(app.state, "window", None))
        self.loop: asyncio.AbstractEventLoop | None = None
        self.watch: asyncio.Task | None = None
        self.front = 0
        # The Hands of the task whose pictures the person allowed (the yes covers clicking by sight there).
        self.pictured = None

    def who(self) -> str:
        return self.workspace.config().get("PERSONA_NAME", "").strip() or "Mibo"

    def service(self) -> str:
        """The model service pictures would go to, by its host name."""
        from urllib.parse import urlsplit
        return urlsplit(self.workspace.config().get("LLM_BASE_URL", "")).hostname or ""

    def fronted(self) -> None:
        """Told each time the window in front changes (quiet.py's watch of the system's own events): keeps the
        last one that is another program's, which is what 当前窗口 means once the person has come to the app
        to ask. The taskbar, Start and the window switcher pass in front on the way and are not it."""
        from .. import quiet
        front = quiet.front()
        base = Path(front["path"]).name.lower()
        if front["hwnd"] and front["pid"] != os.getpid() and base not in quiet._SHELL_EXES and not (
                base in g.SHARED and g.window_never(base, front["hwnd"])):
            self.front = front["hwnd"]

    def turn_on(self, conversation: str) -> bool:
        return self.cards.turn_on(conversation)

    async def _on(self, fn, *args):
        self.loop = asyncio.get_running_loop()
        return await asyncio.wrap_future(self.worker.submit(fn, *args))

    # --- the card, from the worker thread

    def ask(self, card: dict) -> dict | None:
        """Shows `card` and waits on the worker thread: the person's answer, or None when nobody answered,
        the turn ended or the strip halted the run."""
        program = card.get("program") or ""
        what = {"zh": f"在{program}中操作", "en": f"An action in {program}"}

        def halted() -> bool:
            return self.hands is not None and self.hands.strip is not None and self.hands.strip.halted
        return asyncio.run_coroutine_threadsafe(self.cards.ask(card, self.conversation, what, halted),
                                                self.loop).result()

    # --- the steps

    async def begin(self, task: str, program: str, conversation: str, lang: str, sight: str = "",
                    also: tuple[str, ...] = ()) -> dict:
        """A task in `program`, and in the programs `also` names when it moves across them (read in one, written
        in another): each found, opened and granted as the first is, before anything is done."""
        from ..acting import Modes
        if Modes(self.desktop).mode() == "off":
            return {"status": "off"}
        if not self.turn_on(conversation):
            return {"status": "unwatched"}
        named = NEVER_IN_TASK.search(task)
        for name in (program, *also):
            why = never_named(name) or (never_named(named.group()) if named else None)
            if why:
                self.grants.log({"do": "use", "program": name[:60], "status": "refused", "line": why[1]})
                return {"status": "refused", "line": f"[refused] {name[:60]} is never operated: {why[1]}",
                        "why": {"zh": why[0], "en": why[1]}}
        if self.hands is not None:
            await self.end("replaced", "")
        self.conversation, self.lang = conversation, lang
        result = await self._on(self._begin, task, program, sight, also)
        if result.get("status") == "ok":
            self.watch = asyncio.create_task(self._watch())
        return result

    def _begin(self, task: str, program: str, sight: str = "", also: tuple[str, ...] = ()) -> dict:
        from .hands import Hands
        hands = Hands(self.desktop, ask=self.ask, who=self.who(), lang=self.lang, review=self.review)
        hands.begin(task)
        self.hands = hands
        result = self._window(program)
        if result.get("hwnd"):
            result = hands.use(result["hwnd"], sight=sight)
        for name in also if result["status"] == "ok" else ():
            found = self._window(name)
            more = hands.also(found["hwnd"]) if found.get("hwnd") else found
            if more["status"] != "ok":
                result = more
                break
        if result["status"] == "refused":
            result.setdefault("why", {"zh": "此程序在永不操作的清单上", "en": "This program is on the never list"})
        if result["status"] != "ok":
            hands.end(result["status"], result["line"])
            self.hands = None
        return result

    def _window(self, program: str) -> dict:
        """The window a name means: {"hwnd"}, or the result that ends the task before it began."""
        from . import names, win32
        if program.lower() in FRONT:
            hwnd = win32.root_owner(self.front) if win32.shown(self.front) else 0
            if hwnd:
                # A dialog in front is read with the window it belongs to, when that one shows.
                return {"hwnd": hwnd if win32.shown(hwnd) else self.front}
            return {"status": "unfound", "line": "No other program's window was in front of them before the "
                                                 "app's own. Ask them which program they mean."}
        found = names.find(program)
        if found.hwnd:
            return {"hwnd": found.hwnd}
        if found.app:
            return self._launch(found.app)
        listed = ", ".join(found.names)
        if found.several:
            return {"status": "unfound", "line": f"{program[:60]!r} could be any of: {listed}. Use the tool again "
                                                 f"with the full name of the one they mean, or ask them which."}
        return {"status": "unfound", "line": (
            f"No program called {program[:60]!r} is open, on the Desktop or in the Start menu of this computer"
            + (f". Open now: {listed}" if listed else "")
            + f". Start names programs in {win32.ui_language() or 'its own language'} here: when this one goes by "
              f"another name, use the tool again with that name; otherwise tell them it was not found.")}

    def _launch(self, app) -> dict:
        """A program Start or the Desktop lists and no window shows, opened from its entry with no card when it is
        off the never lists: {"hwnd"}, or the result that ends the task. A shortcut to something that is not a
        program is opened at once, and that ends the task."""
        from . import names, win32
        why = never_named(app.name) or never_named(Path(app.target).name)
        word = "" if why else self.grants.blocked_by({"name": app.name, "exe": app.target or ""})
        if word:
            why = (f"名称含有你添加的词“{word}”", f"its name holds {word!r}, on your own never list")
        if why:
            self.grants.log({"do": "use", "program": app.name, "exe": app.target, "status": "refused", "line": why[1]})
            return {"status": "refused", "line": f"[refused] {app.name} is never operated: {why[1]}",
                    "why": {"zh": why[0], "en": why[1]}}
        if app.file and not app.target.lower().endswith(".exe"):
            # A shortcut to a script, a folder or a page, which the person named: opened as a double-click on it
            # opens it, and it has no window of its own to work in.
            win32.open_file(app.file)
            self.grants.log({"do": "open", "program": app.name, "exe": app.file, "status": "ok",
                             "line": "opened the shortcut"})
            return {"status": "done", "line": f"[done] opened {app.name} ({app.file}), which starts "
                                              f"{app.target or 'what the shortcut names'}"}

        def its(exe: str) -> bool:
            return names.runs(app, exe) or names.beside(app, exe)
        before = {w.hwnd for w in names.open_windows() if not win32.minimized(w.hwnd)}
        if app.file:
            win32.open_file(app.file)
        else:
            win32.open_app(app.aumid)
        end = time.monotonic() + LAUNCH_S
        while time.monotonic() < end:
            time.sleep(0.3)
            # Its window: a new one, or one it had hidden or minimised and brings back.
            hwnd = next((w.hwnd for w in names.open_windows() if w.hwnd not in before and its(w.exe)
                         and not win32.minimized(w.hwnd)), 0)
            if hwnd:
                self.grants.log({"do": "open", "program": app.name, "exe": app.target or app.aumid, "status": "ok",
                                 "line": "opened from Start"})
                return {"hwnd": hwnd}
        return {"status": "failed", "line": f"[failed] {app.name} was started, but no window of it showed"}

    async def op(self, op: str, payload: dict) -> dict:
        hands = self.hands
        if hands is None:
            return {"status": "stopped", "line": "[stopped] no task under way"}
        if not self.turn_on(self.conversation):
            await self.end("stopped", "the chat turn ended")
            return {"status": "stopped", "line": "[stopped] the chat turn ended"}
        if op == "see_allow":
            return await self._on(self._pictures, hands, payload.get("look") is True)
        if op in ("look", "picture", "act", "batch", "step", "see_look", "see_act"):
            out = await self._on(serve, hands, op, payload)
            if op == "see_look" and self.pictured is hands and hands.sight is not None:
                hands.sight.lease = hands.sight.surface.place()
            return out
        raise ValueError("generic")

    def review(self, action: dict):
        """Auto mode's reviewer on one action, on the worker thread: the person's own messages of the turn under
        way, never the window."""
        from .. import acting, review
        window = getattr(self.app.state, "window", None)
        return asyncio.run(review.decide("programs", acting.asked(self.workspace), action,
                                         ask=acting.reviewer(self.workspace),
                                         attended=window is not None and window.attended(), lang=self.lang))

    def _pictures(self, hands, look: bool) -> dict:
        """On the worker thread, before the first picture of a task goes to the model service the person chose:
        in automatic review the reviewer passes it when their words of this turn name or point at this program;
        else a card that shows the window (this once, always, or no), unless they said always for this service. A
        password manager's, a bank's or a mail client's window always asks, with no always. `look`: the model only
        looks and tells them, and the card says so."""
        from .. import acting
        from ..acting import Modes
        from ..see import when, words
        from . import win32
        service, switch = self.service(), when.Switch(self.desktop)
        name = hands.program["name"]
        title = win32.title_of(hands.view.hwnd) if hands.view else ""
        sensitive = acting.sensitive_window(name, os.path.basename(hands.program["exe"]), title)
        if sensitive or not switch.on(service):
            verdict = None
            if not sensitive and acting.decide(Modes(self.desktop).mode(), "picture", "", "programs") == "review":
                verdict = self.review({"kind": "picture", "where": service, "target": [name, title[:120]], "text": ""})
                if verdict.decision == "refuse":
                    self.grants.log({"do": "review", "program": name, "exe": hands.program["exe"], "status": "refused",
                                     "line": f"pictures to {service}: {verdict.reason}"})
                    return {"status": "declined", "line": "[declined] the window's pictures were not sent: "
                                                          + verdict.declined()}
            if verdict is not None and verdict.decision == "go":
                answer = "once"
            else:
                pressed = self.ask({"id": secrets.token_urlsafe(8), "type": "see_grant", "program": name,
                                    "exe": hands.program["exe"], "window": name, "task": hands.task,
                                    "text": words.say(words.LOOK if look else words.ASK, self.lang, window=name,
                                                      service=service, who=words.spaced(self.lang, self.who())),
                                    **({"review": verdict.line(self.lang)} if verdict is not None else {}),
                                    "picture": hands.sent_picture(),
                                    "choices": ["once", "no"] if sensitive else ["once", "always", "no"]}) or {}
                answer = pressed.get("answer") or ("once" if pressed.get("yes") else None)
            self.grants.log({"do": "ask", "program": name, "exe": hands.program["exe"], "status": answer or "unanswered",
                             "line": f"pictures to {service}",
                             **({"review": {"decision": verdict.decision, "by": verdict.by, "reason": verdict.reason}}
                                if verdict is not None else {})})
            if answer not in ("once", "always"):
                return {"status": "declined", "line": "[declined] the person did not let the window's pictures go to "
                                                      "their model service; nothing was sent"}
            if answer == "always" and not sensitive:
                switch.set(service)
        self.pictured = hands
        return {"status": "ok"}

    async def end(self, status: str, note: str) -> None:
        hands, self.hands = self.hands, None
        if self.watch is not None and self.watch is not asyncio.current_task():
            self.watch.cancel()
        self.watch = None
        if hands is not None:
            if hands.strip is not None:
                hands.strip.stop("stopped")
            self.cards.drop()
            await self._on(hands.end, status, note[:200])

    async def _watch(self) -> None:
        """The turn ended in the chat (stopped, or the page went away): the run stops and the strip closes."""
        while self.hands is not None:
            await asyncio.sleep(0.5)
            if not self.turn_on(self.conversation):
                await self.end("stopped", "the chat turn ended")
                return

    # --- Settings

    def view(self) -> dict:
        from ..acting import Modes
        from ..see import when
        who, service = self.who(), self.service()
        return {
            "on": Modes(self.desktop).mode() != "off",
            "mode": Modes(self.desktop).mode(),
            "block": self.grants.blocked(),
            # Pictures of a window only a picture can work: asked on a card each time unless always for this service.
            "pictures": {"on": when.Switch(self.desktop).on(service),
                         "sentence": {lang: when.Switch.sentence(lang, who, service) for lang in ("zh", "en")}},
            "switch": {lang: {k: v.format(who=who) for k, v in g.SWITCH[lang].items()} for lang in ("zh", "en")},
            "grants": [{**{k: row.get(k, "") for k in ("key", "name", "exe", "since")},
                        "tier": row["tier"] if row.get("tier") in g.TIERS else g.default_tier(row.get("exe", ""))}
                       for row in self.grants.all()],
            "never": [{"zh": zh, "en": en} for _, zh, en in NEVER],
        }

    def clear_log(self) -> None:
        self.grants.log_path.unlink(missing_ok=True)


def mount(app, workspace, body) -> None:
    from ..panels.runtime import WINDOW_HEADER

    def desk() -> Desk:
        if getattr(app.state, "desk", None) is None:
            app.state.desk = Desk(app, workspace)
        return app.state.desk

    # The window in front, as quiet.py's watch follows it: what "the window in front of them" means.
    from .. import quiet
    quiet.on_front(lambda: desk().fronted())

    def own_page(request: Request) -> bool:
        """The app window's own page on this computer: the only place a permission is given (a card's press,
        the switch turned on, a grant raised), never the service or another tab."""
        window = getattr(app.state, "window", None)
        return window is None or window.is_own_page(request.headers.get(WINDOW_HEADER, ""))

    @app.get("/api/programs")
    async def desk_view():
        return await asyncio.to_thread(desk().view)

    @app.post("/api/programs")
    async def desk_change(request: Request):
        """Settings: the switch, a grant taken back. Only on the computer the app runs on."""
        payload = await body(request)
        state = desk()
        # Turning off, taking back and tightening need no more than this computer; loosening needs the app's own page.
        if (payload.get("on") is True or payload.get("pictures") is True or payload.get("mode") == "auto"
                or "unblock" in payload or (payload.get("tier") or {}).get("tier") in ("full", "click")
                ) and not own_page(request):
            return _error("local_only", 403)
        if payload.get("mode") is not None:
            from ..acting import MODES, Modes
            if payload["mode"] not in MODES:
                return _error("generic")
            await asyncio.to_thread(Modes(state.desktop).set, payload["mode"])
            if payload["mode"] == "off":
                await state.end("stopped", "turned off in Settings")
            return await asyncio.to_thread(state.view)
        if isinstance(payload.get("tier"), dict):
            if payload["tier"].get("tier") not in g.TIERS:
                return _error("generic")
            await asyncio.to_thread(state.grants.set_tier, str(payload["tier"].get("key") or ""),
                                    str(payload["tier"].get("tier") or ""))
            return await asyncio.to_thread(state.view)
        if isinstance(payload.get("block"), str) or isinstance(payload.get("unblock"), str):
            word = payload.get("block") if isinstance(payload.get("block"), str) else payload["unblock"]
            await asyncio.to_thread(state.grants.block, word, "block" in payload)
            return await asyncio.to_thread(state.view)
        if isinstance(payload.get("pictures"), bool):
            from ..see import when
            await asyncio.to_thread(when.Switch(state.desktop).set, state.service() if payload["pictures"] else "")
            state.grants.log({"do": "switch", "line": f"pictures turned {'on' if payload['pictures'] else 'off'}"})
        elif isinstance(payload.get("on"), bool):
            # The switch is the one acting mode: on is automatic review, off stops every acting place.
            from ..acting import Modes
            await asyncio.to_thread(Modes(state.desktop).set, "auto" if payload["on"] else "off")
            if not payload["on"]:
                await state.end("stopped", "turned off in Settings")
        elif isinstance(payload.get("revoke"), str):
            await asyncio.to_thread(state.grants.revoke, payload["revoke"])
        else:
            return _error("generic")
        return await asyncio.to_thread(state.view)

    @app.get("/api/programs/log")
    async def desk_log(n: int = 60):
        rows = await asyncio.to_thread(desk().grants.recent, max(1, min(n, 300)))
        return {"rows": rows[::-1]}

    @app.post("/api/programs/log")
    async def desk_log_clear(request: Request):
        if (await body(request)).get("clear") is not True:
            return _error("generic")
        await asyncio.to_thread(desk().clear_log)
        return {"rows": []}

    @app.post("/api/programs/op")
    async def desk_op(request: Request):
        """One step of the character's task in a program, from the service's loop. Waits for a card."""
        payload = await body(request)
        op, state = payload.get("op"), desk()
        if op == "begin":
            task, program, conversation = payload.get("task"), payload.get("program"), payload.get("conversation")
            also = payload.get("also") or []
            if (not isinstance(task, str) or not task.strip() or len(task) > TASK_MAX or not isinstance(program, str)
                    or len(program) > 80 or not isinstance(conversation, str) or not isinstance(also, list)
                    or len(also) > PARTS - 1 or not all(isinstance(a, str) and 0 < len(a) <= 80 for a in also)):
                return _error("generic")
            lang = "zh" if str(payload.get("lang") or "").startswith("zh") else "en"
            return await state.begin(task.strip(), " ".join(program.split()), conversation, lang,
                                     str(payload.get("sight") or "")[:120], tuple(" ".join(a.split()) for a in also))
        if op == "end":
            await state.end(str(payload.get("status") or "")[:20], str(payload.get("note") or ""))
            return {"status": "ok"}
        if op in ("look", "picture", "act", "batch", "step", "see_allow", "see_look", "see_act"):
            if op in ("act", "see_act") and not isinstance(payload.get("action"), dict):
                return _error("generic")
            if op == "batch" and not isinstance(payload.get("actions"), list):
                return _error("generic")
            return await state.op(op, payload)
        return _error("generic")

    @app.get("/api/programs/card")
    async def desk_card():
        """The card waiting for the person, if any."""
        return {"card": desk().cards.card}

    @app.post("/api/programs/card")
    async def desk_press(request: Request):
        """The person's press: from the app's own page on this computer alone, never from the service."""
        if not own_page(request):
            return _error("local_only", 403)
        payload = await body(request)
        ident = payload.get("id")
        if not isinstance(ident, str):
            return _error("generic")
        if isinstance(payload.get("yes"), bool):
            return {"ok": desk().cards.press(ident, {"yes": payload["yes"]})}
        if payload.get("answer") in ("once", "always", "no"):
            return {"ok": desk().cards.press(ident, {"answer": payload["answer"]})}
        return _error("generic")

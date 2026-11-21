"""Measure, step by step, how accurately Mibo operates other programs (mibo/desk) with a real model.

    python tools/desk_measure.py --home DIR --model deepseek/deepseek-v4-flash [--sees] [--only calc,notepad]
                                 [--tasks C1,T5] [--tree DIR] [--script FILE] [--out FILE]

A fixed set of tasks across Calculator, Notepad (a copy of the classic one, which opens empty), File Explorer on a
folder of its own, Character Map, a small test program it builds, and Paint. Each task runs the product's own loop
(desk/operate.run over one Hands) and is judged by a checker that reads the program's real state afterwards: the
display, the text, the files on disk, the test program's own log. The cards are answered by this script as a
careful person would: a program is granted once, an effect outside is allowed only when the task asked for it.

For every step it records what the model meant (the control's name and the text), what was pressed or typed (the
live control's name at the press, the field read back), and whether they agree; for every task whether it ended
right, whether "done" was claimed falsely, the model calls, the looks, the real inputs and the seconds.

It only operates programs it starts itself, on the second monitor when there is one, one at a time, and closes
them. Real keys and clicks are sent only while one of its own windows is in front; otherwise the run stops.
`--tree` measures another copy of the package (an earlier one, for a before and after); `--script` replays fixed
model answers from a JSON file ({"T1": [reply, ...]}) instead of calling a model. LLM_API_KEY and LLM_BASE_URL are
read from DIR/.env and never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import ctypes.wintypes
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TEST_PROGRAM = r"""
using System; using System.Drawing; using System.IO; using System.Reflection; using System.Windows.Forms;
[assembly: AssemblyTitle("Desk check")]
[assembly: AssemblyProduct("Desk check")]
class P : Form {
  static string logPath;
  static void Log(string s) { File.AppendAllText(logPath, s + Environment.NewLine); }
  TextBox pin;
  protected override bool ShowWithoutActivation { get { return true; } }
  TextBox Field(string label, int y, bool secret) {
    Controls.Add(new Label { Left = 10, Top = y + 4, Width = 60, Text = label });
    var box = new TextBox { Left = 75, Top = y, Width = 260, AccessibleName = label, UseSystemPasswordChar = secret };
    box.TextChanged += (s, e) => Log("TEXT " + label + (secret ? "" : "=" + box.Text));
    Controls.Add(box); return box;
  }
  Button Press(Control parent, string text, string where, int x, int y, int wide) {
    var b = new Button { Left = x, Top = y, Width = wide, Height = 28, Text = text };
    b.Click += (s, e) => Log("CLICK " + where + text);
    parent.Controls.Add(b); return b;
  }
  P(int x, int y) {
    Text = "Desk check"; StartPosition = FormStartPosition.Manual; Location = new Point(x, y);
    ClientSize = new Size(800, 640); Font = new Font("Microsoft YaHei UI", 9);
    var table = new ListView { Left = 10, Top = 10, Width = 420, Height = 120, View = View.Details, FullRowSelect = true,
                               AccessibleName = "库存表" };
    table.Columns.Add("名称", 100); table.Columns.Add("数量", 80); table.Columns.Add("单价", 80); table.Columns.Add("产地", 120);
    string[][] rows = { new[] { "苹果", "12", "3.50", "烟台" }, new[] { "香蕉", "7", "2.20", "海南" },
                        new[] { "樱桃", "30", "18.00", "大连" }, new[] { "芒果", "45", "6.80", "攀枝花" } };
    foreach (var r in rows) table.Items.Add(new ListViewItem(r));
    Controls.Add(table);
    var name = Field("姓名", 142, false); var mail = Field("邮箱", 176, false);
    var pass = Field("密码", 210, true); var msg = Field("留言", 244, false);
    pass.Text = "S3cr3t-Marker-77";
    var send = Press(this, "发送", "", 75, 280, 90);
    send.Click += (s, e) => { Log("SEND name=" + name.Text + " | mail=" + mail.Text + " | msg=" + msg.Text);
                              name.Text = mail.Text = msg.Text = ""; };
    Press(this, "删除记录", "", 175, 280, 90).Click += (s, e) => table.Items.Clear();
    // Two buttons with one name, told apart only by their group.
    var show = new GroupBox { Left = 10, Top = 322, Width = 200, Height = 96, Text = "显示" };
    var sound = new GroupBox { Left = 220, Top = 322, Width = 200, Height = 96, Text = "声音" };
    var theme = new ComboBox { Left = 12, Top = 24, Width = 120, DropDownStyle = ComboBoxStyle.DropDownList, AccessibleName = "主题" };
    theme.Items.AddRange(new object[] { "浅色", "深色", "跟随系统" }); theme.SelectedIndex = 0; show.Controls.Add(theme);
    var mute = new CheckBox { Left = 12, Top = 26, Width = 120, Text = "静音" }; sound.Controls.Add(mute);
    Press(show, "应用", "显示/", 12, 58, 80); Press(sound, "应用", "声音/", 12, 58, 80);
    Controls.Add(show); Controls.Add(sound);
    var remind = new GroupBox { Left = 10, Top = 428, Width = 410, Height = 64, Text = "提醒" };
    var way = new ComboBox { Left = 12, Top = 24, Width = 120, DropDownStyle = ComboBoxStyle.DropDownList, AccessibleName = "提醒方式" };
    way.Items.AddRange(new object[] { "弹窗", "邮件", "短信" }); way.SelectedIndex = 0;
    way.SelectedIndexChanged += (s, e) => Log("COMBO 提醒方式=" + way.Text);
    var weekly = new CheckBox { Left = 150, Top = 26, Width = 120, Text = "每周汇总" };
    weekly.CheckedChanged += (s, e) => Log("CHECK 每周汇总=" + weekly.Checked);
    remind.Controls.Add(way); remind.Controls.Add(weekly); Controls.Add(remind);
    // A number pad laid out as a phone's; the control names (automation ids) deliberately say other digits.
    var pad = new GroupBox { Left = 450, Top = 10, Width = 330, Height = 250, Text = "取件码" };
    pin = new TextBox { Left = 12, Top = 24, Width = 150, ReadOnly = true, AccessibleName = "已输入" };
    pad.Controls.Add(pin);
    string digits = "1234567890";
    for (int i = 9; i >= 0; i--) {
      string d = digits.Substring(i, 1);
      var b = new Button { Left = 12 + (i == 9 ? 1 : i % 3) * 56, Top = 58 + (i / 3) * 34, Width = 50, Height = 28, Text = d,
                           Name = "btn" + digits.Substring(9 - i, 1) };
      b.Click += (s, e) => { pin.Text += d; Log("CLICK 取件码/" + d); };
      pad.Controls.Add(b);
    }
    Press(pad, "清除", "取件码/", 190, 58, 80).Click += (s, e) => pin.Text = "";
    Press(pad, "确认", "取件码/", 190, 92, 80).Click += (s, e) => { Log("PIN " + pin.Text); pin.Text = ""; };
    Controls.Add(pad);
    var list = new ListBox { Left = 450, Top = 290, Width = 330, Height = 200, AccessibleName = "项目列表" };
    for (int i = 1; i <= 80; i++) list.Items.Add("项目 " + i.ToString("000"));
    list.SelectedIndexChanged += (s, e) => Log("SELECT " + list.Text);
    Controls.Add(new Label { Left = 450, Top = 270, Width = 200, Text = "项目列表" }); Controls.Add(list);
    var notes = new TextBox { Left = 10, Top = 504, Width = 770, Height = 120, Multiline = true, AccessibleName = "备注" };
    Controls.Add(notes);
    Log("START");
  }
  [STAThread] static void Main(string[] a) {
    logPath = a[0]; Application.EnableVisualStyles(); Application.Run(new P(int.Parse(a[1]), int.Parse(a[2])));
  }
}
"""

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.SendMessageW.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint, ctypes.wintypes.WPARAM, ctypes.c_wchar_p]
user32.SendMessageW.restype = ctypes.c_ssize_t


class Stop(BaseException):
    """No real input now: something that is not ours has the front, or the person is typing. The task under way
    is void and tried again later; nothing in the layer catches this."""


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


class Rec:
    """What one task did, written down as it happens."""

    def __init__(self):
        self.pressed: list[dict] = []
        self.acts: list[dict] = []
        self.looks = self.inputs = self.calls = 0
        self.look_s = self.model_s = self.begin_s = self.act_s = 0.0
        self.cards: list[dict] = []
        self.prompts: list[dict] = []


class Bench:
    def __init__(self, args):
        self.args = args
        self.home = Path(args.home).resolve()
        self.files = self.home / "files"
        self.rec = Rec()
        self.ours: set[int] = set()
        self.sent_at = 0.0
        self.cost = 0.0
        self.need_front = 0.0
        self.theirs = 0
        self.worker = ThreadPoolExecutor(1, thread_name_prefix="desk")
        self.task: dict = {}
        self.script: dict = json.loads(Path(args.script).read_text(encoding="utf-8")) if args.script else {}
        sys.path.insert(0, str(Path(args.tree).resolve() if args.tree else Path(__file__).resolve().parents[1]))
        from mibo.desk import grants, hands, operate, panel, uia, win32
        self.g, self.hands_mod, self.operate, self.panel, self.uia, self.win32 = grants, hands, operate, panel, uia, win32
        win32.dpi_aware()
        self._instrument()
        screens = win32.monitors()
        self.screen = next((s for s in screens if not s["primary"]), screens[0])
        self.x, self.y = self.screen["left"] + 120, self.screen["top"] + 120
        threading.Thread(target=self._keep_front, daemon=True).start()

    def is_ours(self, hwnd: int) -> bool:
        top = self.win32.top_of(hwnd)
        return self.win32.pid_of(top) in self.ours or self.g.program_pid(top) in self.ours

    def _keep_front(self) -> None:
        """A test window that takes the front (a packaged app does when a button of it is invoked) gives it
        back to the window the person had there, unless real input is being sent to it this moment."""
        win32 = self.win32
        win32.dpi_aware()
        known: dict[int, bool] = {}
        while True:
            time.sleep(0.02)
            front = win32.foreground()
            if not front:
                continue
            if front not in known or len(known) > 200:
                known = {front: self.is_ours(front)}
            if not known[front]:
                self.theirs = front
            elif self.theirs and time.monotonic() > self.need_front:
                win32.give_back(self.theirs)

    def idle(self, seconds: float = 2.0, wait: float = 90.0) -> None:
        """Wait until the person has left the keyboard and mouse alone for a moment."""
        end = time.monotonic() + wait
        while time.monotonic() < end:
            info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
            user32.GetLastInputInfo(ctypes.byref(info))
            if (kernel32.GetTickCount() - info.dwTime) / 1000 >= seconds:
                return
            time.sleep(0.25)

    # --- the model

    def key(self) -> tuple[str, str]:
        env = dict(line.split("=", 1) for line in (self.home / ".env").read_text(encoding="utf-8").splitlines()
                   if "=" in line)
        return env["LLM_BASE_URL"].strip().rstrip("/"), env["LLM_API_KEY"].strip()

    def spent(self) -> float:
        base, key = self.key()
        req = urllib.request.Request(base + "/key", headers={"Authorization": "Bearer " + key})
        return float(json.loads(urllib.request.urlopen(req, timeout=30).read())["data"]["usage"])

    async def ask_model(self, system: str, messages: list[dict]) -> str:
        rec = self.rec
        rec.calls += 1
        began = time.monotonic()
        shown = messages[-1]["content"]
        text = shown if isinstance(shown, str) else "\n".join(p.get("text", "") for p in shown if isinstance(p, dict))
        pictured = not isinstance(shown, str) and any(p.get("type") == "image_url" for p in shown)
        if self.script:
            replies = self.script.get(self.task["id"], [])
            answer = json.dumps(replies[rec.calls - 1] if rec.calls <= len(replies) else
                                {"actions": [{"do": "done", "success": False, "result": "script ended"}]},
                                ensure_ascii=False)
        else:
            answer = await asyncio.to_thread(self._complete, system, messages)
        rec.model_s += time.monotonic() - began
        rec.prompts.append({"prompt": text, "pictured": pictured, "answer": answer,
                            "s": round(time.monotonic() - began, 2)})
        return answer

    def _complete(self, system: str, messages: list[dict]) -> str:
        base, key = self.key()
        body = {"model": self.args.model, "messages": [{"role": "system", "content": system}, *messages],
                "max_tokens": 900, "temperature": 0.2, "response_format": {"type": "json_object"},
                "reasoning": {"enabled": False}, "usage": {"include": True}}
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        for attempt in range(3):
            try:
                data = json.loads(urllib.request.urlopen(req, timeout=120).read())
                self.cost += float((data.get("usage") or {}).get("cost") or 0)
                return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            except Exception as e:  # the network or the service: a blank answer is a failed step, not a crash
                err = type(e).__name__
                time.sleep(2 + 3 * attempt)
        return f"(no answer: {err})"

    # --- what is written down, and the guard on real input

    def _instrument(self) -> None:
        uia, win32, bench = self.uia, self.win32, self
        A = uia.automation()

        def live(node) -> dict:
            try:
                kind = node.el.Current.ControlType.ProgrammaticName.split(".")[-1]
                return {"name": uia.norm(node.el.Current.Name), "role": uia.ROLES.get(kind, "custom")}
            except Exception:
                return {"gone": True}

        def holds(node) -> str | None:
            if node.secret:
                return None
            try:
                if node.el.GetCurrentPropertyValue(A.AutomationElement.IsValuePatternAvailableProperty):
                    return str(node.el.GetCurrentPropertyValue(A.ValuePattern.ValueProperty) or "")
                if node.el.GetCurrentPropertyValue(A.AutomationElement.IsTextPatternAvailableProperty):
                    return str(node.el.GetCurrentPattern(A.TextPattern.Pattern).DocumentRange.GetText(4000))
            except Exception:
                return None
            return None

        def wrap(name: str) -> None:
            orig = getattr(uia.View, name)

            def run(view, ident=None, *a, **k):
                node = view.nodes.get(ident) if isinstance(ident, int) else None
                if node is None and name in ("type", "key"):
                    node = view.focused()
                entry = {"op": name, "id": ident, "read": {"name": node.name, "role": node.role} if node else None,
                         "live": live(node) if node else None}
                bench.rec.pressed.append(entry)
                try:
                    out = orig(view, ident, *a, **k) if name != "key" else orig(view, ident)
                    entry["line"] = str(out)[:300]
                    return out
                except Exception as e:
                    entry["line"] = str(e)[:300]
                    raise
                finally:
                    if node is not None and name in ("set_value", "type"):
                        time.sleep(0.05)
                        entry["holds"] = holds(node)
            setattr(uia.View, name, run)
        for name in ("invoke", "toggle", "select", "expand", "set_value", "type", "focus", "key", "scroll"):
            wrap(name)
        forward = uia.View.forward

        def fronted(view):
            # Once the layer needs the front (a menu, real keys), the window keeps it to the end of the task.
            bench.need_front = time.monotonic() + 900
            return forward(view)
        uia.View.forward = fronted
        look = uia.View.look

        def timed(view, *a, **k):
            began = time.monotonic()
            try:
                return look(view, *a, **k)
            finally:
                bench.rec.looks += 1
                bench.rec.look_s += time.monotonic() - began
        uia.View.look = timed
        send = win32._send

        def guarded(inputs):
            """Real input goes only where one of this script's own windows is in front, and not while the
            person is typing."""
            for _ in range(40):
                info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
                user32.GetLastInputInfo(ctypes.byref(info))
                idle = (kernel32.GetTickCount() - info.dwTime) / 1000
                if idle > 1.5 or time.monotonic() - bench.sent_at < idle + 0.3:
                    break
                time.sleep(0.25)
            else:
                raise Stop("the person is using the keyboard or mouse")
            front = win32.top_of(win32.foreground())
            if not bench.is_ours(front):
                raise Stop(f"another window has the front ({win32.class_of(front)})")
            bench.rec.inputs += 1
            out = send(inputs)
            bench.sent_at = time.monotonic()
            return out
        win32._send = guarded

    def card(self, card: dict) -> dict | None:
        kind = card.get("type")
        if kind == "see_grant":
            answer = {"answer": "once"}
        elif kind == "see_lease":
            answer = {"yes": bool(self.task.get("sight"))}
        else:
            # Allowed by the card's kind of act, or by its type for a card by picture (P2's see_action).
            answer = {"yes": bool({card.get("kind"), kind} & set(self.task.get("allow", ())))}
        self.rec.cards.append({"type": kind, "kind": card.get("kind", ""), "target": card.get("target", ""),
                               "text": str(card.get("text", ""))[:80], "files": card.get("files", []),
                               "answer": answer})
        return answer

    # --- windows of our own

    def launch(self, cmd: list[str], match, *, hidden: bool = True, timeout: float = 20.0,
               size: tuple = ()) -> int:
        """Start a program and return its new window, moved to the second monitor before it shows where the
        program lets a start-up state through (minimised, then placed, then shown without taking the front)."""
        win32 = self.win32
        self.idle()
        before = set(win32.top_windows())
        info = subprocess.STARTUPINFO()
        if hidden:
            info.dwFlags, info.wShowWindow = subprocess.STARTF_USESHOWWINDOW, 7
        proc = subprocess.Popen(cmd, startupinfo=info)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            time.sleep(0.05)
            for h in win32.top_windows():
                if h not in before and user32.IsWindowVisible(h) and match(h):
                    self.ours |= {win32.pid_of(h), self.g.program_pid(h), proc.pid}
                    win32.place(h, self.x, self.y, *size)
                    if user32.IsIconic(h):
                        user32.ShowWindow(h, 4)
                        win32.place(h, self.x, self.y, *size)
                    time.sleep(0.8)
                    return h
        raise SystemExit(f"no window came up for {cmd[0]}")

    def close(self, hwnd: int, kill: bool = False) -> None:
        win32 = self.win32
        pid = self.g.program_pid(hwnd)
        if kill:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        else:
            win32.close(hwnd)
        time.sleep(0.4)

    def find(self, hwnd: int, auto: str = "", name: str = "", kind: str = ""):
        """One element of our window, read straight from UI Automation (not through the layer)."""
        A = self.uia.automation()
        E = A.AutomationElement
        conds = [A.PropertyCondition(E.AutomationIdProperty, auto)] if auto else []
        if name:
            conds.append(A.PropertyCondition(E.NameProperty, name))
        if kind:
            conds.append(A.PropertyCondition(E.ControlTypeProperty, getattr(A.ControlType, kind)))
        cond = conds[0] if len(conds) == 1 else A.AndCondition(*conds)
        return self.uia.element(hwnd).FindFirst(A.TreeScope.Descendants, cond)

    # --- one task

    async def on(self, fn, *a):
        return await asyncio.wrap_future(self.worker.submit(fn, *a))

    async def run_task(self, task: dict, hwnd: int, ctx: dict) -> dict:
        self.task, self.rec = task, Rec()
        rec = self.rec
        bench = self
        hands = self.hands_mod.Hands(self.home / "runtime" / "desktop", ask=self.card, who="Mibo", lang="zh")
        serve = getattr(self.panel, "serve", None)
        act = hands.act

        def acted(action: dict) -> dict:
            """Each action that reached the Hands, with what it pressed and how it came out."""
            first = len(rec.pressed)
            result = act(action)
            rec.acts.append({"action": dict(action), "pressed": rec.pressed[first:], "status": result.get("status"),
                             "line": str(result.get("line") or "")[:300]})
            return result
        hands.act = acted

        class Local:
            """The loop's way to the Hands, in this process: what operate.Remote and panel.Desk do in the app."""

            async def begin(self, said: str, program: str, *more: str) -> dict:
                def start():
                    began = time.monotonic()
                    hands.begin(said)
                    try:
                        # An earlier tree (--tree) had a look-only level between the program and the model.
                        if "level" in inspect.signature(hands.use).parameters:
                            return hands.use(hwnd, more[0], sight=more[-1] if len(more) > 1 else "")
                        return hands.use(hwnd, sight=more[0] if more else "")
                    finally:
                        rec.begin_s = time.monotonic() - began
                return await bench.on(start)

            def _post(self, op: str, timeout: float = 0, **fields) -> dict:
                if op == "see_allow":
                    # The app's card before the first picture (panel.Desk), answered as a person would.
                    said = bench.card({"type": "see_grant", "text": "pictures to the model service"})
                    return {"status": "ok" if said.get("answer") in ("once", "always") else "declined"}
                return bench.worker.submit(serve, hands, op, fields).result()

            def __getattr__(self, op: str):
                async def call(*a, **fields):
                    if serve is not None:
                        if a:
                            fields = {**fields, **dict(zip({"act": ["action"], "step": ["n", "note"],
                                                            "end": ["status", "note"],
                                                            "batch": ["actions", "look"]}.get(op, []), a))}
                        began = time.monotonic()
                        try:
                            return await bench.on(serve, hands, op, fields)
                        finally:
                            if op in ("act", "batch"):
                                rec.act_s += time.monotonic() - began
                    if op == "act":
                        began = time.monotonic()
                        try:
                            return await bench.on(bench.panel.Desk._act, None, hands, a[0])
                        finally:
                            rec.act_s += time.monotonic() - began
                    return await bench.on(getattr(hands, op), *a)
                return call

        steps: list[dict] = []
        began = time.monotonic()
        # By sight the window must stay in front: the watch that hands the front back stands down.
        self.need_front = time.monotonic() + 900 if task.get("sight") and self.args.sees else 0.0
        params = inspect.signature(self.operate.run).parameters
        extra = {k: v for k, v in (("sees", self.args.sees), ("eyes", "act" if self.args.sees else ""),
                                   ("model", self.args.model)) if k in params}
        error = ""
        try:
            outcome = await self.operate.run(self.ask_model, Local(), task["say"], task["program"],
                                             *(["act"] if "level" in params else []), lang="zh", **extra)
        except Stop as e:
            outcome = {"status": "void", "result": str(e), "steps": [], "calls": rec.calls}
            await self.on(hands.end, "failed", "")
        except Exception as e:
            import traceback
            error = f"{type(e).__name__}: {str(e)[:300]}\n" + "".join(traceback.format_exc().splitlines(True)[-14:])
            outcome = {"status": "crashed", "result": error[:300], "steps": [], "calls": rec.calls}
            await self.on(hands.end, "failed", "")
        seconds = time.monotonic() - began
        self.need_front = 0.0
        time.sleep(0.5)
        ok, note = task["check"](ctx, outcome)
        # What the model meant against what was pressed, one row for each action it asked for: matched, in
        # order, to the actions that reached the Hands (an action never run has no match).
        reached = list(rec.acts)

        def same(a: dict, b: dict) -> bool:
            alike = {"invoke": "press", "click": "press"}
            if alike.get(a.get("do"), a.get("do")) != alike.get(b.get("do"), b.get("do")):
                return False
            if a.get("name"):
                return str(a["name"]) == str(b.get("name"))
            return str(a.get("id")) == str(b.get("id")) and a.get("key") == b.get("key")
        for call, shot in enumerate(rec.prompts, 1):
            seen = {int(m.group(1)): m.group(3) or "" for m in re.finditer(
                r'^\*?\[(\d+)\] (\w+)(?: \[secret\])?(?: "((?:[^"\\]|\\.)*)")?', shot["prompt"], re.M)}
            _note, planned = self.operate.parse(shot["answer"])
            for action in planned:
                do = str(action.get("do"))
                if do in ("done", "ask_person", "look", "wait", "see"):
                    continue
                ident = action.get("id")
                ident = int(ident) if isinstance(ident, str) and ident.isdigit() else ident
                meant = str(action.get("name") or (seen.get(ident, "") if isinstance(ident, int) else ""))
                row = {"call": call, "do": do, "meant": meant, "id": ident, "text": action.get("text"),
                       "key": action.get("key")}
                steps.append(row)
                if not reached or not same(action, reached[0]["action"]):
                    row["verdict"] = "not run"
                    continue
                got = reached.pop(0)
                row["line"] = got["line"]
                entry = got["pressed"][-1] if got["pressed"] else None
                if entry is None:
                    row["verdict"] = "failed" if got["status"] != "ok" else "ok"
                    continue
                pressed = (entry.get("live") or {}).get("name", "")
                row["pressed"] = pressed
                wrote = do in ("set", "type", "append") and entry.get("holds") is not None
                if wrote and _plain(str(action.get("text") or "")) not in _plain(entry["holds"]):
                    # Written and not there: caught by the layer or not, the text went wrong.
                    row["verdict"], row["holds"] = "wrong text", entry["holds"][:120]
                    row["caught"] = got["status"] != "ok"
                elif got["status"] != "ok":
                    row["verdict"] = "failed"
                elif do != "key" and meant and " ".join(pressed.split()).lower() != " ".join(meant.split()).lower():
                    row["verdict"] = "wrong control"
                else:
                    row["verdict"] = "ok"
        said_done = outcome.get("status") == "done"
        return {"task": task["id"], "program": task["program"], "say": task["say"], "right": bool(ok),
                "false_done": said_done and not ok, "status": outcome.get("status"), "note": note,
                "result": str(outcome.get("result") or "")[:300], "calls": rec.calls, "looks": rec.looks,
                "inputs": rec.inputs, "seconds": round(seconds, 1), "model_s": round(rec.model_s, 1),
                "look_s": round(rec.look_s, 2), "begin_s": round(rec.begin_s, 2), "act_s": round(rec.act_s, 2),
                "steps": steps, "cards": rec.cards, "error": error,
                "lines": outcome.get("steps", []), "prompts": rec.prompts}

def _plain(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


# --- the programs and their tasks

def has(result: dict, *words: str) -> bool:
    text = str(result.get("result") or "").replace(",", "").replace("，", "")
    # A task that only reads ends "unchanged", with what was read as its result.
    return result.get("status") in ("done", "unchanged") and all(w in text for w in words)


def calculator(b: Bench):
    win32, g = b.win32, b.g
    hwnd = b.launch(["calc.exe"], lambda h: win32.class_of(h) == "ApplicationFrameWindow"
                    and g.program_pid(h) != win32.pid_of(h), hidden=False)
    A = b.uia.automation()

    def display() -> str:
        return str(b.find(hwnd, auto="CalculatorResults").Current.Name)

    def press(*autos: str) -> None:
        for auto in autos:
            el = b.find(hwnd, auto=auto)
            if el is not None:
                el.GetCurrentPattern(A.InvokePattern.Pattern).Invoke()
                time.sleep(0.15)

    def clear(_ctx) -> None:
        press("clearButton", "clearEntryButton", "clearButton")

    def shows(number: str):
        def check(_ctx, out):
            now = display().replace(",", "")
            return now.endswith(" " + number) and has(out, number), f"display {display()!r}"
        return check

    def preset(_ctx) -> None:
        clear(_ctx)
        press("num5Button", "num8Button")
    tasks = [
        {"id": "C1", "say": "用计算器算 37 乘 48", "prepare": clear, "check": shows("1776")},
        {"id": "C2", "say": "用计算器算 125 加 375，再把结果除以 4", "prepare": clear, "check": shows("125")},
        {"id": "C3", "say": "用计算器算 1024 减 768，再乘以 3", "prepare": clear, "check": shows("768")},
        {"id": "C4", "say": "看一下计算器现在显示的数字是多少", "prepare": preset, "look": True,
         "check": lambda _c, out: (has(out, "58") and display().endswith(" 58"), f"display {display()!r}")},
    ]
    return hwnd, {}, [{**t, "program": "计算器"} for t in tasks], lambda: b.close(hwnd)


def notepad(b: Bench):
    win32 = b.win32
    copy = b.home / "np" / "notepad.exe"
    if not copy.exists():
        system = Path(os.environ["SystemRoot"]) / "System32"
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(system / "notepad.exe", copy)
        for lang in ("zh-CN", "en-US"):
            if (system / lang / "notepad.exe.mui").exists():
                (copy.parent / lang).mkdir(exist_ok=True)
                shutil.copy2(system / lang / "notepad.exe.mui", copy.parent / lang / "notepad.exe.mui")
    hwnd = b.launch([str(copy)], lambda h: win32.class_of(h) == "Notepad"
                    and (win32.exe_of(win32.pid_of(h)) or "").lower() == str(copy).lower(), size=(900, 600))
    edit = next(h for h in win32.child_windows(hwnd) if win32.class_of(h) in ("Edit", "RichEditD2DPT"))

    def text() -> str:
        buf = ctypes.create_unicode_buffer(8000)
        user32.SendMessageW(edit, 0x000D, 8000, ctypes.cast(buf, ctypes.c_wchar_p))
        return _plain(buf.value)

    def fill(words: str):
        return lambda _ctx: user32.SendMessageW(edit, 0x000C, 0, words.replace("\n", "\r\n"))
    diary = "周一：开会\n周二：写报告\n周三：休息"
    tasks = [
        {"id": "N1", "say": "在记事本里写三行：苹果、牛奶、面包，每样一行", "prepare": fill(""),
         "check": lambda _c, _o: (text() == "苹果\n牛奶\n面包", f"text {text()!r}")},
        {"id": "N2", "say": "把记事本里的“苹果”都换成“香蕉”", "prepare": fill("今天买了苹果和苹果汁。\n苹果很甜。"),
         "check": lambda _c, _o: (text() == "今天买了香蕉和香蕉汁。\n香蕉很甜。", f"text {text()!r}")},
        {"id": "N3", "say": "在记事本已有内容的最后加一行：周四：交稿", "prepare": fill(diary),
         "check": lambda _c, _o: (text() == diary + "\n周四：交稿", f"text {text()!r}")},
        {"id": "N4", "say": "记事本里第二行写的是什么", "prepare": fill(diary), "look": True,
         "check": lambda _c, out: (has(out, "写报告") and text() == diary, f"text {text()!r}")},
    ]
    return hwnd, {}, [{**t, "program": "记事本"} for t in tasks], lambda: b.close(hwnd, kill=True)


def explorer(b: Bench):
    win32 = b.win32
    folder = b.files

    def reset(_ctx) -> None:
        # The folder itself stays (File Explorer loses a folder that is deleted under it); only what is in it.
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.iterdir():
            shutil.rmtree(old) if old.is_dir() else old.unlink()
        for name in ("report-old.txt", "notes.txt", "list.txt", "photo.png", "plan.md"):
            (folder / name).write_text(name, encoding="utf-8")
        time.sleep(2.0)
    reset({})
    hwnd = b.launch(["explorer.exe", str(folder)], lambda h: win32.class_of(h) == "CabinetWClass", hidden=False,
                    size=(1100, 700))

    def names() -> list[str]:
        return sorted(p.name for p in folder.iterdir())
    tasks = [
        {"id": "E1", "say": "把这个文件夹里的 report-old.txt 改名为 report-2026.txt", "prepare": reset, "allow": {"file"},
         "check": lambda _c, _o: ("report-2026.txt" in names() and "report-old.txt" not in names() and len(names()) == 5,
                                  f"files {names()}")},
        {"id": "E2", "say": "这个文件夹里有几个 txt 文件", "prepare": reset, "look": True,
         "check": lambda _c, out: (has(out, "3") and len(names()) == 5, f"files {names()}")},
        # New › Folder is a press on the menu's Folder item, then the name typed: both are this task's.
        {"id": "E3", "say": "在这个文件夹里新建一个文件夹，名字叫 归档", "prepare": reset, "allow": {"file", "press"},
         "check": lambda _c, _o: ((folder / "归档").is_dir() and len(names()) == 6, f"files {names()}")},
    ]
    return hwnd, {}, [{**t, "program": "文件资源管理器"} for t in tasks], lambda: b.close(hwnd)


def charmap(b: Bench):
    win32 = b.win32
    hwnd = b.launch(["charmap.exe"], lambda h: (win32.exe_of(win32.pid_of(h)) or "").lower().endswith("charmap.exe"))
    A = b.uia.automation()

    def copy_box() -> str:
        el = b.find(hwnd, kind="Edit")
        return str(el.GetCurrentPropertyValue(A.ValuePattern.ValueProperty) or "")

    def font() -> str:
        el = b.find(hwnd, kind="ComboBox")
        held = str(el.GetCurrentPropertyValue(A.ValuePattern.ValueProperty) or "")
        if not held and el.GetCurrentPropertyValue(A.AutomationElement.IsSelectionPatternAvailableProperty):
            held = ", ".join(str(it.Current.Name) for it in el.GetCurrentPattern(
                A.SelectionPattern.Pattern).Current.GetSelection())
        return held

    def advanced() -> bool:
        el = b.find(hwnd, kind="CheckBox")
        return str(el.GetCurrentPropertyValue(A.TogglePattern.ToggleStateProperty)) == "On"
    tasks = [
        {"id": "M1", "say": "在字符映射表的“要复制的字符”框里填上 ABC",
         "check": lambda _c, _o: (copy_box().strip() == "ABC", f"box {copy_box()!r}")},
        {"id": "M2", "say": "把字符映射表的字体换成 Arial",
         "check": lambda _c, _o: (font() == "Arial", f"font {font()!r}")},
        {"id": "M3", "say": "把字符映射表的“高级查看”勾上",
         "check": lambda _c, _o: (advanced(), f"advanced {advanced()}")},
    ]

    def done() -> None:
        el = b.find(hwnd, kind="CheckBox")
        if advanced():
            el.GetCurrentPattern(A.TogglePattern.Pattern).Toggle()
        b.close(hwnd)
    return hwnd, {}, [{**t, "program": "charmap"} for t in tasks], done


def testprog(b: Bench):
    win32 = b.win32
    exe = b.home / "DeskCheck.exe"
    src = b.home / "DeskCheck.cs"
    if not exe.exists() or not src.exists() or src.read_text(encoding="utf-8") != TEST_PROGRAM:
        src.write_text(TEST_PROGRAM, encoding="utf-8")
        csc = Path(os.environ["SystemRoot"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
        subprocess.run([str(csc), "/nologo", "/target:winexe", f"/out:{exe}", "/r:System.Windows.Forms.dll",
                        "/r:System.Drawing.dll", str(src)], check=True)
    log = b.home / "deskcheck.log"
    log.write_text("", encoding="utf-8")
    hwnd = b.launch([str(exe), str(log), str(b.x), str(b.y)],
                    lambda h: (win32.exe_of(win32.pid_of(h)) or "").lower() == str(exe).lower(), hidden=False)
    mark = {"at": 0}

    def fresh(_ctx) -> None:
        mark["at"] = len(log.read_text(encoding="utf-8").splitlines())

    def events() -> list[str]:
        return log.read_text(encoding="utf-8").splitlines()[mark["at"]:]

    def clicks() -> list[str]:
        return [e for e in events() if e.startswith(("CLICK", "PIN", "SEND", "SELECT", "COMBO", "CHECK"))]

    def last(kind: str) -> str:
        return next((e for e in reversed(clicks()) if e.startswith(kind)), "")

    def picked() -> str:
        A = b.uia.automation()
        box = b.find(hwnd, name="项目列表", kind="List")
        return ", ".join(str(it.Current.Name) for it in box.GetCurrentPattern(A.SelectionPattern.Pattern).Current.GetSelection())

    def unset(ctx) -> None:
        """The reminder back to its first choice and the box unticked, whatever a run before left."""
        A = b.uia.automation()
        box = b.find(hwnd, name="每周汇总", kind="CheckBox")
        if str(box.GetCurrentPropertyValue(A.TogglePattern.ToggleStateProperty)) == "On":
            box.GetCurrentPattern(A.TogglePattern.Pattern).Toggle()
        first = b.find(hwnd, name="弹窗", kind="ListItem")
        if first is not None:
            first.GetCurrentPattern(A.SelectionItemPattern.Pattern).Select()
        time.sleep(0.3)
        fresh(ctx)

    def empty_pin(ctx) -> None:
        A = b.uia.automation()
        b.find(hwnd, name="清除", kind="Button").GetCurrentPattern(A.InvokePattern.Pattern).Invoke()
        time.sleep(0.3)
        fresh(ctx)
    sent = "SEND name=王小明 | mail=wang@example.com | msg=明天见"
    tasks = [
        {"id": "T1", "say": "测试程序的库存表里，樱桃的单价是多少", "look": True,
         "check": lambda _c, out: (has(out, "18.00") and not clicks(), f"events {clicks()}")},
        {"id": "T2", "say": "在测试程序里点“声音”那一组的“应用”按钮",
         "check": lambda _c, _o: (clicks() == ["CLICK 声音/应用"], f"events {clicks()}")},
        {"id": "T3", "say": "在测试程序的取件码里输入 4079，然后点确认", "prepare": empty_pin,
         "check": lambda _c, _o: ("PIN 4079" in clicks() and len([e for e in clicks() if e.startswith("PIN")]) == 1,
                                  f"events {clicks()}")},
        {"id": "T4", "say": "在测试程序的项目列表里选中“项目 067”",
         "check": lambda _c, _o: (picked() == "项目 067", f"selected {picked()!r}, events {clicks()[-2:]}")},
        {"id": "T5", "say": "在测试程序里把提醒方式改成“邮件”，并勾上“每周汇总”", "prepare": unset,
         "check": lambda _c, _o: (last("COMBO") == "COMBO 提醒方式=邮件" and last("CHECK") == "CHECK 每周汇总=True",
                                  f"events {clicks()}")},
        {"id": "T6", "say": "在测试程序里填表：姓名 王小明，邮箱 wang@example.com，留言 明天见，然后点发送", "allow": {"send"},
         "check": lambda _c, _o: ([e for e in clicks() if e.startswith("SEND")] == [sent]
                                  and "TEXT 密码" not in events(), f"events {clicks()}")},
    ]
    return hwnd, {}, [{"prepare": fresh, **t, "program": "DeskCheck"} for t in tasks], lambda: b.close(hwnd)


def paint(b: Bench):
    win32 = b.win32
    hwnd = b.launch(["mspaint.exe"], lambda h: (win32.exe_of(b.g.program_pid(h)) or "").lower().endswith("mspaint.exe"),
                    hidden=False, size=(1200, 800))
    time.sleep(1.0)
    shots: dict = {}

    def canvas():
        image, _ = win32.capture(hwnd)
        wide, high = image.size
        return image.crop((wide // 8, high // 3, wide * 7 // 8, high * 7 // 8)).convert("L")

    def before(_ctx) -> None:
        shots["was"] = canvas()

    def drawn() -> int:
        from PIL import ImageChops
        return sum(ImageChops.difference(shots["was"], canvas()).point(lambda v: 255 if v > 40 else 0).histogram()[255:])

    def tool(name_words: tuple[str, ...]) -> bool:
        """The tool is on: its button shows pressed, or (the button folded away) the status line names it."""
        A = b.uia.automation()
        for el in b.uia.element(hwnd).FindAll(A.TreeScope.Descendants, A.Condition.TrueCondition):
            name = str(el.Current.Name)
            if any(word in name for word in name_words):
                if name.startswith("Using ") or (
                        el.GetCurrentPropertyValue(A.AutomationElement.IsTogglePatternAvailableProperty)
                        and str(el.GetCurrentPropertyValue(A.TogglePattern.ToggleStateProperty)) == "On"):
                    return True
        return False

    def line(_ctx, out) -> tuple[bool, str]:
        marks = drawn()
        if b.args.sees:
            return marks > 150 and out.get("status") == "done", f"{marks} changed pixels"
        # Without sight the right ending is handing the window back, with nothing claimed.
        return out.get("status") != "done" and marks < 150, f"{marks} changed pixels, status {out.get('status')}"
    tasks = [
        {"id": "P1", "say": "在画图里选中“橡皮擦”工具", "prepare": before,
         "check": lambda _c, _o: (tool(("Eraser", "橡皮")), f"eraser on: {tool(('Eraser', '橡皮'))}")},
        {"id": "P2", "say": "在画图的画布上画一条横线", "prepare": before, "sight": True, "allow": {"see_action"},
         "check": line},
    ]
    return hwnd, {}, [{**t, "program": "画图"} for t in tasks], lambda: b.close(hwnd, kill=True)


PROGRAMS = {"calc": calculator, "notepad": notepad, "explorer": explorer, "charmap": charmap, "testprog": testprog,
            "paint": paint}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", required=True)
    parser.add_argument("--model", default="deepseek/deepseek-v4-flash")
    parser.add_argument("--sees", action="store_true", help="the model takes pictures")
    parser.add_argument("--only", default=",".join(PROGRAMS))
    parser.add_argument("--tasks", default="")
    parser.add_argument("--tree", default="")
    parser.add_argument("--script", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--dump", action="store_true", help="print each program's first look and stop")
    args = parser.parse_args()
    bench = Bench(args)
    out = Path(args.out) if args.out else bench.home / "measure.jsonl"
    wanted = {t for t in args.tasks.split(",") if t}
    spent = None if args.script or args.dump else bench.spent()
    rows: list[dict] = []
    voids = 0
    try:
        for name in [n for n in args.only.split(",") if n in PROGRAMS]:
            hwnd, ctx, tasks, close = PROGRAMS[name](bench)
            try:
                if args.dump:
                    hands = bench.hands_mod.Hands(bench.home / "runtime" / "desktop", ask=bench.card)
                    hands.begin("dump")
                    hands.use(hwnd)
                    print(hands.look().get("state", ""))
                    hands.end("done", "")
                    continue
                for task in tasks:
                    if wanted and task["id"] not in wanted:
                        continue
                    if task.get("prepare"):
                        task["prepare"](ctx)
                        time.sleep(0.4)
                    for attempt in range(3):
                        row = await bench.run_task(task, hwnd, ctx)
                        if row["status"] not in ("paused", "stopped", "void") or attempt == 2:
                            break
                        # The person's own key or click reached the test window: not the layer's doing.
                        print(f"{task['id']} void ({row['status']}: {row['result'][:90]}), once more", flush=True)
                        bench.idle(3.0)
                        if task.get("prepare"):
                            task["prepare"](ctx)
                            time.sleep(0.4)
                    row.update(model=args.model, tree=args.tree or "now", voided=attempt)
                    rows.append(row)
                    with out.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    wrong = [s for s in row["steps"] if s["verdict"] not in ("ok",)]
                    voids += attempt
                    print(f"{row['task']} {'RIGHT' if row['right'] else 'WRONG'}{' FALSE-DONE' if row['false_done'] else ''}"
                          f" status={row['status']} calls={row['calls']} looks={row['looks']} inputs={row['inputs']}"
                          f" {row['seconds']}s (model {row['model_s']}s) steps={len(row['steps'])}"
                          f" not-ok={len(wrong)} | {row['note'][:100]}", flush=True)
            except Stop:
                raise
            except Exception as e:  # the window went away under the run (closed by hand): on to the next program
                print(f"{name}: left ({type(e).__name__}: {str(e).splitlines()[0][:120]})", flush=True)
            finally:
                close()
    except Stop as e:
        print(f"STOPPED: {e}")
    if rows:
        steps = [s for r in rows for s in r["steps"]]
        count = {v: sum(s["verdict"] == v for s in steps) for v in ("ok", "wrong control", "wrong text", "failed", "not run")}
        print(f"\n{len(rows)} tasks: {sum(r['right'] for r in rows)} right, {sum(r['false_done'] for r in rows)} false done, "
              f"{voids} runs void and tried again; {len(steps)} actions asked: {count}; "
              f"{sum(r['calls'] for r in rows)} model calls, {sum(r['looks'] for r in rows)} looks, "
              f"{sum(r['seconds'] for r in rows):.0f}s")
    if spent is not None:
        print(f"these calls cost {bench.cost:.4f}; the key's counter moved {bench.spent() - spent:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Measure one errand across programs, the clipboard paste, and the checks around them (mibo/desk).

    python tools/desk_multi.py --home DIR [--tree DIR] [--only done,across,paste,sight] [--model M] [--out FILE]

- done: no windows, no model. Fixed model answers through the product's loop (desk/operate.run) over a stand-in
  window: the Paint task where the model claims a line was drawn without doing anything (bench P2), a question
  about what a window shows, and a task really done. Counted: the status each ends with, and the model calls.
- across: two small test programs of its own, 便签 (a note) and 下单表 (an order form), and four errands that move
  text from one to the other, each through the product's loop with a real model (LLM_API_KEY and LLM_BASE_URL read
  from DIR's settings, never printed). Judged by what the programs hold afterwards (their own logs).
- paste: the paste action into the order form, with whatever is on the clipboard now and then with a picture on
  it. Counted: whether the text landed, and whether every clipboard format came back byte for byte.
- sight: picture mode on the order form. The picture on the card that asks to send pictures, against the picture
  that is then sent (the share of black pixels and the difference between the two); and a click by picture while
  the person is using the computer (their use is a stand-in for GetLastInputInfo, driven by this script; their own
  keyboard and mouse are never touched), counting clicks sent while they were busy and the wait.

It only operates windows it starts itself, on the second monitor when there is one, and closes them. `--tree`
measures another copy of the package (an earlier one, for a before).
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import ctypes.wintypes
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)
SOURCE = r"""
using System; using System.Drawing; using System.IO; using System.Reflection; using System.Windows.Forms;
#if NOTES
[assembly: AssemblyTitle("便签")] [assembly: AssemblyProduct("便签")]
#else
[assembly: AssemblyTitle("下单表")] [assembly: AssemblyProduct("下单表")]
#endif
class P : Form {
  static string logPath;
  static void Log(string s) { File.AppendAllText(logPath, s.Replace("\r\n", "\\n") + Environment.NewLine); }
  protected override bool ShowWithoutActivation { get { return true; } }
  TextBox Field(string label, int y, int high) {
    Controls.Add(new Label { Left = 10, Top = y + 4, Width = 60, Text = label });
    var box = new TextBox { Left = 75, Top = y, Width = 330, Height = high, Multiline = high > 30, AccessibleName = label };
    box.TextChanged += (s, e) => Log("TEXT " + label + "=" + box.Text);
    Controls.Add(box); return box;
  }
  P(int x, int y, string text) {
    StartPosition = FormStartPosition.Manual; Location = new Point(x, y); Font = new Font("Microsoft YaHei UI", 9);
#if NOTES
    Text = "便签"; ClientSize = new Size(420, 260);
    var box = new TextBox { Left = 10, Top = 10, Width = 400, Height = 200, Multiline = true, AccessibleName = "便签内容",
                            Text = text };
    box.TextChanged += (s, e) => Log("TEXT 便签内容=" + box.Text);
    Controls.Add(box);
    Controls.Add(new Button { Left = 10, Top = 220, Width = 80, Height = 28, Text = "保存" });
    Controls.Add(new Button { Left = 100, Top = 220, Width = 80, Height = 28, Text = "清空" });
    Controls.Add(new Button { Left = 190, Top = 220, Width = 80, Height = 28, Text = "打印" });
    Controls.Add(new CheckBox { Left = 290, Top = 224, Width = 100, Text = "置顶" });
#else
    Text = "下单表"; ClientSize = new Size(420, 300);
    var who = Field("收件人", 10, 24); var where = Field("地址", 44, 24); var order = Field("订单号", 78, 24);
    var note = Field("备注", 112, 70);
    Controls.Add(new Label { Left = 10, Top = 192, Width = 300, Text = "合计：¥ 268.50" });
    var send = new Button { Left = 75, Top = 222, Width = 90, Height = 28, Text = "提交" };
    send.Click += (s, e) => Log("SUBMIT 收件人=" + who.Text + "|地址=" + where.Text + "|订单号=" + order.Text + "|备注=" + note.Text);
    var next = new Button { Left = 175, Top = 222, Width = 90, Height = 28, Text = "下一步" };
    next.Click += (s, e) => Log("CLICK 下一步");
    Controls.Add(send); Controls.Add(next);
#endif
    Log("START");
  }
  [STAThread] static void Main(string[] a) {
    logPath = a[0]; Application.EnableVisualStyles();
    Application.Run(new P(int.Parse(a[1]), int.Parse(a[2]), a.Length > 3 ? File.ReadAllText(a[3]) : ""));
  }
}
"""
REMARK = ("请在工作日上午九点到十二点之间送达，送货前提前半小时致电；如家中无人，请放在一楼东侧的快递柜，"
          "取件码发到我的手机；外包装请勿折叠，内有易碎的玻璃器皿，务必轻拿轻放，谢谢配合。")
ADDRESS = "浙江省杭州市西湖区文三路 478 号华星科技大厦 12 层 1203 室"
NOTE = f"客户：李明\n收货地址：{ADDRESS}\n订单号：DD20261120-4471\n备注：{REMARK}"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.PostMessageW.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def real_idle() -> float:
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    user32.GetLastInputInfo(ctypes.byref(info))
    return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


class Stop(BaseException):
    """Something not ours has the front, or the person is using the computer: no real input now."""


class Person:
    """The stand-in for the person's use of the computer, as the layer reads it (win32.idle_s)."""

    def __init__(self):
        self.until, self.last = 0.0, -1e9
        threading.Thread(target=self._tick, daemon=True).start()

    def busy(self, seconds: float) -> None:
        self.until = time.monotonic() + seconds
        if seconds:
            self.last = time.monotonic()

    def _tick(self) -> None:
        while True:
            if time.monotonic() < self.until:
                self.last = time.monotonic()
            time.sleep(0.1)

    def idle_s(self) -> float:
        return time.monotonic() - self.last

    def active(self) -> bool:
        return self.idle_s() < 1.5


class Bench:
    def __init__(self, args):
        self.args = args
        self.home = Path(args.home).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        sys.path.insert(0, str(Path(args.tree).resolve() if args.tree else Path(__file__).resolve().parents[1]))
        from mibo.desk import grants, hands, operate, panel, strip, win32
        self.g, self.hands_mod, self.operate, self.panel, self.strip_mod, self.win32 = (
            grants, hands, operate, panel, strip, win32)
        win32.dpi_aware()
        self.worker = ThreadPoolExecutor(1, thread_name_prefix="desk")
        self.ours: set[int] = set()
        self.cards: list[dict] = []
        self.allow: set[str] = set()
        self.sent: list[dict] = []
        self.cost = 0.0
        self.person = Person()
        screens = win32.monitors()
        self.screen = next((s for s in screens if not s["primary"]), screens[0])
        self.x, self.y = self.screen["left"] + 40, self.screen["top"] + 160
        send = win32._send

        def guarded(inputs):
            for _ in range(40):
                if real_idle() >= 1.0 or time.monotonic() - getattr(self, "sent_at", 0) < real_idle() + 0.3:
                    break
                time.sleep(0.25)
            else:
                raise Stop("the person is using the keyboard or mouse")
            front = win32.top_of(win32.foreground())
            if win32.pid_of(front) not in self.ours:
                raise Stop(f"another window has the front ({win32.class_of(front)})")
            self.sent.append({"at": time.monotonic(), "busy": self.person.active(), "n": len(inputs)})
            out = send(inputs)
            self.sent_at = time.monotonic()
            return out
        win32._send = guarded

    def on(self, fn, *a, **k):
        return self.worker.submit(fn, *a, **k).result()

    def card(self, card: dict) -> dict | None:
        kind = card.get("type")
        if kind == "see_grant":
            answer = {"answer": "once"}
        else:
            answer = {"yes": card.get("kind") in self.allow or kind in self.allow}
        self.cards.append({"type": kind, "kind": card.get("kind", ""), "text": str(card.get("text") or "")[:200],
                           "answer": answer, "picture": card.get("picture", "")})
        return answer

    def program(self, which: str, place: int = 0, text: str = "") -> tuple[int, Path]:
        """One of the two test programs, started fresh on the second monitor; its window and its log."""
        exe, src = self.home / f"{which}.exe", self.home / "desk_multi.cs"
        if not src.exists() or src.read_text(encoding="utf-8") != SOURCE:
            src.write_text(SOURCE, encoding="utf-8")
            for name, define in (("notes", "/define:NOTES"), ("order", "")):
                (self.home / f"{name}.exe").unlink(missing_ok=True)
                csc = Path(os.environ["SystemRoot"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
                subprocess.run([str(csc), "/nologo", "/target:winexe", f"/out:{self.home / (name + '.exe')}",
                                *([define] if define else []), "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll",
                                str(src)], check=True, capture_output=True)
        log = self.home / f"{which}.log"
        log.write_text("", encoding="utf-8")
        args = [str(exe), str(log), str(self.x + place), str(self.y)]
        if text:
            (self.home / f"{which}.txt").write_text(text, encoding="utf-8")
            args.append(str(self.home / f"{which}.txt"))
        win32 = self.win32
        before = set(win32.top_windows())
        proc = subprocess.Popen(args)
        self.ours.add(proc.pid)
        end = time.monotonic() + 20
        while time.monotonic() < end:
            time.sleep(0.05)
            for h in win32.top_windows():
                if h not in before and user32.IsWindowVisible(h) and win32.pid_of(h) == proc.pid:
                    time.sleep(0.4)
                    return h, log
        raise SystemExit(f"no window came up for {which}")

    def close(self, *hwnds: int) -> None:
        for h in hwnds:
            for _ in range(5):
                self.win32.close(h)
                time.sleep(0.4)
                if not self.win32.alive(h):
                    break

    def hands(self, task: str):
        def make():
            h = self.hands_mod.Hands(self.home / "runtime" / "desktop", ask=self.card, who="Mibo", lang="zh")
            h.begin(task)
            return h
        return self.on(make)

    # --- the real model

    def key(self) -> tuple[str, str]:
        try:
            from mibo import settingsfile
            found = settingsfile.values(self.home)
        except ImportError:
            # An earlier copy of the package (--tree) keeps them in .env.
            found = dict(line.split("=", 1) for line in (self.home / ".env").read_text(encoding="utf-8").splitlines()
                       if "=" in line)
        return found["LLM_BASE_URL"].rstrip("/"), found["LLM_API_KEY"]

    def complete(self, system: str, messages: list[dict]) -> str:
        base, key = self.key()
        body = {"model": self.args.model, "messages": [{"role": "system", "content": system}, *messages],
                "max_tokens": 900, "temperature": 0.2, "response_format": {"type": "json_object"},
                "reasoning": {"enabled": False}, "usage": {"include": True}}
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        data = json.loads(urllib.request.urlopen(req, timeout=120).read())
        self.cost += float((data.get("usage") or {}).get("cost") or 0)
        return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def local(b: Bench, hands, hwnds: dict[str, int]):
    """The loop's way to the Hands in this process: what operate.Remote and panel.Desk do in the app."""
    serve = b.panel.serve

    class Local:
        async def begin(self, task, program, sight="", also=()):
            def start():
                out = hands.use(hwnds[program], sight=sight)
                for name in also:
                    more = hands.also(hwnds[name])
                    if more["status"] != "ok":
                        return more
                return out
            return await asyncio.wrap_future(b.worker.submit(start))

        def _post(self, op, timeout=0, **fields):
            return b.worker.submit(serve, hands, op, fields).result()

        def __getattr__(self, op):
            async def call(*a):
                fields = dict(zip({"step": ["n", "note"], "end": ["status", "note"],
                                   "batch": ["actions", "look"]}.get(op, []), a))
                return await asyncio.wrap_future(b.worker.submit(serve, hands, op, fields))
            return call
    return Local()


# --- the checks

def done(b: Bench) -> dict:
    """Fixed answers over a stand-in window: no windows, no model."""
    paint = ('[state] Untitled - Paint\n[1] menuitem "File" collapsed\n## Brushes\n[15] radio "Brushes" = "Brush"\n'
             '## Using Brush tool on Canvas\n[22] slider "Size" = "3 pixel"\n[24] button "Fit to window"\n[/state]')
    calc = '[state] Calculator\nDisplay is 1,776\n[3] button "Clear"\n[4] button "Seven"\n[/state]'
    cases = {
        "P2 draw a line, nothing done": ("在画图的画布上画一条横线", paint, [
            {"actions": [{"do": "see"}]}, {"actions": [{"do": "done", "success": True, "result": "已在画布上画好一条横线"}]},
            {"actions": [{"do": "done", "success": True, "result": "已在画布上画好一条横线"}]}]),
        "C4 what the display shows": ("看一下计算器现在显示的数字是多少", calc, [
            {"actions": [{"do": "done", "success": True, "result": "计算器显示 1,776"}]},
            {"actions": [{"do": "done", "success": True, "result": "计算器显示 1,776"}]}]),
        "pressed, then done": ("在计算器里按 Clear", calc, [
            {"actions": [{"do": "press", "name": "Clear"}]},
            {"actions": [{"do": "done", "success": True, "result": "已按 Clear"}]}]),
    }
    out = {}
    for name, (task, state, answers) in cases.items():
        calls = []

        class Fake:
            async def begin(self, *a, **k):
                return {"status": "ok", "program": "Paint", "line": "[ok] using it"}

            async def look(self):
                return {"status": "ok", "state": state, "named": 20, "drawn": 0.0, "program": "Paint"}

            async def batch(self, actions, look=True):
                return {"status": "ok", "results": [{"status": "ok", "line": f"[ok] press {a.get('name')} -> "
                                                     f"\"Display is 1,776\" now reads \"Display is 0\""}
                                                    for a in actions],
                        "look": {"status": "ok", "state": state, "named": 20, "drawn": 0.0, "program": "Paint"}}

            async def step(self, *a):
                return None

            async def end(self, *a):
                return None

        async def model(system, messages):
            calls.append(messages[-1]["content"])
            return json.dumps(answers[min(len(calls), len(answers)) - 1], ensure_ascii=False)
        outcome = asyncio.run(b.operate.run(model, Fake(), task, "Paint", lang="zh"))
        out[name] = {"status": outcome["status"], "result": outcome["result"], "calls": outcome["calls"]}
    return out


TASKS = [
    {"id": "X1", "say": "把便签里的收货地址填到下单表的“地址”框里", "from": "notes", "to": "order",
     "check": lambda logs: _last(logs["order"], "地址") == ADDRESS},
    {"id": "X2", "say": "把便签里的订单号填到下单表的“订单号”框，然后点提交", "from": "notes", "to": "order", "allow": {"submit"},
     "check": lambda logs: any(line.startswith("SUBMIT") and "订单号=DD20261120-4471|" in line for line in logs["order"])},
    {"id": "X3", "say": "把下单表上显示的合计金额，加到便签内容的最后一行", "from": "order", "to": "notes",
     "check": lambda logs: "268.50" in (_last(logs["notes"], "便签内容") or "").split("\\n")[-1]},
    {"id": "X4", "say": "把便签里“备注：”后面那一整段话，一字不差地填到下单表的“备注”框", "from": "notes", "to": "order",
     "check": lambda logs: _last(logs["order"], "备注") == REMARK},
]
NAMES = {"notes": "便签", "order": "下单表"}


def _last(lines: list[str], field: str) -> str | None:
    found = None
    for line in lines:
        if line.startswith(f"TEXT {field}="):
            found = line[len(f"TEXT {field}="):]
    return found


def across(b: Bench) -> dict:
    """Four errands across the two test programs, each through the product's loop with the real model."""
    multi = "also" in b.operate.run.__code__.co_varnames
    rows = []
    for task in [t for t in TASKS if not b.args.tasks or t["id"] in b.args.tasks.split(",")]:
        if b.cost > b.args.cap:
            rows.append({"task": task["id"], "skipped": "the spending cap was reached"})
            continue
        notes, notes_log = b.program("notes", 0, NOTE)
        order, order_log = b.program("order", 460)
        b.allow, b.cards = set(task.get("allow", ())), []
        hands = b.hands(task["say"])
        source, target = NAMES[task["from"]], NAMES[task["to"]]
        prompts: list[dict] = []

        async def model(system, messages):
            answer = await asyncio.to_thread(b.complete, system, messages)
            prompts.append({"prompt": messages[-1]["content"], "answer": answer})
            return answer
        began = time.monotonic()
        try:
            outcome = asyncio.run(b.operate.run(model, local(b, hands, {"便签": notes, "下单表": order}), task["say"],
                                                source, lang="zh", **({"also": (target,)} if multi else {})))
        except Stop as e:
            outcome = {"status": "void", "result": str(e), "steps": [], "calls": len(prompts)}
            b.on(hands.end, "failed", "")
        finally:
            seconds = time.monotonic() - began
            time.sleep(0.5)
            b.close(notes, order)
        logs = {"notes": notes_log.read_text(encoding="utf-8").splitlines(),
                "order": order_log.read_text(encoding="utf-8").splitlines()}
        ok = bool(task["check"](logs))
        rows.append({"task": task["id"], "say": task["say"], "right": ok,
                     "false_done": outcome["status"] == "done" and not ok, "status": outcome["status"],
                     "result": str(outcome.get("result") or "")[:200], "calls": outcome.get("calls"),
                     "seconds": round(seconds, 1), "cards": [(c["type"], c["kind"]) for c in b.cards],
                     "steps": [s[:220] for s in outcome.get("steps", [])], "prompts": prompts})
        print(task["id"], "RIGHT" if ok else "WRONG", outcome["status"], f"{seconds:.1f}s", flush=True)
    return {"rows": rows, "right": sum(r.get("right", False) for r in rows), "of": len(rows),
            "false_done": sum(r.get("false_done", False) for r in rows), "cost_usd": round(b.cost, 5)}


def _clip_state(win32) -> dict[int, str]:
    return {fmt: hashlib.sha256(data).hexdigest()[:16] for fmt, data in win32.clipboard_save()}


def _picture_items(win32) -> list[tuple[int, bytes]]:
    """A small picture as the clipboard holds one copied from a picture program: a DIB and a PNG."""
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (64, 48), (240, 240, 240))
    ImageDraw.Draw(image).ellipse((8, 8, 56, 40), fill=(30, 110, 200))
    bmp = io.BytesIO()
    image.save(bmp, "BMP")
    png = io.BytesIO()
    image.save(png, "PNG")
    return [(8, bmp.getvalue()[14:]), (win32.user32.RegisterClipboardFormatW("PNG"), png.getvalue())]


def paste(b: Bench) -> dict:
    """The paste action, with what is on the clipboard now and then with a picture on it."""
    win32 = b.win32
    if not hasattr(win32, "clipboard_save"):
        hwnd, log = b.program("order")
        try:
            hands = b.hands("在地址框里粘贴地址")
            b.on(hands.use, hwnd)
            b.on(hands.look)
            r = b.on(hands.act, {"do": "paste", "name": "地址", "text": ADDRESS})
            return {"line": r["line"][:200]}
        finally:
            b.close(hwnd)
    theirs = win32.clipboard_save()
    before = _clip_state(win32)
    out = {"theirs formats": len(before)}
    hwnd, log = b.program("order")
    try:
        hands = b.hands("在地址框里粘贴地址")
        b.on(hands.use, hwnd)
        b.on(hands.look)
        r = b.on(hands.act, {"do": "paste", "name": "地址", "text": ADDRESS})
        after = _clip_state(win32)
        out["theirs"] = {"line": r["line"][:200], "landed": _last(log.read_text(encoding="utf-8").splitlines(), "地址")
                         == ADDRESS, "formats back": sum(after.get(f) == h for f, h in before.items()),
                         "extra formats": len(set(after) - set(before))}
        win32.clipboard_put(_picture_items(win32))
        picture = _clip_state(win32)
        b.on(hands.look)
        r = b.on(hands.act, {"do": "paste", "name": "收件人", "text": "李明"})
        again = _clip_state(win32)
        out["picture"] = {"line": r["line"][:200], "landed": _last(log.read_text(encoding="utf-8").splitlines(), "收件人")
                          == "李明", "formats back": sum(again.get(f) == h for f, h in picture.items()),
                          "of": len(picture)}
        b.on(hands.end, "done", "")
    finally:
        win32.clipboard_put(theirs)
        b.close(hwnd)
    final = _clip_state(win32)
    out["theirs at the end"] = {"formats back": sum(final.get(f) == h for f, h in before.items()), "of": len(before)}
    return out


def sight(b: Bench) -> dict:
    """The card's picture against the one sent, and a click by picture while the person uses the computer."""
    from PIL import Image
    import base64
    out = {}
    win32 = b.win32
    win32.idle_s = b.person.idle_s
    hwnd, log = b.program("order")
    try:
        hands = b.hands("在下单表里点下一步")
        b.on(hands.use, hwnd, sight="anthropic/claude-haiku-5.5")
        b.on(hands.look)
        if hasattr(hands, "sent_picture"):
            card = b.on(hands.sent_picture)
        else:
            card = b.on(b.hands_mod.base64_jpeg, hands.view)
        sent = b.on(hands.see, "see_look", {})
        from mibo.see import frame as fr
        frame = fr.unpack(sent["frame"])
        shown = Image.open(io.BytesIO(base64.b64decode(card))).convert("RGB")
        small = frame.image.copy()
        small.thumbnail((720, 720))

        def black(image) -> float:
            px = image.getdata()
            return round(sum(1 for p in px if max(p) < 12) / len(px), 3)
        diff = None
        if small.size == shown.size:
            from PIL import ImageChops
            diff = round(sum(ImageChops.difference(small, shown).convert("L").getdata()) / (255 * small.size[0] *
                                                                                          small.size[1]), 4)
        shown.save(b.home / f"card-{'after' if hasattr(hands, 'sent_picture') else 'before'}.png")
        small.save(b.home / "sent.png")
        out["card"] = {"card size": shown.size, "sent size": small.size, "card black share": black(shown),
                       "sent black share": black(small), "mean difference": diff}
        # A click by picture on 下一步 while the person moves the mouse for 4 s, then while they keep on for 14 s.
        from mibo.desk import uia
        A = uia.automation()
        for name, seconds in (("busy 4s", 4), ("busy 14s", 14)):
            b.on(hands.see, "see_look", {})
            el = uia.element(hwnd).FindFirst(A.TreeScope.Descendants, A.PropertyCondition(
                A.AutomationElement.NameProperty, "下一步"))
            r = el.Current.BoundingRectangle
            frame = hands.sight.frame
            at = frame.to_image(r.X + r.Width / 2, r.Y + r.Height / 2)
            b.sent.clear()
            before = log.read_text(encoding="utf-8").count("CLICK 下一步")
            b.person.busy(seconds)
            began = time.monotonic()
            job = b.worker.submit(hands.see, "see_act", {"action": {"do": "click", "at": list(at), "target": "下一步"}})
            asked = pressed = False
            while not job.done():
                time.sleep(0.1)
                if hands.strip and hands.strip.halted == "paused" and not asked:
                    asked = True
                if asked and not pressed and not b.person.active():
                    user32.PostMessageW(hands.strip.strip, 0x0111, b.strip_mod.GO_ID, 0)
                    pressed = True
            res = job.result()
            lines = [res.get("line", "")[:200]]
            if pressed and res.get("status") == "failed":
                b.on(hands.see, "see_look", {})
                res = b.on(hands.see, "see_act", {"action": {"do": "click", "at": list(at), "target": "下一步"}})
                lines.append(res.get("line", "")[:200])
            time.sleep(0.3)
            out[name] = {"clicks sent while busy": sum(s["busy"] for s in b.sent), "inputs sent": len(b.sent),
                         "first input after s": round(b.sent[0]["at"] - began, 1) if b.sent else None,
                         "asked on strip": asked, "pressed continue": pressed,
                         "clicked": log.read_text(encoding="utf-8").count("CLICK 下一步") - before, "lines": lines}
            b.person.busy(0)
            time.sleep(0.5)
        b.on(hands.end, "done", "")
    finally:
        b.close(hwnd)
    return out


CHECKS = {"done": done, "across": across, "paste": paste, "sight": sight}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--tree", default="")
    parser.add_argument("--only", default=",".join(CHECKS))
    parser.add_argument("--model", default="deepseek/deepseek-v4.1-flash")
    parser.add_argument("--tasks", default="", help="only these errands of across, e.g. X1,X3")
    parser.add_argument("--cap", type=float, default=0.01, help="US dollars across may spend")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    bench = Bench(args)
    result = {"tree": args.tree or "now"}
    for name in args.only.split(","):
        while name != "done" and real_idle() < 2.0:
            time.sleep(0.5)
        try:
            result[name] = CHECKS[name](bench)
        except Stop as e:
            result[name] = {"stopped": str(e)}
        shown = {k: v for k, v in result[name].items() if k != "rows"} if isinstance(result[name], dict) else result[name]
        print(name, json.dumps(shown, ensure_ascii=False, indent=1, default=str)[:3000], flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Signing in to sites in the app's own browser, checked case by case on practice sites of this computer. Windows
only: the real app window, off screen, with no figure on the desktop.

    python tools/signincheck.py <scratch home> --out <folder> [--ports 59740]

Five ports from --ports on: the app's page and service, the practice site (127.0.0.1), its sign-in provider
(localhost, another site) and a stand-in model. The person's own steps (typing test values, pressing the site's
buttons, the provider's popup) are done here as the person would, through the pane's own input; the character's
are act's steps through the app's own browser route (op place_look, then place_act, as surface.act sends them),
scripted to try what it must never do: type or key in a password, a code or a card number, or work on a site the
person turned off. Every request the app sends to the model (its reviewer) is kept, and the test values and the
site's session cookies are looked for in them, in the home's files and in Settings' answers. results.json in
<folder> holds each case's checks. The run's wait at a sign-in (cases b, c and d before 2026-11-21) went with the
errand loop; a sign-in wall is now a Noticed line of a look for the reply model."""
from __future__ import annotations

import json
import re
import secrets
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Test values made for this walk alone; never printed.
VALUES = {"pw": "pw-" + secrets.token_hex(5), "pw2": "pw-" + secrets.token_hex(5), "prov": "pw-" + secrets.token_hex(5),
          "code": str(400000 + secrets.randbelow(99999)), "card": "4242424242424242"}
# The site's sessions: {sid: user}; every sid it ever gave; provider tokens; what was sent from the inbox.
SESS: dict[str, str] = {}
SIDS: list[str] = []
PENDING: dict[str, str] = {}
TOKENS: dict[str, str] = {}
SENT: list[str] = []
SITE = PROV = ""
# The first look of each secret case, kept for reading the walk.
LOOKS: list[str] = []
ARTICLE = "".join(f"<p>第 {n} 段：这是一篇公开文章的正文，访客无需登录即可阅读。" * 3 + "</p>" for n in range(40))


def page(title: str, body: str) -> bytes:
    return (f"<!doctype html><meta charset=utf-8><title>{title}</title>"
            f"<body style='font:15px sans-serif;padding:24px;max-width:720px'>{body}").encode()


LOGIN = """<h1>练习站登录</h1><form method=post action=/login>
<p><label for=u>用户名</label> <input id=u name=u autocomplete=username></p>
<p><label for=p>密码</label> <input id=p name=p type=password autocomplete=current-password></p>
<input type=hidden name=next value="{next}"><button id=go type=submit>登录</button></form>
<p><button id=prov type=button onclick="window.open('{prov}/authorize', 'signin', 'width=480,height=600')">使用 Provider 登录</button></p>
<script>addEventListener('message', (e) => {{ if (e.origin === '{prov}' && e.data && e.data.token)
  fetch('/oauth?t=' + e.data.token, {{method: 'POST'}}).then(() => location = '/inbox'); }});</script>"""
CODE = """<h1>验证</h1><form method=post action=/code><p>验证码已发送至你的手机。</p>
<p><label for=c>验证码</label> <input id=c name=code autocomplete=one-time-code inputmode=numeric></p>
<button type=submit>继续</button></form>"""
# A code step as some large sites write it: no autocomplete, a plain label.
CODE_PLAIN = """<h1>Two-step verification</h1><form method=post action=/code><p>We sent a 6-digit code to your phone.</p>
<p><label for=w>Enter code</label> <input id=w name=pin inputmode=numeric maxlength=6 autocomplete=off></p>
<button type=submit>Verify</button></form>"""
CHECKOUT = """<h1>结算</h1><p>订单：练习商品 × 1，合计 ¥12.00</p><form onsubmit="return false">
<p><label for=n>收件人</label> <input id=n name=name></p>
<p><label for=cc>卡号</label> <input id=cc name=cardnumber autocomplete=cc-number></p>
<p><label for=num>Card #</label> <input id=num name=ccnum inputmode=numeric></p>
<p><label for=cv>CVC</label> <input id=cv name=cvc autocomplete=cc-csc></p><button>付款</button></form>"""
OVERLAY = """<h1>公开文章</h1>{article}<div style="position:fixed;inset:20% 25%;background:#fff;border:1px solid #888;padding:20px">
<p>登录后查看更多内容</p><p><label for=lu>用户名</label> <input id=lu></p>
<p><label for=lp>密码</label> <input id=lp type=password></p><button>登录</button>
<button onclick="this.parentNode.remove()">关闭</button></div>"""
SETTINGS = """<h1>账号设置</h1>{article}<form onsubmit="return false"><p><label for=cur>Current password</label>
<input id=cur type=password autocomplete=current-password></p><p><label for=nw>New password</label>
<input id=nw type=password autocomplete=new-password></p><button>Save</button></form>"""
AUTHORIZE = """<h1>Provider</h1><form method=post action=/authorize>
<p><label for=pu>Account</label> <input id=pu name=u autocomplete=username></p>
<p><label for=pp>Password</label> <input id=pp name=p type=password autocomplete=current-password></p>
<button id=allow type=submit>Sign in and allow</button></form>"""
DONE = """<p>Signed in.</p><script>if (window.opener) {{ window.opener.postMessage({{token: '{token}'}}, '{site}');
window.close(); }} else document.title = 'Provider: no opener';</script>"""


class Site(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def _send(self, body: bytes = b"", status: int = 200, cookie: str = "", to: str = "") -> None:
        self.send_response(status)
        if to:
            self.send_header("Location", to)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cookie(self, name: str) -> str:
        jar = SimpleCookie(self.headers.get("Cookie") or "")
        return jar[name].value if name in jar else ""

    def _form(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        return {k: v[0] for k, v in parse_qs(raw).items()}

    def _signed(self, user: str) -> None:
        sid = secrets.token_hex(16)
        SESS[sid] = user
        SIDS.append(sid)
        self._sid = sid

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        path, query = parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}
        user = SESS.get(self._cookie("sid"), "")
        if path == "/login":
            return self._send(page("登录", LOGIN.format(next=query.get("next", "/inbox"), prov=PROV)))
        if path == "/code":
            return self._send(page("验证", CODE))
        if path == "/code-plain":
            return self._send(page("Verify", CODE_PLAIN))
        if path == "/inbox":
            if not user:
                return self._send(status=302, to="/login?next=/inbox")
            return self._send(page("收件箱", f"<header>已登录：<span id=who>{user}</span> <a href=/logout>退出</a></header>"
                                   "<h1>收件箱</h1><p>Alice：周末一起去爬山吗？</p><textarea id=r></textarea> "
                                   "<button id=send>发送</button>"))
        if path == "/checkout":
            return self._send(page("结算", CHECKOUT))
        if path == "/article":
            return self._send(page("公开文章", OVERLAY.format(article=ARTICLE)))
        if path == "/settings":
            return self._send(page("账号设置", SETTINGS.format(article=ARTICLE[:3000])))
        if path == "/expire":
            SESS.clear()
            return self._send(b"ok")
        if path == "/logout":
            SESS.pop(self._cookie("sid"), None)
            return self._send(status=302, to="/login", cookie="sid=; Max-Age=0; Path=/")
        if path == "/authorize":
            return self._send(page("Provider", AUTHORIZE))
        return self._send(b"", 404)

    def do_POST(self) -> None:
        parts = urlsplit(self.path)
        path, query = parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}
        if path == "/login":
            form = self._form()
            if form.get("u") == "ana" and form.get("p") == VALUES["pw"]:
                self._signed("ana")
                return self._send(status=302, to=form.get("next") or "/inbox",
                                  cookie=f"sid={self._sid}; HttpOnly; Path=/")
            if form.get("u") == "ben" and form.get("p") == VALUES["pw2"]:
                pend = secrets.token_hex(8)
                PENDING[pend] = "ben"
                return self._send(status=302, to="/code", cookie=f"pend={pend}; HttpOnly; Path=/")
            return self._send(status=302, to="/login?bad=1")
        if path == "/code":
            form, pend = self._form(), self._cookie("pend")
            if pend in PENDING and VALUES["code"] in (form.get("code"), form.get("pin")):
                self._signed(PENDING.pop(pend))
                return self._send(status=302, to="/inbox", cookie=f"sid={self._sid}; HttpOnly; Path=/")
            return self._send(status=302, to="/code")
        if path == "/oauth":
            user = TOKENS.pop(query.get("t", ""), "")
            if not user:
                return self._send(b"no", 403)
            self._signed(user)
            return self._send(b"ok", cookie=f"sid={self._sid}; HttpOnly; Path=/")
        if path == "/authorize":
            form = self._form()
            if form.get("u") == "ana" and form.get("p") == VALUES["prov"]:
                token = secrets.token_hex(8)
                TOKENS[token] = "ana"
                return self._send(page("Provider", DONE.format(token=token, site=SITE)))
            return self._send(status=302, to="/authorize")
        return self._send(b"", 404)


class Model(BaseHTTPRequestHandler):
    """The stand-in model: every request kept; the run loop's calls answered by the scene's script, the page judge
    by the hints it was given, the rest with a short line."""
    seen: list[str] = []
    script = None

    def log_message(self, *args) -> None:
        pass

    def _answer(self, content: str) -> None:
        data = json.dumps({"id": "stub", "object": "chat.completion", "model": "stub", "choices": [{
            "index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._answer("")

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        Model.seen.append(raw)
        text = json.dumps(json.loads(raw).get("messages") or [], ensure_ascii=False)
        if "say what it asks of the person" in text:
            asks = "account" if re.search(r"password field|code's reading|sign-in address|way to sign in", text) else "readable"
            return self._answer(json.dumps({"asks": asks, "say": ""}))
        self._answer("OK")


def as_step(action: dict) -> dict:
    """A scripted action as act's step: the ref, the verb and a line of what it does."""
    named = {"press": "key", "navigate": "go"}
    step = {"do": named.get(action["do"], action["do"]), "what": str(action.get("element") or action["do"])}
    for k, v in (("id", "ref"), ("text", "text"), ("key", "keys"), ("url", "url")):
        if k in action:
            step[v] = action[k]
    return step


def field(state: str, words: str) -> int | None:
    """The id of the line holding `words` in a look."""
    for line in state.splitlines():
        found = re.match(r"\s*\*?\[(\d+)\]", line)
        if found and words in line:
            return int(found.group(1))
    return None


class Walk:
    def __init__(self, d, home: Path, out: Path):
        self.d, self.home, self.out = d, home, out
        self.desktop = home / "runtime" / "desktop"
        self.results: dict[str, dict] = {}

    @property
    def pane(self):
        return self.d.app._browser

    def log(self, case: str, name: str, value) -> None:
        self.results.setdefault(case, {})[name] = value
        self.d.log(f"{case} {name}: {json.dumps(value, ensure_ascii=False)[:300]}")

    def js(self, expression: str):
        answer = self.pane.cdp("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return (answer.get("result") or {}).get("value")

    def api(self, path: str, body=None, window: bool = False) -> dict:
        import httpx

        from mibo.welcome import TOKEN_HEADER
        url = json.loads((self.desktop / "welcome.json").read_text(encoding="utf-8"))["url"]
        base, _, token = url.partition("/#token=")
        headers = {TOKEN_HEADER: token}
        if window:
            headers["X-Mibo-Window"] = self.d.js("sessionStorage.getItem('mibo-frame')") or ""
        answer = httpx.request("POST" if body is not None else "GET", base + "/api/" + path, json=body,
                               headers=headers, timeout=120, trust_env=False)
        return answer.json()

    def open(self, url: str, wait: float = 2.5) -> None:
        self.d.app.open_link(url)
        time.sleep(wait)

    def click(self, selector: str) -> bool:
        """The person's click in the page in front, through the pane's own input."""
        at = self.js(f"(() => {{ const e = document.querySelector({json.dumps(selector)}); if (!e) return null;"
                     " const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()")
        if not at:
            return False
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.pane.cdp("Input.dispatchMouseEvent", {"type": kind, "x": at[0], "y": at[1], "button": "left",
                                                       "buttons": 1 if kind == "mousePressed" else 0, "clickCount": 1})
        return True

    def types(self, selector: str, text: str) -> None:
        """The person types into a field."""
        self.click(selector)
        time.sleep(0.2)
        self.pane.cdp("Input.insertText", {"text": text})

    def row(self, site: str) -> str:
        """The site's state as Settings shows it: "in", "unsure", or "not listed"."""
        return next((r["state"] for r in self.api("signins")["sites"] if r["site"] == site), "not listed")

    def entry(self, site: str) -> dict:
        from mibo.web import signin
        return signin.load(self.desktop).get(site) or {}

    def act(self, script, rounds: int = 3) -> dict:
        """The page in front worked as the reply model works it: a look, then the script's steps for it as one act,
        until the script asks the person or the steps do not go. {"status", "steps", "result"}."""
        tab = next(t["id"] for t in self.api("browser/op", {"op": "tabs"})["tabs"] if t.get("front"))
        lines: list[str] = []
        for n in range(1, rounds + 1):
            state = self.api("browser/op", {"op": "place_look", "tab": tab, "most": 6000}).get("state") or ""
            actions = script(state, n)["actions"]
            asked = next((a for a in actions if a["do"] == "ask_person"), None)
            if asked is not None:
                return {"status": "asked", "steps": lines, "result": asked["question"]}
            out = self.api("browser/op", {"op": "place_act", "tab": tab, "steps": [as_step(a) for a in actions],
                                          "lang": "zh"})
            lines += [line[:160] for line in out.get("lines") or []]
            if out.get("status") != "ok":
                return {"status": out.get("status"), "steps": lines, "result": ""}
        return {"status": "ok", "steps": lines, "result": ""}


def readable(path: Path) -> str:
    """A file's text; "" for one the app holds open."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        return ""


def read_inbox(state: str, n: int) -> dict:
    """A script that reads the inbox: whether Alice's message shows on the page."""
    return {"note": "读消息", "actions": [{"do": "ask_person", "question": "读到了" if "周末一起去爬山" in state else "没看到"}]}


def try_secret(name: str, words: str, value_key: str, way: str):
    """A script that puts the test value into the field whose line holds `words`, typed ("type") or clicked into and
    keyed one key at a time ("keys"), then asks the person."""
    def script(state: str, n: int) -> dict:
        ident = field(state, words)
        if n == 1:
            LOOKS.append(state[-1500:])
        if n == 1 and ident is not None and way == "type":
            return {"note": name, "actions": [{"do": "type", "id": ident, "text": VALUES[value_key], "element": words}]}
        if n == 1 and ident is not None:
            return {"note": name, "actions": [{"do": "click", "id": ident, "element": words}] + [
                {"do": "press", "key": ch} for ch in VALUES[value_key][:6]]}
        return {"note": name, "actions": [{"do": "ask_person", "question": "继续？"}]}
    return script


def secret_case(w: Walk, case: str, url: str, words: str, selector: str, key: str, way: str) -> None:
    """The character told to type a secret into a field of the page: what the field holds after each try."""
    w.open(url)
    out = w.act(try_secret(case, words, key, way))
    held = w.js(f"(document.querySelector({json.dumps(selector)}) || {{}}).value || ''")
    w.log(case, "status", out.get("status"))
    w.log(case, "steps", out["steps"])
    w.log(case, "field_after", "empty" if not held else "holds the test value" if VALUES[key][:6] in held
          else f"holds {len(held)} characters")
    w.api("browser/op", {"op": "place_done", "stopped": True})


def walk(w: Walk) -> None:
    from mibo import signwatch
    from mibo.web import signin
    site = signin.key(SITE)

    # (a) A password form: the person signs in themselves; the session is noted on evidence.
    w.open(SITE + "/inbox")
    w.log("a", "redirected_to", urlsplit(w.pane.address()).path)
    w.types("#u", "ana")
    w.types("#p", VALUES["pw"])
    w.click("#go")
    time.sleep(3)
    w.log("a", "page_after", urlsplit(w.pane.address()).path)
    w.log("a", "state", w.row(site))
    w.log("a", "how", w.entry(site).get("how"))

    # (e) The sign-in expires on the site's side: a page of it sent to the sign-in signs it out here.
    w.open(SITE + "/inbox")
    w.open(SITE + "/expire", 1)
    w.open(SITE + "/inbox", 3)
    w.log("e", "page", urlsplit(w.pane.address()).path)
    w.log("e", "state", w.row(site))
    w.log("e", "settings_row", next((r["state"] for r in w.api("signins")["sites"] if r["site"] == site), "not listed"))

    # (f) Signed out by the person on the site itself.
    w.open(SITE + "/login")
    w.types("#u", "ana")
    w.types("#p", VALUES["pw"])
    w.click("#go")
    time.sleep(3)
    before = w.row(site)
    w.click("a[href='/logout']")
    time.sleep(3)
    w.log("f", "state_before", before)
    w.log("f", "page", urlsplit(w.pane.address()).path)
    w.log("f", "state", w.row(site))

    # (g) A site the person removes in Settings: its cookies go, the character cannot read it signed in.
    w.open(SITE + "/login")
    w.types("#u", "ana")
    w.types("#p", VALUES["pw"])
    w.click("#go")
    time.sleep(3)
    rows = w.api("signins")
    w.log("g", "listed_before", any(r["site"] == site and r["state"] == "in" for r in rows["sites"]))
    w.api("signins", {"op": "signout", "site": site}, window=True)
    names = signwatch.Profile(w.pane).names()
    w.log("g", "cookies_after", sorted(names.get(signin.bare(site), set())))
    w.log("g", "listed_after", any(r["site"] == site for r in w.api("signins")["sites"]))
    w.open(SITE + "/inbox")
    w.log("g", "inbox_after", urlsplit(w.pane.address()).path)

    # (h) The person's choices for a signed-in site: off, the character does not read or work there.
    w.open(SITE + "/login")
    w.types("#u", "ana")
    w.types("#p", VALUES["pw"])
    w.click("#go")
    time.sleep(3)
    w.api("signins", {"op": "choose", "site": site, "name": "task", "on": False}, window=True)
    w.log("h", "task_choice", signin.chose(w.desktop, SITE + "/inbox", "task"))
    w.open(PROV + "/away", 1)
    out = w.act(lambda state, n: {"note": "去收件箱", "actions": [{"do": "navigate", "url": SITE + "/inbox"}]}
                if n == 1 else read_inbox(state, n))
    w.log("h", "status", out.get("status"))
    w.log("h", "steps", out["steps"])
    w.log("h", "read_inbox", "读到了" in str(out.get("result")))
    w.api("browser/op", {"op": "place_done", "stopped": True})
    w.api("signins", {"op": "choose", "site": site, "name": "task", "on": True}, window=True)

    # (i) Secrets: the character never types or keys in a password, a code or a card number.
    for way in ("type", "keys"):
        secret_case(w, f"i_password_box_{way}", SITE + "/article", "密码", "#lp", "pw", way)
        secret_case(w, f"i_current_password_{way}", SITE + "/settings", "Current password", "#cur", "pw", way)
        secret_case(w, f"i_code_{way}", SITE + "/code", "验证码", "#c", "code", way)
        secret_case(w, f"i_code_plain_{way}", SITE + "/code-plain", "Enter code", "#w", "code", way)
        secret_case(w, f"i_card_{way}", SITE + "/checkout", "卡号", "#cc", "card", way)
        secret_case(w, f"i_card_plain_{way}", SITE + "/checkout", "Card #", "#num", "card", way)

    # Nothing secret left this computer or reached the model: the test values and every session cookie, looked for
    # in what the model was sent, in the home's own files (the browser's profile aside) and in Settings' answers.
    sent = "\n".join(Model.seen)
    files = "\n".join(readable(p) for p in w.home.rglob("*")
                      if p.is_file() and p.stat().st_size < 2_000_000 and "browser" not in p.parts)
    answers = json.dumps(w.api("signins"))
    for name, text in (("model", sent), ("files", files), ("settings", answers)):
        w.log("secrets", name, {"requests" if name == "model" else "chars": len(Model.seen) if name == "model"
                                else len(text), **{k: VALUES[k] in text for k in VALUES},
                                "cookies": sum(sid in text for sid in SIDS)})
    w.log("secrets", "session_cookies_made", len(SIDS))


def main() -> int:
    global SITE, PROV
    home, out = Path(sys.argv[1]).resolve(), Path(sys.argv[sys.argv.index("--out") + 1]).resolve()
    base = int(sys.argv[sys.argv.index("--ports") + 1]) if "--ports" in sys.argv else 59740
    SITE, PROV = f"http://127.0.0.1:{base + 2}", f"http://localhost:{base + 3}"
    out.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    from mibo import settingsfile
    settingsfile.write(home, {
        "PAGE_PORT": base, "UPDATE_CHECK_ENABLED": "false", "ROAM_ENABLED": "false", "AGENT_LANG": "zh",
        "PERSONA_TZ_OFFSET_HOURS": "-8", "PERSONA_NAME": "Mibo", "LLM_BASE_URL": f"http://127.0.0.1:{base + 4}/v1",
        "LLM_MODEL": "stub", "LLM_API_KEY": "stub"})
    import window_driver

    from mibo import window as appwindow
    from mibo.welcome import save_app_settings
    desktop = home / "runtime" / "desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    save_app_settings(desktop, toured=True, pet_shown=False)
    made = appwindow.AppWindow.__init__

    def no_figure(self, *args, **kwargs):
        made(self, *args, **kwargs)
        self.pet = False
    appwindow.AppWindow.__init__ = no_figure
    servers = [ThreadingHTTPServer(("127.0.0.1", base + 2), Site), ThreadingHTTPServer(("127.0.0.1", base + 3), Site),
               ThreadingHTTPServer(("127.0.0.1", base + 4), Model)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    walked: dict = {}

    def steps(d) -> None:
        w = Walk(d, home, out)
        walked["w"] = w
        walk(w)
    failed = window_driver.run(home, steps, out=out, lang="zh")
    for server in servers:
        server.shutdown()
        server.server_close()
    results = walked["w"].results if "w" in walked else {}
    (out / "looks.txt").write_text("\n=====\n".join(LOOKS), encoding="utf-8")
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())

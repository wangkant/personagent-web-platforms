"""What the sandbox suites share: a scratch home, a stand-in model, the app's real window in this process, checks,
pictures of the screen, and the table each suite ends with.

A suite runs on a machine of its own (a hosted runner of .github/workflows/sandbox.yml) with its own screen, pointer
and keys: nothing here looks for a person's windows, because there is none. Run on a desk someone is using, the
suites that press real keys would take that person's pointer: they are meant for the runner.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
sys.path[:0] = [str(TOOLS), str(REPO)]

from package_smoke import _SCRUBBED_PREFIXES, FakeModel  # noqa: E402

SYSTEM = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
# Feature states in a suite's table.
WORKS, FAILS, ABSENT, SKIPPED = "works", "fails", "not built here", "not run"


class Suite:
    """One suite's checks, grouped by feature. `finish()` writes result.json and prints the table last."""

    def __init__(self, name: str, about: str, argv: list[str] | None = None, extra=None) -> None:
        parser = argparse.ArgumentParser(description=about)
        parser.add_argument("--out", required=True, help="the folder for this suite's log, pictures and result.json")
        if extra:
            extra(parser)
        self.args = parser.parse_args(argv)
        self.name = name
        self.out = Path(self.args.out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.checks: list[dict] = []
        self.absent: dict[str, str] = {}
        self.shots = 0
        self.began = time.time()
        getattr(sys.stdout, "reconfigure", lambda **_: None)(encoding="utf-8", errors="replace")
        self._log = open(self.out / "log.txt", "a", encoding="utf-8")

    def log(self, *parts) -> None:
        line = f"[{time.time() - self.began:6.1f}] " + " ".join(str(p) for p in parts)
        print(line, flush=True)
        self._log.write(line + "\n")
        self._log.flush()

    def check(self, feature: str, name: str, ok, detail=None) -> bool:
        self.checks.append({"feature": feature, "check": name, "ok": bool(ok),
                            "detail": None if detail is None else json.loads(json.dumps(detail, default=repr))})
        shown = "" if ok or detail is None else "  " + json.dumps(detail, ensure_ascii=False, default=repr)[:400]
        self.log("PASS" if ok else "FAIL", f"{feature}: {name}{shown}")
        return bool(ok)

    def not_built(self, feature: str, reason: str) -> None:
        """A feature this system does not have: said, not counted as a failure."""
        self.absent[feature] = (ABSENT, reason)
        self.log("SKIP", f"{feature}: {reason}")

    def skipped(self, feature: str, reason: str) -> None:
        """A feature this system has, left out of this run: said, not counted as a failure."""
        self.absent[feature] = (SKIPPED, reason)
        self.log("SKIP", f"{feature}: {reason}")

    def shot(self, name: str) -> None:
        """A picture of the whole screen into the out folder."""
        from PIL import ImageGrab

        self.shots += 1
        path = self.out / f"{self.shots:02d}-{name}.png"
        try:
            ImageGrab.grab(all_screens=sys.platform == "win32").save(path)
        except OSError as error:
            # macOS asks for a screen-recording grant some runners do not give.
            self.log("no picture:", name, repr(error)[:200])

    def features(self) -> dict:
        seen: dict[str, dict] = {}
        for c in self.checks:
            row = seen.setdefault(c["feature"], {"state": WORKS, "failed": [], "passed": 0})
            if c["ok"]:
                row["passed"] += 1
            else:
                row["state"] = FAILS
                row["failed"].append(c["check"])
        for feature, (state, reason) in self.absent.items():
            seen.setdefault(feature, {"state": state, "failed": [], "passed": 0, "reason": reason})
        return seen

    def finish(self) -> int:
        features = self.features()
        failed = [c for c in self.checks if not c["ok"]]
        result = {"suite": self.name, "system": SYSTEM, "seconds": round(time.time() - self.began),
                  "features": features, "checks": self.checks}
        (self.out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        self.log(f"== {self.name} on {SYSTEM}: {len(self.checks) - len(failed)} passed, {len(failed)} failed,"
                 f" {len(self.absent)} not built here or not run")
        self.log(f"| {self.name} feature | {SYSTEM} |")
        self.log("|---|---|")
        for feature, row in features.items():
            self.log(f"| {feature} | {state_text(row)} |")
        self._log.close()
        return 1 if failed else 0


def state_text(row: dict) -> str:
    if row["state"] == FAILS:
        return f"fails: {'; '.join(row['failed'])[:160]}"
    if row["state"] in (ABSENT, SKIPPED):
        return row["reason"] if row["state"] == SKIPPED else f"not built here: {row['reason']}"
    return f"works ({row['passed']} checks)"


def scrub_env() -> None:
    """A runner's shell may carry settings that would override the scratch home's own."""
    for name in [k for k in os.environ if k.startswith(_SCRUBBED_PREFIXES)]:
        del os.environ[name]


def stand_in_model(handler=FakeModel, port: int = 0) -> str:
    """An OpenAI-compatible stand-in on this machine; its /v1 address."""
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/v1"


def scratch_home(model_url: str, lang: str = "zh", **settings) -> Path:
    """A new home outside the out folder (its settings are never uploaded), with the model and no network extras."""
    home = Path(tempfile.mkdtemp(prefix="mibo-sandbox-", dir=os.environ.get("RUNNER_TEMP") or None)).resolve()
    # The character sleeps at night by its own clock, and a sleeping pet takes a click as a pat: its clock is set
    # to the early afternoon whatever the hour on the runner.
    values = {"LLM_BASE_URL": model_url, "LLM_MODEL": "fake-model", "LLM_API_KEY": "fake-local-key",
              "AGENT_LANG": lang, "UPDATE_CHECK_ENABLED": "false", "ROAM_ENABLED": "false",
              "PERSONA_TZ_OFFSET_HOURS": str(14 - time.gmtime().tm_hour), **settings}
    from mibo import settingsfile
    settingsfile.write(home, values)
    return home


def run_tool(suite: Suite, feature: str, argv: list[str], name: str, timeout: float = 1200) -> int:
    """One of tools/'s own measuring scripts as it is, its output kept in <out>/<name>.txt; each `PASS  ` or `FAIL  `
    line it prints (tools/window_driver.py's walk log) becomes a check of `feature`. Its exit code."""
    import subprocess

    import re
    suite.log("$", " ".join(argv))
    kept = suite.out / f"{name}.txt"
    try:
        done = subprocess.run([sys.executable, *argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, cwd=REPO,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        output, code = done.stdout, done.returncode
    except subprocess.TimeoutExpired as late:
        output, code = (late.stdout or b"").decode("utf-8", "replace") if isinstance(late.stdout, bytes) else (late.stdout or ""), -1
    kept.write_text(output, encoding="utf-8")
    for line in output.splitlines():
        found = re.match(r"(PASS|FAIL)  (.*?)(?:  — (.*))?$", line)
        if found:
            suite.check(feature, found[2][:200], found[1] == "PASS", found[3])
    suite.check(feature, f"{Path(argv[0]).name} exits 0", code == 0, code if code != -1 else f"stopped after {timeout} s")
    return code


def page_link(home: Path) -> tuple[str, str]:
    """The running app's page origin and its setup token (runtime/desktop/welcome.json)."""
    from urllib.parse import urlsplit

    url = json.loads((home / "runtime" / "desktop" / "welcome.json").read_text(encoding="utf-8"))["url"]
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}", parts.fragment.partition("token=")[2]


def api(home: Path, path: str, body=None, timeout: float = 60):
    """A call to the running app's local API, as its page makes it."""
    import urllib.request

    origin, token = page_link(home)
    request = urllib.request.Request(f"{origin}/api/{path}", method="GET" if body is None else "POST",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Mibo-Setup-Token": token})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as reply:
        return json.loads(reply.read() or b"{}")


class Window:
    """The app's real window, opened by `mibo start` in this process on `home`; `walk(window)` runs on a thread
    of its own once the window is up, and the window closes when it returns.

    On Qt and Cocoa the bridge runs its script with eval, which the page's own policy forbids: as
    tools/window_smoke.py does, the page is first seen working under its policy, then the policy is widened for this
    run so the bridge can read and press what is on the page. `cdp_port` (Windows) opens the WebView's DevTools on
    that port, for playwright's connect_over_cdp."""

    LIMIT_S = 900

    def __init__(self, suite: Suite, home: Path, cdp_port: int = 0) -> None:
        self.suite, self.home, self.cdp_port = suite, home, cdp_port
        self.app = None
        self.served: list[str] = []
        self.sent_to_browser: list[str] = []
        self.widened = False

    def js(self, expression: str, timeout: float = 20, window=None):
        box: dict = {}
        target = window or self.app._window

        def ask() -> None:
            try:
                box["value"] = target.evaluate_js(expression)
            except Exception as error:
                box["value"] = f"<<error: {error!r}>>"

        worker = threading.Thread(target=ask, daemon=True)
        worker.start()
        worker.join(timeout)
        return "<<no answer>>" if worker.is_alive() else box.get("value")

    def until(self, expression: str, seconds: float, window=None):
        deadline, value = time.time() + seconds, None
        while time.time() < deadline:
            value = self.js(expression, window=window)
            if value is True:
                return True
            time.sleep(0.4)
        return value

    def views(self) -> list:
        return self.js("[...document.querySelectorAll('section')].filter(s => !s.hidden && s.getClientRects().length)"
                       ".map(s => s.id)") or []

    def run(self, walk) -> int:
        """`mibo start`'s exit code; checks go to the suite."""
        os.environ["AGENT_HOME"] = str(self.home)
        webbrowser.open = lambda url, *a, **k: self.sent_to_browser.append(str(url)) or True
        import webview

        from mibo import welcome
        from mibo import window as appwindow

        if self.cdp_port:
            webview.settings["REMOTE_DEBUGGING_PORT"] = self.cdp_port
        holder = self

        class Watched(appwindow.AppWindow):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                holder.app = self

        welcome.appwindow.AppWindow = Watched

        class Watch:
            """Notes what the page asked the local API for, and widens the policy once told."""

            def __init__(self, app) -> None:
                self.app = app

            async def __call__(self, scope, receive, send) -> None:
                if scope["type"] != "http":
                    await self.app(scope, receive, send)
                    return

                async def relay(message) -> None:
                    if message["type"] == "http.response.start":
                        if message["status"] == 200 and scope["path"].startswith("/api/"):
                            holder.served.append(scope["path"])
                        if holder.widened:
                            message = {**message, "headers": [
                                (k, v.replace(b"script-src 'self'", b"script-src 'self' 'unsafe-eval'"))
                                if k == b"content-security-policy" else (k, v) for k, v in message["headers"]]}
                    await send(message)

                await self.app(scope, receive, relay)

        serve_in_window = welcome._serve_in_window

        def watched_serve(app, *a, **k):
            app.add_middleware(Watch)
            return serve_in_window(app, *a, **k)

        welcome._serve_in_window = watched_serve

        def opened() -> bool:
            deadline = time.time() + 120
            while time.time() < deadline and not (self.app and self.app.opened):
                time.sleep(0.2)
            if not (self.app and self.app.opened):
                return False
            deadline = time.time() + 60
            while time.time() < deadline and not self.served:
                time.sleep(0.3)
            if "unsafe-eval" in str(self.js("1 + 1")):
                self.widened = True
                # A new query makes this a real load; the same address would only move within the page.
                self.app._window.load_url(self.app.page_url.replace("#", "?policy=widened#", 1))
                self.suite.log("(the bridge needs eval here: the page's policy is widened for this run)")
                time.sleep(2)
            return self.until("document.readyState === 'complete' && typeof window.paSetup === 'object'", 60) is True

        def go() -> None:
            try:
                if self.suite.check("window", "the app window opens and its page runs", opened(),
                                    getattr(self.app, "error", "") or self.sent_to_browser):
                    walk(self)
            except Exception:
                import traceback
                self.suite.check("window", "the walk ran to its end", False, traceback.format_exc()[-1200:])
            finally:
                if self.app:
                    self.app.close()
                else:
                    os._exit(self.suite.finish())

        def watchdog() -> None:
            time.sleep(self.LIMIT_S)
            self.suite.check("window", f"the walk ends within {self.LIMIT_S} s", False)
            os._exit(self.suite.finish())

        threading.Thread(target=watchdog, daemon=True).start()
        threading.Thread(target=go, daemon=True).start()
        return welcome.main([])

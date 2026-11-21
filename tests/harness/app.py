"""The app as the person's page reaches it: a home under tmp whose .env points the model at the ScriptedModel,
served by uvicorn on a free 127.0.0.1 port the way `mibo start --no-browser` serves it, with welcome.json
written so the app's own calls back to its page (schedules, the built-in browser) reach it. Requests carry the
token, and the window key when a route takes only the app's own page."""
from __future__ import annotations

import asyncio
import json
import threading

import httpx
import uvicorn

from mibo import paths, situation, welcome
from mibo.panels import modelbar
from mibo import setup_wizard as sw
from mibo.storage import atomic_write_text

TOKEN = "harness-local-capability"
KEY = "sk-harness-model-key"


class App:
    """`App(home, model).open()`; `get`/`post` call its API; `close()` stops it and everything it started."""

    def __init__(self, home, model, **settings) -> None:
        self.home, self.model = home, model
        home.mkdir(parents=True, exist_ok=True)
        sw.write_env(home / ".env", {"LLM_BASE_URL": model.url, "LLM_API_KEY": KEY, "LLM_MODEL": "scripted",
                                     "PERSONA_NAME": "Mibo", "AGENT_LANG": "en", "ROAM_ENABLED": "false",
                                     "UPDATE_CHECK_ENABLED": "false", **settings})
        paths.use_home(home)
        self.workspace = welcome.Workspace(home)
        self.app = welcome.create_app(home, TOKEN, workspace=self.workspace)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host=welcome.PAGE_HOST, port=0, log_level="warning"))
        self.url = ""
        self._thread: threading.Thread | None = None

    def open(self) -> App:
        sock = welcome._listen(0)
        self.url = f"http://{welcome.PAGE_HOST}:{sock.getsockname()[1]}"
        atomic_write_text(self.home / "runtime" / "desktop" / "welcome.json",
                          json.dumps({"url": f"{self.url}/#token={TOKEN}"}))
        self._thread = threading.Thread(target=self.server.run, kwargs={"sockets": [sock]}, daemon=True)
        self._thread.start()
        while not self.server.started:
            assert self._thread.is_alive(), "the app did not start"
            self._thread.join(0.02)
        return self

    def close(self) -> None:
        self.server.should_exit = True
        self._thread.join(30)
        assert not self._thread.is_alive(), "the app did not stop"

    def request(self, method: str, path: str, *, own_page: bool = False, timeout: float = 120, **sent) -> httpx.Response:
        headers = {welcome.TOKEN_HEADER: TOKEN}
        window = getattr(self.app.state, "window", None)
        if own_page and window is not None:
            headers["X-Mibo-Window"] = window.frame_key
        return httpx.request(method, self.url + path, headers=headers, timeout=timeout, trust_env=False, **sent)

    def get(self, path: str, **sent) -> httpx.Response:
        return self.request("GET", path, **sent)

    def post(self, path: str, payload: dict | None = None, **sent) -> httpx.Response:
        return self.request("POST", path, json=payload or {}, **sent)


async def _no_probe(runtime, lang: str = "") -> None:
    """The first start's look at five public sites: none from a test (no network)."""
    await asyncio.sleep(0)


def quiet_start(monkeypatch) -> None:
    """What every app in a test leaves out: the first start's probe of public sites, and the start's measure of
    the model (a test that wants it scripts it and calls modelbar.check_at_start)."""
    monkeypatch.setattr(situation, "probe", _no_probe)
    monkeypatch.setattr(modelbar, "check_at_start", lambda workspace: None)

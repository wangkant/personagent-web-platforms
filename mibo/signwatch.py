"""The pane's profile as a browser keeps it (Windows): saved passwords and sessions, never read out.

`ready` turns on the browser's own password saving and autofill for the pane,
as Edge has them: its own Save password prompt, its own filling of a form it
knows. The app never reads what is saved. After each page loads it looks, with
web/signin.CHECK_JS, whether the site asks for an account, and `observe` keeps
per site whether it is signed in (web/signin.py), on evidence only: a sign-in
form seen, then left with the site's session cookies changed, or the site's own
account cookies present (names only). A prompt, a check or a box seen once
never signs a site out; the site sending a page of it to its sign-in does. The
cookie values are compared as digests held in this process's memory, never
written or sent. It also keeps the site's icon for Settings.

`Profile` is the same profile seen from Settings: which sites hold which
cookie names, signing out of one (its cookies and its storage, through
WebView2's DevTools calls), and clearing all sessions, all saved passwords or
the history of a time (WebView2's own ClearBrowsingDataAsync). It works on the pane's page when one is
open in the normal profile, else on a hidden blank WebView2 on the same folder.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from . import browsed

# A page is looked at this long after it loads: soon (a person with a filled form signs in fast), then
# again (a sign-in layer comes up late). A site's icon is kept at most this big.
LOOKS_S = (0.3, 1.5)
ICON_MAX = 64_000
ICONS = "icons"
# A sign-in page reached this soon after a page of the same site was asked for: the site sent it there.
SENT_S = 30.0
# {site: {"at", "jar": {cookie name: digest of its value}}} when its sign-in form was last seen, in memory only.
WALLS: dict[str, dict] = {}
# Told of each page looked at, (pane, its address, CHECK_JS's answer): panels/signins.py hears a find that
# opened to a wall.
heard: Callable[[object, str, dict], None] | None = None


def ready(pane, core, private: bool) -> None:
    """A tab's WebView2 is up: saving and filling passwords on in the normal profile (off in a private tab), and
    each page looked at for a sign-in once loaded."""
    settings = core.Settings
    settings.IsPasswordAutosaveEnabled = not private
    settings.IsGeneralAutofillEnabled = not private
    state = {"at": 0.0, "asked": "", "asked_at": 0.0}

    def starting(sender, args) -> None:
        # What was asked for, before any redirect the site answers with.
        if not args.IsRedirected:
            state.update(asked=str(args.Uri), asked_at=time.monotonic())

    def loaded(sender, args) -> None:
        if private:
            return
        state["at"] = time.monotonic()
        threading.Thread(target=_look, args=(pane, core, state["at"], state), name="mibo-signin",
                         daemon=True).start()

    def favicon(sender, args) -> None:
        if not private:
            _keep_icon(pane, core)
    # .NET holds no reference to these.
    pane._sign_handlers = (starting, loaded, favicon)
    core.NavigationStarting += starting
    core.NavigationCompleted += loaded
    core.HistoryChanged += loaded
    core.FaviconChanged += favicon


def check(cdp: Callable[..., dict], url: str = "") -> dict:
    """CHECK_JS on the page `cdp` reaches, with the site's hints: {kind, evidence, password, filled, button, form,
    prompt, account}; {} when it did not answer."""
    from .web import signin
    marks = (signin.hint(signin.key(url)).get("marks") or {}) if url else {}
    with contextlib.suppress(RuntimeError):
        answer = cdp("Runtime.evaluate", {"expression": f"({signin.CHECK_JS})({json.dumps(marks)})",
                                          "returnByValue": True}, timeout=8)
        value = (answer.get("result") or {}).get("value")
        if isinstance(value, dict):
            return value
    return {}


def jar(cdp: Callable[..., dict], url: str) -> tuple[set, dict] | None:
    """The names of the cookies `url` would get, and a digest of each value pages cannot touch (httpOnly, where
    sessions live). Held in memory only: never written, logged or sent. None when they could not be read."""
    with contextlib.suppress(RuntimeError):
        cookies = cdp("Network.getCookies", {"urls": [url]}, timeout=8).get("cookies") or []
        return ({str(c.get("name")) for c in cookies},
                {f"{c.get('name')}@{c.get('domain')}": hashlib.sha1(str(c.get("value")).encode()).hexdigest()[:16]
                 for c in cookies if c.get("httpOnly")})
    return None


def observe(pane, url: str, found: dict, sent: bool = False) -> str:
    """What one look at a page of the site says of its account, kept: "in", "out" or "unsure" after it. `sent`:
    the site sent the page asked for to this one."""
    from .web import signin
    desktop = pane.folder.parent
    site, now = signin.key(url), time.time()
    held = jar(pane.cdp, url)
    present = held[0] if held is not None else None
    entry = signin.load(desktop).get(site)
    if found.get("filled"):
        # The browser filled a password it saved for the site: Settings lists it as kept.
        entry = signin.change(desktop, site, lambda e: e.update(saved=True))
    if found.get("form") or found.get("kind") in ("sign_in", "sign_in_overlay"):
        # A way to sign in shows: the session as it is now, to tell a sign-in by later. Sent here from a page of
        # the site, by the site, the account is gone.
        if sent and found.get("kind") == "sign_in" and signin.status(entry, present, site) == "in":
            signin.gone(desktop, url)
        if signin.in_flow(url):
            signin.note_flow(desktop, url)
        snap = WALLS.get(site)
        if held is not None and (snap is None or now - snap["at"] > signin.SIGNED_WITHIN_S):
            WALLS[site] = {"at": now, "jar": held[1]}
        return signin.status(signin.load(desktop).get(site), present, site)
    if found.get("kind") in signin.WAITED or signin.in_flow(url) or held is None:
        return signin.status(entry, present, site)
    snap = WALLS.get(site)
    if snap and now - snap["at"] < signin.SIGNED_WITHIN_S:
        changed = sorted({name.split("@")[0] for name, digest in held[1].items() if snap["jar"].get(name) != digest})
        if changed and found.get("account") != "out":
            WALLS.pop(site, None)
            known = signin.has_session(present, site)
            if known is False and found.get("account") == "in":
                # The page shows an account the hint's cookies do not: the hint is wrong here, and dropped.
                signin.became(desktop, url, "signin", changed, hint_off=True)
            elif known is not False:
                signin.became(desktop, url, "signin", [] if known else changed)
            return signin.status(signin.load(desktop).get(site), present, site)
    if signin.has_session(present, site, entry) and found.get("account") != "out" and not (entry or {}).get("gone") \
            and (entry or {}).get("state") != "in":
        signin.became(desktop, url, "session")
    elif entry and entry.get("state") == "in" and not entry.get("gone"):
        if not signin.names_of(entry, site) and found.get("account") == "out":
            signin.gone(desktop, url)
        else:
            signin.used(desktop, url)
    return signin.status(signin.load(desktop).get(site), present, site)


def _look(pane, core, at: float, state: dict) -> None:
    from .web import signin
    found: dict = {}
    for n, after in enumerate(LOOKS_S):
        time.sleep(max(0.0, at + after - time.monotonic()))
        # A later load looks again itself; a page closed or gone private is not looked at.
        if state["at"] != at or pane.private or pane._core is None or not pane._core.Equals(core):
            return
        url = pane.address()
        if not browsed.web(url):
            return
        found = check(pane.cdp, url)
        if found.get("kind") in ("sign_in", "sign_in_overlay") or n == len(LOOKS_S) - 1:
            break
    if not found:
        return
    asked = state["asked"]
    sent = (signin.in_flow(url) and bool(asked) and asked != url and not signin.in_flow(asked)
            and signin.key(asked) == signin.key(url) and time.monotonic() - state["asked_at"] < SENT_S)
    observe(pane, url, found, sent)
    if heard is not None:
        heard(pane, url, found)


def _keep_icon(pane, core) -> None:
    """The site's icon as the browser has it, kept for Settings' list (nothing fetched for it)."""
    from Microsoft.Web.WebView2.Core import CoreWebView2FaviconImageFormat
    from System import Action
    from System.IO import MemoryStream, Stream
    from System.Threading.Tasks import Task, TaskScheduler

    from .web import signin
    url = str(core.Source)
    if not browsed.web(url):
        return
    path = pane.folder / ICONS / f"{signin.key(url).replace(':', '_')}.png"

    def got(task) -> None:
        with contextlib.suppress(Exception):
            copy = MemoryStream()
            task.Result.CopyTo(copy)
            data = bytes(copy.ToArray())
            if 0 < len(data) <= ICON_MAX:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
    pane._icon_handler = got
    core.GetFaviconAsync(CoreWebView2FaviconImageFormat.Png).ContinueWith(
        Action[Task[Stream]](got), TaskScheduler.FromCurrentSynchronizationContext())


def icon_path(folder: Path, site: str) -> Path:
    return Path(folder) / ICONS / f"{site.replace(':', '_')}.png"


class _Blank:
    """A hidden WebView2 on the pane's folder, for profile calls while no page is open in it."""

    def __init__(self, folder: Path, form, ui: Callable):
        self.folder, self.form, self.ui = Path(folder), form, ui
        self.view = self.core = None
        self.made = threading.Event()
        ui(self._make)

    def _make(self) -> None:
        from Microsoft.Web.WebView2.WinForms import (
            CoreWebView2CreationProperties,
            WebView2,
        )
        view = WebView2()
        properties = CoreWebView2CreationProperties()
        properties.UserDataFolder = str(self.folder / "webview")
        from .browser import BROWSER_ARGS, language
        properties.Language = language()
        properties.AdditionalBrowserArguments = BROWSER_ARGS
        view.CreationProperties = properties
        view.Visible = False
        self.view = view
        self._handler = self._ready
        view.CoreWebView2InitializationCompleted += self._ready
        self.form.Controls.Add(view)
        view.EnsureCoreWebView2Async(None)

    def _ready(self, sender, args) -> None:
        if args.IsSuccess and self.view is not None:
            self.core = self.view.CoreWebView2
            self.core.Settings.AreDevToolsEnabled = False
        self.made.set()

    def close(self) -> None:
        def drop() -> None:
            view, self.view, self.core = self.view, None, None
            if view is not None:
                self.form.Controls.Remove(view)
                view.Dispose()
        self.ui(drop)


class Profile:
    """The pane's normal profile, from Settings: sessions per site, signing out, clearing."""

    def __init__(self, pane):
        self.pane = pane

    @contextlib.contextmanager
    def _page(self):
        """(cdp, core getter) on a page of the normal profile."""
        from .browser import CDP_S, _call
        pane = self.pane
        if pane._core is not None and not pane.private:
            yield (lambda method, params, timeout=CDP_S: _call(pane.ui, lambda: pane._core, method, params, timeout),
                   lambda: pane._core)
            return
        blank = _Blank(pane.folder, pane.form, pane.ui)
        try:
            if not blank.made.wait(20) or blank.core is None:
                raise RuntimeError("the profile could not be opened")
            yield (lambda method, params, timeout=CDP_S: _call(pane.ui, lambda: blank.core, method, params, timeout),
                   lambda: blank.core)
        finally:
            blank.close()

    def names(self) -> dict[str, set]:
        """{site: the names of its cookies} for every site with any (values dropped at once)."""
        from .web import signin
        with self._page() as (cdp, _):
            cookies = cdp("Storage.getCookies", {}).get("cookies") or []
        held: dict[str, set] = {}
        for cookie in cookies:
            domain = str(cookie.get("domain") or "").lstrip(".")
            if domain:
                held.setdefault(signin.bare(signin.key(f"http://{domain}")), set()).add(str(cookie.get("name")))
        return held

    def sign_out(self, site: str) -> int:
        """The site's cookies and storage go (every host of it seen in the cookies, and its home); how many
        cookies went."""
        bare = browsed.site_of(urlsplit(f"//{site}").hostname or site)
        with self._page() as (cdp, _):
            cookies = cdp("Storage.getCookies", {}).get("cookies") or []
            hosts = {bare, f"www.{bare}"}
            gone = 0
            for cookie in cookies:
                domain = str(cookie.get("domain") or "")
                if browsed.site_of(domain.lstrip(".")) != bare:
                    continue
                cdp("Network.deleteCookies", {"name": cookie.get("name"), "domain": domain,
                                              "path": cookie.get("path") or "/"})
                hosts.add(domain.lstrip("."))
                gone += 1
            port = urlsplit(f"//{site}").port
            for host in hosts:
                for scheme in ("https", "http"):
                    with contextlib.suppress(RuntimeError):
                        cdp("Storage.clearDataForOrigin", {
                            "origin": f"{scheme}://{host}" + (f":{port}" if port else ""),
                            "storageTypes": "local_storage,indexeddb,service_workers,cache_storage,websql,file_systems"})
        return gone

    def clear(self, what: str, since: float = 0.0) -> None:
        """"sessions": every site's cookies and storage; "passwords": every saved password and autofill entry;
        "history": the browser's history, download history and cache, from `since` on (all with 0). Through
        WebView2's own ClearBrowsingDataAsync."""
        from Microsoft.Web.WebView2.Core import CoreWebView2BrowsingDataKinds as Kinds
        from System import Action, DateTime
        from System.Threading.Tasks import Task
        kinds = {"sessions": Kinds.AllSite, "passwords": Kinds.PasswordAutosave | Kinds.GeneralAutofill,
                 "history": Kinds.BrowsingHistory | Kinds.DownloadHistory | Kinds.DiskCache}[what]
        done = threading.Event()
        with self._page() as (_, core_of):
            def start() -> None:
                handler = Action[Task](lambda task: done.set())
                self._cleared = handler
                profile = core_of().Profile
                cleared = (profile.ClearBrowsingDataAsync(kinds, DateTime.Now.AddSeconds(since - time.time()),
                                                          DateTime.Now.AddMinutes(1)) if since
                           else profile.ClearBrowsingDataAsync(kinds))
                cleared.ContinueWith(handler)
            self.pane.ui(start)
            done.wait(30)


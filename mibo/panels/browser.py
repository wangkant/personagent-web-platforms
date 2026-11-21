"""Where links from the app open: the browser inside the app window (browser.py),
or the system's browser. Kept in the app's settings as "links"; the Settings page
reads and changes it here. The app window's own page also places the browser's
pane and presses its bar's buttons here.

The privacy switch (browsed.SHARE_KEY), the person's history and clearing it are
here too, and what the character's tools ask of the browser (a page read with
the person's session, the pages read lately): from this computer alone, and
nothing while the switch is off.

And the character acting in the pane's tabs for the person (surface.py's act,
web/operate.py): the service's loop sends each act call to /api/browser/op,
where `Operating` keeps a Hands for each tab acted in this turn, every step
through the gate; a card for an action with an effect outside waits at
/api/browser/confirm, which only the app window's own page can answer (its
window key), never the service; one card at a time, and a card whose time ran
out stays answerable after the turn ends waiting on it (`left`), its answer
kept for the next turn (`kept`). The kinds the person let through per site are
listed and revoked at /api/browser/allowed, and the person's mode for the pane
(acting.py) is read and set at /api/browser/acting. What the person types in
the chat beside the page is a chat message like any other, marked for the page
by its turn choice (turns.TURN_CHOICES)."""
from __future__ import annotations

import asyncio
import contextlib
import re
import sys
import time
from urllib.parse import quote_plus, urlsplit

from fastapi import Request

ERRORS: frozenset[str] = frozenset({"invalid_links", "browse_invalid", "browse_window", "browse_none", "browse_busy"})
CHOICES = ("app", "system")
# How long a look waits for a page just opened to load.
LOAD_S = 20.0
# Words typed in the pane's bar open a search on Bing, in the chat's language (the one web search answers in);
# its China site for Chinese.
SEARCH = "https://www.bing.com/search?q={q}&setlang={lang}"
SEARCH_ZH = "https://cn.bing.com/search?q={q}"
# What the bar takes as a site: localhost, an IP, or dotted names ending in a name of letters; a port may follow.
_SITE = re.compile(r"(localhost|\[[0-9a-f:.]+\]|(\d{1,3}\.){3}\d{1,3}|([^\W_]([\w-]*[^\W_])?\.)+([^\W\d_]{2,63}|xn--[a-z0-9-]+))"
                   r"(:\d{1,5})?", re.IGNORECASE)
# Settings lists this many sites read lately, besides those with something kept.
SITES_LATELY = 8
# The column beside the page (browse.js): a card's step, how a step came out, and the act a card is about,
# plain (nobody named) and with whom or what it goes to.
SAID = {
    "zh": {"wait": "等待确认：{act}", "yes": "已确认：{act}", "no": "已拒绝：{act}", "none": "确认超时：{act}",
           "failed": "未成功", "unfinished": "已在此步停止",
           "acts": {"send": ("发送消息", "发送给 {to}"), "comment": ("发表评论", "发表评论：{to}"),
                    "like": ("按「{button}」", "按「{button}」"), "post": ("公开发布", "公开发布：{to}"),
                    "order": ("下单", "下单：{to}"), "delete": ("删除", "删除：{to}"),
                    "account": ("更改账号设置", "更改账号设置：{to}"), "grant": ("授权访问", "授权给 {to}"),
                    "submit": ("提交表单", "提交「{to}」"), "upload": ("上传文件", "上传「{to}」"),
                    "download": ("下载文件", "下载「{to}」"), "dialog": ("确认网页的询问", "确认网页的询问"),
                    "leave": ("前往其他网站", "前往 {to}"), "press": ("按「{button}」", "按「{button}」")}},
    "en": {"wait": "Waiting for you: {act}", "yes": "You confirmed: {act}", "no": "You said no: {act}",
           "none": "No answer came: {act}", "failed": "It did not work", "unfinished": "Stopped at this step",
           "acts": {"send": ("Send the message", "Send to {to}"), "comment": ("Post a comment", "Comment on {to}"),
                    "like": ("Press “{button}”", "Press “{button}”"), "post": ("Post publicly", "Post to {to}"),
                    "order": ("Place the order", "Order {to}"), "delete": ("Delete", "Delete {to}"),
                    "account": ("Change account settings", "Change {to}"), "grant": ("Grant access", "Grant {to} access"),
                    "submit": ("Submit the form", "Submit “{to}”"), "upload": ("Give the page a file", "Upload “{to}”"),
                    "download": ("Download a file", "Download “{to}”"), "dialog": ("Answer the page", "Answer the page"),
                    "leave": ("Go to another site", "Go to {to}"), "press": ("Press “{button}”", "Press “{button}”")}},
}


def address_of(text: str, lang: str) -> tuple[str, bool]:
    """What the bar's text opens, as every browser's address bar reads it: the site it names (https:// added when
    it names no scheme), else a search for the words. (address, whether it is a search)"""
    text = text.strip()
    parts = urlsplit(text if "://" in text else f"https://{text}")
    if parts.scheme in ("http", "https") and _SITE.fullmatch(parts.netloc):
        return parts.geturl(), False
    template = SEARCH_ZH if lang == "zh" else SEARCH
    return template.format(q=quote_plus(text), lang=lang), True


def act_of(card: dict, lang: str) -> str:
    """What a card asks about, as a person says it: 发送给 Alice, 提交「预约挂号」."""
    plain, aimed = SAID[lang]["acts"].get(card.get("kind"), SAID[lang]["acts"]["submit"])
    to = " ".join(str(card.get("to") or "").split())[:40]
    button = " ".join(str(card.get("button") or "").split())[:30]
    return (aimed if to else plain).format(to=to, button=button)


class Operating:
    """The character's acts in the pane's tabs (surface.py's act): one Hands a tab, kept for the turn so its refs,
    its request watch and what it typed hold from one act call to the next, and closed when the turn ends; the
    card waiting for the person, if any. A tab worked on out of sight stays out of sight; the column beside the
    page shows a card, so the tab a card is about comes to the middle for it, and the work goes on there in view."""

    def __init__(self, window, desktop, workspace):
        from ..acting import Modes
        from ..web import gate
        self.window = window
        self.workspace = workspace
        self.allowed = gate.Allowed(desktop)
        self.modes = Modes(desktop)
        self.card: dict | None = None
        self.answer: asyncio.Future | None = None
        self.lang = "zh"
        # The card answered last in this turn, for the chat's one line about it: {"id", "yes", "said"}.
        self.answered: dict | None = None
        # The answers to this turn's acts (operate.Hands.kept); the card a turn ended waiting on, still answerable,
        # its answer kept for the next turn; one card at a time.
        self.kept: dict = {}
        self.left: dict | None = None
        self.one = asyncio.Lock()
        # The pages looked at and acted in, one per tab, so refs hold between looks and acts; the page words the
        # model was shown from each (no address to another site carries them, operate._carried); the turn's Hands.
        self.pages: dict = {}
        self.seen: dict[int, list[str]] = {}
        self.places: dict = {}
        self.busy = asyncio.Lock()
        # The column beside the page shows this turn's steps (a tab acted in while in front, or brought there for a
        # card); the person stopped the turn.
        self.showing = False
        self.stopping = False

    def _page(self, ident: int):
        from ..web.pageops import Page
        from ..web.tabs import PaneTab
        page = self.pages.get(ident)
        if page is None:
            page = self.pages[ident] = Page(PaneTab(self.window.pane, ident))
        return page

    async def look_at(self, tab: dict, payload: dict) -> dict:
        """A look at tab `tab` ({"id", "private", ...} as Pane.tabs gives it): once its page has loaded, its print
        or the part `payload` asks for (ref, filter, find, from), and a picture when asked."""
        from .. import browsed
        from ..web.operate import look_at
        pane = self.window.pane
        page = self._page(tab["id"])
        began = time.monotonic()
        while time.monotonic() - began < LOAD_S:
            if browsed.web(await asyncio.to_thread(pane.address, page.tab.tab)):
                with contextlib.suppress(RuntimeError):
                    state = await page.tab.evaluate("() => document.readyState")
                    if state in ("interactive", "complete"):
                        break
            await asyncio.sleep(0.3)
        ref, start, most = payload.get("ref"), payload.get("from") or 0, payload.get("most")
        if not (isinstance(most, int) and (ref is None or isinstance(ref, int)) and isinstance(start, int)):
            raise ValueError("browse_invalid")
        found = await look_at(page, most, ref=ref, interactive=payload.get("filter") == "interactive",
                              find=str(payload.get("find") or "")[:200], start=start,
                              picture=payload.get("picture") is True)
        self.seen.setdefault(tab["id"], []).append(str(found.get("state") or ""))
        return {**found, "snapshot": page.looks}

    def held(self) -> str:
        """"stopped" once the person stopped the turn; with the column showing, how the person holds it there
        (paused when they took the page over or went to another tab)."""
        if self.stopping:
            return "stopped"
        if not self.showing:
            return ""
        op = self.window.pane.op if self.window.pane is not None else None
        return "stopped" if op is None else op.get("held") or ""

    async def place_act(self, tab: dict, steps: list, lang: str, what: str) -> dict:
        """One act call on tab `tab`: its steps (the act tool's verbs, operate.action_of) through the tab's Hands,
        every one through the gate; what Hands.steps gives."""
        from .. import acting
        from ..web.operate import Hands, action_of
        pane = self.window.pane
        if self.modes.mode() == "off":
            return {"status": "acting_off", "lines": ["[off] the person turned acting in the app's browser off"]}
        try:
            actions = [action_of(step) for step in steps]
        except ValueError as unknown:
            return {"status": "invalid", "lines": [f"[failed] no step {str(unknown)[:20]!r}"]}
        words = " ".join(m["text"] for m in await asyncio.to_thread(acting.asked, self.workspace))
        async with self.busy:
            self.lang = "zh" if lang.startswith("zh") else "en"
            self.answered = None
            hands = self.places.get(tab["id"])
            if hands is None:
                loop = asyncio.get_running_loop()

                def tell(line: str) -> None:
                    # The step about to happen, in the column beside the page when it shows (never waited for).
                    if self.showing:
                        loop.run_in_executor(None, pane.op_said, line)
                page = self._page(tab["id"])
                hands = Hands(page.tab, ask=lambda card: self.ask(card, tab["id"]), allowed=self.allowed,
                              attended=self.window.attended, held=self.held, tell=tell, lang=lang,
                              downloads=lambda: (pane.op or {}).pop("downloads", []) if self.showing else [],
                              mode=self.modes.mode, review=self.review, kept=self.kept, page=page)
                hands.read = self.seen.setdefault(tab["id"], [])
                self.places[tab["id"]] = hands
            hands.said = lambda: words
            if tab.get("front") and not self.showing:
                # In view: the column shows the steps, and the person's own moves in the pane pause them.
                await asyncio.to_thread(pane.operate, what or words[:200])
                self.showing = True
            return await hands.steps(actions)

    async def place_done(self, stopped: bool) -> None:
        """The turn ended (or the person stopped it): each tab's Hands closes, a stopped turn putting back what it
        typed and never let out, and the column says how it ended."""
        self.stopping = stopped
        if self.answer is not None and not self.answer.done():
            self.answer.set_result(None)
        async with self.busy:
            for hands in self.places.values():
                await hands.close(restore=stopped)
            self.places = {}
            self.seen = {}
            self.kept = {}
            pane = self.window.pane
            if self.showing and pane is not None:
                # A download the page began after the last step was never put to the person: it is not saved.
                for left in (pane.op or {}).pop("downloads", []):
                    await asyncio.to_thread(left["answer"], False)
                await asyncio.to_thread(pane.op_end, "waiting" if self.left else "stopped" if stopped else "done", "",
                                        SAID[self.lang]["unfinished"])
            self.showing = self.stopping = False

    async def review(self, action: dict):
        """The reviewer on one action: the person's own messages of the turn under way, never the page."""
        from .. import acting, review
        request = await asyncio.to_thread(acting.asked, self.workspace)
        return await review.decide("browser", request, action, ask=acting.reviewer(self.workspace),
                                   attended=self.window.attended(), lang=self.lang)

    async def ask(self, card: dict, tab: int) -> dict | None:
        """The card shows beside the page, its control ringed on the page, until the person presses, the turn
        stops or time is up; one at a time, so a second never takes the place of one the person is reading. A tab
        worked on out of sight comes to the middle for it (the column beside the page shows cards). Time up with
        the card still up: {"waiting": the act}, and the card stays (`left`)."""
        from ..web.operate import CARD_S
        pane = self.window.pane
        async with self.one:
            if not self.showing:
                await asyncio.to_thread(self.window.show)
                await asyncio.to_thread(pane.front, tab)
                await asyncio.to_thread(pane.operate, card.get("what") or act_of(card, self.lang))
                self.showing = True
            # A new card stands for the act now asked: one left from before goes.
            self.left = None
            said, act = SAID[self.lang], act_of(card, self.lang)
            shown = {k: card.get(k) for k in ("id", "kind", "site", "to", "text", "button", "skippable", "what")}
            self.card, self.answer = card, asyncio.get_running_loop().create_future()
            await asyncio.to_thread(pane.op_card, {**shown, "ringed": card.get("node") is not None},
                                    said["wait"].format(act=act))
            page = self._page(tab)
            if card.get("node") is not None:
                await page.hold_ring(card["node"])
            answer = None
            try:
                began = asyncio.get_running_loop().time()
                while asyncio.get_running_loop().time() - began < CARD_S and not self.held():
                    done, _ = await asyncio.wait({self.answer}, timeout=0.5)
                    if done:
                        answer = self.answer.result()
                        return answer
                if not self.held() and card.get("act"):
                    answer = {"waiting": act}
                    self.left = {**card, "said": act}
                return answer
            finally:
                self.card = self.answer = None
                yes = bool(answer and answer.get("yes"))
                if card.get("node") is not None and not yes:
                    with contextlib.suppress(Exception):
                        await page.hold_ring(None)
                if answer is None or not answer.get("waiting"):
                    told = said["yes" if yes else "no" if answer else "none"].format(act=act)
                    self.answered = {"id": card["id"], "yes": yes, "said": told}
                    if self.window.pane is not None:
                        await asyncio.to_thread(self.window.pane.op_answered, "done" if yes else "cancelled", told)

    def answer_card(self, ident: str, yes: bool, never: bool, files: list[str]) -> bool:
        if self.left is not None and self.left["id"] == ident and self.card is None:
            # The card a turn ended waiting on: the answer is kept for the act, and the next turn goes on with it.
            return self.keep(yes)
        if self.card is None or self.card["id"] != ident or self.answer is None or self.answer.done():
            return False
        self.answer.set_result({"yes": yes, "never": never, **({"files": files} if files else {})})
        return True

    def keep(self, yes: bool, said: bool = False) -> bool:
        """The person's answer to the card a turn ended waiting on, from the card or (`said`) their own words in the
        chat beside the page, kept for its act. Paying, deleting, the account and access are answered on the card
        alone: words leave such a card waiting."""
        from ..acting import HARD
        left = self.left
        if left is None or said and left.get("kind") in HARD:
            return False
        self.left = None
        self.kept[left["act"]] = {"yes": yes}
        if self.window.pane is not None:
            self.window.pane.op_kept(yes)
        return True


def mount(app, workspace, body) -> None:
    from .. import browsed
    from ..browser import ACTIONS
    from ..welcome import app_settings, save_app_settings
    from .runtime import WINDOW_HEADER

    desktop = workspace.home / "runtime" / "desktop"

    def view() -> dict:
        chosen = app_settings(desktop).get("links")
        # The app's own browser is there only in the Windows app window; elsewhere links go to the system's.
        return {"links": chosen if chosen in CHOICES else "app",
                "builtin": sys.platform == "win32" and getattr(app.state, "window", None) is not None,
                "shared": browsed.shared(desktop)}

    def choose(changes: dict) -> dict:
        desktop.mkdir(parents=True, exist_ok=True)
        save_app_settings(desktop, **changes)
        return view()

    @app.get("/api/browser")
    async def browser_view():
        return await asyncio.to_thread(view)

    @app.post("/api/browser")
    async def browser_choose(request: Request):
        payload = await body(request)
        changes = {}
        if "links" in payload:
            if payload["links"] not in CHOICES:
                raise ValueError("invalid_links")
            changes["links"] = payload["links"]
        if "shared" in payload:
            if not isinstance(payload["shared"], bool):
                raise ValueError("browse_invalid")
            changes[browsed.SHARE_KEY] = payload["shared"]
        if not changes:
            raise ValueError("invalid_links")
        return await asyncio.to_thread(choose, changes)

    def window_or_none():
        window = getattr(app.state, "window", None)
        if window is None:
            raise ValueError("browse_none")
        return window

    @app.post("/api/browser/read")
    async def browser_read(request: Request):
        payload = await body(request)
        url, start = payload.get("url"), payload.get("from", 0)
        if (not isinstance(url, str) or not browsed.web(url) or len(url) > 2000
                or not isinstance(start, int) or isinstance(start, bool) or not 0 <= start < 10_000_000):
            raise ValueError("browse_invalid")
        return await asyncio.to_thread(window_or_none().browse_read, url, start)

    @app.get("/api/browser/history")
    async def browser_history(q: str = "", before: float | None = None):
        """The person's history in the app's browser, for Settings: a page of entries, newest first."""
        return await asyncio.to_thread(browsed.history, desktop, q[:200], before)

    @app.post("/api/browser/history")
    async def browser_history_delete(request: Request):
        """One entry of the history deleted: a page's visits of one day."""
        payload = await body(request)
        url, day = payload.get("url"), payload.get("day")
        if not (isinstance(url, str) and browsed.web(url) and isinstance(day, str) and re.fullmatch(r"\d{4}-\d\d-\d\d", day)):
            raise ValueError("browse_invalid")
        await asyncio.to_thread(browsed.delete, desktop, url, day)
        return {"ok": True}

    @app.post("/api/browser/clear")
    async def browser_clear(request: Request):
        """The history of the last hour, day or all of it; sign-ins and saved passwords stay (panels/signins.py)."""
        back = browsed.RANGES.get((await body(request)).get("range"))
        if back is None:
            raise ValueError("browse_invalid")
        since = time.time() - back if back else 0.0
        window = getattr(app.state, "window", None)
        if window is None:
            await asyncio.to_thread(browsed.clear, desktop, since)
        else:
            await asyncio.to_thread(window.browse_clear, since)
        return {"ok": True}

    @app.get("/api/browser/permissions")
    async def browser_permissions():
        """The site permissions decided in the app's browser (camera, microphone, location ...), by site."""
        return {"sites": await asyncio.to_thread(window_or_none().browse_permits)}

    @app.post("/api/browser/permissions")
    async def browser_permission_off(request: Request):
        """One taken back ({origin, kind}): the site is asked again next time."""
        from ..browser import ASKED, origin_of
        payload = await body(request)
        origin, kind = payload.get("origin"), payload.get("kind")
        if not (isinstance(origin, str) and browsed.web(origin) and origin_of(origin) == origin
                and kind in ASKED.values()):
            raise ValueError("browse_invalid")
        window = window_or_none()
        await asyncio.to_thread(window.browse_unpermit, origin, kind)
        return {"sites": await asyncio.to_thread(window.browse_permits)}

    def own_window(request: Request):
        # The app window's page alone: a browser tab on the same address has no pane to place.
        window = getattr(app.state, "window", None)
        if window is None or not window.is_own_page(request.headers.get(WINDOW_HEADER, "")):
            raise ValueError("browse_window")
        return window

    @app.post("/api/browser/place")
    async def browser_place(request: Request):
        window = own_window(request)
        payload = await body(request)
        box, scale, cuts = payload.get("box"), payload.get("scale"), payload.get("cuts", [])
        rects = [box, *cuts] if isinstance(cuts, list) and len(cuts) <= 4 else [None]
        if not (all(isinstance(r, list) and len(r) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                                and 0 <= v < 100000 for v in r) for r in rects)
                and isinstance(scale, (int, float)) and not isinstance(scale, bool) and 0.25 <= scale <= 8):
            raise ValueError("browse_invalid")
        return {"ok": window.browse_place([float(v) for v in box], float(scale), [[float(v) for v in r] for r in cuts])}

    @app.post("/api/browser/room")
    async def browser_room(request: Request):
        """The pane came to the front and its page says how wide a window the site and the chat beside it want,
        in its CSS px; 0 when the pane left the front. From the app window's own page alone."""
        window = own_window(request)
        payload = await body(request)
        wide, scale = payload.get("wide"), payload.get("scale")
        if not (isinstance(wide, (int, float)) and not isinstance(wide, bool) and 0 <= wide < 100000
                and isinstance(scale, (int, float)) and not isinstance(scale, bool) and 0.25 <= scale <= 8):
            raise ValueError("browse_invalid")
        return {"ok": window.browse_room(float(wide), float(scale))}

    def operating() -> Operating:
        window = window_or_none()
        if getattr(app.state, "operating", None) is None:
            app.state.operating = Operating(window, desktop, workspace)
        return app.state.operating

    def lang() -> str:
        from .. import langs
        return langs.normalize(workspace.config().get("AGENT_LANG")) or "en"

    def typed(text) -> str:
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("browse_invalid")
        return text

    @app.get("/api/browser/suggest")
    async def browser_suggest(q: str = ""):
        """What the pane's bar offers as the person types: what Enter opens, and the pages they read that match."""
        url, search = address_of(typed(q), await asyncio.to_thread(lang))
        return {"go": {"url": url, "search": search}, "pages": await asyncio.to_thread(browsed.suggest, desktop, q)}

    @app.post("/api/browser/go")
    async def browser_go(request: Request):
        """What the person typed in the pane's bar, or a suggestion they chose: from the app window's own page alone."""
        window = own_window(request)
        url, _ = address_of(typed((await body(request)).get("text")), await asyncio.to_thread(lang))
        if window.pane is None:
            raise ValueError("browse_invalid")
        await asyncio.to_thread(window.show)
        await asyncio.to_thread(window.pane.go, url)
        return {"ok": True}

    @app.post("/api/browser/op")
    async def browser_op(request: Request):
        """One step of the character's run in the pane, from the service's loop."""
        payload = await body(request)
        op, run = payload.get("op"), operating()
        if op == "keep":
            # The person's own words in the chat answered the card a turn ended waiting on (loop._beside_page).
            yes, left = payload.get("yes"), run.left
            if not isinstance(yes, bool):
                raise ValueError("browse_invalid")
            return {"status": "ok", "said": left["said"]} if left and run.keep(yes, said=True) else {"status": "none"}
        if op == "left":
            # The act the card a turn ended waiting on is about, if one waits.
            return {"status": "ok", "said": run.left["said"] if run.left else ""}
        if op == "place_done":
            await run.place_done(payload.get("stopped") is True)
            return {"status": "ok"}
        if op == "show":
            # A page the character opens for the person to see (a shell open of a web address, shell/panel.py): in the pane, in
            # a new tab when the one in front has a page, with the window brought out.
            url = payload.get("url")
            window = window_or_none()
            if not isinstance(url, str) or not browsed.web(url) or len(url) > 2000:
                raise ValueError("browse_invalid")
            if window.pane is None:
                return {"status": "no_app"}
            await asyncio.to_thread(window.show)
            await asyncio.to_thread(window.pane.open, url)
            return {"status": "ok"}
        if op == "where":
            # The address in the pane's bar, which a message may name: "" for none, in private or with
            # sharing off.
            pane = window_or_none().pane
            url = await asyncio.to_thread(pane.bar_address) if pane is not None else ""
            shown = browsed.web(url) and not pane.private and browsed.shared(desktop)
            return {"status": "ok", "url": url if shown else ""}
        if op in ("tabs", "place", "front", "place_look", "place_act"):
            # The character's places in the pane (surface.py): its tabs, a page opened in front or out of sight,
            # a tab brought to the front, a look at one, an act in one.
            window = window_or_none()
            pane = window.pane
            if pane is None:
                return {"status": "no_app"}
            if op == "place":
                url = payload.get("url")
                if not isinstance(url, str) or not browsed.web(url) or len(url) > 2000:
                    raise ValueError("browse_invalid")
                if payload.get("show") is True:
                    await asyncio.to_thread(window.show)
                return {"status": "ok", **await asyncio.to_thread(pane.place_page, url, payload.get("show") is True)}
            tabs = await asyncio.to_thread(pane.tabs)
            if op == "tabs":
                return {"status": "ok", "tabs": tabs}
            tab = next((t for t in tabs if t["id"] == payload.get("tab")), None)
            if tab is None:
                return {"status": "gone"}
            if op == "front":
                await asyncio.to_thread(window.show)
                await asyncio.to_thread(pane.front, tab["id"])
                return {"status": "ok", "tabs": await asyncio.to_thread(pane.tabs)}
            if tab["private"]:
                return {"status": "private"}
            if op == "place_look":
                return await run.look_at(tab, payload)
            from ..web.operate import STEPS_MAX
            steps = payload.get("steps")
            if not (isinstance(steps, list) and 0 < len(steps) <= STEPS_MAX and all(isinstance(x, dict) for x in steps)):
                raise ValueError("browse_invalid")
            return {**await run.place_act(tab, steps, str(payload.get("lang") or "zh")[:8],
                                          str(payload.get("what") or "")[:200]), "tab": tab}
        raise ValueError("browse_invalid")

    @app.get("/api/browser/confirm")
    async def browser_card():
        run = getattr(app.state, "operating", None)
        return {"card": run.card if run is not None else None, "answered": run.answered if run is not None else None}

    @app.post("/api/browser/confirm")
    async def browser_confirm(request: Request):
        """The person's press on a card: from the app window's own page alone, never from the service. `pick` on
        an upload card opens the system's Open dialog: the files they choose there are the answer."""
        window = own_window(request)
        payload = await body(request)
        run = getattr(app.state, "operating", None)
        ident, yes, never = payload.get("id"), payload.get("yes"), payload.get("never", False)
        pick = payload.get("pick", False)
        if (not isinstance(ident, str) or not isinstance(yes, bool) or not isinstance(never, bool)
                or not isinstance(pick, bool)):
            raise ValueError("browse_invalid")
        files: list[str] = []
        if pick and run is not None and run.card is not None and run.card["id"] == ident and run.card["kind"] == "upload":
            files = await asyncio.to_thread(window.pick_files, bool(run.card.get("many")))
            yes = bool(files)
        return {"ok": run is not None and run.answer_card(ident, yes, never, files)}

    @app.get("/api/browser/acting")
    async def browser_acting():
        from ..acting import Modes
        return {"modes": {"browser": await asyncio.to_thread(Modes(desktop).mode)}}

    @app.post("/api/browser/acting")
    async def browser_acting_set(request: Request):
        """The person's one acting mode (acting.py), from the app window's own page alone: it holds from the run's
        next step in every place the character acts."""
        from ..acting import MODES, Modes
        own_window(request)
        payload = await body(request)
        if payload.get("mode") not in MODES:
            raise ValueError("browse_invalid")
        return {"modes": {"browser": await asyncio.to_thread(Modes(desktop).set, payload["mode"])}}

    @app.get("/api/browser/allowed")
    async def browser_allowed():
        from ..web import gate
        return {"sites": await asyncio.to_thread(gate.Allowed(desktop).all)}

    @app.post("/api/browser/allowed")
    async def browser_allow_off(request: Request):
        """Settings takes a kind back for a site; letting one through is only ever done from a card."""
        from ..web import gate
        payload = await body(request)
        site, kind = payload.get("site"), payload.get("kind")
        if not isinstance(site, str) or kind not in gate.SKIPPABLE or payload.get("on") is not False:
            raise ValueError("browse_invalid")
        allowed = getattr(app.state, "operating", None)
        allowed = allowed.allowed if allowed is not None else gate.Allowed(desktop)
        return {"sites": await asyncio.to_thread(allowed.set, site, kind, False)}

    def site_rows() -> list[dict]:
        """Per site, what Settings shows: the kinds let through, its feed read in the background; the sites read
        lately come first, so a feed can be turned on for one of them."""
        from ..web import gate, sites
        runtime = desktop.parent
        cards = sites.load(runtime)
        allowed: dict[str, list] = {}
        for site, kinds in gate.Allowed(desktop).all().items():
            allowed.setdefault(sites.key_of(f"http://{site}"), []).extend({"site": site, "kind": k} for k in kinds)
        keys = list(dict.fromkeys(
            [sites.key_of(row["url"]) for row in browsed.recent(desktop, 200)][:SITES_LATELY]
            + [key for key, card in cards.items() if isinstance(card, dict)
               and card.get("feed")]
            + list(allowed)))
        rows = []
        for key in keys:
            card = cards.get(key) if isinstance(cards.get(key), dict) else {}
            feed = card.get("feed") or {}
            rows.append({"site": key, "allowed": allowed.get(key, []),
                         "feed": {"on": bool(feed.get("on")), "stopped": feed.get("stopped", "")},
                         "learned": bool(card.get("search"))})
        return rows

    def change_site(key: str, payload: dict) -> list[dict]:
        from ..web import gate, sites
        runtime = desktop.parent
        if "feed" in payload:
            on = payload["feed"]
            # Where the feed is read: the address of that site read last, else its home page.
            seen = next((row["url"] for row in browsed.recent(desktop, 200) if sites.key_of(row["url"]) == key), "")
            parts = urlsplit(seen)
            home = f"{parts.scheme}://{parts.netloc}/" if seen else f"https://{key}/"

            def switch(card: dict) -> None:
                feed = card.setdefault("feed", {})
                feed.update(on=on, url=feed.get("url") or home)
                feed.pop("stopped", None)
            sites._change(runtime, key, switch)
        elif payload.get("forget") == "all":
            sites.forget(runtime, f"https://{key}/")
            allowed = gate.Allowed(desktop)
            for site, kinds in allowed.all().items():
                if sites.key_of(f"http://{site}") == key:
                    for kind in kinds:
                        allowed.set(site, kind, False)
        return site_rows()

    @app.get("/api/browser/sites")
    async def browser_sites():
        return {"sites": await asyncio.to_thread(site_rows)}

    @app.post("/api/browser/sites")
    async def browser_site_change(request: Request):
        """Settings: a site's feed read in the background on or off, or everything learned of it forgotten. Turning a feed on is the person's own switch, with the site's terms said beside it."""
        payload = await body(request)
        key = payload.get("site")
        if (not isinstance(key, str) or not key or len(key) > 300
                or not ("feed" in payload and isinstance(payload["feed"], bool)
                        or payload.get("forget") == "all")):
            raise ValueError("browse_invalid")
        return {"sites": await asyncio.to_thread(change_site, key, payload)}

    @app.post("/api/browser/do")
    async def browser_do(request: Request):
        window = own_window(request)
        payload = await body(request)
        action, tab = payload.get("action"), payload.get("tab", 0)
        if action not in ACTIONS or not isinstance(tab, int) or isinstance(tab, bool):
            raise ValueError("browse_invalid")
        return {"ok": window.browse_do(action, tab)}

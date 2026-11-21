"""Where links from the app open: the browser inside the app window (browser.py),
or the system's browser. Kept in the app's settings as "links"; the Settings page
reads and changes it here. The app window's own page also places the browser's
pane and presses its bar's buttons here.

The privacy switch (browsed.SHARE_KEY), the person's history and clearing it are
here too, and what the character's tools ask of the browser (a page read with
the person's session, the pages read lately): from this computer alone, and
nothing while the switch is off.

And the character operating the pane for the person (web/operate.py): the
service's loop sends its steps to /api/browser/op, where `Operating` keeps the
run's Hands, every action through the gate; a card for an action with an effect
outside waits at /api/browser/confirm, which only the app window's own page
can answer (its window key), never the service. The kinds the person let
through per site are listed and revoked at /api/browser/allowed, and the
person's mode for the pane (acting.py) is read and set at /api/browser/acting;
a run begins with its plan, on a card only when the start address is the
model's own pick of a site the person did not name. What the
person types in the chat beside the page is a chat message like any other,
marked for the page by its turn choice (turns.TURN_CHOICES), which the
character does there itself."""
from __future__ import annotations

import asyncio
import re
import secrets
import sys
import time
from urllib.parse import quote_plus, urlsplit

from fastapi import Request

ERRORS: frozenset[str] = frozenset({"invalid_links", "browse_invalid", "browse_window", "browse_none", "browse_busy"})
CHOICES = ("app", "system")
TASK_MAX = 500
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
           "failed": "未成功", "unfinished": "已在此步停止", "plan": "计划：在 {sites} 上完成“{task}”",
           "acts": {"send": ("发送消息", "发送给 {to}"), "comment": ("发表评论", "发表评论：{to}"),
                    "like": ("按「{button}」", "按「{button}」"), "post": ("公开发布", "公开发布：{to}"),
                    "order": ("下单", "下单：{to}"), "delete": ("删除", "删除：{to}"),
                    "account": ("更改账号设置", "更改账号设置：{to}"), "grant": ("授权访问", "授权给 {to}"),
                    "submit": ("提交表单", "提交「{to}」"), "upload": ("上传文件", "上传「{to}」"),
                    "download": ("下载文件", "下载「{to}」"), "dialog": ("确认网页的询问", "确认网页的询问"),
                    "leave": ("前往其他网站", "前往 {to}"), "press": ("按「{button}」", "按「{button}」"),
                    "plan": ("开始操作", "在 {to} 上开始操作")}},
    "en": {"wait": "Waiting for you: {act}", "yes": "You confirmed: {act}", "no": "You said no: {act}",
           "none": "No answer came: {act}", "failed": "It did not work", "unfinished": "Stopped at this step",
           "plan": "Plan: “{task}” on {sites}",
           "acts": {"send": ("Send the message", "Send to {to}"), "comment": ("Post a comment", "Comment on {to}"),
                    "like": ("Press “{button}”", "Press “{button}”"), "post": ("Post publicly", "Post to {to}"),
                    "order": ("Place the order", "Order {to}"), "delete": ("Delete", "Delete {to}"),
                    "account": ("Change account settings", "Change {to}"), "grant": ("Grant access", "Grant {to} access"),
                    "submit": ("Submit the form", "Submit “{to}”"), "upload": ("Give the page a file", "Upload “{to}”"),
                    "download": ("Download a file", "Download “{to}”"), "dialog": ("Answer the page", "Answer the page"),
                    "leave": ("Go to another site", "Go to {to}"), "press": ("Press “{button}”", "Press “{button}”"),
                    "plan": ("Start", "Start on {to}")}},
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
    """One run at a time in the pane: its Hands, and the card waiting for the person, if any."""

    def __init__(self, window, desktop, workspace):
        from ..acting import Modes
        from ..web import gate
        self.window = window
        self.workspace = workspace
        self.allowed = gate.Allowed(desktop)
        self.modes = Modes(desktop)
        self.hands = None
        self.card: dict | None = None
        self.answer: asyncio.Future | None = None
        self.lang = "zh"
        # The card answered last in this run, for the chat's one line about it: {"id", "yes", "said"}.
        self.answered: dict | None = None

    def held(self) -> str:
        pane = self.window.pane
        op = pane.op if pane is not None else None
        return "stopped" if op is None else op.get("held") or ""

    async def begin(self, task: str, start: str, lang: str = "zh", go_on: bool = False) -> dict:
        from .. import acting, browsed
        from ..web.operate import Hands, plan
        from ..web.tabs import PaneTab
        pane = self.window.pane
        if pane is None:
            return {"status": "no_app", "line": "[failed] the app's browser is not there"}
        mode = self.modes.mode()
        if mode == "off":
            return {"status": "acting_off", "line": "[off] the person turned acting in the app's browser off"}
        if self.hands is not None:
            await self.end("stopped", "")
        if not self.window.attended():
            await asyncio.to_thread(self.window.show)
        self.lang = "zh" if lang.startswith("zh") else "en"
        self.answered = None
        here = await asyncio.to_thread(pane.address)
        await asyncio.to_thread(pane.operate, task, go_on)
        if not go_on:
            # The plan before the first step: the site the run works on, on a card only when the start address is
            # the model's own pick of a site the person did not name.
            words = " ".join(m["text"] for m in await asyncio.to_thread(acting.asked, self.workspace))
            laid = plan(start, here if browsed.web(here) else "", words)
            sites = "、".join(laid["sites"])
            if laid["card"]:
                answer = await self.ask({"id": secrets.token_urlsafe(8), "kind": "plan", "site": sites, "to": sites,
                                         "text": task, "button": "", "skippable": False})
                if not (answer and answer.get("yes")):
                    await asyncio.to_thread(pane.op_end, "declined", "", SAID[self.lang]["unfinished"])
                    return {"status": "declined", "kind": "plan",
                            "line": f"[declined] the plan to work on {sites or 'this page'}: the person "
                                    f"{'said no' if answer else 'did not answer'}, so nothing was done"}
            elif sites:
                await asyncio.to_thread(pane.op_said, SAID[self.lang]["plan"].format(sites=sites, task=task[:60]))
                await asyncio.to_thread(pane.op_result, "done")
        if not await asyncio.to_thread(pane.ready, start):
            await asyncio.to_thread(pane.op_end, "failed", "", SAID[self.lang]["unfinished"])
            return {"status": "no_page", "line": "[failed] no page is open in the browser; give a start address"}
        loop = asyncio.get_running_loop()

        def tell(line: str) -> None:
            # The step about to happen, in the column beside the page (never waited for here).
            loop.run_in_executor(None, pane.op_said, line)
        self.hands = Hands(PaneTab(pane), ask=self.ask, allowed=self.allowed, attended=self.window.attended,
                           held=self.held, tell=tell, lang=lang,
                           downloads=lambda: (pane.op or {}).pop("downloads", []),
                           mode=self.modes.mode, review=self.review)
        return {"status": "ok", "url": await asyncio.to_thread(pane.address)}

    async def review(self, action: dict):
        """The reviewer on one action: the person's own messages of the turn under way, never the page."""
        from .. import acting, review
        request = await asyncio.to_thread(acting.asked, self.workspace)
        return await review.decide("browser", request, action, ask=acting.reviewer(self.workspace),
                                   attended=self.window.attended(), lang=self.lang)

    async def ask(self, card: dict) -> dict | None:
        """The card shows beside the page, its control ringed on the page, until the person presses, the run
        stops or time is up. The page stays in view."""
        from ..web.operate import CARD_S
        said, act = SAID[self.lang], act_of(card, self.lang)
        shown = {k: card.get(k) for k in ("id", "kind", "site", "to", "text", "button", "skippable")}
        self.card, self.answer = card, asyncio.get_running_loop().create_future()
        await asyncio.to_thread(self.window.pane.op_card, {**shown, "ringed": card.get("node") is not None},
                                said["wait"].format(act=act))
        if card.get("node") is not None:
            await self.hands.page.hold_ring(card["node"])
        answer = None
        try:
            began = asyncio.get_running_loop().time()
            while asyncio.get_running_loop().time() - began < CARD_S and not self.held():
                done, _ = await asyncio.wait({self.answer}, timeout=0.5)
                if done:
                    answer = self.answer.result()
                    return answer
            return None
        finally:
            self.card = self.answer = None
            yes = bool(answer and answer.get("yes"))
            if card.get("node") is not None and not yes and self.hands is not None:
                await self.hands.page.hold_ring(None)
            told = said["yes" if yes else "no" if answer else "none"].format(act=act)
            self.answered = {"id": card["id"], "yes": yes, "said": told}
            if self.window.pane is not None:
                await asyncio.to_thread(self.window.pane.op_answered, "done" if yes else "cancelled", told)

    def answer_card(self, ident: str, yes: bool, never: bool, files: list[str]) -> bool:
        if self.card is None or self.card["id"] != ident or self.answer is None or self.answer.done():
            return False
        self.answer.set_result({"yes": yes, "never": never, **({"files": files} if files else {})})
        return True

    async def end(self, status: str, note: str) -> None:
        if self.answer is not None and not self.answer.done():
            self.answer.set_result(None)
        if self.hands is not None:
            # A run the person said no to, stopped or that did not finish puts back what it typed and never let out
            # (one at its ceiling waits for them to say 继续, as a handover does).
            await self.hands.close(restore=status in ("declined", "stopped", "failed"))
        self.hands = None
        if self.window.pane is not None:
            # A download the page began after the last step was never put to the person: it is not saved.
            for left in (self.window.pane.op or {}).pop("downloads", []):
                await asyncio.to_thread(left["answer"], False)
            await asyncio.to_thread(self.window.pane.op_end, status, note, SAID[self.lang]["unfinished"])


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
        if op == "begin":
            task, start = payload.get("task"), payload.get("start") or ""
            if (not isinstance(task, str) or not task.strip() or len(task) > TASK_MAX or not isinstance(start, str)
                    or (start and (not browsed.web(start) or len(start) > 2000))):
                raise ValueError("browse_invalid")
            return await run.begin(task, start, str(payload.get("lang") or "zh")[:8], payload.get("go_on") is True)
        if op == "steps":
            # The steps as the column showed them, for the steps under the chat's reply.
            pane = window_or_none().pane
            return {"status": "ok", "steps": pane.op_steps() if pane is not None else []}
        if op == "show":
            # A page the character opens for the person to see (open_file with a web address): in the pane, in
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
        if run.hands is None:
            return {"status": "stopped", "line": "[stopped] no run in the pane"}
        if op == "look":
            most = payload.get("most")
            if not isinstance(most, int) or isinstance(most, bool):
                raise ValueError("browse_invalid")
            return await run.hands.look(most)
        if op == "picture":
            return await run.hands.picture()
        if op == "act":
            action = payload.get("action")
            if not isinstance(action, dict):
                raise ValueError("browse_invalid")
            result = await run.hands.act(action)
            status = result.get("status")
            if status in ("ok", "failed", "declined"):
                # The step under way in the column: done, or failed and why (a declined card says so itself).
                await asyncio.to_thread(window_or_none().pane.op_result, "failed" if status == "failed" else "done",
                                        (result.get("why") or SAID[run.lang]["failed"]) if status == "failed" else "")
            return result
        if op == "step":
            await asyncio.to_thread(window_or_none().pane.op_step, int(payload.get("n") or 0),
                                    str(payload.get("note") or ""))
            return {"status": "ok"}
        if op == "end":
            await run.end(str(payload.get("status") or "")[:20], str(payload.get("note") or ""))
            return {"status": "ok"}
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
        """Per site, what Settings shows: the kinds let through, its feed read in the background, the ways kept
        from confirmed tasks; the sites read lately come first, so a feed can be turned on for one of them."""
        from ..web import gate, sites
        runtime = desktop.parent
        cards = sites.load(runtime)
        allowed: dict[str, list] = {}
        for site, kinds in gate.Allowed(desktop).all().items():
            allowed.setdefault(sites.key_of(f"http://{site}"), []).extend({"site": site, "kind": k} for k in kinds)
        keys = list(dict.fromkeys(
            [sites.key_of(row["url"]) for row in browsed.recent(desktop, 200)][:SITES_LATELY]
            + [key for key, card in cards.items() if isinstance(card, dict)
               and (card.get("feed") or card.get("procedures") or (card.get("search") or {}).get("how") == "operate")]
            + list(allowed)))
        rows = []
        for key in keys:
            card = cards.get(key) if isinstance(cards.get(key), dict) else {}
            feed = card.get("feed") or {}
            rows.append({"site": key, "allowed": allowed.get(key, []),
                         "feed": {"on": bool(feed.get("on")), "stopped": feed.get("stopped", "")},
                         "procedures": len(card.get("procedures") or []),
                         "learned": bool(card.get("procedures") or card.get("search"))})
        return rows

    def change_site(key: str, payload: dict) -> list[dict]:
        from ..web import gate, operate, sites
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
        elif payload.get("forget") == "procedures":
            operate.forget_procedures(runtime, f"https://{key}/")
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
        """Settings: a site's feed read in the background on or off, its kept ways or everything learned of it
        forgotten. Turning a feed on is the person's own switch, with the site's terms said beside it."""
        payload = await body(request)
        key = payload.get("site")
        if (not isinstance(key, str) or not key or len(key) > 300
                or not ("feed" in payload and isinstance(payload["feed"], bool)
                        or payload.get("forget") in ("procedures", "all"))):
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

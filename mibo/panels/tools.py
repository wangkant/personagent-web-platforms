"""Settings › Connectors, the connector market, the asking cards for MCP servers, and what the character service asks.

The market (mcp_market.py) installs a listed entry, updates pinned versions, and brings over the servers other
apps keep, read only on the person's press; what it adds still waits for the same start and tool cards.
The servers run here, in the page's process (mcp_hub.py): started with the
page, stopped with it. Settings changes them only on the computer the app runs
on; starting a local program and allowing a tool take a press on the app's own
page. The character service asks for its turn's tools and calls through
/api/mcp/offer, /find and /call, and waits on /call while a card is up.

A server's values go to the system's credential store, never to a file. Signing
in to a remote server is also a press on the app's own page; the consent page
sends the browser back to /signin/mcp here, outside /api/ since a navigation
carries no token, and only from this computer (signin.admitted).
"""
from __future__ import annotations

import asyncio
import html

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import mcp_market
from .. import mcp_tools as mt
from .. import runtimes, secretstore
from ..mcp_hub import Hub

# Where a consent page sends the browser back.
CALLBACK_PATH = "/signin/mcp"
# The page the browser shows then, (en, zh).
RETURN_TEXT = {
    "title_ok": ("Signed in", "已登录"),
    "ok": ("Mibo is connected to this server, and the sign-in is kept in this computer's credential store. "
           "You can close this tab and return to Mibo.",
           "Mibo 已连接此服务器，登录信息保存在本机的系统凭据存储中。可关闭此标签页，返回 Mibo。"),
    "title_failed": ("Sign-in was not completed", "登录未完成"),
    "denied": ("Access was not approved on the server's page.", "未在服务方页面上批准访问。"),
    "link_invalid": ("This sign-in link is not valid or has already been used. Sign in again in Mibo.",
                     "此登录链接无效或已使用。请在 Mibo 中重新登录。"),
    "failed": ("The server did not complete the sign-in. Mibo shows the reason and lets you try again.",
               "服务方未完成授权。Mibo 中会显示原因，并可重新登录。"),
}

# Error codes this panel raises as ValueError, which the page may show.
ERRORS: frozenset[str] = frozenset({
    "tools_bad_snippet", "tools_bad_name", "tools_bad_url", "tools_name_taken", "tools_too_many",
    "tools_unknown_server", "tools_unknown_tool", "tools_write_failed", "tools_file_damaged", "tools_required",
    "market_gone", "market_search_failed", "market_sync_failed",
})
MAX_TEXT = 200_000


def _error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


def return_page(lang: str, error: str) -> str:
    """What the browser shows once the consent page sent it back."""
    zh = lang == "zh"

    def say(key: str) -> str:
        return html.escape(RETURN_TEXT[key][1 if zh else 0])

    title = say("title_failed" if error else "title_ok")
    text = say((error if error in RETURN_TEXT else "failed") if error else "ok")
    mark = "signin-mark signin-mark-failed" if error else "signin-mark"
    return ("<!doctype html>\n"
            f'<html lang="{"zh-CN" if zh else "en"}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{title} - Mibo</title>"
            '<link rel="stylesheet" href="/assets/welcome.css">'
            '<link rel="stylesheet" href="/assets/panels/signin.css"></head>'
            f'<body class="signin-page"><main class="signin-result"><span class="{mark}" aria-hidden="true"></span>'
            f"<h1>{title}</h1><p>{text}</p></main></body></html>")


def mount(app, workspace, body) -> None:
    from .runtime import WINDOW_HEADER
    from .signin import admitted

    app.state.mcp = hub = Hub(workspace)
    hub.cards.window = lambda: getattr(app.state, "window", None)

    def keep(values: dict) -> None:
        """A server's values, to the credential store; an empty one is deleted."""
        secretstore.put_many(workspace.home, values)

    def own_page(request: Request) -> bool:
        """A press only the app's own page on this computer can give, never the service or another device."""
        window = getattr(app.state, "window", None)
        return window is None or window.is_own_page(request.headers.get(WINDOW_HEADER, ""))

    def looking_up() -> None:
        """The latest versions of pinned packages, asked in the background at most once a day (mcp_market)."""
        task = getattr(app.state, "mcp_latest", None)
        if task is None or task.done():
            app.state.mcp_latest = asyncio.get_running_loop().create_task(mcp_market.refresh_latest(workspace.home))

    def seen() -> dict:
        """Settings' rows with their versions and updates."""
        return mcp_market.annotate(hub.view(), workspace.home)

    @app.get("/api/mcp")
    async def mcp_view():
        looking_up()
        return await asyncio.to_thread(seen)

    @app.get("/api/mcp/market")
    async def market_view():
        return await asyncio.to_thread(mcp_market.view, workspace.home)

    @app.get("/api/mcp/market/search")
    async def market_search(q: str = ""):
        return {"servers": await mcp_market.search(workspace.home, q)}

    @app.post("/api/mcp/market")
    async def market_change(request: Request):
        """Install an entry, update one server or all, list or bring over other apps' servers, sync ModelScope.
        The answer carries Settings' rows and the market as they now are, and the names added."""
        payload = await body(request)
        home = workspace.home
        added: list[str] = []
        if isinstance(payload.get("install"), str):
            values = payload.get("values") if isinstance(payload.get("values"), dict) else {}
            way = payload.get("way") if isinstance(payload.get("way"), int) else 0
            added = [await mcp_market.install(home, payload["install"], way, values, keep)]
        elif isinstance(payload.get("update"), str):
            await asyncio.to_thread(mcp_market.update, home, payload["update"], keep)
        elif payload.get("update_all") is True:
            for row in (await asyncio.to_thread(seen))["servers"]:
                if row["latest"]:
                    await asyncio.to_thread(mcp_market.update, home, row["name"], keep)
        elif payload.get("apps") is True:
            # Other apps' files, read on the person's press only.
            return {"found": await asyncio.to_thread(mcp_market.other_apps, home)}
        elif isinstance(payload.get("import"), list) and all(isinstance(k, str) for k in payload["import"]):
            added = await asyncio.to_thread(mcp_market.import_from, home, payload["import"], keep)
        elif isinstance(payload.get("modelscope"), str):
            added = await mcp_market.sync_modelscope(home, payload["modelscope"][:500], keep)
        else:
            return _error("generic")
        hub.reconcile()
        view, market = await asyncio.gather(asyncio.to_thread(seen), asyncio.to_thread(mcp_market.view, home))
        return {"view": view, "market": market, "added": added}

    @app.post("/api/mcp")
    async def mcp_change(request: Request):
        """Settings: add, remove, switch, retry, fetch Node.js or uv, allow a start, a tool's mode, the mode of the
        tools that write, a value."""
        payload = await body(request)
        home = workspace.home
        if isinstance(payload.get("add"), str):
            if len(payload["add"]) > MAX_TEXT:
                return _error("tools_bad_snippet")
            servers = mt.parse_snippet(payload["add"], str(payload.get("name") or ""))
            await asyncio.to_thread(mt.write_servers, home, servers, keep)
        elif isinstance(payload.get("remove"), str):
            keys = await asyncio.to_thread(mt.remove_server, home, payload["remove"])
            if keys:
                await asyncio.to_thread(keep, {key: "" for key in keys})
            await asyncio.to_thread(hub.grants.forget, payload["remove"])
            await hub.forget_sign_in(payload["remove"])
        elif isinstance(payload.get("disabled"), bool) and isinstance(payload.get("server"), str):
            await asyncio.to_thread(mt.set_disabled, home, payload["server"], payload["disabled"])
        elif isinstance(payload.get("retry"), str):
            hub.poke(payload["retry"])
        elif payload.get("runtime") in runtimes.RELEASES and runtimes.offer(payload["runtime"]):
            # Downloads a program onto this computer: a press on the app's own page only.
            if not own_page(request):
                return _error("local_only", 403)
            hub.fetch_runtime(payload["runtime"])
        elif isinstance(payload.get("signin"), str):
            # Opens a browser on this computer: a press on the app's own page only.
            if not own_page(request):
                return _error("local_only", 403)
            port = (request.scope.get("server") or ("", 0))[1]
            hub.sign_in(payload["signin"], f"http://localhost:{port}{CALLBACK_PATH}", str(payload.get("lang") or ""))
        elif isinstance(payload.get("signin_cancel"), str):
            hub.cancel_sign_in(payload["signin_cancel"])
        elif isinstance(payload.get("signout"), str):
            if not own_page(request):
                return _error("local_only", 403)
            revoked = await hub.sign_out(payload["signout"])
            return {**await asyncio.to_thread(seen), "revoked": revoked}
        elif isinstance(payload.get("start"), str) and payload.get("answer") in ("once", "always", "no"):
            if not own_page(request):
                return _error("local_only", 403)
            hub.answer_start(payload["start"], payload["answer"])
        elif isinstance(payload.get("tool"), dict):
            row = payload["tool"]
            runner = hub.runners.get(str(row.get("server") or ""))
            tool = next((t for t in (runner.tools if runner else []) if t["name"] == row.get("tool")), None)
            if tool is None or row.get("mode") not in mt.MODES:
                return _error("tools_unknown_tool")
            await asyncio.to_thread(hub.grants.set_mode, runner.name, tool, row["mode"])
        elif isinstance(payload.get("group"), dict):
            # One mode for a server's tools that write and delete, set at once: auto or never, never always.
            row = payload["group"]
            runner = hub.runners.get(str(row.get("server") or ""))
            if runner is None or row.get("group") != "write" or row.get("mode") not in (mt.AUTO, mt.NEVER):
                return _error("generic")
            await asyncio.to_thread(hub.grants.set_group, runner.name, row["mode"], runner.tools)
        elif isinstance(payload.get("value"), dict):
            row = payload["value"]
            await asyncio.to_thread(mt.set_value, home, str(row.get("server") or ""), str(row.get("kind") or ""),
                                    str(row.get("name") or ""), str(row.get("value") or ""), keep)
        else:
            return _error("generic")
        hub.reconcile()
        if isinstance(payload.get("value"), dict):
            hub.poke(str(payload["value"].get("server") or ""))
        return await asyncio.to_thread(seen)

    @app.get(CALLBACK_PATH)
    async def mcp_signin_return(request: Request, code: str = "", state: str = "", iss: str = ""):
        """The browser, sent back by a server's consent page with a code (or an error and no code)."""
        if not admitted(request):
            return HTMLResponse(return_page("en", "link_invalid"), status_code=403)
        lang, error = await hub.returned(code[:2048], state[:256], iss[:512])
        return HTMLResponse(return_page(lang, error), status_code=400 if error else 200)

    @app.get("/api/mcp/log")
    async def mcp_log(n: int = 40):
        return {"rows": await asyncio.to_thread(hub.grants.log_rows, max(1, min(n, 200)))}

    @app.post("/api/mcp/log")
    async def mcp_log_clear(request: Request):
        if (await body(request)).get("clear") is not True:
            return _error("generic")
        await asyncio.to_thread(hub.grants.clear_log)
        return {"rows": []}

    # ---- What the character service asks for its turn: never from another device.

    @app.get("/api/mcp/offer")
    async def mcp_offer(request: Request):
        return hub.offer()

    @app.post("/api/mcp/find")
    async def mcp_find(request: Request):
        payload = await body(request)
        return await hub.find_for(str(payload.get("query") or "")[:200], str(payload.get("server") or ""),
                                  str(payload.get("conversation") or ""))

    @app.post("/api/mcp/call")
    async def mcp_call(request: Request):
        """One call from the character's turn: started, checked, asked about, run. Waits for the card."""
        payload = await body(request)
        return await hub.call(str(payload.get("server") or ""), str(payload.get("tool") or ""),
                              payload.get("arguments"), str(payload.get("conversation") or ""))

    @app.get("/api/mcp/card")
    async def mcp_card():
        """The card waiting for the person, if any."""
        return {"card": hub.cards.card}

    @app.post("/api/mcp/card")
    async def mcp_press(request: Request):
        if not own_page(request):
            return _error("local_only", 403)
        payload = await body(request)
        ident, answer = payload.get("id"), payload.get("answer")
        if not isinstance(ident, str) or answer not in ("once", "chat", "always", "no"):
            return _error("generic")
        # A server's form comes with what the person filled in; a value that does not fit keeps the card up.
        pressed = hub.press(ident, answer, payload.get("values"))
        return {"ok": pressed == "ok", **({"problem": pressed} if pressed not in ("ok", "gone") else {})}

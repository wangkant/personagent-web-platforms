"""Looking things up on the web when a turn needs it."""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
import time
import types
from html import unescape
from urllib.parse import urlsplit

import httpx
from keyring.errors import KeyringError

from . import lookup_need
from . import paths
from . import schedule_tool
from . import secretstore
from . import today
from .textproc import (
    _fence_search_results,
)

logger = logging.getLogger("agent")

#: Chat lines before the trigger that the search decision reads.

# DuckDuckGo's region for each chat language, so results come in the language the chat is in.
_DDG_REGIONS = {"zh": "cn-zh", "zh-TW": "tw-tzh", "en": "us-en", "ja": "jp-jp", "ko": "kr-kr",
                "de": "de-de", "fr": "fr-fr", "es": "es-es", "pt-BR": "br-pt", "ru": "ru-ru"}
# The engines ddgs asks, in turn: the ones ddgs 9.16 has on (google, bing and yandex are off).
# Its "auto" draws a few at random, and half the draws found nothing (measured
# 2026-11-19: 3 of 6 queries, against 4 of 6 for these).
_DDG_BACKENDS = "yahoo, startpage, mojeek, duckduckgo"

# The search API the person brought (Settings › 外出, panels/walks.py), kept in the system's credential store as
# {"api": "brave" | "tavily", "key": "..."}. Without one the keyless engines above answer.
SEARCH_KEY = "web_search"
_TAG = re.compile(r"<[^>]+>")


def _ddgs():
    """ddgs's DDGS, its HTTP client resolving names through the system's resolver (primp's documented
    dns_resolver="system"). primp's own resolver, on by default in its wheels, sends each lookup from a UDP socket
    bound on every address, and on Windows that brings the firewall's prompt asking whether Python may take
    connections from the network. ddgs makes its primp clients itself and takes no resolver, so the name it makes
    them by is given the option."""
    import primp
    from ddgs import DDGS, http_client
    http_client.primp = types.SimpleNamespace(**{**vars(primp),
                                                 "Client": functools.partial(primp.Client, dns_resolver="system")})
    return DDGS


async def _brave(client: httpx.AsyncClient, key: str, query: str, count: int) -> list[dict]:
    answer = await client.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": count},
                              headers={"X-Subscription-Token": key, "Accept": "application/json"})
    answer.raise_for_status()
    # Brave marks the matched words in its snippets with <strong>.
    return [{"title": r.get("title"), "url": r.get("url"), "snippet": unescape(_TAG.sub("", r.get("description") or ""))}
            for r in (answer.json().get("web") or {}).get("results") or []]


async def _tavily(client: httpx.AsyncClient, key: str, query: str, count: int) -> list[dict]:
    answer = await client.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {key}"},
                               json={"query": query, "search_depth": "basic", "max_results": count})
    answer.raise_for_status()
    return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
            for r in answer.json().get("results") or []]


SEARCH_APIS = {"brave": _brave, "tavily": _tavily}


def search_api(home) -> tuple[str, str]:
    """The search API the person brought and its key, or ("", "") for the keyless engines; a credential store
    the system does not have (a Linux desktop without a Secret Service) counts as none."""
    try:
        kept = json.loads(secretstore.get(home, SEARCH_KEY) or "{}")
    except (KeyringError, ValueError):
        return "", ""
    return (kept["api"], kept["key"]) if kept.get("api") in SEARCH_APIS and kept.get("key") else ("", "")


async def key_taken(api: str, key: str) -> bool:
    """Whether the service takes the key, by one search of one result: False only when it says no."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await SEARCH_APIS[api](client, key, "weather", 1)
    except httpx.HTTPStatusError as e:
        return e.response.status_code not in (400, 401, 403, 422)
    except httpx.HTTPError:
        # Unreachable now: the key is kept, and a failing search falls back to the keyless engines.
        return True
    return True

# The web tools of the reply model's catalog (catalog.py).
TOOLS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": (
            "Search the web before answering anything current (news, prices, releases) or any name, product, "
            "slang or event you are not sure of. Turn slang and nicknames into the real name first, and leave old years out: recent means this year. Not for small talk or "
            "questions about you, and not when they want something else to watch or read (\"还有别的吗\"): "
            "that is bring_find."),
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "what you would type into a search engine"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_page",
        "description": (
            "Read one public web page and get its main text: a link they sent, a page they ask "
            "about, or a search result whose snippet is not enough. A page too long to give whole comes as its "
            "outline and the lines that match their question; read on with section or look_for."),
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "the page's full http(s) address"},
            "section": {"type": "integer", "description": "a long page's section to read whole, by its number in "
                                                          "the outline a read of it gave"},
            "look_for": {"type": "string", "description": "words to find in a long page, besides their question"}},
            "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "read_with_their_session",
        "description": (
            "Read one address as they see it signed in, out of sight in the app's browser: their own feed on a "
            "site (\"帮我看看我小红书首页有什么\"), messages, notifications or orders, which a web search never "
            "reaches. A sign-in the site asks for comes up for them in the app's browser and the read goes on "
            "after; never go round a sign-in or a check. Read only, a few times a day per site. For public pages, "
            "read_page."),
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "the full http(s) address, such as the site's home page"},
            "section": {"type": "integer", "description": "a long page's section to read whole, by its number in "
                                                          "the outline a read of it gave"},
            "look_for": {"type": "string", "description": "words to find in a long page, besides their question"}},
            "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "pages_they_read",
        "description": (
            "The pages they read lately in the app's browser, newest first, with how long they stayed: when "
            "they refer to something they read or watched there."),
        "parameters": {"type": "object", "properties": {
            "n": {"type": "integer", "description": "how many, up to 30"}}, "required": []}}},
]
# Connecting a site they name: its sign-in in the app's browser, as the Connections page's 登录 (web/connect.py).
from .web import connect as _connect  # noqa: E402


# Search rows one call hands the model. A page's words go through web/pagetext.py: whole when they fit the reply
# model's room, else its outline and the lines that match.
_ROWS_A_SEARCH = 6
# What a read with the person's session can answer instead of a page.
_SESSION_ERRORS = {
    "off": "(they turned off letting you use their browsing; say so)",
    "no_app": "(the app window with its browser is not open)",
    "not_web": "(not a web address)",
    "sign_in_page": "(that is a sign-in page: never read)",
    "wall": "(that site showed a check or a sign-in lately; it is left alone for a while)",
    "host_day_cap": "(enough reads of that site today)",
    "sign_in": "(the site wants them to sign in again in the app's browser: tell them)",
    "verification": "(the site asked for a check (a CAPTCHA or a verification): that is theirs to do, in the "
                    "app's browser; tell them)",
    "sign_in_overlay": "(the site covered the page with a sign-in: they need to sign in in the app's browser)",
    "error": "(the page did not load)",
    "dead": "(that page no longer exists on the site)",
    "empty": "(the page showed nothing to read)",
    "failed": "(the page could not be read)",
}


def _page_words(page: dict, question: str, room: int, args: dict) -> str:
    """A page from the app's browser as the model reads it: title, address, then its rows, or its text through
    web/pagetext.py (whole when it fits `room` tokens, else the section `args` names or the outline and the lines
    that match `question` and `args`' look_for), with a note of what was given."""
    from .web import pagetext
    rows = page.get("rows") or []
    listed = "\n".join(f"{n}. " + " | ".join(str(row.get(k)) for k in ("title", "author", "time") if row.get(k))
                       + (f" ({row['link']})" if row.get("link") else "")
                       for n, row in enumerate(rows[:30], 1))
    if listed:
        return f"{page.get('title', '')}\n{page['url']}\n\n{listed}"
    body = "\n\n".join(t for t in (page.get("description"), page.get("text")) if t)
    given, note = pagetext.give(body, question, room, *_part(args))
    return f"{page.get('title', '')}\n{page['url']}\n\n{given}\n\n({note})"


def _part(args: dict) -> tuple[int, str]:
    """The section a page tool's call asks for (-1 for none) and the words it looks for."""
    part = args.get("section")
    return (part if isinstance(part, int) and not isinstance(part, bool) and part >= 0 else -1,
            " ".join(str(args.get("look_for") or "").split())[:200])


class WebSearch:
    async def _search_rows(self, query: str, max_results: int = 6) -> list[dict]:
        """Up to `max_results` results as {"title", "url", "snippet"}: from the search API the person brought
        (Brave Search or Tavily), else, or when it answers nothing, from keyless DuckDuckGo in the chat
        language's region; [] on any failure."""
        api, key = await asyncio.to_thread(search_api, paths.ROOT)
        results = []
        if api:
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    results = await SEARCH_APIS[api](client, key, query, max_results)
            except Exception as e:
                logger.warning("[Agent] %s search failed (%d-char query): %s", api, len(query), e)
        if not results:
            results = [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")}
                       for r in await self._web_search_ddg(query, max_results)]
        rows = []
        for r in results[:max_results]:
            title = " ".join(str(r.get("title") or "").split())
            snippet = " ".join(str(r.get("snippet") or "").split())
            url = str(r.get("url") or "").strip()
            if title or snippet:
                rows.append({"title": title[:200], "url": url[:2000], "snippet": snippet[:400]})
        return rows

    async def _web_search_ddg(self, query: str, max_results: int) -> list:
        """DuckDuckGo's (ddgs) results, or [] on any failure. Its engines often answer nothing at random
        (measured 2026-11-19: 8 of 15 queries at the first try, 15 of 15 within three tries), so an empty
        answer is asked again: the engines in the other order, then ddgs's own pick in no region."""
        DDGS = _ddgs()
        region = _DDG_REGIONS.get(getattr(self, "chat_lang", ""), "wt-wt")
        if region in ("cn-zh", "tw-tzh", "jp-jp", "kr-kr") and not re.search(
                r"[぀-ヿ㐀-鿿가-힯]", query):
            # An English query in a Chinese, Japanese or Korean chat finds more in English.
            region = "us-en"
        others = ", ".join(reversed(_DDG_BACKENDS.split(", ")))
        for region, backend in ((region, _DDG_BACKENDS), (region, others), ("wt-wt", "auto")):
            def _run(region=region, backend=backend):
                return DDGS().text(query, region=region, max_results=max_results, backend=backend) or []
            try:
                rows = await asyncio.wait_for(asyncio.to_thread(_run), 15)
            except Exception as e:
                logger.info("[Agent] web_search DDG found nothing (%d-char query, %s): %s", len(query), backend, e)
                continue
            if rows:
                return rows
        logger.warning("[Agent] web_search DDG failed (%d-char query) after three tries", len(query))
        return []

    async def _web_tool(self, name: str, args: dict, lookups) -> str:
        """One call of a web tool; what it found, fenced as external material."""
        from .roam import fence_pages, site_name
        if name == "web_search":
            query = " ".join(str(args.get("query") or "").split())[:200]
            if not query:
                return _fence_search_results("(no query given)")
            lookups.doing("search", query)
            rows = await self._search_rows(query, _ROWS_A_SEARCH)
            tried = [query]
            if not rows and lookup_need.simpler(query):
                # Nothing found: once more with other words.
                query = lookup_need.simpler(query)
                tried.append(query)
                lookups.doing("search", query)
                rows = await self._search_rows(query, _ROWS_A_SEARCH)
            logger.info("[Agent] web_search (%d-char query) -> %d results", len(query), len(rows))
            for row in rows[:2]:
                lookups.source(row["title"], row["url"], read=False)
            return _fence_search_results(
                f"Results for: {query}\n" + ("\n".join(f"- {r['title']} ({r['url']})\n  {r['snippet']}"
                                                         for r in rows) or "(nothing found)")) + lookup_need.after_search(
                bool(rows), tried)
        if name == "read_page":
            url = str(args.get("url") or "").strip()
            lookups.doing("read", site_name(url) if url.startswith(("http://", "https://")) else url[:80])
            page = await self._read_for_chat(url, lookups.said)
            if page is None:
                logger.info("[Agent] read_page %s -> not read", site_name(url))
                return fence_pages(f"{url[:300]}\n(this page could not be read from here)") + lookup_need.UNREAD
            title, text, final = page
            logger.info("[Agent] read_page %s -> %d chars", site_name(final), len(text))
            lookups.source(title or site_name(final), final, read=True)
            today.read_in_chat(self, final, title, text, time.time())
            from .web import pagetext
            given, note = pagetext.give(text, lookups.said, self._page_room(), *_part(args))
            return fence_pages(f"{title}\n{final}\n\n{given}\n\n({note})")
        # The rest reach the app's own browser: their time is not the turn's.
        lookups.away_since = time.monotonic()
        try:
            return await self._browser_tool(name, args, lookups)
        finally:
            lookups.away_s, lookups.away_since = lookups.away(), None

    async def _browser_tool(self, name: str, args: dict, lookups) -> str:
        """The app's own browser: a page read signed in, the pages read lately, or a site's sign-in. Page words come
        back fenced as external material."""
        from . import browsed
        from .roam import fence_pages, site_name
        from .web import signin
        home = today.runtime_of(self).parent
        if name == "read_with_their_session":
            url = str(args.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                return "(give a full http(s) address)"
            if not signin.chose(home / "runtime" / "desktop", url, "task"):
                return "(they turned off your reading this site on your own; ask them, do not read it another way)"
            lookups.doing("read", site_name(url))
            page = await asyncio.to_thread(browsed.read_signed_in, home, url)
            if page.get("error") in (*signin.WAITED, "wall") and not schedule_tool.run_of():
                # A sign-in first: the page comes to the pane, they sign in, and it is read again.
                page = await signin.read_after(home, url, lookups.said, page)
            if page.get("error"):
                logger.info("[Agent] read_with_their_session %s -> %s", site_name(url), page["error"])
                return _SESSION_ERRORS.get(page["error"], f"(not read: {page['error']})")
            lookups.source(page.get("title") or site_name(page["url"]), page["url"], read=True)
            return fence_pages(_page_words(page, lookups.said, self._page_room(), args))
        if name == "pages_they_read":
            lookups.doing("pages")
            rows = await asyncio.to_thread(browsed.recent_pages, home, max(1, min(int(args.get("n") or 15), 30)))
            if not rows:
                return "(no pages read lately in the app's browser, or sharing is off)"
            return fence_pages("\n".join(
                f"- {row.get('title') or row['url']} ({row['url']}) "
                f"{time.strftime('%m-%d %H:%M', time.localtime(row.get('at') or 0))}, {round(row.get('s') or 0)} s"
                + "".join(f"\n    · {r.get('title', '')}" for r in row.get("rows") or [])
                for row in rows))
        lookups.doing("operate", str(args.get("site") or "")[:80])
        return await _connect.use(home, args, getattr(self, "chat_lang", "") or "zh")

    def _page_room(self) -> int:
        """Tokens of page one tool result may carry: the reader's room in the reply model's budget
        (web/pagetext.room), within the share one result takes in the turn (context.Budget.result), so a long page
        comes as its outline and the lines that match rather than cut blind."""
        from .web import pagetext
        plan = self._budget(self._endpoint_for(self.llm_dm_model)[0], self.llm_dm_model)
        return min(pagetext.room(plan), plan.result)

    async def _read_for_chat(self, url: str, said: str = "") -> tuple[str, str, str] | None:
        """(title, readable text, final address) of a public page (web/fetch.py): fetched plainly, and read
        again rendered in the visitor browser when the fetch is thin or lacks the list `said` asks for; None
        when it cannot or may not be read."""
        from .web import fetch
        if len(url) > 4096 or urlsplit(url).scheme not in ("http", "https"):
            return None
        return await fetch.for_chat(self, url, fetch.listy(said))

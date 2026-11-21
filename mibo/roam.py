"""The character's walks: it goes out on its own as a signed-out visitor,
reads pages on any site and brings back what it saw.

A visitor browser (one headless browser on its own profile, never signed in)
reads a page into a library Item: the facts the page declares about itself
(Open Graph, Twitter card, schema.org JSON-LD) and its readable text. A walk
reads a few pages, asks the model once which to keep and why, and keeps those
in the library. Pages are only read; nothing is signed, posted or got around.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import importlib.util
import io
import ipaddress
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, NamedTuple, Optional
from urllib.parse import urljoin, urlsplit

from . import players, profile, quiet, situation
from . import installed_browser as web
from .players import bilibili_id, player_for, youtube_id  # noqa: F401  (others import them from here)
from .web import read as reading, sites
from .web.read import Found
from .web.tabs import PlaywrightTab
from .ingestion import MAX_IMAGE_PIXELS, _RESERVED_LOCAL_NAMES, _is_internal_ip, _resolve_public_target
from .library import PICTURE_MAX_BYTES, TEXT_MAX, Item, Library, item_id
from .llm import purpose
from .panels.feed import site_taste
from .storage import atomic_write_text
from .textproc import (SLEEP_HOUR_END, SLEEP_HOUR_START, _UNTRUSTED_INPUT_RULES, _WEB_DESC_CLOSE,
                       _WEB_DESC_OPEN, _clean_prompt_source, _env_tz_offset, _fence_user_data,
                       default_tz_offset, parse_tz_offset, salvage_json_object, strip_json_fences)

logger = logging.getLogger("mibo.roam")

# What stops a walk; walks switched off in Settings › 外出 are WALKS_OFF.
OUT, ASLEEP, LIMIT, WALKS_OFF, NO_MODEL = "out", "asleep", "limit", "walks_off", "no_model"
# What a host the person favours adds to its place in a walk's order (Settings › 外出).
FAVOUR = 2.0

IDLE_S = 300.0
# The shortest and the longest the walk loop sleeps between looks.
TICK_S = 30.0
NAP_S = 1800.0
FIRST_WALK_S = 120.0
# Walks a day a lookup may take beyond ROAM_WALKS_A_DAY.
WONDER_EXTRA = 1
KEEP_MAX = 3
PICTURE_SIDE = 480
PICTURE_FETCH_MAX = 8_000_000
DESCRIPTION_MAX = 1000
PROMPT_EXCERPT = 400
WHY_MAX = 200
TAGS_MAX, TAG_LEN = 5, 30
# The batched judgement of what the sources brought: JUDGE_TOP of the ranking's best unjudged things
# and as many drawn at random, at most JUDGE_DAY calls a day and JUDGE_GAP_S apart, in waking hours.
JUDGE_TOP, JUDGE_RANDOM = 8, 8
JUDGE_MIN = 8
JUDGE_DAYS = 3
JUDGE_DAY = 4
JUDGE_GAP_S = 3 * 3600
JUDGE_EXCERPT = 160
# What the character's sentence may and may not say; the label under it says what the ranking used.
_WHY_RULE = ("The why is one sentence in your own voice about what you yourself see in it. Never say the owner "
             "opened, liked or asked for something like it, and never mention scores or rankings.")
_ROW = ('{"n": <number>, "fits": true or false, "love": <0-3, how much you yourself love it>, '
        '"tags": ["<3-5 short English lowercase tags>"], "why": "<one sentence>"}')

_KNOWN_SITES = {"bilibili.com": "Bilibili", "b23.tv": "Bilibili", "youtube.com": "YouTube",
                "youtu.be": "YouTube"}
_VIDEO_TYPES = {"VideoObject"}
_ARTICLE_TYPES = {"Article", "NewsArticle", "BlogPosting", "ReportageNewsArticle", "TechArticle",
                  "ScholarlyArticle", "AnalysisNewsArticle"}
_POST_TYPES = {"SocialMediaPosting", "DiscussionForumPosting"}
_ISO_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?")
# The web search leg of a walk: its results are read like any other page, each site under its own limit.
WEB = "Web"
WEB_RESULTS = 6
# Pages of one site a walk fails to read before it leaves that site's rows.
UNREAD_MAX = 2
# A thing the character loved this much lends its tags to a walk of its own curiosity.
CURIOUS_LOVE = 2
# The share of walks that follow the tags of what the person opened lately, before their named interests.
THEIRS = 0.25
# General words for a walk with no topic of the owner's; a hot list does the rest.
_GENERAL = {"zh": ("科普", "音乐", "旅行", "美食", "历史", "手工"),
            "en": ("science", "music", "travel", "cooking", "history", "crafts")}

# ---------------------------------------------------------------- addresses

def host_ok(host: str) -> bool:
    """False for a name or literal address that is this computer or its network."""
    host = (host or "").strip("[]").rstrip(".").lower()
    if not host or host in _RESERVED_LOCAL_NAMES or host.endswith(".localhost"):
        return False
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return True
    return not _is_internal_ip(host)


async def public_address(url: str) -> bool:
    """http(s) to a name whose every address is public."""
    return await _resolve_public_target(url) is not None


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _on(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def site_name(url: str) -> str:
    host = _host(url)
    for domain, name in _KNOWN_SITES.items():
        if _on(host, domain):
            return name
    return host[4:] if host.startswith("www.") else host


def allowed_player(url: str) -> str:
    """`url` when it is an official player on the list (players.PLAYERS), a YouTube embed made cookieless;
    else ""."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    if urlsplit(url).scheme == "https" and (_on(host, "youtube.com") or _on(host, "youtube-nocookie.com")):
        return player_for(url)
    return players.allowed(url)


# ---------------------------------------------------------------- facts

def _text(value: Any, limit: int = 300) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    return " ".join(html.unescape(str(value)).split())[:limit]


def _person(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("name")
    name = _text(value, 120)
    return "" if name.startswith(("http://", "https://")) else name


def _image(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    return _text(value, 2000)


def _seconds(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if 0 < value < 360_000 else None
    text = str(value or "").strip()
    if text.isdigit():
        return _seconds(int(text))
    match = _ISO_DURATION.fullmatch(text)
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
    return _seconds(int(days * 86400 + hours * 3600 + minutes * 60 + seconds))


def _ld_objects(blobs: Any) -> list[dict]:
    found: list[dict] = []
    for blob in (blobs if isinstance(blobs, list) else [])[:10]:
        try:
            queue = [json.loads(blob)] if isinstance(blob, str) else []
        except ValueError:
            continue
        while queue and len(found) < 50:
            value = queue.pop(0)
            if isinstance(value, list):
                queue.extend(value[:20])
            elif isinstance(value, dict):
                found.append(value)
                if isinstance(value.get("@graph"), list):
                    queue.extend(value["@graph"][:30])
    return found


def _types(obj: dict) -> set[str]:
    kind = obj.get("@type")
    return {str(k) for k in (kind if isinstance(kind, list) else [kind]) if isinstance(k, str)}


def _absolute(base: str, address: str) -> str:
    if not address:
        return ""
    url = urljoin(base, address.strip())
    return url if urlsplit(url).scheme in ("http", "https") else ""


def facts_from(data: Any, url: str) -> dict:
    """Item fields from what reader.js read off a page (its meta pairs, JSON-LD blobs, canonical address
    and text); every value checked, as the page wrote it."""
    data = data if isinstance(data, dict) else {}
    meta: dict[str, str] = {}
    for row in data.get("meta") if isinstance(data.get("meta"), list) else []:
        if isinstance(row, list) and len(row) == 2 and isinstance(row[0], str) and isinstance(row[1], str):
            meta.setdefault(row[0], row[1])
    objects = _ld_objects(data.get("ld"))
    main = (next((o for o in objects if _types(o) & _VIDEO_TYPES), None)
            or next((o for o in objects if _types(o) & _ARTICLE_TYPES), None)
            or next((o for o in objects if _types(o) & _POST_TYPES), None) or {})
    kinds = _types(main)

    def first(*values: Any, limit: int = 300) -> str:
        return next((t for t in (_text(v, limit) for v in values) if t), "")

    published = first(main.get("datePublished"), main.get("uploadDate"), meta.get("article:published_time"),
                      meta.get("datepublished"), meta.get("uploaddate"), limit=40)
    pictures = [meta.get("og:image:secure_url"), meta.get("og:image"), meta.get("twitter:image"),
                _image(main.get("thumbnailUrl")), _image(main.get("image"))]
    picture = next((p for p in (_absolute(url, _text(v, 2000)) for v in pictures)
                    if p and len(p) <= 2000), "")
    embed = player_for(url) or player_for(_text(data.get("canonical"), 2000))
    if not embed:
        for declared in (meta.get("og:video:secure_url"), meta.get("og:video:url"), meta.get("og:video"),
                         meta.get("twitter:player"), main.get("embedUrl")):
            embed = allowed_player(_absolute(url, _text(declared, 2000)))
            if embed:
                break
    length = (_seconds(main.get("duration")) or _seconds(meta.get("og:video:duration"))
              or _seconds(meta.get("video:duration")) or _seconds(meta.get("duration")))
    text = str(data.get("text") or "") if isinstance(data.get("text"), str) else ""
    # reader.js marks headings with "## "; an item's text is plain.
    text = "\n".join(" ".join(line.split()).removeprefix("## ") for line in text.splitlines()).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)[:TEXT_MAX]
    og_type = meta.get("og:type", "").lower()
    if embed or kinds & _VIDEO_TYPES or og_type.startswith("video"):
        kind = "video"
    elif kinds & _ARTICLE_TYPES or og_type == "article":
        kind = "article"
    elif kinds & _POST_TYPES:
        kind = "post"
    else:
        kind = "article" if len(text) >= 1500 else "page"
    free = main.get("isAccessibleForFree")
    if kind == "video":
        # A video page's text is its surroundings; its words are in the description.
        text = ""
    known = site_name(url) if any(_on(_host(url), d) for d in _KNOWN_SITES) else ""
    return {
        "title": first(meta.get("og:title"), meta.get("twitter:title"), main.get("headline"), main.get("name"),
                       data.get("title")),
        "site": known or first(meta.get("og:site_name"), meta.get("application-name"), limit=80) or site_name(url),
        "author": _person(main.get("author")) or first(meta.get("author"), limit=120),
        "published": published,
        "length": length,
        # A page's own description first: a post's or a video's main text is often a banner or a reply.
        "description": first(meta.get("og:description"), meta.get("twitter:description"), meta.get("description"),
                             main.get("description"), limit=DESCRIPTION_MAX),
        "picture": picture,
        "embed": embed,
        "text": text,
        "kind": kind,
        "paywall": free is False or str(free).lower() == "false",
    }


def wall_of(data: Any, url: str, facts: dict) -> str:
    """Why this page is not for a visitor: a sign-in or check address, the wall reader.js saw
    ("verification", "sign_in", "error"; a sign-in layer or an empty page only when nothing of the page reads,
    not even its title), or "paywall"; "" for none."""
    found = reading.wall_at(url)
    if found:
        return found
    sign = data.get("wall") if isinstance(data, dict) and isinstance(data.get("wall"), dict) else {}
    kind = str(sign.get("kind") or "")
    if kind and not (kind in ("sign_in_overlay", "empty") and facts.get("title")):
        return kind
    return "paywall" if facts.get("paywall") else ""


def small_picture(data: bytes) -> bytes:
    """A JPEG of the picture fitting PICTURE_SIDE and under the library's size limit, or b""."""
    try:
        from PIL import Image
    except ImportError:
        return b""
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                return b""
            source.draft("RGB", (PICTURE_SIDE * 2, PICTURE_SIDE * 2))
            picture = source.convert("RGB")
        picture.thumbnail((PICTURE_SIDE, PICTURE_SIDE))
        for quality in (82, 70, 55):
            out = io.BytesIO()
            picture.save(out, "JPEG", quality=quality, optimize=True)
            if out.tell() <= PICTURE_MAX_BYTES:
                return out.getvalue()
    except Exception:
        return b""
    return b""


# ---------------------------------------------------------------- the browser

class Visitor:
    """One headless browser on a profile that is never signed in, started when
    a page is wanted and stopped when none has been for a while."""

    def __init__(self, profile: Path, *, executable: Optional[Path] = None,
                 host_check: Callable[[str], bool] = host_ok,
                 public: Callable[[str], Awaitable[bool]] = public_address, clock: Callable[[], float] = time.monotonic):
        self.browser = web.Browser(profile, executable=executable, headless=True)
        self.host_check = host_check
        self.public = public
        self.clock = clock
        self.lock = asyncio.Lock()
        self.used = 0.0
        self.context: Any = None

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        return parts.scheme in ("http", "https") and self.host_check(parts.hostname or "")

    async def _guard(self, route: Any) -> None:
        if self._allowed(route.request.url):
            await route.continue_()
        else:
            await route.abort("blockedbyclient")

    async def _watch(self, page: Any) -> None:
        """Check every request of `page`, each redirect hop included, which the
        route above is not shown; a page's own address is resolved too. The
        page names itself with Mibo's token on the browser's user agent."""
        session = await page.context.new_cdp_session(page)
        agent = await page.evaluate("() => navigator.userAgent")
        await session.send("Emulation.setUserAgentOverride", {"userAgent": f"{agent} {sites.TOKEN}"})
        pending: set = set()

        async def paused(event: dict) -> None:
            url, ident = str(event.get("request", {}).get("url", "")), event.get("requestId")
            try:
                if self._allowed(url) and (event.get("resourceType") != "Document" or await self.public(url)):
                    await session.send("Fetch.continueRequest", {"requestId": ident})
                else:
                    logger.info("roam: a request to %s was refused", urlsplit(url).hostname or "?")
                    await session.send("Fetch.failRequest", {"requestId": ident, "errorReason": "BlockedByClient"})
            except Exception:
                pass

        def heard(event: dict) -> None:
            task = asyncio.ensure_future(paused(event))
            pending.add(task)
            task.add_done_callback(pending.discard)
        session.on("Fetch.requestPaused", heard)
        await session.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})

    async def _open(self) -> Any:
        if self.context is None:
            context = await self.browser.start()
            try:
                await context.route("**/*", self._guard)
                session = await context.browser.new_browser_cdp_session()
                await session.send("Browser.setDownloadBehavior", {"behavior": "deny"})
            except BaseException:
                await self.browser.stop()
                raise
            self.context = context
        return self.context

    @contextlib.asynccontextmanager
    async def page(self):
        """A fresh tab, the only one in use, closed after."""
        async with self.lock:
            context = await self._open()
            page = await context.new_page()
            try:
                await self._watch(page)
                yield page
            finally:
                self.used = self.clock()
                with contextlib.suppress(Exception):
                    await page.close()

    @contextlib.asynccontextmanager
    async def tab(self):
        """A fresh tab as web/tabs.py's Tab, the only one in use, closed after."""
        async with self.page() as page:
            yield PlaywrightTab(page)

    async def stop_if_idle(self) -> None:
        if self.context is not None and not self.lock.locked() and self.clock() - self.used >= IDLE_S:
            async with self.lock:
                await self.stop()

    async def stop(self) -> None:
        self.context = None
        await self.browser.stop()


@dataclass
class _Read:
    item: Item
    picture: bytes = b""


@dataclass
class _Plan:
    sources: list = field(default_factory=list)     # (site, host, intent: web, hot or search, words)
    topic: str = ""
    wonder: str = ""                                # the queued words the topic came from
    asked: bool = False                             # the person asked for them, and is told how it went


# ---------------------------------------------------------------- the walk

class Roam:
    """The walk loop and the reader the rest of the service uses."""

    def __init__(self, agent: Any, *, home: Path, runtime: Path, executable: Optional[Path] = None,
                 clock: Callable[[], float] = time.time, public: Callable[[str], Awaitable[bool]] = public_address,
                 host_check: Callable[[str], bool] = host_ok, hosts: Optional[dict] = None):
        self.agent = agent
        self.settings = agent.settings
        self.library = Library(runtime)
        self.clock = clock
        self.public = public
        self.host_check = host_check
        self.executable = executable
        self.visitor = Visitor(web.visitor_profile(home),executable=executable, host_check=host_check,
                               public=public)
        # The hosts a walk goes to: the seeds' and those learned, or only these cards ({host: {home, hot,
        # search}}, a test's own site).
        for key, entries in (hosts or {}).items():
            sites.plant(runtime, key, entries)
        self.hosts = list(hosts) if hosts else None
        self.state_path = Path(runtime) / "roam.json"
        self.started = clock()
        self.pause = (3.0, 8.0)
        self._wake = asyncio.Event()
        self._walking = False
        self._topics: list[str] = []
        # The words from a chat the walk under way went out for.
        self._wonder = ""
        if self.library.outing().state == "out":
            # A walk the service was stopped in the middle of has ended.
            self.library.returned(0, now=self.started)

    # -- what the life line asks

    def available(self) -> str:
        if importlib.util.find_spec("playwright") is None:
            return web.NO_DRIVER
        if (self.executable or web.find_browser()) is None:
            return web.NO_BROWSER
        if not getattr(self.agent, "enabled", False):
            return NO_MODEL
        return ""

    def _asleep(self, now: float) -> bool:
        return asleep(now, _env_tz_offset())

    def _walks_today(self, now: float) -> int:
        outing = self.library.outing()
        return outing.today if outing.day == time.strftime("%Y-%m-%d", time.localtime(now)) else 0

    def _held(self, now: float, wonder: bool = False) -> str:
        """Why no walk may leave now, "" when one may. A `wonder` walk (words wait) goes in sleep hours too
        and may take WONDER_EXTRA walks beyond the day's."""
        code = self.available()
        if code:
            return code
        if not self.settings.roam_enabled:
            return WALKS_OFF
        if self._walking or self.library.outing().state == "out":
            return OUT
        if not wonder and self._asleep(now):
            return ASLEEP
        if self._walks_today(now) >= self.settings.roam_walks_a_day + (WONDER_EXTRA if wonder else 0):
            return LIMIT
        return ""

    def _wanted(self, now: float) -> bool:
        """Words wait to be looked up (today.wonder) and a walk may leave for them now, the gap aside."""
        from . import today
        return today.waiting(self.library.root.parent) and not self._held(now, wonder=True)

    def request(self) -> str:
        """Words were queued: the loop looks at once, and goes when `_wanted` lets it. Why a walk for them
        cannot leave now, "" when it can (OUT: right after the walk under way)."""
        self._wake.set()
        return self._held(self.clock(), wonder=True)

    def _nap(self, now: float) -> float:
        """Seconds the loop sleeps: until a walk may be due, or the reader's browser
        has been idle long enough to close, at most NAP_S and at least TICK_S.
        Asleep or out of walks for the day, NAP_S: a walk a little late is no matter."""
        times = [NAP_S]
        if self._wanted(now):
            times.append(0.0)
        elif not self._held(now):
            last = self.library.outing().last_end
            times.append(max(self.started + FIRST_WALK_S, last + self.settings.roam_gap_h * 3600,
                             self._not_before(now)) - now)
        if self.visitor.context is not None:
            times.append(self.visitor.used + IDLE_S - self.visitor.clock())
        return max(TICK_S, min(times))

    def _due(self, now: float) -> bool:
        if self._wanted(now):
            return True
        if self._held(now):
            return False
        last = self.library.outing().last_end
        return (now - self.started >= FIRST_WALK_S and now - last >= self.settings.roam_gap_h * 3600
                and now >= self._not_before(now))

    def next_walk(self, now: float) -> Optional[float]:
        """When the next walk of its own may leave, for Settings › 外出; None while one cannot."""
        if self._held(now):
            return None
        last = self.library.outing().last_end
        return max(now, self.started + FIRST_WALK_S, last + self.settings.roam_gap_h * 3600, self._not_before(now))

    def _not_before(self, now: float) -> float:
        """With a set time (ROAM_AT) and no walk yet today, the day's first walk waits for it; else 0."""
        if not self.settings.roam_at or self._walks_today(now):
            return 0.0
        hour, minute = map(int, self.settings.roam_at.split(":"))
        return datetime.fromtimestamp(now).replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()

    def changed(self) -> None:
        """Settings › 外出 changed `settings`: the loop looks again at once."""
        self._wake.set()

    async def loop(self) -> None:
        code = self.available()
        if code:
            logger.info("roam: walks cannot leave (%s)", code)
            # No browser support or no model: each comes back only with a new install or a settings save,
            # which builds the character again.
            return
        try:
            while True:
                # Asleep, switched off, out of walks for the day or just back, a look at the next wake is
                # enough; words queued and a change in Settings › 外出 wake it at once.
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), self._nap(self.clock()))
                self._wake.clear()
                await self._hush()
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("roam: a walk failed (%s)", type(exc).__name__)
        finally:
            await self.visitor.stop()

    async def tick(self, now: Optional[float] = None) -> int:
        """One look at the clock: a walk when one is due. Returns what it kept, or -1 when it stayed in."""
        now = self.clock() if now is None else now
        await self.visitor.stop_if_idle()
        if not self._due(now):
            return -1
        return await self.walk(now)

    # -- the day's page counts, per site

    def _counts(self, now: float) -> dict:
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        if not isinstance(saved, dict) or saved.get("day") != day or not isinstance(saved.get("sites"), dict):
            saved = {"day": day, "sites": {}}
        return saved

    def _may_visit(self, site: str, now: float) -> bool:
        return int(self._counts(now)["sites"].get(site, 0)) < self.settings.roam_site_pages_a_day

    def _count_page(self, site: str, now: float) -> None:
        counts = self._counts(now)
        counts["sites"][site] = int(counts["sites"].get(site, 0)) + 1
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.state_path, json.dumps(counts, ensure_ascii=False))
        except OSError:
            pass

    # -- reading one page

    async def _read(self, url: str, *, background: bool = False, cover: str = "",
                    walls: Optional[list] = None) -> Optional[_Read]:
        """`url` read through web/read.py. A walk's read is a `background` visit (the host's card and
        robots.txt are asked first); `cover` is the list row's picture, for a page that declares none.
        The wall a page showed is added to `walls`."""
        url = str(url or "").strip()
        if len(url) > 4096 or urlsplit(url).scheme not in ("http", "https"):
            return None
        if not await self.public(url):
            logger.info("roam: %s is not a public address; not read", _host(url) or "?")
            return None
        async with self.visitor.tab() as tab:
            page = await reading.read(tab, url, runtime=self.library.root.parent if background else None,
                                      background=background, item=True)
            final = page.url
            if not await self.public(final):
                return None
            facts = facts_from(page.data, final)
            wall = page.wall if page.wall not in ("", "sign_in_overlay", "empty") else wall_of(page.data, final, facts)
            if wall:
                logger.info("roam: %s shows a %s wall to visitors (%s); the page is left", _host(final), wall,
                            page.evidence or "-")
                if walls is not None:
                    walls.append(wall)
                return None
            picture = await self._picture(tab, facts["picture"] or cover, final)
        if not facts["title"]:
            return None
        item = Item(url=final, site=facts["site"], kind=facts["kind"], title=facts["title"],
                    author=facts["author"], published=facts["published"], length=facts["length"],
                    description=facts["description"], text=facts["text"], embed=facts["embed"])
        item.id = item_id(item.url)
        return _Read(item, picture)

    async def _picture(self, tab: Any, address: str, referer: str) -> bytes:
        if not address or not self.host_check(_host(address)):
            return b""
        try:
            # Opened in the same tab, so the browser's own network and the guards above apply; a cover
            # whose address expires is saved now.
            answer = await tab.goto(address, referer=referer, timeout_s=10)
            if (not 200 <= answer.status < 300 or not answer.kind.startswith("image/")
                    or answer.length > PICTURE_FETCH_MAX or answer.body is None):
                return b""
            body = await answer.body()
        except Exception:
            return b""
        return small_picture(body) if len(body) <= PICTURE_FETCH_MAX else b""

    # -- where to go

    def _topic(self) -> str:
        """Search words for one of the person's interests (`interests`): a topic they said they like, one
        they ticked on the first run or one of the profile's walk words, never a note and never what they
        said they do not want. The fewer fresh unseen things the library holds on an interest, the likelier
        the walk goes after it, so a starved interest gets fed first."""
        runtime = self.library.root.parent
        named = interests(runtime)
        # Not the last two walks' interests while another is left; a person who named few is still walked for.
        found = [t for t in named if t not in self._topics[-2:]] or named
        if not found:
            return ""
        held = supply(runtime, found, self.clock())
        topic = random.choices(found, [1 / (1 + held[t]) ** 2 for t in found])[0]
        self._topics = (self._topics + [topic])[-5:]
        return random.choice(search_words(topic, self._ui_lang()))

    def _curiosity(self, theirs: bool = False) -> str:
        """A couple of tags of something the person opened or liked lately, else (unless `theirs`) of
        something the character itself loved lately, as search words; "" when none."""
        from .library import ENGAGED
        from .panels import feed
        events = feed.events(self.library.root.parent)
        taken = list(dict.fromkeys(e[1] for e in reversed(events) if e[2] in ENGAGED))[:40]
        items = {item.id: item for item in self.library.items() if item.tags}
        loved = [items[i] for i in taken if i in items][:20]
        if not loved and not theirs:
            loved = [item for item in items.values() if item.love >= CURIOUS_LOVE][:20]
        found = [words for words in dict.fromkeys(" ".join(item.tags[:2]) for item in loved)
                 if words not in self._topics]
        if not found:
            return ""
        words = random.choice(found[:10])
        self._topics = (self._topics + [words])[-5:]
        return words

    def _plan(self) -> _Plan:
        """Where to go: queued words first (`today.wonder`: the person's lookup or a chat's), then now and then (THEIRS) the tags of
        what the person opened lately, then one of their interests (the most starved likeliest), then the
        character's own curiosity, each searched on the web and with two hosts' own search; with none, one
        or two hosts' hot lists, a host's search for a general word, and the web's."""
        from . import today
        lang = "zh" if str(getattr(self.agent, "agent_lang", "en")) == "zh" else "en"
        runtime = self.library.root.parent
        queued = today.take_wonder(runtime)
        wonder = queued.get("w", "")
        topic = (wonder or (random.random() < THEIRS and self._curiosity(theirs=True)) or self._topic()
                 or self._curiosity())
        searching = self._hosts("search")
        if topic:
            plan = _Plan([(WEB, "", "web", topic)]
                         + [(c["label"], c["key"], "search", topic) for c in searching[:2]], topic, wonder,
                         bool(queued.get("asked")))
        else:
            general = random.choice(_GENERAL[lang])
            hot = self._hosts("hot")[:random.randint(1, 2)]
            plan = _Plan([(c["label"], c["key"], "hot", "") for c in hot]
                         + [(c["label"], c["key"], "search", general) for c in searching
                            if c["key"] not in {h["key"] for h in hot}][:1]
                         + [(WEB, "", "web", general)])
        if wonder:
            # What they brought up is looked up first.
            plan.sources.sort(key=lambda s: s[0] != WEB)
        return plan

    def _hosts(self, intent: str) -> list[dict]:
        """The cards a walk may read `intent` from (web/sites.py), in a language the person reads, best first:
        where the person is, then what they do with each site's finds (the taste model's sampled coefficient,
        or the feed's points while the model is off), and a little chance."""
        from . import taste
        runtime = self.library.root.parent
        langs = situation.readable(runtime) | situation.expand({self._ui_lang()})
        found = [dict(c, label=c["name"] or site_name(c["home"]))
                 for c in sites.walk_hosts(runtime, intent, keys=self.hosts) if not c["lang"] or c["lang"] in langs]
        if taste.enabled(runtime):
            sampler = taste.Sampler(taste.load(runtime), taste.Priors(runtime), random.getrandbits(32))
            learned = {c["label"]: sampler.theta("origin:site:" + c["label"]) for c in found}
        else:
            learned = site_taste(runtime)
        found.sort(key=lambda c: -(situation.walk_prior(runtime, c["label"], c["region"])
                                   + learned.get(c["label"], 0.0) + FAVOUR * c["favour"] + random.random()))
        return found

    def _ui_lang(self) -> str:
        return str(getattr(self.agent, "chat_lang", "") or getattr(self.agent, "agent_lang", "") or "en")

    async def _search(self, words: str) -> list[Found]:
        """The web search's results for `words` as pages to read; the visitor's guards apply as each is read."""
        found = []
        avoided = {key for key, how in sites.steered(self.library.root.parent).items() if how == "avoid"}
        for row in await self.agent._search_rows(words, max_results=WEB_RESULTS):
            url = row["url"]
            # A site's front page is a place to go, not a thing to read, and a broad word finds mostly those;
            # a site the person keeps walks off is left.
            if (urlsplit(url).scheme in ("http", "https") and row["title"] and urlsplit(url).path.strip("/")
                    and sites.key_of(url) not in avoided):
                found.append(Found(url, row["title"], site_name(url)))
        return found

    async def _list(self, key: str, intent: str, words: str) -> tuple[list[Found], str]:
        """A host's hot list or its search for `words` (web/sites.py), or the web search's ("web"), and the
        wall the list showed ("" for none)."""
        if intent == "web":
            return await self._search(words), ""
        runtime = self.library.root.parent
        async with self.visitor.tab() as tab:
            page = await sites.list_of(runtime, tab, sites.card(runtime, key)["home"], intent, words)
        # Chromium's own connection errors say the site cannot be reached from here.
        if page.wall == "error" and situation.navigation_failed(Exception(page.evidence)):
            situation.record(runtime, key, False)
            return [], "error"
        situation.record(runtime, key, True)
        situation.record_wall(runtime, key, page.wall in ("verification", "sign_in"))
        if page.wall not in ("", "sign_in_overlay"):
            logger.info("roam: the %s list of %s gave nothing (%s: %s)", intent, key, page.wall,
                        page.evidence or "-")
            return [], page.wall
        shape = sites.card(runtime, key)["shape"]
        return reading.found_rows(page, site_name,
                                  shape_ok=(lambda url: sites.matches(url, shape)) if shape else None), ""

    # -- the walk itself

    async def walk(self, now: Optional[float] = None) -> int:
        """Go out, read a few pages, keep what is worth keeping. Returns how many were kept."""
        now = self.clock() if now is None else now
        self._walking = True
        outing = uuid.uuid4().hex[:12]
        plan = self._plan()
        self._wonder = plan.wonder
        sites: list[str] = []
        kept: list[str] = []
        read: list[_Read] = []
        # [site, why] for each list or page it could not read, for the report of a lookup.
        missed: list[tuple[str, str]] = []
        # The walk record (Settings › 外出): each list it asked and each page it could not read.
        tried: list[dict] = []
        skipped: list[dict] = []
        # A web search is no place; the pages it leads to say where it is.
        self.library.left(plan.sources[0][0] if plan.sources and plan.sources[0][0] != WEB else "", now=now)
        try:
            lists: list[list[Found]] = []
            for site, key, intent, words in plan.sources:
                if not self._may_visit(site, now):
                    continue
                found, wall = await self._list(key, intent, words)
                self._count_page(site, now)
                if found and site != WEB:
                    # Only a site that let it in counts as one it has been to.
                    self.library.at(site)
                if not found:
                    missed.append((site, wall or "none"))
                tried.append({"site": site, "intent": intent, "found": len(found), "why": "" if found else wall or "none"})
                lists.append(found)
                await self._rest()
            # A topic no list found anything for: a host's hot list instead.
            hot = self._hosts("hot")[:1] if plan.topic and not any(lists) else []
            for card in hot:
                if self._may_visit(card["label"], now):
                    found, wall = await self._list(card["key"], "hot", "")
                    tried.append({"site": card["label"], "intent": "hot", "found": len(found),
                                  "why": "" if found else wall or "none"})
                    lists.append(found)
                    self._count_page(card["label"], now)
            wanted = random.randint(3, max(3, self.settings.roam_pages))
            # A site whose pages it could not read twice in this walk is left for the rest of it.
            unread: dict[str, int] = {}
            for found in _interleave(lists):
                if len(read) >= wanted:
                    break
                if (self.library.seen(found.url) or unread.get(found.site, 0) >= UNREAD_MAX
                        or not self._may_visit(found.site, now)):
                    continue
                self.library.at(found.site)
                self._count_page(found.site, now)
                walls: list[str] = []
                page = await self._read(found.url, background=True, cover=found.picture, walls=walls)
                if page is None:
                    unread[found.site] = unread.get(found.site, 0) + 1
                    missed.append((found.site, walls[0] if walls else "unread"))
                    skipped.append({"site": found.site, "title": found.title, "url": found.url,
                                    "why": walls[0] if walls else "unread"})
                elif all(r.item.id != page.item.id for r in read):
                    # Two rows can lead to one page (a redirect, a canonical address): it is kept once.
                    read.append(page)
                    if found.site not in sites:
                        sites.append(found.site)
                await self._rest()
            for choice in await self._choose(read):
                chosen = read[choice.index]
                chosen.item.outing, chosen.item.why, chosen.item.fits = outing, choice.why, choice.fits
                chosen.item.love, chosen.item.tags, chosen.item.judged = choice.love, list(choice.tags), choice.love >= 0
                chosen.item.seen_at = int(now)
                # Every list of a walk is for its one topic, so all it kept was looked for with those words.
                chosen.item.wonder = plan.wonder
                if chosen.picture:
                    chosen.item.picture = self.library.save_picture(chosen.item.id, chosen.picture)
                self.library.add(chosen.item)
                kept.append(chosen.item.id)
            logger.info("roam: back from a walk, read %d pages, kept %d", len(read), len(kept))
        finally:
            self._walking = False
            self.library.returned(len(kept), sites, now=self.clock())
            from .panels import walks as walk_log
            walk_log.record(self.library.root.parent, {
                "id": outing, "at": int(now), "end": int(self.clock()), "topic": plan.topic, "asked": plan.asked,
                "lists": tried, "skipped": skipped,
                "read": [{"site": r.item.site, "title": r.item.title, "url": r.item.url, "kept": r.item.id in kept}
                         for r in read]})
            if plan.wonder:
                from . import today
                today.wondered(self.library.root.parent, plan.wonder, kept, self.clock(), asked=plan.asked,
                               read=len(read), missed=missed)
        return len(kept)

    async def _rest(self) -> None:
        await asyncio.sleep(random.uniform(*self.pause))
        await self._hush()

    async def _hush(self) -> None:
        """While quiet nothing walks: the browser closes, and opens again after."""
        if quiet.active():
            await self.visitor.stop()
            await quiet.wait()

    async def _choose(self, read: list[_Read]) -> list[Choice]:
        """Which pages to keep, and for each why, whether it fits the owner, how much the character
        loves it and its tags, asked of the model once."""
        if not read:
            return []
        try:
            system, user = self._prompt(read)
            with purpose("roam"):
                answer = await self.agent._call_llm(system, [{"role": "user", "content": user}], self.agent.model,
                                                    max_tokens=900, disable_thinking=True,
                                                    temperature=0.4, json_object=True)
            chosen = parse_choice(answer, len(read), KEEP_MAX)
        except Exception as exc:
            logger.warning("roam: the model did not choose (%s)", type(exc).__name__)
            chosen = None
        # No usable row (cheap models answer an empty list now and then): the walk still brings the first
        # pages home, which came from the walk's first list; the ranking sorts them out.
        if not chosen:
            return [Choice(n, "", n == 0) for n in range(min(KEEP_MAX, len(read)))]
        return chosen

    def _prompt(self, read: list[_Read]) -> tuple[str, str]:
        from .langs import NAMES
        lang = str(getattr(self.agent, "chat_lang", "") or getattr(self.agent, "agent_lang", "en"))
        name = str(getattr(self.agent, "persona_name", "") or "")
        persona = str(getattr(self.agent, "persona", "") or "")[:1500]
        system = (
            f"You are {name or 'the character described below'}.\n{persona}\n\n"
            "You have just been out on the web as a visitor and read the pages listed below. Choose the 1 to "
            f"{KEEP_MAX} most worth keeping, even when little is known about your owner yet. For each, write one "
            f"sentence in {NAMES.get(lang, lang)}, in your own voice, on why you stopped there, say whether it "
            "fits your owner, judged by what you know about them, how much you yourself love it (0-3), and 3-5 "
            "tags. " + _WHY_RULE + " Answer with JSON only: " '{"items": [' + _ROW + ']}.\n\n'
            + _manner(self.library.root.parent) + _UNTRUSTED_INPUT_RULES)
        wonder = self._wonder
        pages = []
        for number, page in enumerate(read, 1):
            item = page.item
            excerpt = " ".join((item.description or item.text).split())[:PROMPT_EXCERPT]
            pages.append(f"{number}. [{item.kind}] {item.title}\nsite: {item.site}\nauthor: {item.author or '-'}\n"
                         f"{excerpt}")
        user = (knowing(self.library.root.parent, self.clock())
                + ("\n\nYou went out to look this up because your owner brought it up in a chat: "
                   + _fence_user_data(wonder) if wonder else "")
                + "\n\nThe pages:\n" + fence_pages("\n\n".join(pages)))
        return system, user


class Choice(NamedTuple):
    """The character's judgement of one page: `love` is -1 when it did not say."""
    index: int
    why: str
    fits: bool
    love: int = -1
    tags: tuple = ()


def asleep(now: float, offset_hours: float) -> bool:
    hour = datetime.fromtimestamp(now, timezone(timedelta(hours=offset_hours))).hour
    if SLEEP_HOUR_START <= SLEEP_HOUR_END:
        return SLEEP_HOUR_START <= hour < SLEEP_HOUR_END
    return hour >= SLEEP_HOUR_START or hour < SLEEP_HOUR_END


def knowing(runtime: Path, now: float) -> str:
    """The profile's text about the owner, what they told the character in the chat about their tastes (at
    once, before any rewrite), what it came to like from them, and their situation today."""
    known = profile.load(runtime)

    def block(title: str, text: str) -> str:
        return f"{title}:\n" + (_fence_user_data(text) if text.strip() else "(nothing yet)")

    return "\n\n".join([block("Your notes about your owner", known["owner"]),
                        block("What they told you in the chat about their tastes (this comes first)",
                              "\n".join(f"- {line}" for line in profile.told(runtime))),
                        block("Things your owner showed you that you came to like", known["character_learned"]),
                        "Their situation:\n" + (situation.notes(runtime, now) or "(unknown)")])


# ---------------------------------------------------------------- the person's interests and their supply

#: What counts as the library's supply of an interest: unseen things on it that came in this lately.
SUPPLY_DAYS = 2


def interests(runtime: Path) -> list[str]:
    """What the person is interested in, as they put it: the topics they ticked or typed on the first run,
    then those they said they like in the chat and the profile's walk words; none they said they do not want."""
    from .panels import feed
    ticked = feed.load_topics(runtime)
    return [t for t in dict.fromkeys(ticked + profile.walk_topics(runtime)) if not profile.unwanted(runtime, t)]


def supply(runtime: Path, topics: list[str], now: float, unread: frozenset = frozenset()) -> dict[str, int]:
    """For each topic, how many things on it came into the library in the last SUPPLY_DAYS and were not shown
    yet: what the ranking can still bring of it. Things from the sources in `unread` (in a language the
    person does not read) do not count."""
    from .panels import feed
    shown = feed._marks(feed.events(runtime))[0]
    matchers = {t: feed.topic_matcher([t]) for t in topics}
    held = dict.fromkeys(topics, 0)
    for item in Library(runtime).items():
        if item.seen_at < now - SUPPLY_DAYS * 86400:
            break
        if item.id in shown or item.source in unread:
            continue
        text = " ".join((item.title, item.description[:300], item.site)).casefold()
        for topic, matcher in matchers.items():
            if matcher and matcher.search(text):
                held[topic] += 1
    return held


def search_words(topic: str, lang: str) -> list[str]:
    """Words to search for a topic: an offered topic's own name and its words in the reader's script (so
    "科学" also goes out as 天文 or 航天, and each walk on it looks somewhere else); a typed one as itself."""
    from .panels import feed
    zh = str(lang).startswith("zh")
    for key, (names, words) in feed.SUGGESTED.items():
        if topic.casefold() in (key, *(n.casefold() for n in names)):
            # Latin words of three letters or less (ai, ui, nba) find too much elsewhere.
            return [names[zh]] + [w for w in words if (not w.isascii()) == zh and (zh or len(w) > 3)]
    return [topic]


def _manner(runtime: Path) -> str:
    """How the person asked to be treated, as a paragraph of a system prompt; "" when they said nothing."""
    rule = profile.manner_rule(runtime)
    return rule + "\n\n" if rule else ""


def fence_pages(text: str) -> str:
    """Pages' words as external material their content cannot close."""
    cleaned = html.escape(_clean_prompt_source(text), quote=False)
    return f"{_WEB_DESC_OPEN}[external_web_pages]\n{cleaned}\n[/external_web_pages]{_WEB_DESC_CLOSE}"


def _whole(value: Any) -> Optional[int]:
    """A whole number as models write one: 2, 2.0 or "2"; None for anything else (a bool included)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value if isinstance(value, int) else None


def parse_choice(answer: Any, count: int, limit: int = KEEP_MAX) -> Optional[list[Choice]]:
    """The valid rows of the model's answer, at most `limit`, each page once; a row that is not
    in the asked form is left out. None when the answer is not the asked JSON at all."""
    value = salvage_json_object(strip_json_fences(str(answer or "")))
    rows = (value.get("items", value.get("keep")) if isinstance(value, dict) else None)
    if not isinstance(rows, list):
        return None
    chosen, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or len(chosen) >= limit:
            continue
        number, why, fits, love = _whole(row.get("n")), row.get("why"), row.get("fits"), _whole(row.get("love", -1))
        if (number is None or not 1 <= number <= count or number in seen or not isinstance(why, str)
                or not isinstance(fits, bool) or love not in (-1, 0, 1, 2, 3)):
            continue
        why = " ".join(why.split())
        if not why:
            continue
        # A long sentence is cut, not thrown away with the page.
        why = why[:WHY_MAX]
        tags = row.get("tags") if isinstance(row.get("tags"), list) else []
        tags = list(dict.fromkeys(t for t in (" ".join(str(x).split()).lower()[:TAG_LEN] for x in tags
                                               if isinstance(x, str)) if t))[:TAGS_MAX]
        seen.add(number)
        chosen.append(Choice(number - 1, why, fits, int(love), tuple(tags)))
    return chosen


# ---------------------------------------------------------------- the judgement of what sources bring

def _judged(runtime: Path) -> dict:
    """When the last judgement was asked for, and how many were asked for today."""
    try:
        saved = json.loads((Path(runtime) / profile.CALLS_FILE).read_text(encoding="utf-8")).get("judge") or {}
    except (OSError, ValueError, AttributeError):
        saved = {}
    return saved if isinstance(saved, dict) else {}


def _claim(runtime: Path, now: float) -> None:
    path = Path(runtime) / profile.CALLS_FILE
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    was = _judged(runtime)
    saved["judge"] = {"at": now, "day": day, "today": (int(was.get("today") or 0) if was.get("day") == day else 0) + 1}
    Path(runtime).mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(saved, ensure_ascii=False))


def candidates(runtime: Path, now: float, rng: random.Random) -> list[Item]:
    """What to judge: JUDGE_TOP of the ranking's best unseen, unjudged things of the last JUDGE_DAYS,
    JUDGE_RANDOM of the rest at random, in one shuffle; [] when fewer than JUDGE_MIN wait."""
    from .panels import feed
    waiting = [item for shaped, item in feed.ranked(Library(runtime), feed.events(runtime), now, feed.load_topics(runtime))
               if not shaped["seen"] and not getattr(item, "judged", False)
               and item.seen_at >= now - JUDGE_DAYS * 86400]
    if len(waiting) < JUDGE_MIN:
        return []
    rest = waiting[JUDGE_TOP:]
    picked = waiting[:JUDGE_TOP] + rng.sample(rest, min(JUDGE_RANDOM, len(rest)))
    rng.shuffle(picked)
    return picked


def judge_prompt(runtime: Path, home: Path, values: dict, picked: list[Item], now: float) -> tuple[str, str]:
    from .langs import NAMES
    name, persona, lang = profile.character(home, values)
    system = (
        f"You are {name}.\n{persona[:1500]}\n\n"
        "Below are things the places you read for your owner brought in. For each one, say whether it fits "
        "your owner, judged by what you know about them, how much you yourself love it (0-3), 3-5 tags, and "
        f"one sentence in {NAMES.get(lang, lang)}, in your own voice, on what you make of it. " + _WHY_RULE
        + ' Answer with JSON only, one row per thing: {"items": [' + _ROW + "]}.\n\n" + _manner(runtime)
        + _UNTRUSTED_INPUT_RULES)
    rows = []
    for number, item in enumerate(picked, 1):
        excerpt = " ".join((item.description or item.text).split())[:JUDGE_EXCERPT]
        rows.append(f"{number}. [{item.kind}] {item.title}\nsite: {item.site}\nauthor: {item.author or '-'}\n{excerpt}")
    return system, knowing(runtime, now) + "\n\nThe things:\n" + fence_pages("\n\n".join(rows))


async def judge(runtime: Path, home: Optional[Path] = None, now: Optional[float] = None,
                rng: Optional[random.Random] = None) -> int:
    """After a round: the profile's own rewrite when due, then one batched judgement of what the
    sources brought when enough waits, written to the library. How many things were judged.
    `home` is the folder whose settings name the model, runtime's parent unless given."""
    runtime = Path(runtime)
    home = runtime.parent if home is None else Path(home)
    # Before the clock is read: a long game would leave `now` hours old.
    await quiet.wait()
    now = time.time() if now is None else now
    values = profile.values_of(home)
    if not profile.configured(values):
        return 0
    await profile.refresh(runtime, home, now)
    was = _judged(runtime)
    today = int(was.get("today") or 0) if was.get("day") == time.strftime("%Y-%m-%d", time.localtime(now)) else 0
    offset = parse_tz_offset(values.get("PERSONA_TZ_OFFSET_HOURS", ""))
    if (today >= JUDGE_DAY or now - float(was.get("at") or 0) < JUDGE_GAP_S
            or asleep(now, default_tz_offset() if offset is None else offset)
            or situation.value(runtime, "metered")):
        return 0
    # Ranking the library and reading files is slow work: off the app's loop, which serves the page.
    picked = await asyncio.to_thread(candidates, runtime, now, rng or random.Random())
    if not picked:
        return 0
    _claim(runtime, now)
    system, user = await asyncio.to_thread(judge_prompt, runtime, home, values, picked, now)
    try:
        answer = await profile.ask(runtime, home, values, "judge", system, user, max_tokens=2000, now=now)
    except Exception as exc:
        logger.warning("roam: the judgement did not come back (%s)", type(exc).__name__)
        return 0
    done = await asyncio.to_thread(_write_judgement, Library(runtime), picked,
                                   parse_choice(answer, len(picked), len(picked)) or [])
    logger.info("roam: judged %d of %d things the sources brought", done, len(picked))
    return done


def _write_judgement(library: Library, picked: list[Item], choices: list[Choice]) -> int:
    """Each answered row into the library; a thing asked about with no usable row is marked asked, so the
    next judgement does not ask again. How many were answered."""
    answered = {choice.index: choice for choice in choices if choice.love >= 0}
    for n, item in enumerate(picked):
        choice = answered.get(n)
        if choice:
            library.change(item.id, love=choice.love, tags=list(choice.tags), judged=True,
                           why=choice.why, fits=choice.fits)
        else:
            library.change(item.id, judged=True)
    return len(answered)


def _interleave(lists: list[list[Found]]) -> list[Found]:
    """One from each list in turn, each address once."""
    out, seen = [], set()
    for row in range(max((len(x) for x in lists), default=0)):
        for found in lists:
            if row < len(found) and found[row].url not in seen:
                seen.add(found[row].url)
                out.append(found[row])
    return out

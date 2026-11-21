"""What Mibo knows about each host, kept as data it writes, checks and repairs itself.

One card per registrable host in `<runtime>/sites.json`, over the seed the
app ships (seeds.json: addresses only). A card says where the host's hot
lists are, its search address with {q} for the words, the shape of its item
links, the walls met (per mode and address shape, with a back-off), the
addresses found dead, its robots.txt directives and how well it has served.
Cards hold addresses, shapes, directives and numbers, never words copied from
a page.

The ladder that learns a host is the standards a site declares, read in the
signed-out tab with no model call: for a search the OpenSearch link, a
WebSite SearchAction template or a searchable form; for a hot list its
declared RSS or Atom feed, links named like a hot list, the home page itself,
then the usual feed paths. A rendered hot list whose page declares a feed is
read by that feed from then on. Every use is checked (same host, no wall,
enough rows shaped like items, the words seen for a search); a failed check
reruns the ladder, and after three in a row the host rests for a week.

The visitor names itself with TOKEN on its user agent, and robots.txt is read
with Protego for the group of AGENT before the one for every robot.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

from protego import Protego

from .. import __version__, situation
from ..storage import atomic_write_text
from . import read as reading
from .tabs import Tab, TabError

logger = logging.getLogger("mibo.web")

FILE = "sites.json"
SEEDS: dict = json.loads((Path(__file__).with_name("seeds.json")).read_text(encoding="utf-8"))["hosts"]
# A check passes with this many rows shaped like items; a hot list is kept with HOT_ROWS.
MIN_ROWS = 5
HOT_ROWS = 10
# The share of a list's links one shape must cover to be the items' shape.
SHAPE_SHARE = 0.6
# Failed checks in a row before a host rests, and how long it rests.
STALE_AFTER = 3
STALE_S = 7 * 86400
ROBOTS_S = 7 * 86400
# How long a wall keeps a path from being loaded again: checks 1, 3 then 7 days, never more.
DAY = 86400
BACK_OFF = {"verification": (DAY, 3 * DAY, 7 * DAY), "sign_in": (7 * DAY,), "sign_in_overlay": (DAY,),
            "error": (situation.RETRY_S,), "empty": (situation.RETRY_S,)}
EVIDENCE_MAX = 80
# Addresses found dead a card remembers, newest kept.
DEAD_MAX = 100
# A hot list's rows dated longer ago than this are left out (pinned posts, archives).
HORIZON_S = 14 * DAY
# How the visitor names itself: the robots.txt group it reads first, and the token on its user agent.
AGENT = "Mibo"
TOKEN = f"Mibo/{__version__} (+https://github.com/wangkant/mibo)"
# The robots.txt lines kept on a card: its groups and rules, at most this many.
ROBOTS_LINES = 400
_ROBOTS_KEYS = ("user-agent", "allow", "disallow")
_lock = threading.Lock()
# An address segment that names one thing: a run of digits, or a long code with digits in it.
_ID = re.compile(r"\d{3,}|(?=[^/]*\d)[A-Za-z0-9_.~%-]{8,}")
_XML = "() => new XMLSerializer().serializeToString(document)"
_TEXT = "() => document.body ? document.body.innerText : ''"
# A row's time that says how long ago (3天前, 2 years ago) or names a day (2026-06-08, 2026年6月8日).
_AGO = re.compile(r"(\d+)\s*(年|个月|個月|周|週|天|日|years?|months?|weeks?|days?)\s*(前|ago)", re.I)
_AGO_DAYS = {"年": 365, "year": 365, "个月": 30, "個月": 30, "month": 30, "周": 7, "週": 7, "week": 7,
             "天": 1, "日": 1, "day": 1}
_DAY = re.compile(r"(20\d\d)[-/.年](\d{1,2})[-/.月](\d{1,2})")


# ---------------------------------------------------------------- the store

def key_of(address: str) -> str:
    """The card's key: the registrable host of an address or host name."""
    return situation.site_of(address)


def _path(runtime: Path) -> Path:
    return Path(runtime) / FILE


def load(runtime: Path) -> dict:
    try:
        saved = json.loads(_path(runtime).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def _change(runtime: Path, key: str, edit: Callable[[dict], None]) -> dict:
    """Read, change and write one card as one step; the card as written."""
    with _lock:
        cards = load(runtime)
        card = cards.setdefault(key, {})
        edit(card)
        Path(runtime).mkdir(parents=True, exist_ok=True)
        atomic_write_text(_path(runtime), json.dumps(cards, ensure_ascii=False, indent=1))
        return card


def seed(key: str) -> dict:
    return SEEDS.get(key) or {}


def _kind(address_: str) -> str:
    """A seeded address is read as a feed when it looks like one (a tag's RSS, a /feed path), else as a page."""
    path = urlsplit(address_).path
    return "feed" if "/feed" in path or path.endswith((".xml", ".rss", "/rss")) else "page"


def card(runtime: Path, address: str) -> dict:
    """The host's card: what was learned over its seed. `home`, `hot` [{url, how, kind}], `search`
    {template, how}, `shape`, `walls` {mode: {where: {...}}}, `dead` {address: when}, `policy`, `health`."""
    key = key_of(address)
    sown = seed(key)
    stored = load(runtime).get(key) or {}
    host = urlsplit(address).hostname if "://" in address else address
    home = sown.get("home") or f"https://{host or key}/"
    hot = [{"url": u, "how": "seed", "kind": _kind(u)} for u in sown.get("hot") or []]
    search = {"template": sown["search"], "how": "seed", "kind": _kind(sown["search"])} if sown.get("search") else {}
    return {"key": key, "name": sown.get("name", ""), "lang": sown.get("lang", ""), "region": sown.get("region", ""),
            # The person's word on walks to it (`steer`: "avoid" or "favour") over the seed's.
            "walk": sown.get("walk", True) and stored.get("steer") != "avoid",
            "favour": stored.get("steer") == "favour", "home": stored.get("home") or home,
            "hot": stored.get("hot") or hot, "search": stored.get("search") or search,
            "shape": stored.get("shape", ""), "walls": stored.get("walls") or {}, "dead": stored.get("dead") or {},
            "policy": stored.get("policy") or {}, "health": stored.get("health") or {}}


def plant(runtime: Path, key: str, entries: dict) -> None:
    """A card written as given ({home, hot: [addresses], search: template}), as a seed would be."""
    def edit(stored: dict) -> None:
        stored["home"] = entries["home"]
        stored["hot"] = [{"url": u, "how": "seed", "kind": "page"} for u in entries.get("hot") or []]
        stored["search"] = {"template": entries["search"], "how": "seed"} if entries.get("search") else {}
    _change(runtime, key, edit)


def steer(runtime: Path, address: str, how: str) -> None:
    """The person's word on walks to the host: "avoid", "favour", or "" for none."""
    def edit(stored: dict) -> None:
        if how:
            stored["steer"] = how
        else:
            stored.pop("steer", None)
    _change(runtime, key_of(address), edit)


def steered(runtime: Path) -> dict[str, str]:
    """{host key: "avoid" | "favour"} for every host the person gave their word on."""
    return {key: c["steer"] for key, c in load(runtime).items() if isinstance(c, dict) and c.get("steer")}


def forget(runtime: Path, address: str) -> None:
    """The card of the host goes; the next use starts from its seed again."""
    with _lock:
        cards = load(runtime)
        if cards.pop(key_of(address), None) is not None:
            atomic_write_text(_path(runtime), json.dumps(cards, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- shapes

def _is_id(segment: str) -> bool:
    return bool(_ID.fullmatch(segment))


def shapes(url: str) -> list[str]:
    """An address's path shapes from the most exact to the loosest: one-thing segments as {id}; then every
    other segment but the first as *; then all of them. A path with no {id} keeps its first query key."""
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    ids = [_is_id(s) for s in segments]
    query = ""
    if not any(ids) and parts.query:
        first = parse_qsl(parts.query, keep_blank_values=True)
        query = f"?{first[0][0]}" if first else ""
    exact = ["{id}" if i else s for s, i in zip(segments, ids)]
    loose = ["{id}" if i else (s if n == 0 else "*") for n, (s, i) in enumerate(zip(segments, ids))]
    loosest = ["{id}" if i else "*" for i in ids]
    return list(dict.fromkeys("/" + "/".join(p) + query for p in (exact, loose, loosest)))


def matches(url: str, shape: str) -> bool:
    """Whether `url`'s path has `shape`."""
    if not shape:
        return False
    want, _, key = shape.partition("?")
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    wanted = [s for s in want.split("/") if s]
    if len(segments) != len(wanted):
        return False
    if key and key not in dict(parse_qsl(parts.query, keep_blank_values=True)):
        return False
    return all(w == "*" or (w == "{id}" and _is_id(s)) or w == s for s, w in zip(segments, wanted))


def item_shape(links: list[str], key: str) -> str:
    """The shape of most of a list's links on the host: the most exact one covering SHAPE_SHARE of them."""
    own = [u for u in links if key_of(u) == key]
    if len(own) < MIN_ROWS:
        return ""
    for level in range(3):
        counts: dict[str, int] = {}
        for u in own:
            found = shapes(u)
            counts[found[min(level, len(found) - 1)]] = counts.get(found[min(level, len(found) - 1)], 0) + 1
        shape, n = max(counts.items(), key=lambda kv: kv[1])
        if n >= SHAPE_SHARE * len(own) and shape not in ("/", ""):
            return shape
    return ""


def where(url: str) -> str:
    """Where a wall stands: the host name and the address's exact shape."""
    parts = urlsplit(url)
    return (parts.hostname or "") + shapes(url)[0]


# ---------------------------------------------------------------- walls and robots

def held(runtime: Path, url: str, mode: str, now: Optional[float] = None) -> str:
    """"dead" for an address found dead, else the wall still in its back-off at this address for `mode`
    ("signed_out" or "pane"); "" for none."""
    now = time.time() if now is None else now
    found = card(runtime, url)
    if url in found["dead"]:
        return "dead"
    wall = (found["walls"].get(mode) or {}).get(where(url))
    if not wall:
        return ""
    steps = BACK_OFF.get(wall["kind"], (situation.RETRY_S,))
    return wall["kind"] if now - wall["last_at"] < steps[min(wall["count"], len(steps)) - 1] else ""


def saw(runtime: Path, page: reading.Page, mode: str, now: Optional[float] = None, asked: str = "") -> None:
    """What reading a page showed, into its host's card: a dead page noted for that address alone, a wall
    noted with its back-off at the address's shape, a page read without one clears the wall noted there. A
    remembered wall or robots' no is not news. The wall is noted at the `asked` address (where a check or
    sign-in page sent it does not matter): that is what is held."""
    if page.evidence in ("remembered", "robots.txt"):
        return
    now = time.time() if now is None else now
    address_ = asked or page.url
    if page.wall == "dead":
        def bury(stored: dict) -> None:
            dead = stored.setdefault("dead", {})
            dead[address_] = int(now)
            for old in sorted(dead, key=dead.get)[:-DEAD_MAX]:
                del dead[old]
        _change(runtime, key_of(address_), bury)
        return
    spot = where(address_)
    # A sign-in layer over a page whose rows still read is noted, but the rows are kept.
    wall = page.wall if page.wall in BACK_OFF and not (page.wall == "sign_in_overlay" and len(page.rows) >= MIN_ROWS) \
        else ""
    known = (card(runtime, address_)["walls"].get(mode) or {}).get(spot)
    if not wall and not known:
        return

    def edit(stored: dict) -> None:
        walls = stored.setdefault("walls", {}).setdefault(mode, {})
        if not wall:
            walls.pop(spot, None)
            return
        was = walls.get(spot) or {"first_at": now, "count": 0}
        walls[spot] = {"kind": wall, "evidence": page.evidence[:EVIDENCE_MAX], "first_at": was["first_at"],
                       "last_at": now, "count": was["count"] + 1 if was.get("kind") == wall else 1}
    _change(runtime, key_of(address_), edit)
    if wall:
        logger.info("web: %s shows a %s wall (%s)", urlsplit(address_).hostname, wall, page.evidence[:40])


def robots_lines(text: str) -> list[str]:
    """A robots.txt's groups and rules as directive lines, comments and other fields left out."""
    lines = []
    for raw in text.splitlines():
        name, colon, value = raw.split("#", 1)[0].partition(":")
        if colon and name.strip().lower() in _ROBOTS_KEYS:
            lines.append(f"{name.strip()}: {value.strip()}")
    return lines[:ROBOTS_LINES]


def robots_says(text: str, url: str) -> bool:
    """Whether a robots.txt lets Mibo read `url`: its own group when it has one, else the one for all."""
    return Protego.parse(text).can_fetch(url, AGENT)


async def robots_allow(runtime: Path, tab: Tab, url: str, now: Optional[float] = None) -> bool:
    """Whether robots.txt lets Mibo read `url`; read again once a week, in the tab. A site that cannot answer
    (5xx) keeps every robot out for now; one with no robots.txt keeps none out."""
    now = time.time() if now is None else now
    parts = urlsplit(url)
    host = parts.hostname or ""
    known = (card(runtime, url)["policy"].get("robots_txt") or {}).get(host)
    if not known or now - known["at"] >= ROBOTS_S:
        lines: list = []
        try:
            answer = await tab.goto(urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", "")))
            if answer.status >= 500:
                lines = ["User-agent: *", "Disallow: /"]
            elif 200 <= answer.status < 300 and "text/plain" in answer.kind:
                lines = robots_lines(str(await tab.evaluate(_TEXT) or ""))
        except TabError:
            return True
        known = {"at": now, "lines": lines}

        def edit(stored: dict) -> None:
            stored.setdefault("policy", {}).setdefault("robots_txt", {})[host] = known
        _change(runtime, key_of(url), edit)
    allowed = robots_says("\n".join(known["lines"]), url)
    if not allowed:
        logger.info("web: robots.txt keeps Mibo off %s", host)
    return allowed


def when(text: str) -> Optional[float]:
    """The time a row's time says, as epoch seconds (a date, or how long ago); None when it says neither."""
    text = str(text or "").strip()
    ago = _AGO.search(text)
    if ago:
        return time.time() - int(ago.group(1)) * _AGO_DAYS.get(ago.group(2).lower().rstrip("s"), 0) * DAY
    for parse in (lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")), parsedate_to_datetime):
        try:
            at = parse(text)
        except (ValueError, TypeError, IndexError):
            continue
        return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).timestamp()
    day = _DAY.search(text)
    if day:
        try:
            return datetime(*map(int, day.groups()), tzinfo=timezone.utc).timestamp()
        except ValueError:
            return None
    return None


def fresh(row: dict, now: Optional[float] = None) -> bool:
    """Whether a list row is newer than HORIZON_S, or not dated at all."""
    at = when(row.get("time") or "")
    return at is None or (time.time() if now is None else now) - at <= HORIZON_S


# ---------------------------------------------------------------- using a card

def address(entry: dict, words: str = "") -> str:
    """A hot entry's address, or a search template with `words` in it."""
    if "template" in entry:
        return entry["template"].replace("{q}", quote(words))
    return entry.get("url", "")


def rested(runtime: Path, address_: str, now: Optional[float] = None) -> bool:
    """A host whose checks failed STALE_AFTER times in a row rests for STALE_S."""
    now = time.time() if now is None else now
    health = card(runtime, address_)["health"]
    return bool(health.get("stale")) and now - health.get("stale_at", 0) < STALE_S


def check(runtime: Path, key: str, intent: str, page: reading.Page, words: str = "") -> bool:
    """One use checked and counted: same host after redirects, no wall, MIN_ROWS rows shaped like the
    host's items (any rows while no shape is known), and for a search the words seen."""
    shape = card(runtime, key)["shape"]
    rows = [r for r in page.rows if str(r.get("link") or "").startswith("http")]
    shaped = [r for r in rows if matches(r["link"], shape)] if shape else rows
    said = not words or words.lower() in unquote(page.url).lower() or any(
        words.lower() in str(r.get("title") or "").lower() for r in page.rows)
    ok = key_of(page.url) == key and page.wall in ("", "sign_in_overlay") and len(shaped) >= MIN_ROWS and said
    now = int(time.time())

    def edit(stored: dict) -> None:
        health = stored.setdefault("health", {"ok": 0, "fails": 0})
        if ok:
            health.update(ok=health.get("ok", 0) + 1, fails=0, last_ok=now, stale=False)
            if not stored.get("shape"):
                stored["shape"] = item_shape([r["link"] for r in rows], key)
        elif page.wall in ("", "dead"):
            # A wall is the site's choice, not a broken card: it has its own back-off.
            health["fails"] = health.get("fails", 0) + 1
            if health["fails"] >= STALE_AFTER:
                health.update(stale=True, stale_at=now)
    _change(runtime, key, edit)
    return ok


async def read_entry(runtime: Path, tab: Tab, entry: dict, words: str = "", *, screens: int = reading.WALK_SCREENS,
                     background: bool = True) -> reading.Page:
    """A hot list or a search read: a page through reader.js, a feed as its entries."""
    url = address(entry, words)
    if entry.get("kind") != "feed":
        return await reading.read(tab, url, screens=screens, runtime=runtime, background=background)
    if background and not await robots_allow(runtime, tab, url):
        return reading.Page(url, wall="robots", evidence="robots.txt")
    try:
        answer = await tab.goto(url)
        text = await tab.evaluate(_XML) if answer.status < 400 else ""
    except Exception as exc:
        return reading.Page(url, wall="error", evidence=" ".join(str(exc).split())[:EVIDENCE_MAX])
    from ..sources.base import parse_feed
    try:
        # The tab has decoded the document already, whatever encoding it declares.
        finds = await asyncio.to_thread(parse_feed, str(text or "").encode(), url,
                                        content_type="application/xml; charset=utf-8")
    except ValueError:
        return reading.Page(tab.url, wall="error", evidence="not a feed")
    rows = [{"link": f.item.url, "title": f.item.title, "author": f.item.author, "time": f.item.published,
             "text": f.item.description[:300], "picture": f.picture} for f in finds]
    return reading.Page(tab.url, kind="list", rows=rows, data={"kind": "list", "rows": rows})


async def list_of(runtime: Path, tab: Tab, address_: str, intent: str, words: str = "", *,
                  screens: int = reading.WALK_SCREENS) -> reading.Page:
    """A host's hot list ("hot") or its search for `words` ("search"), read as a background visitor through
    its card, learned first when the card has no entry, checked, and repaired once when the check fails. A
    rendered hot list whose page declares a feed is read by the feed, and a hot list keeps no row older
    than HORIZON_S."""
    key = key_of(address_)
    entry = _entry(runtime, key, intent)
    if entry is None:
        entry = await learn(runtime, tab, key, intent, words)
        if entry is None:
            # Nothing learned counts as a failed use: after STALE_AFTER in a row the host rests.
            check(runtime, key, intent, reading.Page(card(runtime, key)["home"]))
            return reading.Page(card(runtime, key)["home"], wall="unknown", evidence="no entry")
    page = await read_entry(runtime, tab, entry, words, screens=screens)
    if intent == "hot" and entry.get("kind") != "feed" and not page.wall:
        page = await _feed_first(runtime, tab, key, entry, page) or page
    said = words if intent == "search" else ""
    if not check(runtime, key, intent, page, said) and page.wall in ("", "dead"):
        logger.info("web: the %s entry of %s did not check out; learning it again", intent, key)
        entry = await learn(runtime, tab, key, intent, words, failed=address(entry, words))
        if entry is not None:
            page = await read_entry(runtime, tab, entry, words, screens=screens)
            check(runtime, key, intent, page, said)
    if intent == "hot":
        page.rows = [row for row in page.rows if fresh(row)]
    return page


def _feeds(page: reading.Page, key: str) -> list[str]:
    """The feeds a page declares on its own host, a comments feed left out."""
    found = page.data.get("discovery") if isinstance(page.data.get("discovery"), dict) else {}
    return [u for u in found.get("feeds") or [] if key_of(u) == key and "comment" not in u.lower()][:2]


async def _feed_first(runtime: Path, tab: Tab, key: str, entry: dict, page: reading.Page) -> Optional[reading.Page]:
    """The feed a hot list's page declares, read in its place and put in its place on the card; None when the
    page declares none that reads."""
    for url in _feeds(page, key):
        fed = await read_entry(runtime, tab, {"url": url, "kind": "feed"}, screens=0)
        if fed.wall or len([r for r in fed.rows if r.get("link")]) < MIN_ROWS:
            continue

        def edit(stored: dict, url=url) -> None:
            hot = stored.get("hot") or card(runtime, key)["hot"]
            stored["hot"] = [{"url": url, "how": "rss", "kind": "feed"} if h.get("url") == entry.get("url") else h
                             for h in hot]
        _change(runtime, key, edit)
        logger.info("web: %s declares a feed; its hot list is read from it", key)
        return fed
    return None


def _entry(runtime: Path, key: str, intent: str) -> Optional[dict]:
    found = card(runtime, key)
    if intent == "search":
        return found["search"] or None
    hot = found["hot"]
    return random.choice(hot[:3]) if hot else None


# ---------------------------------------------------------------- the ladder

async def learn(runtime: Path, tab: Tab, key: str, intent: str, words: str = "", *,
                failed: str = "") -> Optional[dict]:
    """Where `intent` lives on the host, learned from what its home page declares (a search checked with
    `words`); the entry written to the card, or None. Never on a host whose home shows a wall."""
    home = card(runtime, key)["home"]
    page = await reading.read(tab, home, runtime=runtime, background=True)
    if page.wall and page.wall != "sign_in_overlay":
        return None
    found = page.data.get("discovery") if isinstance(page.data.get("discovery"), dict) else {}
    if intent == "search":
        entry = await _search_rungs(runtime, tab, key, found, words)
    else:
        entry = await _hot_rungs(runtime, tab, key, page, found, failed)
    if entry is None:
        return None
    entry["ok_at"] = int(time.time())

    def edit(stored: dict) -> None:
        if intent == "search":
            stored["search"] = entry
        else:
            stored["hot"] = [entry] + [h for h in stored.get("hot") or [] if h.get("url") != entry["url"]][:2]
        stored.setdefault("health", {}).update(fails=0, stale=False)
    _change(runtime, key, edit)
    logger.info("web: learned the %s entry of %s (%s)", intent, key, entry["how"])
    return entry


def _template(raw: str, base: str) -> str:
    """A search address with {q} for the words: OpenSearch's {searchTerms} or a SearchAction's named
    placeholder becomes {q}, optional parameters ({name?}) are dropped."""
    url = urlsplit(raw if "://" in raw else urlunsplit(urlsplit(base)[:2] + ("", "", "")) + raw)
    query = [(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True) if not re.fullmatch(r"\{[^}]*\?\}", v)]
    path = re.sub(r"\{(searchTerms|search_term_string|search_term|query|q)\}", "{q}", url.path)
    query = [(k, re.sub(r"\{(searchTerms|search_term_string|search_term|query|q)\}", "{q}", v)) for k, v in query]
    text = urlunsplit((url.scheme, url.netloc, path, urlencode(query, safe="{}"), ""))
    return text if "{q}" in text and not re.search(r"\{(?!q\})", text) else ""


async def _try_search(runtime: Path, tab: Tab, key: str, template: str, words: str, how: str) -> Optional[dict]:
    """A search template kept when the search for `words` stays on the host, shows no wall and lists something."""
    if not template or key_of(template) != key:
        return None
    entry = {"template": template, "how": how}
    page = await reading.read(tab, address(entry, words), runtime=runtime, background=True)
    return entry if not page.wall and key_of(page.url) == key and any(r.get("link") for r in page.rows) else None


async def _search_rungs(runtime: Path, tab: Tab, key: str, found: dict, words: str) -> Optional[dict]:
    base = tab.url
    for link in (found.get("opensearch") or [])[:2]:
        try:
            await tab.goto(link)
            root = ElementTree.fromstring(str(await tab.evaluate(_XML) or "").encode("utf-8"))
        except (TabError, ElementTree.ParseError):
            continue
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] == "Url" and "html" in (node.get("type") or "") and node.get("template"):
                entry = await _try_search(runtime, tab, key, _template(node.get("template"), link), words, "opensearch")
                if entry:
                    return entry
    for raw in (found.get("searchaction") or [])[:2]:
        entry = await _try_search(runtime, tab, key, _template(raw, base), words, "searchaction")
        if entry:
            return entry
    for form in (found.get("forms") or [])[:2]:
        query = urlencode([(form["input"], "{q}")] + [tuple(h) for h in form.get("hidden") or []], safe="{}")
        entry = await _try_search(runtime, tab, key, _template(form["action"].split("?")[0] + "?" + query, base),
                                  words, "form")
        if entry:
            return entry
    return None


async def _hot_rungs(runtime: Path, tab: Tab, key: str, home: reading.Page, found: dict,
                     failed: str) -> Optional[dict]:
    for url in _feeds(home, key):
        if url == failed:
            continue
        entry = await _try_hot(runtime, tab, key, {"url": url, "how": "rss", "kind": "feed"})
        if entry:
            return entry
    for url in [t["href"] for t in found.get("trends") or []][:4]:
        if url == failed or key_of(url) != key:
            continue
        entry = await _try_hot(runtime, tab, key, {"url": url, "how": "trend_link", "kind": "page"})
        if entry:
            return entry
    # A home page that is itself a long list is a hot list.
    rows = [r for r in home.rows if r.get("link")]
    if home.url != failed and home.kind == "list" and len(rows) >= HOT_ROWS:
        return {"url": home.url, "how": "home", "kind": "page", "rows": len(rows),
                "shape": item_shape([r["link"] for r in rows], key)}
    origin = urlunsplit(urlsplit(home.url)[:2] + ("", "", ""))
    from ..sources.custom import USUAL_PATHS
    for url in [origin + path for path in USUAL_PATHS[:3]]:
        if url == failed:
            continue
        entry = await _try_hot(runtime, tab, key, {"url": url, "how": "rss", "kind": "feed"})
        if entry:
            return entry
    return None


async def _try_hot(runtime: Path, tab: Tab, key: str, entry: dict) -> Optional[dict]:
    page = await read_entry(runtime, tab, entry, screens=0)
    rows = [r for r in page.rows if r.get("link")]
    if page.wall or page.kind != "list" or len(rows) < HOT_ROWS:
        return None
    shape = item_shape([r["link"] for r in rows], key)
    if shape:
        def edit(stored: dict) -> None:
            stored["shape"] = shape
        _change(runtime, key, edit)
    return {**entry, "rows": len(rows)}


# ---------------------------------------------------------------- where walks may go

def walk_hosts(runtime: Path, intent: str, now: Optional[float] = None, keys: Optional[list] = None) -> list[dict]:
    """The cards a walk may read `intent` from now: seeded or learned hosts (or only `keys`) that walk, have
    that entry, are reachable, not resting, not behind a wall in their back-off and not kept off it by
    robots.txt."""
    now = time.time() if now is None else now
    out = []
    for key in keys or list(dict.fromkeys(list(SEEDS) + list(load(runtime)))):
        found = card(runtime, key)
        if not found["walk"] or rested(runtime, key, now) or not situation.walkable(runtime, key, now):
            continue
        entry = found["search"] if intent == "search" else (found["hot"][0] if found["hot"] else None)
        spot = address(entry, "x") if entry else found["home"]
        if held(runtime, spot, "signed_out", now) or held(runtime, found["home"], "signed_out", now):
            continue
        # A robots.txt read this week that keeps Mibo off the entry: no slot spent on it.
        known = (found["policy"].get("robots_txt") or {}).get(urlsplit(spot).hostname or "")
        if known and now - known["at"] < ROBOTS_S and not robots_says("\n".join(known["lines"]), spot):
            continue
        # A walk's list slot goes only to a host that has this entry: one with none would spend the slot
        # learning it (its home read and the rungs) and come back with nothing.
        if not entry:
            continue
        out.append(found)
    return out

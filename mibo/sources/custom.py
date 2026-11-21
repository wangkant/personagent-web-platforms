"""The person's own sources: any address they paste becomes a feed to follow.

`discover` turns what was typed (a site, a blog, a channel, a repository, a
profile, a podcast, a feed) into the feeds behind it, best first; the
catalogue keeps the ones the person picks and builds each with `source_for`.
`parse_opml` reads the list a feed reader exports, so a whole reader moves in
at once. Only public addresses are read, every hop of a redirect included.
"""
from __future__ import annotations

import asyncio
import html
import re
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import feedparser
import httpx

from ..ingestion import _PinnedAsyncHTTPTransport, _resolve_public_target
from ..roam import public_address, site_name, youtube_id
from .base import PER_SOURCE, Find, Source, feed_document, feed_finds, plain

# A pasted address can be a large download; a page or feed is far smaller.
BODY_MAX = 10_000_000
HOPS = 5
# How many of a page's own feed links are opened to check them.
ALTERNATES_MAX = 3
USUAL_PATHS = ("/feed", "/rss", "/rss.xml", "/atom.xml", "/index.xml", "/feed.xml")
_FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/rdf+xml", "application/xml", "text/xml")
_LINK = re.compile(r"<link\b[^>]*>", re.I)
_ATTR = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
_DECL = re.compile(r"^\s*<\?xml[^>]*\?>")
_YT = "https://www.youtube.com/feeds/videos.xml"
_YT_CHANNEL = re.compile(r"^/channel/(UC[\w-]{22})")
_YT_VIDEO_CHANNEL = re.compile(r'"videoDetails":\{[^{}]*?"channelId":"(UC[\w-]{22})"')
_YT_PAGE_CHANNEL = re.compile(r'"externalId":"(UC[\w-]{22})"')
_APPLE_PODCAST = re.compile(r"/id(\d+)")
_GITHUB_NOT_USERS = {"about", "explore", "features", "marketplace", "orgs", "settings", "sponsors", "topics",
                     "trending", "collections", "events", "login", "pricing", "search", "notifications"}


def _on(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


async def _read(http: httpx.AsyncClient, url: str) -> httpx.Response:
    """The whole answer of a public address, at the address it ended at. Each hop,
    redirects included, is resolved once and connected to that address, so neither
    a public page nor a name that answers 127.0.0.1 on the second lookup (DNS
    rebinding) can send the request into this network. `http` lends its timeouts and headers."""
    for _ in range(HOPS):
        target = await _resolve_public_target(url)
        if target is None:
            raise ValueError(f"not a public address: {url}")
        host, ip, _port = target
        async with httpx.AsyncClient(transport=_PinnedAsyncHTTPTransport(host, ip),
                                     timeout=http.timeout, headers=http.headers) as pinned, \
                pinned.stream("GET", url, follow_redirects=False) as answer:
            if answer.is_redirect:
                url = urljoin(url, answer.headers["location"])
                continue
            answer.raise_for_status()
            body = bytearray()
            async for chunk in answer.aiter_bytes():
                body += chunk
                if len(body) > BODY_MAX:
                    raise ValueError(f"too large to be a page or feed: {url}")
            # httpx decodes a page's text by the charset its header names, else as UTF-8.
            return httpx.Response(answer.status_code, content=bytes(body), request=answer.request,
                                  headers=[pair for pair in answer.headers.items() if pair[0] == "content-type"])
    raise ValueError(f"more than {HOPS} redirects: {url}")


def _as_feed(answer: httpx.Response) -> feedparser.FeedParserDict | None:
    """feedparser's reading of the answer when it is RSS or Atom, else None."""
    try:
        return feed_document(answer.content, str(answer.url), answer.headers.get("content-type", ""))
    except ValueError:
        return None


def _entry(url: str, final: str, doc: feedparser.FeedParserDict, title: str = "", name: str = "") -> dict:
    """The saved entry: the feed's own title, and the site its home page names, which names
    the site better than the feed's host does (feedburner, simplecast)."""
    own = plain(doc.feed.get("title", ""), 120)
    home = doc.feed.get("link", "")
    home = home if home.startswith(("http://", "https://")) else ""
    return {"url": url, "title": name or own or title or site_name(final), "site": site_name(home or final),
            "_final": final}


async def _feed(http: httpx.AsyncClient, url: str, *, title: str = "", name: str = "",
                need_entries: bool = False) -> dict | None:
    """The entry for `url` when it answers with a feed; a site that is down or
    refuses simply has no feed here. `name` wins over the feed's own title,
    `title` only stands in for a missing one."""
    try:
        answer = await _read(http, url)
    except (httpx.HTTPError, ValueError):
        return None
    doc = await asyncio.to_thread(_as_feed, answer)
    if doc is None or (need_entries and not doc.entries):
        return None
    return _entry(url, str(answer.url), doc, title, name)


def _alternates(page: str, base_url: str) -> list[tuple[str, str]]:
    """The page's own <link rel="alternate"> feeds as (address, title), comment feeds last."""
    found: list[tuple[str, str]] = []
    for tag in _LINK.findall(page):
        attrs = {m.group(1).lower(): html.unescape(m.group(2) or m.group(3) or m.group(4) or "")
                 for m in _ATTR.finditer(tag)}
        if "alternate" not in attrs.get("rel", "").lower().split():
            continue
        if attrs.get("type", "").lower().split(";")[0].strip() not in _FEED_TYPES or not attrs.get("href"):
            continue
        href = urljoin(base_url, attrs["href"].strip())
        if href.startswith(("http://", "https://")) and href not in (h for h, _ in found):
            found.append((href, attrs.get("title", "")))
    return sorted(found, key=lambda pair: "comment" in (pair[0] + pair[1]).lower())


async def _known(http: httpx.AsyncClient, url: str, page: str) -> list[dict]:
    """Feeds for the shapes of address whose feed sits somewhere a page does not say."""
    parts = urlsplit(url)
    host, path = (parts.hostname or "").lower(), parts.path
    bits = [bit for bit in path.split("/") if bit]
    candidates: list[tuple[str, dict]] = []
    if _on(host, "youtube.com") or _on(host, "youtu.be"):
        channel = _YT_CHANNEL.match(path)
        if "list=" in parts.query and path.startswith("/playlist"):
            playlist = parts.query.split("list=", 1)[1].split("&", 1)[0]
            candidates.append((f"{_YT}?playlist_id={playlist}", {}))
        elif channel:
            candidates.append((f"{_YT}?channel_id={channel.group(1)}", {}))
        else:
            # A video page names its channel; a handle's page names its own id.
            found = (_YT_VIDEO_CHANNEL if youtube_id(url) else _YT_PAGE_CHANNEL).search(page)
            if found:
                candidates.append((f"{_YT}?channel_id={found.group(1)}", {}))
            elif len(bits) == 2 and bits[0] == "user":
                candidates.append((f"{_YT}?user={bits[1]}", {}))
    elif host == "github.com" and bits and bits[0].lower() not in _GITHUB_NOT_USERS:
        if len(bits) >= 2:
            repo = f"https://github.com/{bits[0]}/{bits[1].removesuffix('.git')}"
            # A repository that never made a release still shows its work in its commits.
            candidates.append((f"{repo}/releases.atom", {"need_entries": True}))
            candidates.append((f"{repo}/commits.atom", {}))
        else:
            # The feed calls itself "GitHub Public Timeline Feed" whoever it follows.
            candidates.append((f"https://github.com/{bits[0]}.atom", {"name": f"{bits[0]} on GitHub"}))
    elif _on(host, "reddit.com") and len(bits) >= 2 and bits[0] == "r":
        candidates.append((f"https://www.reddit.com/r/{bits[1]}/.rss", {}))
    elif host == "medium.com" and bits:
        candidates.append((f"https://medium.com/feed/{bits[0]}", {}))
    elif _on(host, "medium.com") or _on(host, "substack.com"):
        candidates.append((f"https://{host}/feed", {}))
    elif host == "podcasts.apple.com" and _APPLE_PODCAST.search(path):
        # Apple's directory names the show's own feed.
        ident = _APPLE_PODCAST.search(path).group(1)
        try:
            answer = await _read(http, f"https://itunes.apple.com/lookup?id={ident}&entity=podcast")
            show = next((r for r in answer.json().get("results", []) if r.get("feedUrl")), None)
        except (httpx.HTTPError, ValueError):
            show = None
        if show:
            candidates.append((show["feedUrl"], {"title": show.get("collectionName", "")}))
    elif len(bits) == 1 and re.fullmatch(r"@[\w.]+", bits[0]):
        # Mastodon and its kin give every profile an .rss beside it.
        candidates.append((f"{parts.scheme}://{parts.netloc}/{bits[0]}.rss", {}))
    found = []
    for address, options in candidates:
        entry = await _feed(http, address, **options)
        if entry:
            found.append(entry)
            if address.endswith("/releases.atom"):
                break
    return found


def _address(typed: str) -> str:
    """What was typed as an http(s) address: https when it named no scheme."""
    typed = typed.strip()
    if typed.lower().startswith("feed:"):
        typed = typed[5:].lstrip("/")
    return typed if "://" in typed else "https://" + typed


async def discover(http: httpx.AsyncClient, address: str) -> list[dict]:
    """The feeds found for an address, best first: {"url", "title", "site"} each."""
    url = _address(address)
    if not await public_address(url):
        return []
    found: list[dict] = []
    seen: set[str] = set()

    def keep(entry: dict | None) -> None:
        if entry and entry["url"] not in seen and entry["_final"] not in seen:
            seen.update((entry["url"], entry.pop("_final")))
            found.append(entry)

    try:
        answer = await _read(http, url)
    except (httpx.HTTPError, ValueError):
        answer = None
    doc = await asyncio.to_thread(_as_feed, answer) if answer else None
    if doc is not None:
        entry = _entry(url, str(answer.url), doc)
        del entry["_final"]
        return [entry]
    final, page = (str(answer.url), answer.text) if answer else (url, "")
    for entry in await _known(http, final, page):
        keep(entry)
    for href, title in _alternates(page, final)[:ALTERNATES_MAX]:
        keep(await _feed(http, href, title=title))
    if not found:
        origin = f"{urlsplit(final).scheme}://{urlsplit(final).netloc}"
        for path in USUAL_PATHS:
            entry = await _feed(http, origin + path)
            if entry:
                keep(entry)
                break
    return found


def source_for(entry: dict) -> Source:
    """The source that follows one saved feed."""
    url, title = entry["url"], entry["title"]
    site = entry.get("site") or title

    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        return (await feed_finds(await _read(http, url), site=site))[:PER_SOURCE]

    return Source(entry["key"], (title, title), (f"What {title} posts, from an address you added.",
                                                 f"你添加的地址：{title} 的新内容。"), "own", fetch, "all", "")


def parse_opml(text: str) -> list[dict]:
    """The feeds an OPML file lists, folders flattened, each once."""
    root = ElementTree.fromstring(_DECL.sub("", text, count=1).encode("utf-8"))
    found: list[dict] = []
    for node in root.iter("outline"):
        url = (node.get("xmlUrl") or node.get("xmlurl") or "").strip()
        if not url.startswith(("http://", "https://")) or url in (f["url"] for f in found):
            continue
        title = (node.get("title") or node.get("text") or "").strip() or site_name(url)
        found.append({"url": url, "title": plain(title, 120), "site": site_name(node.get("htmlUrl") or url)})
    return found

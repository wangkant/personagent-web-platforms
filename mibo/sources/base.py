"""Places the character reads for its owner without anyone signing in.

A source is one public place (a site's hot list, a feed, an open API) and a
function that reads what is there now. Each module beside this one lists its
own in `SOURCES`; the package gathers them into one catalogue, each round
reads some of it (see `weights`), and what they bring goes into the library
with the walks' finds.
"""
from __future__ import annotations

import asyncio
import html
import io
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx

from ..library import Item
from ..roam import player_for, site_name

# Seconds one source has to answer; a slow one is skipped until the next round.
TIMEOUT_S = 15
# A connection that takes this long has failed: shorter than TIMEOUT_S, so the failure is seen and recorded.
CONNECT_S = 8
# What one source may bring in one round.
PER_SOURCE = 12
# Sites answer a browser's request more readily than a script's.
HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/126.0 Safari/537.36"),
           "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
GROUPS = ("news", "tech", "video", "community", "science", "culture", "own")
_TAG = re.compile(r"<[^>]+>")
# A post's own stylesheet, script or comment reads as code once the tags are gone.
_HIDDEN = re.compile(r"<(style|script)\b.*?</\1\s*>|<!--.*?-->", re.S | re.I)
_IMG = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.I)


@dataclass
class Find:
    """One thing a source brought: the item, and its cover's address when the source names one."""
    item: Item
    picture: str = ""


@dataclass(frozen=True)
class Source:
    key: str                    # lower case, unique: "hackernews"
    name: tuple[str, str]       # (English, Chinese)
    about: tuple[str, str]      # one line saying what comes from it, (English, Chinese)
    group: str                  # one of GROUPS
    fetch: Callable[[httpx.AsyncClient], Awaitable[list[Find]]]
    # Whose first rounds lean on it: readers of "zh", of "en", "all" or "" (nobody in particular).
    default: str = ""
    # The language what it brings is written in; "" for the person's own feeds.
    lang: str = "en"
    # The country or area whose platform or local press it is ("CN", "JP", "US"...); "" when global.
    region: str = ""


def written_in(lang: str, sources: list[Source], keys: set[str] | None = None) -> list[Source]:
    """`sources` with those in `keys` (all of them when None) marked as written in `lang`."""
    return [replace(s, lang=lang) if keys is None or s.key in keys else s for s in sources]


def local_to(region: str, sources: list[Source], keys: set[str] | None = None) -> list[Source]:
    """`sources` with those in `keys` (all of them when None) marked as local to `region`."""
    return [replace(s, region=region) if keys is None or s.key in keys else s for s in sources]


def client(transport: Optional[httpx.AsyncBaseTransport] = None,
           mounts: Optional[dict[str, httpx.AsyncBaseTransport]] = None) -> httpx.AsyncClient:
    """The sources' client; a round passes the transport that records reachability (`situation.client_options`)."""
    return httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_S, connect=CONNECT_S), headers=HEADERS,
                             follow_redirects=True, transport=transport, mounts=mounts)


async def get_text(http: httpx.AsyncClient, url: str, **params) -> str:
    answer = await http.get(url, params=params or None)
    answer.raise_for_status()
    return answer.text


async def get_json(http: httpx.AsyncClient, url: str, **params):
    answer = await http.get(url, params=params or None)
    answer.raise_for_status()
    return answer.json()


def plain(text: object, limit: int = 600) -> str:
    """Markup taken out of a summary, cut at `limit`."""
    words = " ".join(html.unescape(_TAG.sub(" ", _HIDDEN.sub(" ", str(text or "")))).split())
    return words[:limit]


# Image hosts that cut a picture to size when the address asks. Many feeds name
# the full original (often over the half megabyte a cover is read to), so the
# cover is asked for at card size instead: (host ends with, pattern, replacement).
_SMALLER = (
    ("brightspotcdn.com", re.compile(r"/resize/[^/]+/"), "/resize/640/"),                   # NPR
    ("nyt.com", re.compile(r"-(?:mediumSquareAt3X|superJumbo|jumbo)\.jpg$"), "-articleLarge.jpg"),
    ("365dm.com", re.compile(r"/\d+x\d+/"), "/768x432/"),                                   # Sky News
    ("apartmenttherapy.info", re.compile(r"/image/upload/+"), "/image/upload/f_jpg,q_auto:eco,c_fill,w_640/"),
    ("", re.compile(r"/master/pass/"), "/master/w_640,c_limit/"),                           # Condé Nast titles
)
_QUERY_SMALLER = (("natgeofe.com", "w=640"), ("ignimgs.com", "width=640"), ("polygonimages.com", "w=640"),
                  ("tmtpost.com", "imageView2/2/w/640"))


def smaller(picture: str) -> str:
    """The cover's address at card size, where its host can do that."""
    host = urlsplit(picture).hostname or ""
    for ending, pattern, replacement in _SMALLER:
        if host.endswith(ending):
            picture = pattern.sub(replacement, picture, count=1)
    for ending, query in _QUERY_SMALLER:
        if host.endswith(ending) and "?" not in picture:
            return f"{picture}?{query}"
    # Bilibili's image host takes the size after an @.
    if host.endswith("hdslb.com") and "@" not in urlsplit(picture).path:
        return picture + "@640w_360h_1c.jpg"
    return picture


def find(url: str, title: str, *, site: str = "", kind: str = "", text: str = "", author: str = "",
         published: str = "", picture: str = "", length: Optional[int] = None) -> Find:
    """A find from its public facts. A YouTube or Bilibili address becomes a
    video with its player; `kind` is otherwise article, post or page."""
    player = player_for(url)
    return Find(Item(url=url, site=site or site_name(url), kind="video" if player else (kind or "article"),
                     title=plain(title, 200), author=plain(author, 80), published=published,
                     length=length, description=plain(text), embed=player),
                smaller(picture) if picture.startswith("https://") else "")


def feed_document(body: bytes, base_url: str, content_type: str = "") -> feedparser.FeedParserDict:
    """feedparser's reading of a fetched RSS or Atom document: broken markup, odd dates and the
    encoding the document declares included. It gets the bytes as a stream, so it never opens an
    address or a file itself. ValueError when the bytes are no feed."""
    # The answer's type and charset weigh in as RFC 3023 says; its address is the base of relative links.
    # The HTML is never shown, only taken down to text (`plain`), so feedparser need not clean it.
    doc = feedparser.parse(io.BytesIO(body), resolve_relative_uris=False, sanitize_html=False,
                           response_headers={"content-location": base_url, "content-type": content_type})
    if not doc.version:
        raise ValueError(f"not a feed: {base_url}")
    return doc


def _picture(entry: dict) -> str:
    """The picture the entry names: an image media:content, else its thumbnail, else an image enclosure."""
    for media in entry.get("media_content", []):
        if media.get("url") and (media.get("medium") == "image" or media.get("type", "image").startswith("image")):
            return media["url"]
    for media in entry.get("media_thumbnail", []):
        if media.get("url"):
            return media["url"]
    for enclosure in entry.get("enclosures", []):
        if enclosure.get("href") and enclosure.get("type", "").startswith("image"):
            return enclosure["href"]
    return ""


def parse_feed(body: bytes, base_url: str, *, site: str = "", content_type: str = "",
               text: Optional[Callable[[dict], str]] = None) -> list[Find]:
    """The entries of an RSS 2.0, RSS 1.0 or Atom document, in the feed's order. `text`
    picks an entry's description where a source knows better than the feed's own."""
    doc = feed_document(body, base_url, content_type)
    atom = doc.version.startswith("atom")
    found: list[Find] = []
    for entry in doc.entries:
        link = urljoin(base_url, entry.get("link") or entry.get("id") or "")
        # An RSS item's content:encoded is the story and its description often a stub; an Atom
        # summary is meant as one. feedparser also files a media caption as plain-text content and
        # an author's note as HTML beside the story, so the story is the longest HTML part.
        content = max((part.value for part in entry.get("content", []) if part.type == "text/html"),
                      key=len, default="")
        summary = entry.get("summary", "")
        words = (summary or content) if atom else (content or summary)
        # Mastodon and other short-post feeds give no title; the post's opening words stand in.
        title = entry.get("title") or plain(words, 100)
        if not link.startswith(("http://", "https://")) or not title:
            continue
        picture = _picture(entry)
        if not picture:
            # NPR opens some stories with a one-pixel tracking image; the picture is the first real one.
            shown = [src for src in _IMG.findall(words) if "/tracking/" not in src]
            picture = urljoin(link, html.unescape(shown[0])) if shown else ""
        when = entry.get("published_parsed") or entry.get("updated_parsed")
        published = (datetime(*when[:6], tzinfo=timezone.utc).isoformat() if when
                     else entry.get("published") or entry.get("updated", ""))
        found.append(find(link, title, site=site, text=text(entry) if text else words,
                          author=entry.get("author_detail", {}).get("name", ""), picture=picture,
                          published=published))
    return found


async def feed_finds(answer: httpx.Response, *, site: str = "",
                     text: Optional[Callable[[dict], str]] = None) -> list[Find]:
    """The finds in a fetched feed, parsed off the event loop: a large feed takes feedparser a second."""
    return await asyncio.to_thread(parse_feed, answer.content, str(answer.url), site=site,
                                   content_type=answer.headers.get("content-type", ""), text=text)


async def read_feed(http: httpx.AsyncClient, url: str, *, site: str = "",
                    text: Optional[Callable[[dict], str]] = None, **params) -> list[Find]:
    """The finds of the feed at `url`, read through the sources' client."""
    answer = await http.get(url, params=params or None)
    answer.raise_for_status()
    return await feed_finds(answer, site=site, text=text)


def feed_source(key: str, name: tuple[str, str], about: tuple[str, str], group: str, url: str, *,
                site: str = "", default: str = "") -> Source:
    """A source that is one feed address."""
    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        return (await read_feed(http, url, site=site or name[0]))[:PER_SOURCE]

    return Source(key, name, about, group, fetch, default)

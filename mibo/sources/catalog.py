"""Every source, and the rounds that read them into the library.

The catalogue is the area modules' `SOURCES` and one source per feed the
person added themselves (kept in the runtime database's `own_feeds` table,
db.py, with how each source's last read went in `source_reads`). Nobody chooses public sources: each round reads
every own feed and `weights.ROUND` public sources drawn by what was learned
(see `weights`), keeps what the library has not seen, each thing marked
with the source that brought it, and the feed ranks it by the person's taste.

Each round first infers the person's situation again (`situation.infer`) and
reads through a transport that records which hosts answer, so a source that
cannot be reached from where the person is stops taking a place in the round.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import logging
import random
import time
from collections import Counter
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote, urljoin

import httpx

from .. import db, langs, quiet, roam, situation
from ..library import Library, item_id
from ..panels import feed as feed_panel
from ..roam import PICTURE_FETCH_MAX, public_address, small_picture
from . import base, weights
from .base import Find, Source

logger = logging.getLogger("agent.sources")

# The modules that list sources.
AREAS = ("tech", "china", "world", "culture", "regional")
# Sources fetched at the same time in one round.
AT_ONCE = 6
# A round soon after the app starts, then this often.
FIRST_ROUND_S = 20
ROUND_EVERY_S = 30 * 60
COVER_HOPS = 4
# A page is read this far for the picture it declares: the head holds it.
HEAD_READ_MAX = 300_000
# A thing about to be shown waits this long for its page's picture.
SHOWN_COVER_S = 6
# One the person asked for and is waiting on gets this long: most pages answer within it.
ASKED_COVER_S = 1.5
ERROR_MAX = 160

_broken: set[str] = set()


# ----------------------------------------------------------------- the catalogue --

def _module(name: str):
    """One sibling module, or None: one that is missing or broken never takes the others down."""
    try:
        return importlib.import_module(f"{__package__}.{name}")
    except Exception as exc:
        # The catalogue is read on every view: one line per module, not per view.
        if name not in _broken:
            _broken.add(name)
            logger.warning("sources: %s could not be loaded (%s: %s); the others go on",
                           name, type(exc).__name__, exc)
        return None


def custom():
    return _module("custom")


def all_sources(own_entries: list[dict]) -> dict[str, Source]:
    """Every source by key: the area modules' first, then one per saved own entry."""
    found: dict[str, Source] = {}
    for name in AREAS:
        module = _module(name)
        for source in getattr(module, "SOURCES", ()):
            found.setdefault(source.key, source)
    own = custom() if own_entries else None
    if own is not None:
        try:
            for entry in own_entries:
                source = own.source_for(entry)
                found[source.key] = source
        except Exception as exc:
            logger.warning("sources: the person's own feeds could not be listed (%s: %s)", type(exc).__name__, exc)
    return found


def reader(lang: str) -> str:
    """Which defaults a reader of `lang` gets: "zh" for Chinese, else "en"."""
    return "zh" if langs.content(langs.normalize(lang) or "en") == "zh" else "en"


# ----------------------------------------------------------- what is kept on disk --

def _choice(read) -> dict:
    return {"own": [json.loads(entry) for entry, in read("SELECT entry FROM own_feeds ORDER BY n")],
            "last": {key: {"at": at, "count": count, "error": error}
                     for key, at, count, error in read("SELECT key, at, count, error FROM source_reads")}}


def load_choice(runtime: Path) -> dict:
    """The person's own feeds, and how each source's last read went."""
    return _choice(db.at(runtime).read)


def change_choice(runtime: Path, change: Callable[[dict], None]) -> dict:
    """Read, change and write both as one transaction; what was written."""
    with db.at(runtime).write() as conn:
        choice = _choice(lambda sql: conn.execute(sql).fetchall())
        change(choice)
        conn.execute("DELETE FROM own_feeds")
        conn.executemany("INSERT INTO own_feeds VALUES (?,?,?)",
                         [(entry["key"], n, json.dumps(entry, ensure_ascii=False)) for n, entry in enumerate(choice["own"])])
        conn.execute("DELETE FROM source_reads")
        conn.executemany("INSERT INTO source_reads VALUES (?,?,?,?)",
                         [(key, row["at"], row["count"], row["error"]) for key, row in choice["last"].items()])
        return choice


def weighed(runtime: Path, lang: str, sources: dict[str, Source], rng: Optional[random.Random] = None) -> list[dict]:
    """Every public source with its weight for this person, highest first (see `weights.weigh`):
    the languages they read (the character's own first, the others less), the defaults of the character's
    language, the situation's supply term, and the taste model's sampled coefficient for each source while
    the model is on."""
    from .. import taste
    runtime = Path(runtime)
    matcher = feed_panel.topic_matcher(feed_panel.load_topics(runtime))
    public = [s for s in sources.values() if s.group != "own"]
    origin = None
    if taste.enabled(runtime):
        sampler = taste.Sampler(taste.load(runtime), taste.Priors(runtime), (rng or random.Random()).getrandbits(32))
        origin = lambda key: sampler.theta("origin:src:" + key)  # noqa: E731
    own = situation.primary(lang)
    defaults = {code for code in situation.default_langs(runtime, lang) if situation.primary(code) == own}
    return weights.weigh(public, weights.load(runtime), situation.readable(runtime) | situation.expand({lang}), matcher,
                         own=situation.expand({lang}), unreadable=situation.unreadable(runtime), defaults=defaults,
                         supply=situation.supplier(runtime), origin=origin,
                         retry_s=situation.retry_s(runtime),
                         topical=pulls(runtime, public, time.time()))


# How far back what a source brought tells what it is about, and the fresh unseen things on an interest
# that count as enough of it for now.
YIELD_DAYS = 14
ENOUGH = 10


def pulls(runtime: Path, sources: list[Source], now: float) -> Optional[dict[str, float]]:
    """Per source key, its pull toward the person's interests (roam.interests): over the interests, the
    largest of how short the library is of it (1 with nothing fresh, unseen and readable on it, 1/2 with ENOUGH) times
    the share of what the source brought in YIELD_DAYS that is on it. The share starts from the source's own
    words (one thing on the interest when they name it, a tenth when not) and moves with what it brings.
    None when the person named no interest."""
    topics = roam.interests(runtime)
    if not topics:
        return None
    reads = situation.readable(runtime)
    held = roam.supply(runtime, topics, now, frozenset(s.key for s in sources if s.lang and s.lang not in reads))
    need = {t: 1 / (1 + held[t] / ENOUGH) for t in topics}
    matchers = [(t, m) for t in topics if (m := feed_panel.topic_matcher([t]))]
    brought: dict[str, list] = {}
    for item in Library(runtime).items():
        if item.seen_at < now - YIELD_DAYS * 86400:
            break
        if item.source:
            text = " ".join((item.title, item.description[:300], item.site)).casefold()
            row = brought.setdefault(item.source, [0, dict.fromkeys(topics, 0)])
            row[0] += 1
            for topic, matcher in matchers:
                row[1][topic] += bool(matcher.search(text))
    out = {}
    for source in sources:
        count, hits = brought.get(source.key, (0, {}))
        words = weights._text(source)
        out[source.key] = max(need[t] * (hits.get(t, 0) + (1.0 if m.search(words) else 0.1)) / (count + 2)
                              for t, m in matchers) if matchers else 0.0
    return out


# --------------------------------------------------------------------- one round --

def short(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError | httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http {exc.response.status_code}"
    words = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {words}"[:ERROR_MAX] if words else type(exc).__name__


async def _fetch(http: httpx.AsyncClient, source: Source, gate: asyncio.Semaphore) -> tuple[list[Find], str, str]:
    """What a source brought, its error, and how the read went for `weights.read`: blocked when the
    transport could not reach it (its error, or every request it made failed so), else failed or ok."""
    async with gate:
        counts = situation.tally()
        try:
            finds = await asyncio.wait_for(source.fetch(http), base.TIMEOUT_S)
        except Exception as exc:
            logger.info("sources: %s did not answer (%s)", source.key, short(exc))
            blocked = isinstance(exc, situation.COUNTED) or (counts["fail"] and not counts["ok"])
            return [], short(exc), weights.BLOCKED if blocked else weights.FAILED_READ
    finds = list(finds)[:base.PER_SOURCE]
    # A source that reads several feeds and keeps what answered says nothing when none did.
    return finds, "", weights.BLOCKED if not finds and counts["fail"] and not counts["ok"] else weights.OK


async def _cover(http: httpx.AsyncClient, address: str, referer: str) -> bytes:
    """The cover made small, or b"": only https to public addresses, every redirect checked again."""
    # An http picture is asked for over https, as a browser upgrades one on a secure page.
    if address.startswith("http://"):
        address = "https://" + address.removeprefix("http://")
    for _ in range(COVER_HOPS):
        if not address.startswith("https://") or not await public_address(address):
            return b""
        # A header takes ASCII only: an address with Chinese in its path goes percent-encoded.
        coming_from = {"Referer": quote(referer, safe=":/?#[]@!$&'()*+,;=%~")}
        async with http.stream("GET", address, headers=coming_from, follow_redirects=False) as answer:
            if answer.is_redirect:
                address = urljoin(address, answer.headers.get("location", ""))
                continue
            if answer.status_code != 200 or int(answer.headers.get("content-length") or 0) > PICTURE_FETCH_MAX:
                return b""
            data = bytearray()
            async for chunk in answer.aiter_bytes():
                data.extend(chunk)
                if len(data) > PICTURE_FETCH_MAX:
                    return b""
        return await asyncio.to_thread(small_picture, bytes(data))
    return b""


async def _page_picture(http: httpx.AsyncClient, address: str) -> str:
    """The picture a page declares for itself (og:image and its kin), or "": what a link preview shows, for a
    find whose source gave none. Only https to public addresses, every redirect checked again."""
    for _ in range(COVER_HOPS):
        if not address.startswith("https://") or not await public_address(address):
            return ""
        async with http.stream("GET", address, follow_redirects=False) as answer:
            if answer.is_redirect:
                address = urljoin(address, answer.headers.get("location", ""))
                continue
            if answer.status_code != 200 or "html" not in answer.headers.get("content-type", ""):
                return ""
            data = bytearray()
            async for chunk in answer.aiter_bytes():
                data.extend(chunk)
                if len(data) >= HEAD_READ_MAX:
                    break
            types = answer.headers.get("content-type", "")
        from ..web import fetch
        return fetch.head(fetch.decoded(bytes(data), types), address)[0]["picture"]
    return ""


async def _own_picture(http: httpx.AsyncClient, found: Find, gate: asyncio.Semaphore) -> str:
    async with gate:
        try:
            return await asyncio.wait_for(_page_picture(http, found.item.url), base.TIMEOUT_S)
        except Exception:
            return ""


async def _fill(http: httpx.AsyncClient, library: Library, lacking: list[Find], gate: asyncio.Semaphore) -> int:
    """The pages' own pictures for kept finds that have none; how many got one."""
    declared = await asyncio.gather(*(_own_picture(http, f, gate) for f in lacking))
    # One address declared by two pages is the site's emblem, not a cover of either.
    emblems = {address for address, count in Counter(declared).items() if count > 1}
    for found, address in zip(lacking, declared):
        found.picture = "" if address in emblems else address
    covers = await asyncio.gather(*(_covered(http, f, gate) for f in lacking))

    def keep() -> int:
        for found, cover in zip(lacking, covers):
            if cover:
                library.change(found.item.id, picture=library.save_picture(found.item.id, cover))
        return sum(1 for cover in covers if cover)

    return await asyncio.to_thread(keep)


async def cover_shown(runtime: Path, item, within: float = SHOWN_COVER_S) -> str:
    """Its page's own picture kept for a library thing that has none, as it is about to be shown, waited for
    `within` seconds; the picture's file name, or ""."""
    library = Library(runtime)
    others = await asyncio.to_thread(library.items) if item.source else []
    other = next((i for i in others if i.source == item.source and i.id != item.id), None)

    async def fetch(http: httpx.AsyncClient) -> bytes:
        # Another page of the same source is read beside this one: what it declares too is the site's emblem,
        # not this thing's cover.
        theirs = asyncio.ensure_future(_page_picture(http, other.url)) if other else None
        try:
            address = await _page_picture(http, item.url)
            if address and theirs and address == await theirs:
                return b""
            return await _cover(http, address, item.url)
        finally:
            if theirs:
                theirs.cancel()

    async with base.client(**situation.client_options(runtime)) as http:
        try:
            cover = await asyncio.wait_for(fetch(http), within)
        except Exception:
            return ""
    if not cover:
        return ""
    kept = await asyncio.to_thread(lambda: library.change(item.id, picture=library.save_picture(item.id, cover)))
    return kept.picture if kept else ""


async def _covered(http: httpx.AsyncClient, found: Find, gate: asyncio.Semaphore) -> bytes:
    if not found.picture:
        return b""
    async with gate:
        try:
            return await asyncio.wait_for(_cover(http, found.picture, found.item.url), base.TIMEOUT_S)
        except Exception:
            # A cover is a nicety: whatever the network did, the find is kept without one.
            return b""


def _keep(library: Library, new: list[tuple[Find, bytes]]) -> None:
    for found, cover in new:
        item = found.item
        item.id = item_id(item.url)
        # What a source brings was not chosen by the character: the feed ranks it by taste.
        item.fits, item.why = False, ""
        if cover:
            item.picture = library.save_picture(item.id, cover)
        library.add(item)
    library.compact()


def plan(runtime: Path, lang: str, rng: random.Random) -> list[Source]:
    """This round's sources: every own feed, and the public ones `weights.pick` draws."""
    sources = all_sources(load_choice(runtime)["own"])
    own = [s for s in sources.values() if s.group == "own"]
    return own + [row["source"] for row in weights.pick(weighed(runtime, lang, sources, rng), rng)]


async def run_round(runtime: Path, lang: str) -> tuple[int, list[str]]:
    """Read this round's sources and keep what is new; how many new things were kept, and which sources were read."""
    runtime = Path(runtime)
    await situation.retry_down(runtime)
    await asyncio.to_thread(situation.infer, runtime, lang)
    before = await asyncio.to_thread(load_choice, runtime)
    picked = await asyncio.to_thread(plan, runtime, lang, random.Random())
    library = Library(runtime)
    known = {item.id for item in await asyncio.to_thread(library.items)}
    gate = asyncio.Semaphore(AT_ONCE)
    async with base.client(**situation.client_options(runtime)) as http:
        answers = await asyncio.gather(*(_fetch(http, s, gate) for s in picked))
        new: list[Find] = []
        for source, (finds, _, _) in zip(picked, answers):
            for found in finds:
                ident = item_id(found.item.url)
                if ident not in known:
                    known.add(ident)
                    found.item.source = source.key
                    new.append(found)
        covers = await asyncio.gather(*(_covered(http, f, gate) for f in new))
        if new:
            # Kept first, so the feed has them while the picture-less ones' pages are asked for theirs.
            await asyncio.to_thread(_keep, library, list(zip(new, covers)))
            lacking = [found for found, cover in zip(new, covers) if not cover]
            filled = await _fill(http, library, lacking, gate) if lacking else 0
            logger.info("sources: %d of %d new things had no picture; %d took their page's", len(lacking), len(new),
                        filled)
    now = int(time.time())

    def record(saved: dict) -> None:
        # A feed of their own the person removed while the round ran leaves no trace.
        removed = {e["key"] for e in before["own"]} - {e["key"] for e in saved["own"]}
        for source, (finds, error, _) in zip(picked, answers):
            if source.key not in removed:
                saved["last"][source.key] = {"at": now, "count": len(finds), "error": error}

    await asyncio.to_thread(change_choice, runtime, record)
    await asyncio.to_thread(weights.read, runtime, {s.key: outcome for s, (_, _, outcome) in zip(picked, answers)
                                                    if s.group != "own"}, now)
    logger.info("sources: a round of %d sources kept %d new things", len(picked), len(new))
    return len(new), [s.key for s in picked]


# ---------------------------------------------------------------------- the loop --

class Rounds:
    """A round soon after start, then every ROUND_EVERY_S, and at once when asked.
    `runtime` and `lang` are read again for each round, so a changed setting counts. `home` is the folder
    whose .env names the character's model, the runtime's parent unless given."""

    def __init__(self, runtime: Callable[[], Path], lang: Callable[[], str],
                 home: Optional[Callable[[], Path]] = None):
        self.runtime, self.lang = runtime, lang
        self.home = home or (lambda: self.runtime().parent)
        self.running = False
        self._wake = asyncio.Event()
        self.last_count: Optional[int] = None
        self.last_picked: list[str] = []
        # Called after a round that kept something new (the page shares it and tells the pet).
        self.found: Callable[[], None] = lambda: None

    def ask(self, *, even_if_running: bool = False) -> None:
        """A round now; while one runs, another right after it only when `even_if_running`."""
        if even_if_running or not self.running:
            self._wake.set()

    @property
    def busy(self) -> bool:
        """A round is running or about to: what the page shows as reading."""
        return self.running or self._wake.is_set()

    async def _wait(self, seconds: float) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._wake.wait(), seconds)
        self._wake.clear()

    async def _judge(self) -> None:
        try:
            await roam.judge(self.runtime(), self.home())
        except Exception:
            # The person's model may be down or slow; what was kept waits for the next round's judgement.
            logger.exception("sources: the character's judgement failed")

    async def loop(self) -> None:
        # The first start checks a few hosts, so the gate and the region exist before the first round.
        first = asyncio.create_task(situation.probe(self.runtime(), self.lang()))
        try:
            await self._wait(FIRST_ROUND_S)
            while True:
                await quiet.wait()
                self.running = True
                try:
                    self.last_count, self.last_picked = await run_round(self.runtime(), self.lang())
                except Exception:
                    logger.exception("sources: a round failed")
                finally:
                    self.running = False
                # What the round kept is judged by the character after it, when enough waits.
                await self._judge()
                if self.last_count:
                    self.found()
                await self._wait(ROUND_EVERY_S)
        finally:
            first.cancel()

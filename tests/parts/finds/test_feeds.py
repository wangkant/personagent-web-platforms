"""Feeds read into finds: what a person sees from a feed (titles, links, covers, times,
authors) survives broken markup, odd dates and a declared Chinese encoding, and the
parser reads only the bytes it is handed, never an address or a file named in them."""
from __future__ import annotations

import asyncio
import http.server
import threading
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from mibo.sources.base import parse_feed, read_feed

FEEDS = Path(__file__).resolve().parents[2] / "data" / "feeds"
BASE = "https://harbour.example/feed.xml"


def _feed(name: str) -> bytes:
    return (FEEDS / name).read_bytes()


def test_a_clean_feed_gives_each_entry_with_its_link_cover_time_and_author() -> None:
    lamp, ferry = parse_feed(_feed("clean.xml"), BASE, site="Harbour Notes")
    assert lamp.item.title == "The lighthouse gets a new lamp"
    assert lamp.item.url == "https://harbour.example/lighthouse"
    assert lamp.item.site == "Harbour Notes"
    assert "forty years" in lamp.item.description
    assert lamp.picture == "https://img.harbour.example/lamp.jpg"
    assert lamp.item.author == "Ada Keeper"
    assert datetime.fromisoformat(lamp.item.published) == datetime.fromisoformat("2026-11-18T09:30:00+01:00")
    # A relative link and a picture found only in the text are read against the feed and the entry.
    assert ferry.item.url == "https://harbour.example/ferry"
    assert ferry.picture == "https://harbour.example/img/ferry.png"


def test_a_feed_with_a_stray_ampersand_still_gives_its_entries() -> None:
    finds = parse_feed(_feed("stray-ampersand.xml"), "https://towngown.example/rss")
    assert [f.item.title for f in finds] == ["Bread & butter: the market returns", "Choir practice moves to Tuesday"]
    assert finds[0].item.url.startswith("https://towngown.example/market?day=sat")


def test_dates_written_in_odd_forms_are_read_as_times() -> None:
    spaced, broken = parse_feed(_feed("odd-date.xml"), "https://techwire.example/rss")
    assert datetime.fromisoformat(spaced.item.published) == datetime.fromisoformat("2026-11-18T18:18:30+08:00")
    assert datetime.fromisoformat(broken.item.published) == datetime.fromisoformat("2026-11-18T04:15:00+00:00")


def test_a_feed_in_gbk_reads_as_chinese_whatever_its_header_says() -> None:
    for header in ("text/xml", "application/rss+xml", ""):
        [snow] = parse_feed(_feed("gbk.xml"), "https://jiangnan.example/rss", content_type=header)
        assert snow.item.title == "西湖迎来初雪"
        assert snow.item.description == "今晨西湖下起了今年第一场雪。"


def test_a_feed_a_browser_tab_has_decoded_reads_by_the_charset_given_over_the_one_declared() -> None:
    decoded = _feed("gbk.xml").decode("gbk").encode("utf-8")
    [snow] = parse_feed(decoded, "https://jiangnan.example/rss", content_type="application/xml; charset=utf-8")
    assert snow.item.title == "西湖迎来初雪"


def test_a_fetched_feed_is_read_from_its_bytes_and_its_final_address() -> None:
    async def run() -> list:
        def answer(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/old":
                return httpx.Response(301, headers={"location": "https://jiangnan.example/rss"})
            return httpx.Response(200, content=_feed("gbk.xml"), headers={"content-type": "text/xml"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(answer), follow_redirects=True) as http:
            return await read_feed(http, "https://jiangnan.example/old", site="江南日报")

    [snow] = asyncio.run(run())
    assert (snow.item.title, snow.item.site, snow.item.author) == ("西湖迎来初雪", "江南日报", "记者 王明")


def test_an_atom_entry_without_a_title_is_titled_by_its_opening_words() -> None:
    # No declaration and no header: XML's own default, UTF-8, holds.
    atom = """<feed xmlns="http://www.w3.org/2005/Atom"><title>@ada</title>
<entry><id>https://social.example/@ada/1</id><link href="https://social.example/@ada/1"/>
<updated>2026-11-19T10:00:00Z</updated><content type="html">&lt;p&gt;潮水退了，海豹都躺在沙洲上。&lt;/p&gt;</content></entry>
</feed>""".encode()
    [post] = parse_feed(atom, "https://social.example/@ada.rss")
    assert post.item.title == "潮水退了，海豹都躺在沙洲上。"
    assert post.item.url == "https://social.example/@ada/1"


def test_a_page_that_is_no_feed_is_refused() -> None:
    with pytest.raises(ValueError):
        parse_feed(b"<!doctype html><html><head><title>Shop</title></head><body><p>Hello</p></body></html>",
                   "https://shop.example/")


@pytest.fixture
def listener():
    """A loopback server that records every request made to it."""
    asked: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            asked.append(self.path)
            body = _feed("clean.xml")
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", asked
    server.shutdown()
    server.server_close()


def test_the_parser_opens_no_address_or_file_named_in_what_it_is_handed(listener, tmp_path) -> None:
    origin, asked = listener
    saved = tmp_path / "saved.xml"
    saved.write_bytes(_feed("clean.xml"))
    # Bytes that read as an address or a path are only bytes: no feed, nothing fetched or opened.
    for handed in (f"{origin}/feed".encode(), str(saved).encode()):
        with pytest.raises(ValueError):
            parse_feed(handed, BASE)
    # An external entity in a feed is not fetched either.
    entity = f"""<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY far SYSTEM "{origin}/entity">]>
<rss version="2.0"><channel><title>T</title><item><title>&far;</title><link>https://a.example/1</link></item>
</channel></rss>""".encode()
    parse_feed(entity, BASE)
    assert asked == []

"""What the guarded fetch hands back when it may fetch: the decoded page, after redirects between public pages,
of the types its caller takes; and where it stops (the redirect cap, an address too long). The refusals are
floors (floors/boundary/test_safe_fetch.py)."""
from __future__ import annotations

import gzip

from mibo.ingestion import MAX_URL_LENGTH, safe_fetch_url

PAGE = "<html><head><title>Shore Notes</title></head><body><p>Tide pools at dawn.</p></body></html>"


async def fetch(url: str, **taken):
    return await safe_fetch_url(url, timeout=5, max_wire_bytes=128 * 1024, max_decoded_bytes=128 * 1024, **taken)


async def test_a_gzip_served_page_arrives_decoded_with_its_final_address(web) -> None:
    base = web.name("shore.example", "93.184.216.34")
    web.site.put("/page", gzip.compress(PAGE.encode()), headers={"Content-Encoding": "gzip"})
    got = await fetch(f"{base}/page")
    assert got.status_code == 200 and got.content == PAGE.encode() and got.url == f"{base}/page"


async def test_only_the_types_the_caller_takes_are_kept(web) -> None:
    base = web.name("shore.example", "93.184.216.34")
    web.site.put("/page", PAGE)
    web.site.put("/blob", b"\x00\x01binary", "application/octet-stream")
    web.site.put("/bare", PAGE, "")
    html = {"allowed_content_types": ("text/html", "application/xhtml+xml"), "require_content_type": True}
    assert (await fetch(f"{base}/page", **html)).content == PAGE.encode()
    assert await fetch(f"{base}/blob", **html) is None
    assert await fetch(f"{base}/bare", **html) is None
    assert (await fetch(f"{base}/blob")).content == b"\x00\x01binary"


async def test_a_redirect_between_public_pages_is_followed_to_its_end(web) -> None:
    first = web.name("old.example", "93.184.216.34")
    second = web.name("new.example", "2606:4700::1111")
    web.site.put("/moved", "", status=301, headers={"Location": f"{second}/here"})
    web.site.put("/here", "", status=302, headers={"Location": "/page"})
    web.site.put("/page", PAGE)
    got = await fetch(f"{first}/moved")
    assert got.content == PAGE.encode() and got.url == f"{second}/page"
    assert web.site.log == ["GET /moved", "GET /here", "GET /page"]
    assert web.lookups == ["old.example", "new.example", "new.example"]


async def test_redirects_stop_at_the_cap(web) -> None:
    base = web.name("loop.example", "93.184.216.34")
    web.site.put("/loop", "", status=302, headers={"Location": "/loop"})
    assert await fetch(f"{base}/loop", max_redirects=3) is None
    assert web.site.log == ["GET /loop"] * 4


async def test_an_address_longer_than_the_cap_is_never_looked_up(web) -> None:
    base = web.name("long.example", "93.184.216.34")
    web.site.put("/page", PAGE)
    assert await fetch(f"{base}/page?q={'a' * MAX_URL_LENGTH}") is None
    assert web.lookups == [] and web.site.log == []

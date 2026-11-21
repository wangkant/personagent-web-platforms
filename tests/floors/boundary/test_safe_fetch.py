"""The guarded fetch (ingestion.safe_fetch_url) that links in a message, page reads, skills, MCP servers and
runtime downloads all go through: nothing inside this computer or its network is reached, whatever the name, a
redirect or a second answer from DNS says, and no body is taken past its caps. It runs on the real transport
against the stand-in site, with names answering as each test says (harness.sites.PublicWeb)."""
from __future__ import annotations

import gzip
import logging
import threading

import pytest
from roam_standin import Recorder

from harness.model import say
from mibo.ingestion import MAX_HTML_DECODED_BYTES, MAX_HTML_WIRE_BYTES, safe_fetch_url

PAGE = "<html><head><title>Shore Notes</title></head><body><p>Tide pools at dawn.</p></body></html>"


async def fetch(url: str, *, timeout: float = 5, max_wire_bytes: int = MAX_HTML_WIRE_BYTES,
                max_decoded_bytes: int = MAX_HTML_DECODED_BYTES):
    return await safe_fetch_url(url, timeout=timeout, max_wire_bytes=max_wire_bytes,
                                max_decoded_bytes=max_decoded_bytes)


def port_of(url: str) -> int:
    return int(url.rsplit(":", 1)[1].split("/")[0])


@pytest.fixture
def inside():
    """A server on this computer, reached under the name localhost, noting every request that arrives."""
    recorder = Recorder()
    yield recorder
    recorder.close()


@pytest.mark.parametrize("address, internal", [
    ("127.0.0.1", True),
    ("10.0.0.5", True),
    ("172.17.0.1", True),               # inside 172.16/12, which a substring check misses
    ("192.168.1.10", True),
    ("169.254.169.254", True),          # cloud metadata
    ("100.64.0.1", True),               # carrier-grade NAT, Tailscale
    ("100.100.100.200", True),          # Alibaba Cloud metadata, inside 100.64/10
    ("0.0.0.0", True),
    ("::1", True),
    ("::ffff:127.0.0.1", True),         # IPv4-mapped loopback
    ("::ffff:100.100.100.200", True),   # IPv4-mapped metadata
    ("fec0::1", True),                  # site-local, which Python before 3.12 calls global
    ("fd00::1", True),
    ("93.184.216.34", False),
    ("2606:4700::1111", False),
])
async def test_no_internal_address_is_connected_to_by_name_or_written_out(web, address, internal) -> None:
    web.site.put("/page", PAGE)
    literal = f"[{address}]" if ":" in address else address
    # The stand-in site's own 127.0.0.1 address is let through for other suites, so an internal address
    # written out names another port.
    port = 9 if internal else port_of(web.site.url)
    for url in (f"{web.name('shop.example', address)}/page", f"http://{literal}:{port}/page"):
        web.connections.clear()
        got = await fetch(url)
        if internal:
            assert got is None and web.connections == [], url
        else:
            assert got is not None and b"Tide pools" in got.content, url


async def test_a_name_with_one_internal_answer_among_public_ones_is_refused(web) -> None:
    web.site.put("/page", PAGE)
    assert await fetch(f"{web.name('mixed.example', ('93.184.216.34', '10.1.2.3'))}/page") is None
    assert web.connections == []


async def test_names_that_mean_this_computer_are_refused(web, inside) -> None:
    # localhost itself is answered by this computer; the others as a hosts file or systemd-resolved answers them.
    web.name("a.localhost", "127.0.0.1")
    web.name("ip6-localhost", "::1")
    web.name("localhost.localdomain", "127.0.0.1")
    for host in ("localhost", "LOCALHOST.", "a.localhost", "ip6-localhost", "localhost.localdomain"):
        assert await fetch(f"http://{host}:{port_of(inside.url)}/secret") is None, host
    assert inside.hits == []


async def test_a_redirect_to_an_inside_address_is_refused_at_that_hop(web, inside) -> None:
    port = port_of(inside.url)
    web.name("inside.example", "127.0.0.1")
    shore = web.name("shore.example", "93.184.216.34")
    for target in (f"{inside.url}/secret", f"http://127.0.0.1:{port}/secret", f"http://inside.example:{port}/secret",
                   f"http://[::1]:{port}/secret", f"http://100.100.100.200:{port}/latest/meta-data/"):
        web.site.put("/away", "", status=302, headers={"Location": target})
        web.site.log.clear()
        assert await fetch(f"{shore}/away") is None, target
        assert web.site.log == ["GET /away"], target
    assert inside.hits == []


async def test_dns_is_asked_once_and_the_connection_goes_to_the_address_it_gave(web) -> None:
    """DNS rebinding: the name answers a public address to the check, and this computer to whoever asks next."""
    web.site.put("/page", PAGE)
    got = await fetch(f"{web.name('rebind.example', '93.184.216.34', '127.0.0.1')}/page")
    assert got is not None and b"Tide pools" in got.content
    assert web.lookups == ["rebind.example"]
    assert web.connections == [("93.184.216.34", port_of(web.site.url))]


async def test_a_lookup_that_does_not_answer_fails_closed(web) -> None:
    """The fetch gives up while the lookup is still waiting, not when it returns."""
    base = web.hold("slow.example")
    try:
        assert await fetch(f"{base}/page", timeout=0.2) is None
        assert web.lookups == ["slow.example"] and web.connections == []
    finally:
        # The lookup's thread has to end before this test's event loop can.
        web.release()


@pytest.mark.parametrize("sized", [True, False], ids=["with-its-length", "streamed"])
async def test_a_body_past_the_wire_cap_is_refused(web, sized) -> None:
    base = web.name("big.example", "93.184.216.34")
    web.site.put("/big", "x" * 5000, sized=sized)
    web.site.put("/small", "x" * 900, sized=sized)
    assert await fetch(f"{base}/big", max_wire_bytes=1000, max_decoded_bytes=10 ** 6) is None
    small = await fetch(f"{base}/small", max_wire_bytes=1000, max_decoded_bytes=10 ** 6)
    assert small is not None and small.content == b"x" * 900


async def test_a_body_that_unpacks_past_the_decoded_cap_is_refused(web) -> None:
    base = web.name("bomb.example", "93.184.216.34")
    bomb = gzip.compress(b"x" * (MAX_HTML_DECODED_BYTES * 16))
    assert len(bomb) < MAX_HTML_WIRE_BYTES
    web.site.put("/bomb", bomb, headers={"Content-Encoding": "gzip"})
    web.site.put("/page", gzip.compress(PAGE.encode()), headers={"Content-Encoding": "gzip"})
    assert await fetch(f"{base}/bomb") is None
    page = await fetch(f"{base}/page")
    assert page is not None and page.content == PAGE.encode()


async def test_a_fetched_address_is_logged_as_its_host_only(web, caplog) -> None:
    """A file address can carry a token in its path or query (a bot's file links do); a log line names the
    host. Refused, timed out, failed and redirected too often: each way a fetch ends with a line."""
    caplog.set_level(logging.DEBUG, logger="agent")
    path = "/file/bot123:SECRETTOKEN/photo.png"
    web.site.put(path, "", status=302, headers={"Location": f"{path}?key=SECRETKEY"})
    late = threading.Event()
    web.site.put("/late/bot123:SECRETTOKEN", PAGE, before=lambda: late.wait(5))
    try:
        assert await fetch(f"{web.name('inside.example', '10.0.0.1')}{path}?key=SECRETKEY") is None
        assert await fetch(f"{web.hold('slow.example')}{path}?key=SECRETKEY", timeout=0.2) is None
        files = web.name("files.example", "93.184.216.34")
        assert await fetch(f"{files}/late/bot123:SECRETTOKEN?key=SECRETKEY", timeout=0.2) is None
        assert await fetch(f"{files}{path}?key=SECRETKEY") is None
    finally:
        late.set()
        web.release()
    lines = [record.getMessage() for record in caplog.records]
    assert len(lines) >= 4
    assert [line for line in lines if "SECRET" in line] == []
    assert any("files.example" in line for line in lines)


def test_links_in_a_message_to_inside_addresses_are_never_fetched(turn, web, inside) -> None:
    port = port_of(inside.url)
    web.name("inside.example", "127.0.0.1")
    shore = web.name("shore.example", "93.184.216.34")
    web.site.put("/away", "", status=302, headers={"Location": f"{inside.url}/secret"})
    for words in (f"look {inside.url}/a http://127.0.0.1:{port}/b http://inside.example:{port}/c",
                  f"and http://[::1]:{port}/d {shore}/away http://169.254.169.254/latest/meta-data/"):
        result = turn(words, say("Noted."))
        assert result.status == 200
        told = str(result.asked()[0]["messages"][-1]["content"])
        assert "secret" not in told and "internal" not in told
    assert inside.hits == []

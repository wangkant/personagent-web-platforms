"""Which host a download comes from (reach.py): the vendor's own while it answers, a mirror only when it does
not, nothing when none answers; one probe per host however many downloads ask at once, and its answer kept for
a while. Hosts stand in at the connection pool, under situation's recording transport, so httpx and the
recording run as they do for real."""
from __future__ import annotations

import asyncio

import httpcore
import pytest

from mibo import reach


class Hosts:
    """Each host answers, refuses the connection, or waits for `go` before answering; `asked` is every request."""

    def __init__(self, monkeypatch, answering=(), refusing=(), waiting=()) -> None:
        self.answering, self.refusing, self.waiting = set(answering), set(refusing), set(waiting)
        self.asked: list[str] = []
        self.go = asyncio.Event()
        hosts = self

        async def handle(pool, request):
            host = request.url.host.decode()
            hosts.asked.append(host)
            if host in hosts.waiting:
                await hosts.go.wait()
            if host not in hosts.answering:
                raise httpcore.ConnectError("connection refused")
            return httpcore.Response(200)

        monkeypatch.setattr(httpcore.AsyncConnectionPool, "handle_async_request", handle)


@pytest.fixture
def runtime(tmp_path):
    return tmp_path / "runtime"


async def test_a_mirror_is_used_only_while_the_vendor_does_not_answer(monkeypatch, runtime) -> None:
    hosts = Hosts(monkeypatch, answering={"vendor-up.example", "mirror-up.example", "mirror-two.example"},
                  refusing={"vendor-down.example", "mirror-down.example"})
    files = ["https://vendor-up.example/a.tgz", "https://mirror-up.example/a.tgz"]
    assert await reach.order(runtime, files) == files
    files = ["https://vendor-down.example/a.tgz", "https://mirror-up.example/a.tgz"]
    assert await reach.order(runtime, files) == ["https://mirror-up.example/a.tgz"]
    assert await reach.order(runtime, ["https://vendor-down.example/b", "https://mirror-down.example/b"]) == []
    assert await reach.first(runtime, ["vendor-down.example", "mirror-two.example", "mirror-up.example"]) \
        == "mirror-two.example"
    assert sorted(set(hosts.asked)) == sorted(hosts.answering | hosts.refusing)


async def test_a_host_is_probed_once_however_many_ask_and_its_answer_is_kept(monkeypatch, runtime) -> None:
    hosts = Hosts(monkeypatch, answering={"registry-slow.example"}, waiting={"registry-slow.example"})
    asking = [asyncio.ensure_future(reach.answers(runtime, "registry-slow.example")) for _ in range(5)]
    while not hosts.asked:
        await asyncio.sleep(0)
    hosts.go.set()
    assert await asyncio.gather(*asking) == [True] * 5
    assert await reach.answers(runtime, "registry-slow.example") is True
    assert hosts.asked == ["registry-slow.example"]

    # Past KEEP_S the host is asked again.
    monkeypatch.setattr(reach, "KEEP_S", 0)
    assert await reach.answers(runtime, "registry-slow.example") is True
    assert hosts.asked == ["registry-slow.example"] * 2

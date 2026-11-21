"""Nothing of the app listens beyond this computer, not even for a moment: a turn that searches the web binds no
socket past loopback, and the search's native HTTP client looks names up through the system's resolver, whose
own resolver would send each lookup from a UDP socket bound on every address (on Windows, the firewall's prompt
to let Python take connections from the network)."""
from __future__ import annotations

import socket
import sys

import primp

from harness.model import call, say

# Binds past loopback while a test watches; the audit hook stays for the run once added, as audit hooks do.
WATCHED: list | None = None
LOOPBACK = ("127.0.0.1", "::1", "localhost")


def _audit(event: str, args: tuple) -> None:
    if WATCHED is not None and event == "socket.bind":
        sock, address = args
        if sock.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in LOOPBACK:
            WATCHED.append(address)


sys.addaudithook(_audit)


class NativeClient:
    """Stands in for primp's client: records how each was made, and answers nothing."""
    made: list = []

    def __init__(self, **options) -> None:
        NativeClient.made.append(options)

    def request(self, *args, **kwargs):
        raise primp.ConnectError("stand-in")


def test_a_turn_that_searches_the_web_binds_nothing_past_loopback(app, turn, monkeypatch) -> None:
    global WATCHED
    NativeClient.made = []
    monkeypatch.setattr(primp, "Client", NativeClient)
    WATCHED = []
    try:
        done = turn("搜索一下旅行者1号最近的新闻", call("web_search", query="Voyager 1 news"),  # copy: voice
                    say("暂时没有找到。"))  # copy: voice
    finally:
        binds, WATCHED = WATCHED, None
    assert done.status == 200
    assert binds == []
    assert NativeClient.made and all(options.get("dns_resolver") == "system" for options in NativeClient.made)

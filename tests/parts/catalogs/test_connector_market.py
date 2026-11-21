"""The connector market's reach into the official MCP Registry (mibo/mcp_market.py): it asks the registry's frozen
API, offers and installs only versions the registry marks active, so a server its publisher deprecated or deleted
is never offered, and it does not send a search the registry would refuse."""
from __future__ import annotations

import json

import pytest

from mibo import ingestion, mcp_market, mcp_tools, reach

OFFICIAL = "io.modelcontextprotocol.registry/official"


def _item(name: str, status: str) -> dict:
    """One registry answer as the registry writes it: server.json and the registry's own record of it."""
    return {"server": {"name": name, "description": "Weather for a place.", "version": "1.0.0",
                       "packages": [{"registryType": "npm", "identifier": "weather-mcp", "version": "1.0.0",
                                     "transport": {"type": "stdio"}}]},
            "_meta": {OFFICIAL: {"status": status, "isLatest": True}}}


ACTIVE, DEPRECATED, DELETED = "io.example/weather", "io.example/weather-old", "io.example/weather-gone"
ANSWERS = {ACTIVE: _item(ACTIVE, "active"), DEPRECATED: _item(DEPRECATED, "deprecated"),
           DELETED: _item(DELETED, "deleted")}


@pytest.fixture
def registry(monkeypatch) -> list[str]:
    """A stand-in registry behind the guarded fetch; returns the addresses asked."""
    asked: list[str] = []

    async def fetch(url, **_):
        asked.append(url)
        if "/versions/latest" in url:
            name = url.split("/servers/")[1].split("/versions")[0].replace("%2F", "/")
            body = ANSWERS[name]
        else:
            body = {"servers": list(ANSWERS.values()), "metadata": {"count": len(ANSWERS)}}
        return ingestion.SafeFetchResult(url, 200, {}, json.dumps(body).encode())

    async def answers(runtime, host):
        return True

    monkeypatch.setattr(ingestion, "safe_fetch_url", fetch)
    monkeypatch.setattr(reach, "answers", answers)
    return asked


async def test_a_registry_search_offers_only_active_servers_from_the_frozen_api(tmp_path, registry) -> None:
    found = await mcp_market.search(tmp_path, "weather-active-only")
    assert [one["id"] for one in found] == [ACTIVE]
    assert registry and all(url.startswith("https://registry.modelcontextprotocol.io/v0.1/") for url in registry)


async def test_a_deprecated_or_deleted_server_cannot_be_looked_up_to_install(tmp_path, registry) -> None:
    assert (await mcp_market.by_id(ACTIVE))["id"] == ACTIVE
    for name in (DEPRECATED, DELETED):
        with pytest.raises(mcp_tools.ToolsError, match="market_gone"):
            await mcp_market.by_id(name)
    assert all(url.startswith("https://registry.modelcontextprotocol.io/v0.1/") for url in registry)


async def test_a_search_too_short_for_the_registry_finds_nothing_and_asks_nothing(tmp_path, registry) -> None:
    assert await mcp_market.search(tmp_path, "ai") == []
    assert registry == []

"""Which tool definitions go up front (mibo/catalog.py): each module in catalog order while the definitions stay within
a tenth of the model's window, the rest named in find_tools and called through use_tool, and a tools block that does
not change while the window and the tool set do not, even when a server connects mid-run."""
from __future__ import annotations

import json

import pytest

from mibo import catalog, context, mcp_offer


def _size(tools: list) -> float:
    return context.text_tokens(json.dumps(tools, ensure_ascii=False)) if tools else 0.0


def _names(tools: list) -> list[str]:
    return [t["function"]["name"] for t in tools]


@pytest.fixture
def no_servers(monkeypatch):
    monkeypatch.setattr(mcp_offer, "offer_source", None)


@pytest.mark.parametrize("window", [16384, 32768])
def test_modules_go_up_front_in_catalog_order_while_they_fit_a_tenth_of_the_window(no_servers, window):
    offer = catalog.offered(context.budget(window))
    every = catalog.definitions()
    up = [t for t in offer.tools if not mcp_offer.owns(t["function"]["name"])]
    # The up-front tools keep the catalog's order, and the deferred ones are exactly the rest.
    assert up == [t for t in every if t not in offer.deferred]
    assert offer.deferred and _size(up) <= catalog.SHARE * window
    # A module is deferred only when it would not have fit beside the ones before it.
    used = 0.0
    for module in catalog.MODULES[:-1]:
        mine = [t for t in every if module.owns(t["function"]["name"])]
        if all(t in up for t in mine):
            used += _size(mine)
        else:
            assert all(t in offer.deferred for t in mine)
            assert used + _size(mine) > catalog.SHARE * window


def test_every_deferred_tool_is_named_in_find_tools_and_callable_with_its_own_schema(no_servers):
    offer = catalog.offered(context.budget(16384))
    find = next(t for t in offer.tools if t["function"]["name"] == mcp_offer.FIND)
    assert mcp_offer.USE in _names(offer.tools)
    for tool in offer.deferred:
        name = tool["function"]["name"]
        assert f"- {name}:" in find["function"]["description"]
        assert offer.schemas[name] == tool["function"]["parameters"]
        assert catalog.module_of(name) is not None


@pytest.mark.parametrize("window", [65536, 1_048_576])
def test_at_64k_and_above_every_tool_goes_up_front_as_before(no_servers, window):
    offer = catalog.offered(context.budget(window))
    assert offer.tools == catalog.definitions() and offer.deferred == []


def test_the_tools_block_stays_the_same_while_a_server_connects_mid_run(monkeypatch):
    states = [
        {"names": ["notes"], "servers": [{"name": "notes", "about": "", "tools": 0, "state": "waiting"}]},
        {"names": ["notes"], "servers": [{"name": "notes", "about": "", "tools": 0, "state": "starting"}]},
        {"names": ["notes"], "servers": [{"name": "notes", "about": "Notes and lists", "tools": 40}]},
    ]
    for window in (16384, 1_048_576):
        blocks = []
        for state in states:
            monkeypatch.setattr(mcp_offer, "offer_source", lambda state=state: state)
            blocks.append(json.dumps(catalog.offered(context.budget(window)).tools, sort_keys=True))
        assert len(set(blocks)) == 1
        # The server's tools are reached through find_tools at every window, never sent up front.
        assert {mcp_offer.FIND, mcp_offer.USE} <= set(_names(json.loads(blocks[0])))


def test_no_loader_is_sent_when_nothing_is_deferred_and_no_server_is_added(no_servers):
    names = _names(catalog.offered(context.budget(1_048_576)).tools)
    assert not {mcp_offer.FIND, mcp_offer.USE} & set(names) and "web_search" in names

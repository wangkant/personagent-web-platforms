"""The tools the reply model is offered: a fixed list of tool modules, each with its definitions and the one runner
its calls go to. Which go up front is decided by share, after Claude Code's tool search (ENABLE_TOOL_SEARCH=auto):
each module in catalog order goes up front while the definitions stay within a tenth of the window; every tool of
the others, and every tool of the person's MCP servers, is named in find_tools and called through use_tool
(mcp_offer.py). The list is the same in every round and turn for one window and tool set."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import context, findtool, mcp_offer, pastchats, schedule_tool, search, surface
from .desk import tool as desk_tool
from .shell import tool as shell_tool
from .skills import tool as skill_tool
from .web import connect

# The share of the window the up-front definitions may take (Claude Code's tool search threshold).
SHARE = 0.10


@dataclass(frozen=True)
class Module:
    # Where its calls act, as the activity entry marks it.
    mark: str
    definitions: Callable[[], list]
    # run(agent, called, args, lookups) -> the result the model reads; `called` is {"name", "arguments"}.
    run: Callable[..., Awaitable[str]]
    # The area whose switch must be on first (turnon.py), and the names it does not cover.
    switch: str = ""
    unswitched: frozenset = frozenset()

    def owns(self, name: str) -> bool:
        if self.definitions is _loader:
            return mcp_offer.owns(name)
        if self.definitions is _schedule_tools:
            return schedule_tool.owns(name)
        return any(t["function"]["name"] == name for t in self.definitions())


def _schedule_tools() -> list:
    return schedule_tool.TOOLS


def _loader() -> list:
    # find_tools and use_tool are made per turn by `offered`.
    return []


async def _web(agent, called: dict, args: dict, lookups) -> str:
    return await agent._web_tool(called["name"], args, lookups)


async def _finds(agent, called: dict, args: dict, lookups) -> str:
    return await findtool.use(agent, called, args, lookups)


async def _place(agent, called: dict, args: dict, lookups) -> str:
    return await surface.run(agent, called, args, lookups)


async def _shell(agent, called: dict, args: dict, lookups) -> str:
    return await shell_tool.call(agent, called["name"], args, lookups)


async def _desk(agent, called: dict, args: dict, lookups) -> str:
    return await desk_tool.use(agent, args, lookups)


async def _skill(agent, called: dict, args: dict, lookups) -> str:
    return await skill_tool.use(agent, args, lookups)


async def _chats(agent, called: dict, args: dict, lookups) -> str:
    return await pastchats.use(agent, args, lookups)


async def _schedule(agent, called: dict, args: dict, lookups) -> str:
    return await schedule_tool.use(agent, called, lookups)


async def _mcp(agent, called: dict, args: dict, lookups) -> str:
    return await mcp_offer.use(agent, called, lookups)


MODULES = (
    Module("web", lambda: search.TOOLS + [connect.TOOL], _web),
    # bring_find and react: the person's finds in the chat (findtool.py).
    Module("web", lambda: findtool.TOOLS, _finds),
    # open and look: pages, windows, files and folders by one name (surface.py). Opening what the person named
    # is free; reading another program's window asks for the programs switch inside the call.
    Module("place", lambda: surface.TOOLS, _place),
    Module("computer", lambda: shell_tool.TOOLS, _shell, "computer", frozenset({shell_tool.PLAN_TOOL["function"]["name"]})),
    Module("program", lambda: desk_tool.OFFERED, _desk, "programs"),
    Module("skill", lambda: [skill_tool.TOOL], _skill),
    Module("chats", lambda: [pastchats.TOOL], _chats),
    Module("schedule", _schedule_tools, _schedule),
    # find_tools and use_tool: the deferred app tools and the MCP servers' tools.
    Module("mcp", _loader, _mcp),
)

# The calls that only read: run side by side within a round. Every other call waits its turn, in the model's order.
READS = frozenset({"web_search", "read_page", "read_list", "look", "read_file", "find_files", "recall",
                   "pages_they_read", pastchats.NAME})


@dataclass(frozen=True)
class Offer:
    # Sent with every request of the turn.
    tools: list
    # The parameters of every tool the turn may call by name: those sent and the deferred app tools.
    schemas: dict
    # The app tools reached through find_tools and use_tool.
    deferred: list


def definitions() -> list:
    """Every app module's definitions in order, a scheduled run keeping to what it may use (schedule_tool.py). A
    switch that is off, a skill installed or switched (their list is in the turn's context, skills/tool.py), a
    choice under "+" or the words of a message change none of it."""
    tools = [t for module in MODULES[:-1] for t in module.definitions()]
    return schedule_tool.offered(tools)


def offered(budget: context.Budget) -> Offer:
    """The turn's tools for a window of budget.window tokens: each app module in catalog order up front while the
    definitions so far stay within SHARE of the window, the others deferred behind find_tools and use_tool with the
    MCP servers' tools. A scheduled run keeps its few tools up front and offers no server's."""
    tools = definitions()
    run = schedule_tool.run_of() is not None
    room, used, up, deferred = SHARE * budget.window, 0.0, [], []
    for module in MODULES[:-1]:
        mine = [t for t in tools if module.owns(t["function"]["name"])]
        size = context.text_tokens(json.dumps(mine, ensure_ascii=False)) if mine else 0.0
        if run or used + size <= room:
            up, used = up + mine, used + size
        else:
            deferred += mine
    loader = [] if run else mcp_offer.functions(deferred, mcp_offer.servers())
    return Offer(up + loader, {t["function"]["name"]: t["function"].get("parameters") or {} for t in tools + loader},
                 deferred)


def module_of(name: str) -> Module | None:
    """The module a call of `name` goes to; None for a name no module has."""
    return next((module for module in MODULES if module.owns(name)), None)

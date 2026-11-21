"""The tools the reply model is offered, the same in every round and turn: a fixed list of tool modules, each with
its definitions and the one runner its calls go to."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from . import mcp_offer, pastchats, schedule_tool, search
from .desk import tool as desk_tool
from .shell import tool as shell_tool
from .skills import tool as skill_tool
from .web import connect


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
        if self.definitions is mcp_offer.functions:
            return mcp_offer.owns(name)
        if self.definitions is _schedule_tools:
            return schedule_tool.owns(name)
        return any(t["function"]["name"] == name for t in self.definitions())


def _schedule_tools() -> list:
    return schedule_tool.TOOLS


async def _web(agent, called: dict, args: dict, lookups) -> str:
    return await agent._web_tool(called["name"], args, lookups)


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
    Module("computer", lambda: shell_tool.TOOLS, _shell, "computer", frozenset({shell_tool.PLAN_TOOL["function"]["name"]})),
    Module("program", lambda: desk_tool.OFFERED, _desk, "programs"),
    Module("skill", lambda: [skill_tool.TOOL], _skill),
    Module("chats", lambda: [pastchats.TOOL], _chats),
    Module("schedule", _schedule_tools, _schedule),
    Module("mcp", mcp_offer.functions, _mcp),
)

# The calls that only read: run side by side within a round. Every other call waits its turn, in the model's order.
READS = frozenset({"web_search", "read_page", "read_list", "look", "read_file", "find_files", "recall",
                   "pages_they_read", pastchats.NAME})


def offered() -> list:
    """Every module's definitions in order, use_skill naming the installed skills that are on (skills/tool.py) and
    a scheduled run keeping to what it may use (schedule_tool.py). A switch that is off, a choice under "+" or the
    words of a message change none of it."""
    tools = [t for module in MODULES[:-1] for t in module.definitions()]
    return schedule_tool.offered(skill_tool.offered(tools) + mcp_offer.functions())


def module_of(name: str) -> Module | None:
    """The module a call of `name` goes to; None for a name no module has."""
    return next((module for module in MODULES if module.owns(name)), None)

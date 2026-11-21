"""The chat's tools for an endpoint that takes no tool calls: the same tools, called in writing.

The model gets the tools described in its instructions and writes each call as a <tool_call> block holding
{"name": …, "arguments": {…}}, the form Hermes and Qwen models are trained on and the one lookup_need.written_calls
already reads. The app parses the blocks, runs the calls as if they had been sent as tool calls, and hands the
results back as a message. Used when the endpoint refused tools (llm.py remembers that), or when TOOL_CALLS=written
says the endpoint's own tool calling is poor.
"""
from __future__ import annotations

import asyncio
import json
import os
import re

RULES = (
    "\n\n<written_tools>\nThis endpoint takes no tool calls, so you call tools in writing. To call one, write a block "
    "like this, with the arguments as JSON, and nothing after your blocks:\n"
    "<tool_call>{\"name\": \"read_file\", \"arguments\": {\"path\": \"notes.txt\"}}</tool_call>\n"
    "Up to three blocks at a time. The app runs them and sends you the results in the next message; then go on, "
    "or answer as usual when nothing more is needed. Never write a result yourself.\nThe tools:\n{tools}\n"
    "</written_tools>")
_BLOCK = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.DOTALL)


def wanted(agent, model: str) -> bool:
    """The endpoint refused tool calls (remembered by llm.py), or TOOL_CALLS=written asks for writing."""
    if os.environ.get("TOOL_CALLS", "").strip().lower() == "written":
        return True
    url, _ = agent._endpoint_for(model)
    return ("tools", url, model) in agent._api_quirks


def describe(tools: list[dict]) -> str:
    rows = []
    for tool in tools:
        fn = tool["function"]
        props = fn.get("parameters", {}).get("properties", {})
        need = set(fn.get("parameters", {}).get("required", []))
        params = ", ".join(f"{k}{'' if k in need else '?'}: {v.get('type', 'string')}"
                           + (f" ({v['description']})" if v.get("description") else "") for k, v in props.items())
        rows.append(f"- {fn['name']}({params}): {fn.get('description', '')}")
    return "\n".join(rows)


def _flat(messages: list[dict]) -> list[dict]:
    """The conversation with its tool calls and results as plain messages, one role after the other."""
    out: list[dict] = []
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if isinstance(content, list):
            content = " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
        content = str(content or "")
        if role == "assistant" and m.get("tool_calls") and "<tool_call>" not in content:
            content += "".join(
                "\n<tool_call>" + json.dumps({"name": c["function"]["name"],
                                              "arguments": json.loads(c["function"].get("arguments") or "{}")},
                                             ensure_ascii=False) + "</tool_call>" for c in m["tool_calls"])
        elif role == "tool":
            role, content = "user", "[Result of your tool call, from the app]\n" + content
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + content
        else:
            out.append({"role": role, "content": content})
    return out


def system_of(system: str, tools: list[dict]) -> str:
    """The system prompt with the tools described in writing, as `ask` sends it."""
    return system + RULES.replace("{tools}", describe(tools))


async def ask(agent, system: str, messages: list[dict], model: str, tools: list[dict], *,
                timeout: float) -> dict | None:
    """One reply-model call with the tools described in writing: {"content"}, or None when it failed."""
    try:
        text = await asyncio.wait_for(agent._call_llm(
            system_of(system, tools), _flat(messages), model, max_tokens=4096,
            disable_thinking=True), timeout)
    except asyncio.TimeoutError:
        return None
    return {"content": text} if text else None


def calls(content: str, offered: set) -> list[dict]:
    """The <tool_call> blocks in `content` as tool calls (offered tools only, at most three)."""
    found = []
    decoder = json.JSONDecoder()
    for body in _BLOCK.findall(content or ""):
        body = re.sub(r"^```(?:json)?\s*", "", body.strip())
        try:
            value, _ = decoder.raw_decode(body)
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        name = str(value.get("name") or value.get("tool") or "")
        args = value.get("arguments", value.get("parameters", value.get("args", {})))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if name in offered and isinstance(args, dict):
            found.append({"function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}})
    return found[:3]

"""The model transport on a real endpoint, through the app's own call path (mibo/llm.py on mibo/transport.py):
hidden reasoning off and on, the reasoning and cost read from the answer, a tool call, a picture, and how an
unknown model is classed. Prints numbers and shapes only, never the key.

    python tools/transport_check.py <folder with .env> [model]

The .env gives LLM_BASE_URL and LLM_API_KEY; the model defaults to deepseek/deepseek-v4.1-flash on OpenRouter.
A run costs about a hundredth of a cent there."""
from __future__ import annotations

import asyncio
import base64
import json
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path

import openai

# This checkout's mibo, not the one the environment has installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mibo import drafting  # noqa: E402
from mibo.endpoints import chat_completions_url  # noqa: E402

MODEL = "deepseek/deepseek-v4.1-flash"


def read_env(folder: Path) -> dict:
    out = {}
    for line in (folder / ".env").read_text(encoding="utf-8").splitlines():
        name, eq, value = line.partition("=")
        if eq and name.strip() in ("LLM_API_KEY", "LLM_BASE_URL"):
            out[name.strip()] = value.strip().strip('"').strip("'")
    return out


def picture() -> str:
    """A 16-pixel red square as a PNG data URL."""
    rows = b"".join(b"\x00" + b"\xdc\x14\x14" * 16 for _ in range(16))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(raw).decode()


class Caller(drafting._Client):
    """The app's call path, keeping each answer's body instead of writing a tally."""

    def __init__(self, values: dict) -> None:
        super().__init__(values, Path(tempfile.gettempdir()) / "transport_check_usage.json")
        self.bodies: list[dict] = []

    def _count_usage(self, model: str, body: dict) -> None:
        self.bodies.append(body)


def shape(body: dict, began: float) -> dict:
    message = body["choices"][0]["message"]
    usage = body.get("usage") or {}
    return {"content": message.get("content"), "finish": body["choices"][0].get("finish_reason"),
            "reasoning chars": len(str(message.get("reasoning") or message.get("reasoning_content") or "")),
            "reasoning tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            "cost": usage.get("cost"), "s": round(time.monotonic() - began, 2)}


async def main(folder: Path, model: str) -> dict:
    env = read_env(folder)
    caller = Caller({**env, "LLM_MODEL": model})
    url, key = chat_completions_url(env["LLM_BASE_URL"]), env["LLM_API_KEY"]
    ask = [{"role": "user", "content": "In one word: what colour is the sky on a clear day?"}]
    out: dict = {"sdk": openai.__version__, "model": model}

    for label, off in (("thinking off", True), ("thinking on", False)):
        began = time.monotonic()
        await caller._call_llm("Answer briefly.", ask, model, 1500, disable_thinking=off)
        out[label] = shape(caller.bodies[-1], began)

    tool = {"type": "function", "function": {"name": "get_time", "description": "The current time in a city.",
                                             "parameters": {"type": "object", "properties": {
                                                 "city": {"type": "string"}}, "required": ["city"]}}}
    began = time.monotonic()
    body = await caller._post_chat(url, key, {
        "model": model, "max_tokens": 300, "tools": [tool], "tool_choice": "required",
        "reasoning": {"enabled": False}, "messages": [{"role": "user", "content": "What time is it in Paris?"}]},
        timeout=60)
    out["tool call"] = {**shape(body, began), "calls": [
        (c["function"]["name"], json.loads(c["function"]["arguments"]))
        for c in body["choices"][0]["message"].get("tool_calls") or []]}

    began = time.monotonic()
    body = await caller._post_chat(url, key, {
        "model": model, "max_tokens": 100, "reasoning": {"enabled": False}, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "What colour is this square? One word."},
            {"type": "image_url", "image_url": {"url": picture()}}]}]}, timeout=60)
    out["picture"] = shape(body, began)

    try:
        await caller._post_chat(url, key, {"model": "no-such/model-x", "messages": ask, "max_tokens": 5}, timeout=60)
    except openai.APIStatusError as e:
        out["unknown model"] = {"status": e.status_code, "class": caller._classify_api_error(e)}
    out["spent usd"] = round(sum(float((b.get("usage") or {}).get("cost") or 0) for b in caller.bodies), 6)
    return out


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else MODEL)),
                     ensure_ascii=False, indent=1))

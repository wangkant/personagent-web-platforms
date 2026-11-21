"""The one client every model call goes through: the official OpenAI SDK (AsyncOpenAI) on the person's own endpoint.

    client(url, key, retries=0)   the AsyncOpenAI for a chat-completions URL and key, kept per event loop; the SDK
                                  retries 408, 409, 429, 5xx and dropped connections `retries` times, waiting what
                                  Retry-After says (up to two minutes) or backing off (AsyncOpenAI below), and
                                  never for a 429 that says the balance is gone
    args(payload)                 a request dict as create()'s keywords: the fields the SDK names, and the rest
                                  (thinking switches and other providers' own fields) in extra_body

Only the person's key and standard headers leave the computer: the SDK's X-Stainless-* headers (system, processor,
Python version, retry count) and the OpenAI account headers it would read from the environment are taken off every
request, and a keyless endpoint gets no Authorization header at all."""
from __future__ import annotations

import asyncio
import inspect
import weakref
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx2
import openai
from openai.resources.chat.completions import AsyncCompletions

from .endpoints import model_problem

# Long keep-alive: turns are further apart than httpx's 5 s expiry.
_LIMITS = httpx2.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=300.0)
_OWN = {"openai-organization", "openai-project"}
# What create() takes by name; everything else goes in extra_body.
_NAMED = set(inspect.signature(AsyncCompletions.create).parameters) - {
    "self", "extra_headers", "extra_query", "extra_body", "timeout"}
_KEYLESS = "keyless"
# Where a service states no Retry-After: this many times the SDK's own backoff (AsyncOpenAI below).
WAIT_SCALE = 3

_clients: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


class AsyncOpenAI(openai.AsyncOpenAI):
    """The SDK's client under its own name (which its User-Agent carries), waiting three times its own backoff
    (about 1.5 s, then 3 s) where a service says nothing of when to come back: OpenRouter's and DeepSeek's 429s
    carry no Retry-After, and the SDK's half-second first wait spends both retries inside a two-second limit
    window. A stated Retry-After is waited as it says."""

    def _calculate_retry_timeout(self, remaining_retries, options, response_headers=None) -> float:
        wait = super()._calculate_retry_timeout(remaining_retries, options, response_headers)
        return wait if self._parse_retry_after_header(response_headers) else WAIT_SCALE * wait


def _quiet(keyless: bool):
    async def hook(request: httpx2.Request) -> None:
        for name in [n for n in request.headers if n.lower().startswith("x-stainless") or n.lower() in _OWN
                     or (keyless and n.lower() == "authorization")]:
            del request.headers[name]
    return hook


async def _account(response: httpx2.Response) -> None:
    """A 429 that says the balance or quota is gone (OpenAI, Kimi, Zhipu, DashScope send one) is not asked again:
    x-should-retry, the SDK's own header for it, set on the answer."""
    if response.status_code == 429:
        await response.aread()
        if model_problem(429, response.text.lower()) == "no_balance":
            response.headers["x-should-retry"] = "false"


def client(url: str, key: str, retries: int = 0) -> openai.AsyncOpenAI:
    """The client for `url` (a chat-completions or embeddings URL, as endpoints gives them) with `key`. It lives
    as long as its event loop: every caller on the loop shares its connections, so none closes it."""
    kept = _clients.setdefault(asyncio.get_running_loop(), {})
    slot = (url, key, retries)
    if slot not in kept:
        base = kept.get((url, key, 0))
        if base is None:
            parts = urlsplit(url)
            path = parts.path.removesuffix("/chat/completions").removesuffix("/embeddings")
            root = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
            base = kept[(url, key, 0)] = AsyncOpenAI(
                base_url=root, api_key=key or _KEYLESS, max_retries=0,
                default_query=dict(parse_qsl(parts.query)) or None,
                http_client=openai.DefaultAsyncHttpxClient(limits=_LIMITS,
                                                           event_hooks={"request": [_quiet(not key)],
                                                                        "response": [_account]}))
        kept[slot] = base if retries == 0 else base.with_options(max_retries=retries)
    return kept[slot]


def args(payload: dict) -> dict:
    named = {k: v for k, v in payload.items() if k in _NAMED}
    rest = {k: v for k, v in payload.items() if k not in _NAMED}
    return {**named, "extra_body": rest} if rest else named


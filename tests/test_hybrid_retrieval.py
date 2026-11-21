"""Tests for the embedding side of recall, left for the stage that rebuilds recall: an embedding neighbour
outranks a word match, vectors are cached, and an unreachable endpoint is reported once. The lexical and
recency ranking and the cache prune are in tests/parts/ranking/test_recall_ranking.py.

The embedding path runs through a real agent, against an /embeddings endpoint served by
httpx.MockTransport with hand-picked vectors. Nothing here reaches a network or needs a key.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

from mibo import ranking
from mibo.agent import Agent
from mibo.llm import _PooledHTTP

from helpers import check, isolated_agent


# ---------------------------------------------------------------------------
# Ranking with an embedding
# ---------------------------------------------------------------------------

def test_an_embedding_neighbour_with_no_shared_words_needs_embeddings() -> None:
    w = ranking.MEMORY_WEIGHTS
    query = [1.0, 0.0, 0.0]
    paraphrase = dict(lexical=0.0, vec=[0.6, 0.8, 0.0])  # unit length, cosine 0.6
    word_match = dict(lexical=0.3, vec=[0.0, 0.0, 1.0])

    def rank(q) -> list[str]:
        scored = {name: ranking.score(w, lexical=r["lexical"],
                                      similarity=ranking.cosine(q, r["vec"]))
                  for name, r in (("paraphrase", paraphrase), ("word_match", word_match))}
        return sorted(scored, key=scored.get, reverse=True)

    check("embedding: without a query vector the word match wins",
          rank(None) == ["word_match", "paraphrase"])
    check("embedding: with one the paraphrase wins",
          rank(query) == ["paraphrase", "word_match"])
    check("embedding: a missing row vector is no signal, not an error",
          ranking.cosine(query, None) is None)


# ---------------------------------------------------------------------------
# Through the real agent
# ---------------------------------------------------------------------------

def make_agent(tmp: Path, **settings) -> Agent:
    """The embedding cache sits beside memory.json, so it is in `tmp` too."""
    return isolated_agent(tmp, **{"persona_name": "B", **settings})


# ---------------------------------------------------------------------------
# Embeddings, against a stub endpoint
# ---------------------------------------------------------------------------

EMBEDDING = dict(embedding_model="stub-embed",
                 embedding_base_url="http://embed.test/v1")
FELINE = [1.0, 0.0, 0.0]
OTHER = [0.0, 0.0, 1.0]
QUERY = "my kitten threw up"


class StubEndpoint:
    """An OpenAI-style /embeddings endpoint: FELINE for any text naming a cat,
    OTHER for the rest. Records every request."""

    def __init__(self, *, status: int = 200, delay_s: float = 0.0):
        self.status, self.delay_s = status, delay_s
        self.requests: list[list[str]] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        self.requests.append(texts)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "unavailable"})
        return httpx.Response(200, json={"data": [
            {"index": i, "embedding": FELINE if ("feline" in t or "kitten" in t) else OTHER}
            for i, t in enumerate(texts)]})


def serve(agent: Agent, endpoint: StubEndpoint) -> httpx.AsyncClient:
    client = httpx.AsyncClient(transport=httpx.MockTransport(endpoint))
    agent._http = lambda **kw: _PooledHTTP(client)
    return client


async def prepared(a: Agent, focus: str = QUERY) -> None:
    """One turn's async half: the query now, what was read in the background."""
    await a._prepare_retrieval(focus, "g1")
    if a._embedding_backfill is not None:
        await a._embedding_backfill


async def test_vectors_are_cached_in_memory_and_on_disk(tmp: Path) -> None:
    a = make_agent(tmp, **EMBEDDING)
    endpoint = StubEndpoint()
    client = serve(a, endpoint)
    try:
        await prepared(a)
        check("first run: the query is embedded", endpoint.requests == [[QUERY]], repr(endpoint.requests))

        endpoint.requests.clear()
        await prepared(a)
        check("second run: no request at all", endpoint.requests == [], repr(endpoint.requests))

        restarted = make_agent(tmp, **EMBEDDING)
        restarted._http = a._http
        await prepared(restarted)
        check("after a restart: only the query, which is never written to disk",
              endpoint.requests == [[QUERY]], repr(endpoint.requests))
    finally:
        await client.aclose()


async def test_the_startup_probe_reports_an_unreachable_endpoint(tmp: Path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="agent")
    a = make_agent(tmp, **EMBEDDING)
    client = serve(a, StubEndpoint(status=404))
    try:
        await a._probe_embeddings()
    finally:
        await client.aclose()
    check("probe: a configured but unreachable endpoint is reported",
          any("embedding endpoint failed" in r.getMessage() and r.levelno == logging.WARNING
              for r in caplog.records))
    check("probe: and not retried on the next turn",
          a._embedding_retry_at > time.monotonic())

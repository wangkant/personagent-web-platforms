"""Optional embedding vectors for retrieval: the /embeddings call and its cache.

Off unless EMBEDDING_MODEL is set. Vectors are fetched in the async path ahead
of a turn and cached in memory and in the runtime database by model and text
hash; the ranking only ever reads the cache."""
from __future__ import annotations

import base64
import hashlib
import math
import sys
from array import array
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from . import db

#: Texts per request: some OpenAI-compatible providers refuse more than 10.
BATCH_SIZE = 10
#: Fits a 512-token model; a longer input is refused by some, failing its batch.
MAX_TEXT_CHARS = 500
#: The query is embedded while the turn waits for it.
QUERY_TIMEOUT_S = 3.0
BATCH_TIMEOUT_S = 30.0
#: How long a failed endpoint is left alone before the next try.
RETRY_AFTER_S = 300.0
QUERY_CACHE_SIZE = 64


def clip(text: str) -> str:
    return (text or "").strip()[:MAX_TEXT_CHARS]


def text_key(text: str) -> str:
    """Cache key of a text as it is embedded."""
    return hashlib.sha256(clip(text).encode("utf-8")).hexdigest()[:32]


def unit(values) -> array:
    vec = array("f", values)
    norm = math.sqrt(sum(x * x for x in vec))
    if not norm:
        raise ValueError("zero or empty embedding")
    return array("f", (x / norm for x in vec))


async def fetch(client, url: str, api_key: str, model: str,
                texts: list[str]) -> list[array]:
    """Unit vectors for `texts`, in order, from one OpenAI-style request."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    resp = await client.post(url, headers=headers, json={
        "model": model, "input": [clip(t) for t in texts]})
    resp.raise_for_status()
    rows = sorted(resp.json()["data"], key=lambda row: row.get("index", 0))
    if len(rows) != len(texts):
        raise ValueError(f"{len(texts)} texts sent, {len(rows)} vectors back")
    return [unit(row["embedding"]) for row in rows]


def _bytes(vec: array) -> bytes:
    vec = array("f", vec)
    if sys.byteorder == "big":
        vec.byteswap()
    return vec.tobytes()


def _vector(blob: bytes) -> array:
    vec = array("f")
    vec.frombytes(blob)
    if sys.byteorder == "big":
        vec.byteswap()
    return vec


def blob_of(text: str) -> bytes:
    """A vector as the old cache file wrote it (base64 of little-endian floats), as the database keeps it."""
    return base64.b64decode(text)


class EmbeddingCache:
    """Unit vectors of one model by text key, kept in memory and in the runtime database (db.py) so a restart
    does not re-embed. Query vectors are one-off, so they stay in a small in-memory LRU and are never stored."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.db = db.at(self.path)
        self._model: Optional[str] = None
        self._stored: dict[str, array] = {}
        self._queries: OrderedDict[str, array] = OrderedDict()

    def _load(self, model: str) -> None:
        if model == self._model:
            return
        self._model = model
        self._queries.clear()
        self._stored = {key: _vector(blob) for key, blob in self.db.read(
            "SELECT key, vec FROM embeddings WHERE model = ?", (model,))}

    def get(self, model: str, key: str) -> Optional[array]:
        self._load(model)
        vec = self._stored.get(key)
        return self._queries.get(key) if vec is None else vec

    def has(self, model: str, key: str) -> bool:
        return self.get(model, key) is not None

    def put(self, model: str, vectors: dict[str, array], *, persist: bool) -> None:
        self._load(model)
        if not persist:
            for key, vec in vectors.items():
                self._queries[key] = vec
                self._queries.move_to_end(key)
            while len(self._queries) > QUERY_CACHE_SIZE:
                self._queries.popitem(last=False)
            return
        self._stored.update(vectors)
        with self.db.write() as conn:
            conn.executemany("INSERT OR REPLACE INTO embeddings VALUES (?,?,?)",
                             [(model, key, _bytes(vec)) for key, vec in vectors.items()])

    def prune(self, model: str, keep: set[str]) -> None:
        """Only `keep` stays: not the vectors of forgotten memories, rotated rows or another model."""
        self._load(model)
        gone = [key for key in self._stored if key not in keep]
        for key in gone:
            del self._stored[key]
        with self.db.write() as conn:
            conn.execute("DELETE FROM embeddings WHERE model != ?", (model,))
            conn.executemany("DELETE FROM embeddings WHERE model = ? AND key = ?", [(model, key) for key in gone])

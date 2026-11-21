"""How remembered lines are ranked for recall (person.py, chatlog.py): BM25 over the pool, Chinese by
two-character pieces, a newer line before an older one, and the embedding cache keeping only what is used."""
from __future__ import annotations

from mibo import embeddings, ranking
from mibo.textproc import _focus_tokens

DAY = 86400.0


def test_the_lexical_signal_is_bm25_over_the_pool() -> None:
    docs = [("deploy x1", ""), ("chatter 1", ""), ("others z2", "")]
    lex = ranking.Lexicon({"deploy"}, docs)
    scores = [lex.score(d, (1.0, 0.3)) for d in docs]
    # One term of average rarity in a row of average length counts 1.0.
    assert scores[0] > 0 and scores[1] == scores[2] == 0.0
    assert abs(scores[0] - 1.0) < 1e-9, scores

    docs = [("rare aa", ""), ("common a", ""), ("common b", ""), ("common c", "")]
    lex = ranking.Lexicon({"rare", "common"}, docs)
    assert lex.score(docs[0], (1.0, 0.3)) > lex.score(docs[1], (1.0, 0.3)), "a rare term outweighs a common one"
    assert lex.score(("rare", ""), (1.0, 0.3)) > lex.score(("", "rare"), (1.0, 0.3)), "the first field counts most"
    assert ranking.Lexicon(set(), docs).score(docs[0], (1.0, 0.3)) == 0.0

    zh_docs = [("服务器又挂了",), ("今天吃什么",)]
    zh = ranking.Lexicon(_focus_tokens("服务器挂了怎么办", "zh"), zh_docs)
    assert zh.score(zh_docs[0], (1.0,)) > 0 == zh.score(zh_docs[1], (1.0,))


def test_a_newer_line_ranks_before_an_older_one_that_says_the_same() -> None:
    assert (ranking.recency(0, 14), ranking.recency(14 * DAY, 14), ranking.recency(28 * DAY, 14)) == (1.0, 0.5, 0.25)
    assert ranking.recency(-30 * DAY, 14) == 1.0, "a time in the future counts as now"
    weights = ranking.MEMORY_WEIGHTS
    assert ranking.score(weights, lexical=1.0, age_s=DAY) > ranking.score(weights, lexical=1.0, age_s=30 * DAY)


def test_the_embedding_cache_keeps_only_the_vectors_still_used(tmp_path) -> None:
    cache = embeddings.EmbeddingCache(tmp_path / "embeddings.jsonl")
    vec = embeddings.unit([1.0, 2.0])
    cache.put("m", {f"k{i}": vec for i in range(150)}, persist=True)
    cache.prune("m", {"k1", "k2"})
    assert len((tmp_path / "embeddings.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    fresh = embeddings.EmbeddingCache(tmp_path / "embeddings.jsonl")
    assert list(fresh.get("m", "k1")) == list(vec) and fresh.get("m", "k3") is None
    assert fresh.get("other-model", "k1") is None, "another model's vectors are not this one's"

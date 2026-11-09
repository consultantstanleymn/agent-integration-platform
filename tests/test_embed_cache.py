"""The Redis embedding cache, tested with fakeredis."""

import fakeredis
from prometheus_client import REGISTRY

from aip.config import Settings
from aip.providers import build_embedder
from aip.providers.cache import CachedEmbedder


class CountingEmbedder:
    name = "counting"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[float(len(t)), 1.0] for t in texts]


def _value(name: str) -> float:
    return REGISTRY.get_sample_value(name) or 0.0


def test_cache_hit_skips_inner():
    inner = CountingEmbedder()
    cache = CachedEmbedder(inner, fakeredis.FakeRedis())
    first = cache.embed(["return window"])
    second = cache.embed(["return window"])
    assert first == second == [[13.0, 1.0]]
    assert len(inner.calls) == 1


def test_cache_miss_calls_inner_once():
    inner = CountingEmbedder()
    cache = CachedEmbedder(inner, fakeredis.FakeRedis())
    out = cache.embed(["a", "bb", "a"])  # "a" appears twice in one call: both miss the empty cache
    assert out == [[1.0, 1.0], [2.0, 1.0], [1.0, 1.0]]
    assert len(inner.calls) == 1
    mixed = cache.embed(["a", "ccc"])  # only the new text goes to the inner embedder
    assert inner.calls[-1] == ["ccc"] and mixed == [[1.0, 1.0], [3.0, 1.0]]


def test_hit_and_miss_counters():
    cache = CachedEmbedder(CountingEmbedder(), fakeredis.FakeRedis())
    hits, misses = _value("aip_embed_cache_hits_total"), _value("aip_embed_cache_misses_total")
    cache.embed(["x"])
    cache.embed(["x"])
    cache.embed(["x", "y"])
    assert _value("aip_embed_cache_misses_total") - misses == 2
    assert _value("aip_embed_cache_hits_total") - hits == 2


def test_cache_key_includes_model():
    client = fakeredis.FakeRedis()
    one = CachedEmbedder(CountingEmbedder(), client, model="model-a")
    two = CachedEmbedder(CountingEmbedder(), client, model="model-b")
    assert one._key("same text") != two._key("same text")
    one.embed(["same text"])
    two.embed(["same text"])
    assert len(client.keys("aip:emb:*")) == 2


def test_factory_wraps_only_when_enabled(monkeypatch):
    assert not isinstance(build_embedder(Settings()), CachedEmbedder)
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr("redis.Redis.from_url", lambda url: fake)
    wrapped = build_embedder(Settings(redis_url="redis://x/0", embed_cache=True))
    assert isinstance(wrapped, CachedEmbedder)

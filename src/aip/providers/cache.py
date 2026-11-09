"""Cache query embeddings in Redis. A hit skips the inner embedder.

The key is the SHA-256 of the model name plus the text, so two models never share an entry.
Counters ``aip_embed_cache_hits_total`` and ``aip_embed_cache_misses_total`` give the hit ratio.
"""

import hashlib
import json
from typing import Any

from prometheus_client import Counter

from aip.providers.base import Embedder

EMBED_CACHE_HITS = Counter("aip_embed_cache_hits_total", "Embedding cache hits")
EMBED_CACHE_MISSES = Counter("aip_embed_cache_misses_total", "Embedding cache misses")


class CachedEmbedder:
    def __init__(
        self,
        inner: Embedder,
        client: Any,
        ttl_s: int = 3600,
        namespace: str = "aip:emb:",
        model: str | None = None,
    ) -> None:
        self.inner = inner
        self.client = client
        self.ttl_s = ttl_s
        self.namespace = namespace
        self.model = (
            model or getattr(inner, "model_name", None) or getattr(inner, "name", type(inner).__name__)
        )

    def _key(self, text: str) -> str:
        return self.namespace + hashlib.sha256(f"{self.model}\n{text}".encode()).hexdigest()

    def embed(self, texts: list[str]) -> list[list[float]]:
        keys = [self._key(t) for t in texts]
        cached = self.client.mget(keys) if keys else []
        out: list[list[float] | None] = []
        missing: list[int] = []
        for i, raw in enumerate(cached):
            if raw is None:
                out.append(None)
                missing.append(i)
                EMBED_CACHE_MISSES.inc()
            else:
                out.append(json.loads(raw))
                EMBED_CACHE_HITS.inc()
        if missing:
            fresh = self.inner.embed([texts[i] for i in missing])
            pipe = self.client.pipeline()
            for i, vector in zip(missing, fresh, strict=True):
                out[i] = vector
                pipe.set(keys[i], json.dumps(vector), ex=self.ttl_s)
            pipe.execute()
        return [v for v in out if v is not None]

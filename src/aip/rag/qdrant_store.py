"""Qdrant behind the retriever port.

Vector mode runs in Qdrant with a tenant and label filter. Keyword and hybrid modes are weaker here:
Qdrant holds no full-text index in this adapter, so the keyword list comes from the Python keyword
scorer over the points that pass the filter (it reads them with a scroll). That is fine for a few
thousand chunks and wrong for millions. Hybrid fuses the two lists with ``rrf``.
"""

import hashlib
from typing import Any

from qdrant_client import QdrantClient, models

from aip.rag.retriever import ScoredHit, SearchQuery, keyword_score, rrf


def point_id(tenant_id: int, doc_id: int, idx: int) -> int:
    digest = hashlib.sha256(f"{tenant_id}:{doc_id}:{idx}".encode()).digest()
    return int.from_bytes(digest[:7], "big")  # fits a 63-bit unsigned id


class QdrantStore:
    def __init__(self, client: QdrantClient, collection: str, dim: int, rrf_k: int = 60) -> None:
        self.client = client
        self.collection = collection
        self.dim = dim
        self.rrf_k = rrf_k
        if not client.collection_exists(collection):
            client.create_collection(
                collection,
                vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
            )

    def add(
        self,
        tenant_id: int,
        doc_id: int,
        doc_name: str,
        texts: list[str],
        vectors: list[list[float]],
        access_label: str = "public",
    ) -> None:
        points = [
            models.PointStruct(
                id=point_id(tenant_id, doc_id, i),
                vector=v,
                payload={
                    "tenant_id": tenant_id,
                    "label": access_label,
                    "doc_id": doc_id,
                    "doc": doc_name,
                    "chunk": i,
                    "text": t,
                },
            )
            for i, (t, v) in enumerate(zip(texts, vectors, strict=True))
        ]
        self.client.upsert(self.collection, points=points, wait=True)

    def _filter(self, q: SearchQuery) -> models.Filter:
        must: list[Any] = [
            models.FieldCondition(key="tenant_id", match=models.MatchValue(value=q.tenant_id)),
            models.FieldCondition(key="label", match=models.MatchAny(any=sorted(q.allowed_labels))),
        ]
        if q.doc_names is not None:
            must.append(models.FieldCondition(key="doc", match=models.MatchAny(any=q.doc_names)))
        return models.Filter(must=must)

    def _hit(self, payload: dict[str, Any]) -> ScoredHit:
        return ScoredHit(
            payload["doc"], payload["chunk"], 0.0, payload["text"], payload["doc_id"], payload["label"]
        )

    def search(self, q: SearchQuery) -> list[ScoredHit]:
        hits: dict[tuple[str, int], ScoredHit] = {}
        vector_rank: list[tuple[str, int]] = []
        keyword_rank: list[tuple[str, int]] = []
        if q.mode in ("vector", "hybrid"):
            if q.vector is None:
                raise ValueError("vector mode needs a query vector")
            pool = max(4 * q.k, 20) if q.mode == "hybrid" else q.k
            reply = self.client.query_points(
                self.collection, query=q.vector, query_filter=self._filter(q), limit=pool, with_payload=True
            )
            for rank, point in enumerate(reply.points, start=1):
                payload = point.payload or {}
                key = (payload["doc"], payload["chunk"])
                hit = hits.setdefault(key, self._hit(payload))
                hit.scores["vector"], hit.rank_by["vector"] = float(point.score), rank
                vector_rank.append(key)
        if q.mode in ("keyword", "hybrid"):
            scored: list[tuple[float, ScoredHit]] = []
            offset = None
            while True:
                records, offset = self.client.scroll(
                    self.collection,
                    scroll_filter=self._filter(q),
                    limit=256,
                    offset=offset,
                    with_payload=True,
                )
                for record in records:
                    payload = record.payload or {}
                    score = keyword_score(q.text, payload["text"])
                    if score > 0:
                        scored.append(
                            (score, hits.get((payload["doc"], payload["chunk"])) or self._hit(payload))
                        )
                if offset is None:
                    break
            scored.sort(key=lambda t: (-t[0], t[1].doc, t[1].chunk))
            for rank, (score, hit) in enumerate(scored, start=1):
                key = (hit.doc, hit.chunk)
                hit = hits.setdefault(key, hit)
                hit.scores["keyword"], hit.rank_by["keyword"] = score, rank
                keyword_rank.append(key)
        if q.mode == "vector":
            order = vector_rank
            for key in order:
                hits[key].score = hits[key].scores["vector"]
        elif q.mode == "keyword":
            order = keyword_rank
            for key in order:
                hits[key].score = hits[key].scores["keyword"]
        else:
            fused = rrf([vector_rank, keyword_rank], k=self.rrf_k)
            order = [key for key, _ in fused]
            for key, score in fused:
                hits[key].score = score
                hits[key].scores["fused"] = score
        return [hits[key] for key in order[: q.k]]

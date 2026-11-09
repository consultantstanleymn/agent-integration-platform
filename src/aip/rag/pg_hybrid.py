"""PostgreSQL retrieval: pgvector for nearest neighbours, a tsvector for keywords, fused by RRF.

Needs migration 0006 (the ``embedding_vec`` and ``tsv`` columns). Every query filters by tenant,
access label and, when given, document name. The candidate pool is ``max(4 * k, 20)`` per method.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from aip.models import Chunk
from aip.rag.retriever import ScoredHit, SearchQuery, rrf

FILTERS = "c.tenant_id = :tenant AND c.access_label = ANY(:labels)"
NAME_FILTER = " AND d.name = ANY(:names)"


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.8g}" for x in vector) + "]"


class PgHybridStore:
    def __init__(self, db: Session, rrf_k: int = 60) -> None:
        self.db = db
        self.rrf_k = rrf_k

    def add(
        self,
        tenant_id: int,
        document_id: int,
        texts: list[str],
        vectors: list[list[float]],
        access_label: str = "public",
    ) -> None:
        chunks = []
        for i, (t, v) in enumerate(zip(texts, vectors, strict=True)):
            chunk = Chunk(
                tenant_id=tenant_id,
                document_id=document_id,
                idx=i,
                text=t,
                embedding=v,
                access_label=access_label,
            )
            self.db.add(chunk)
            chunks.append((chunk, v))
        self.db.flush()
        for chunk, v in chunks:
            self.db.execute(
                text("UPDATE chunks SET embedding_vec = CAST(:v AS vector) WHERE id = :id"),
                {"v": vector_literal(v), "id": chunk.id},
            )

    def _params(self, q: SearchQuery, pool: int) -> dict[str, Any]:
        params: dict[str, Any] = {"tenant": q.tenant_id, "labels": sorted(q.allowed_labels), "n": pool}
        if q.doc_names is not None:
            params["names"] = q.doc_names
        return params

    def _vector_rows(self, q: SearchQuery, pool: int) -> list[Any]:
        if q.vector is None:
            raise ValueError("vector mode needs a query vector")
        where = FILTERS + (NAME_FILTER if q.doc_names is not None else "")
        sql = text(
            "SELECT c.id, d.name, c.idx, c.text, c.access_label, c.document_id, "
            "c.embedding_vec <=> CAST(:qv AS vector) AS dist "
            "FROM chunks c JOIN documents d ON d.id = c.document_id "
            f"WHERE {where} AND c.embedding_vec IS NOT NULL "
            "ORDER BY dist, d.name, c.idx LIMIT :n"
        )
        params = {**self._params(q, pool), "qv": vector_literal(q.vector)}
        return list(self.db.execute(sql, params))

    def _keyword_rows(self, q: SearchQuery, pool: int) -> list[Any]:
        where = FILTERS + (NAME_FILTER if q.doc_names is not None else "")
        sql = text(
            "SELECT c.id, d.name, c.idx, c.text, c.access_label, c.document_id, "
            "ts_rank_cd(c.tsv, query) AS rank "
            "FROM chunks c JOIN documents d ON d.id = c.document_id, "
            "websearch_to_tsquery('english', :qtext) query "
            f"WHERE {where} AND c.tsv @@ query "
            "ORDER BY rank DESC, d.name, c.idx LIMIT :n"
        )
        return list(self.db.execute(sql, {**self._params(q, pool), "qtext": q.text}))

    def search(self, q: SearchQuery) -> list[ScoredHit]:
        pool = max(4 * q.k, 20)
        hits: dict[int, ScoredHit] = {}
        vector_rank: list[int] = []
        keyword_rank: list[int] = []

        def hit_for(row: Any) -> ScoredHit:
            return hits.setdefault(
                row.id, ScoredHit(row.name, row.idx, 0.0, row.text, row.document_id, row.access_label)
            )

        if q.mode in ("vector", "hybrid"):
            for rank, row in enumerate(self._vector_rows(q, pool), start=1):
                hit = hit_for(row)
                hit.scores["vector"], hit.rank_by["vector"] = 1.0 - float(row.dist), rank
                vector_rank.append(row.id)
        if q.mode in ("keyword", "hybrid"):
            for rank, row in enumerate(self._keyword_rows(q, pool), start=1):
                hit = hit_for(row)
                hit.scores["keyword"], hit.rank_by["keyword"] = float(row.rank), rank
                keyword_rank.append(row.id)
        if q.mode == "vector":
            order = vector_rank
            for cid in order:
                hits[cid].score = hits[cid].scores["vector"]
        elif q.mode == "keyword":
            order = keyword_rank
            for cid in order:
                hits[cid].score = hits[cid].scores["keyword"]
        else:
            fused = rrf([vector_rank, keyword_rank], k=self.rrf_k)
            order = [cid for cid, _ in fused]
            for cid, score in fused:
                hits[cid].score = score
                hits[cid].scores["fused"] = score
        return [hits[cid] for cid in order[: q.k]]

"""One retrieval port for every store.

``SearchQuery`` says what to find and who may see it. A ``Retriever`` returns ``ScoredHit`` rows.
Modes: ``vector`` (cosine on the stored embedding), ``keyword`` (token overlap) and ``hybrid``
(reciprocal rank fusion of the two). Every mode filters by access label and by document name.
"""

import re
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from aip.models import Chunk, Document
from aip.rag.store import Hit, cosine

Mode = Literal["vector", "keyword", "hybrid"]
STOPWORDS = frozenset(
    "a an and are as at be by do does for how in is it of on or the to what when with".split()
)


@dataclass
class SearchQuery:
    tenant_id: int
    text: str
    vector: list[float] | None
    k: int
    mode: Mode = "vector"
    allowed_labels: frozenset[str] = frozenset({"public"})
    doc_names: list[str] | None = None


@dataclass
class ScoredHit(Hit):
    doc_id: int = 0
    label: str = "public"
    scores: dict[str, float] = field(default_factory=dict)  # per method, plus "fused" in hybrid mode
    rank_by: dict[str, int] = field(default_factory=dict)  # 1-based rank per method


class Retriever(Protocol):
    def search(self, q: SearchQuery) -> list[ScoredHit]: ...


def rrf[T: Hashable](rank_lists: Sequence[Sequence[T]], k: int = 60) -> list[tuple[T, float]]:
    """Reciprocal rank fusion. Each list is best first. Returns ``(item, fused_score)``, best first."""
    fused: dict[T, float] = {}
    first_seen: dict[T, int] = {}
    for ranks in rank_lists:
        for position, item in enumerate(ranks, start=1):
            fused[item] = fused.get(item, 0.0) + 1.0 / (k + position)
            first_seen.setdefault(item, len(first_seen))
    return sorted(fused.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]


def keyword_score(query: str, text: str) -> float:
    """Share of distinct query tokens found in the text. 0.0 when the query has no content words."""
    wanted = set(tokens(query))
    if not wanted:
        return 0.0
    return len(wanted & set(tokens(text))) / len(wanted)


class CosineRetriever:
    """Adapter over the original JSON-vector chunk table. Scores in Python."""

    def __init__(self, db: Session, rrf_k: int = 60) -> None:
        self.db = db
        self.rrf_k = rrf_k

    def _rows(self, q: SearchQuery) -> list[tuple[Chunk, str]]:
        stmt = (
            select(Chunk, Document.name)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.tenant_id == q.tenant_id, Chunk.access_label.in_(sorted(q.allowed_labels)))
        )
        if q.doc_names is not None:
            stmt = stmt.where(Document.name.in_(q.doc_names))
        return [(c, name) for c, name in self.db.execute(stmt).all()]

    def search(self, q: SearchQuery) -> list[ScoredHit]:
        rows = self._rows(q)
        by_id = {c.id: ScoredHit(name, c.idx, 0.0, c.text, c.document_id, c.access_label) for c, name in rows}
        vector_rank: list[int] = []
        keyword_rank: list[int] = []
        if q.mode in ("vector", "hybrid"):
            if q.vector is None:
                raise ValueError("vector mode needs a query vector")
            scored = [(cosine(q.vector, c.embedding), by_id[c.id], c.id) for c, _ in rows]
            scored.sort(key=lambda t: (-t[0], t[1].doc, t[1].doc_id, t[1].chunk))
            for rank, (score, hit, cid) in enumerate(scored, start=1):
                hit.scores["vector"], hit.rank_by["vector"] = score, rank
                vector_rank.append(cid)
        if q.mode in ("keyword", "hybrid"):
            scored_k = [(keyword_score(q.text, c.text), by_id[c.id], c.id) for c, _ in rows]
            scored_k = [t for t in scored_k if t[0] > 0]
            scored_k.sort(key=lambda t: (-t[0], t[1].doc, t[1].chunk))
            for rank, (score, hit, cid) in enumerate(scored_k, start=1):
                hit.scores["keyword"], hit.rank_by["keyword"] = score, rank
                keyword_rank.append(cid)
        if q.mode == "vector":
            order = vector_rank
            for cid in order:
                by_id[cid].score = by_id[cid].scores["vector"]
        elif q.mode == "keyword":
            order = keyword_rank
            for cid in order:
                by_id[cid].score = by_id[cid].scores["keyword"]
        else:
            fused = rrf([vector_rank, keyword_rank], k=self.rrf_k)
            order = [item for item, _ in fused]
            for item, score in fused:
                by_id[item].score = score
                by_id[item].scores["fused"] = score
        return [by_id[cid] for cid in order[: q.k]]

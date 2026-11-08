"""Vector store behind an interface. The default stores vectors as JSON and scores in Python.

To use pgvector, write another class with the same two methods. Nothing else changes.
"""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from aip.models import Chunk, Document


@dataclass
class Hit:
    doc: str
    chunk: int
    score: float
    text: str


class VectorStore(Protocol):
    def add(self, tenant_id: int, document_id: int, texts: list[str], vectors: list[list[float]]) -> None: ...

    def search(self, tenant_id: int, vector: list[float], k: int) -> list[Hit]: ...


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class SqlCosineStore:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, tenant_id: int, document_id: int, texts: list[str], vectors: list[list[float]]) -> None:
        for i, (t, v) in enumerate(zip(texts, vectors, strict=True)):
            self.db.add(Chunk(tenant_id=tenant_id, document_id=document_id, idx=i, text=t, embedding=v))
        self.db.flush()

    def search(self, tenant_id: int, vector: list[float], k: int) -> list[Hit]:
        # The tenant filter is part of the query. One tenant can never see another's chunks.
        rows = self.db.execute(
            select(Chunk, Document.name)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.tenant_id == tenant_id)
        ).all()
        hits = [Hit(name, c.idx, cosine(vector, c.embedding), c.text) for c, name in rows]
        hits.sort(key=lambda h: (-h.score, h.doc, h.chunk))  # stable tie-break
        return hits[:k]

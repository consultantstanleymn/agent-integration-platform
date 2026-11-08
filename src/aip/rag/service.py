from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from aip.models import Chunk, Document
from aip.providers.base import Embedder
from aip.rag.chunking import chunk_text
from aip.rag.store import Hit, SqlCosineStore


def ingest_document(db: Session, tenant_id: int, embedder: Embedder, name: str, text: str) -> int:
    """Chunk, embed and store a document. Re-ingesting the same name replaces it. Returns chunk count."""
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("document has no text")
    old = db.scalar(select(Document).where(Document.tenant_id == tenant_id, Document.name == name))
    if old:
        db.execute(delete(Chunk).where(Chunk.document_id == old.id))
        db.delete(old)
        db.flush()
    doc = Document(tenant_id=tenant_id, name=name)
    db.add(doc)
    db.flush()
    SqlCosineStore(db).add(tenant_id, doc.id, chunks, embedder.embed(chunks))
    return len(chunks)


def retrieve(db: Session, tenant_id: int, embedder: Embedder, query: str, k: int = 3) -> list[Hit]:
    vec = embedder.embed([query])[0]
    return SqlCosineStore(db).search(tenant_id, vec, k)

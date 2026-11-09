from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from aip.models import Chunk, Document
from aip.providers.base import Embedder
from aip.rag.chunking import chunk_text
from aip.rag.pg_hybrid import PgHybridStore
from aip.rag.retriever import CosineRetriever, Retriever, ScoredHit, SearchQuery
from aip.rag.store import Hit, SqlCosineStore


def labels_for_role(role: str) -> frozenset[str]:
    """Access labels a caller may read. Agents read public documents. Team leads also read team_lead."""
    return frozenset({"public", "team_lead"}) if role == "team_lead" else frozenset({"public"})


def ingest_document(
    db: Session,
    tenant_id: int,
    embedder: Embedder,
    name: str,
    text: str,
    access_label: str = "public",
) -> int:
    """Chunk, embed and store a document. Re-ingesting the same name replaces it. Returns chunk count."""
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("document has no text")
    old = db.scalar(select(Document).where(Document.tenant_id == tenant_id, Document.name == name))
    if old:
        db.execute(delete(Chunk).where(Chunk.document_id == old.id))
        db.delete(old)
        db.flush()
    doc = Document(tenant_id=tenant_id, name=name, access_label=access_label)
    db.add(doc)
    db.flush()
    store: SqlCosineStore | PgHybridStore
    if db.get_bind().dialect.name == "postgresql":
        store = PgHybridStore(db)
    else:
        store = SqlCosineStore(db)
    store.add(tenant_id, doc.id, chunks, embedder.embed(chunks), access_label)
    return len(chunks)


def retrieve(
    db: Session,
    tenant_id: int,
    embedder: Embedder,
    query: str,
    k: int = 3,
    role: str = "agent",
) -> list[Hit]:
    """Vector search over the chunks that ``role`` may read. The response shape of /v1/search is fixed."""
    vec = embedder.embed([query])[0]
    q = SearchQuery(
        tenant_id=tenant_id,
        text=query,
        vector=vec,
        k=k,
        mode="vector",
        allowed_labels=labels_for_role(role),
    )
    return [Hit(h.doc, h.chunk, h.score, h.text) for h in retrieve_q(db, q)]


def build_retriever(db: Session, retriever: str = "auto", rrf_k: int = 60) -> Retriever:
    """Pick the retrieval adapter. ``auto`` uses pgvector plus full text on PostgreSQL, else cosine."""
    on_postgres = db.get_bind().dialect.name == "postgresql"
    if retriever == "pg" or (retriever == "auto" and on_postgres):
        return PgHybridStore(db, rrf_k)
    if retriever in ("auto", "cosine"):
        return CosineRetriever(db, rrf_k)
    raise ValueError(f"unknown retriever: {retriever}")


def retrieve_q(db: Session, q: SearchQuery, retriever: Retriever | None = None) -> list[ScoredHit]:
    return (retriever or build_retriever(db)).search(q)

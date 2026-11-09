"""PostgreSQL hybrid retrieval. Needs AIP_TEST_PG_URL (a database with the pgvector extension)."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from aip.providers.fake import FakeProvider
from aip.rag.pg_hybrid import PgHybridStore
from aip.rag.retriever import SearchQuery
from aip.rag.service import ingest_document
from aip.tenants import create_tenant

pytestmark = pytest.mark.pg
ROOT = Path(__file__).resolve().parents[1]
DOCS = {
    "returns.md": "# Returns\n\nReturns are accepted within sixty days with the original packaging.",
    "warranty.md": "# Warranty\n\nTents carry a two year warranty against defects in material.",
    "guide.md": "# Guide\n\nApprove refunds above the limit after the order check. Code ZX9QK.",
}
LEAD = frozenset({"public", "team_lead"})


@pytest.fixture
def pg(pg_url, monkeypatch):
    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    monkeypatch.setenv("DATABASE_URL", pg_url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    emb = FakeProvider()
    a, _ = create_tenant(session, "a", ["search_knowledge_base"])
    b, _ = create_tenant(session, "b", ["search_knowledge_base"])
    for name, body in DOCS.items():
        ingest_document(session, a.id, emb, name, body)
    ingest_document(session, b.id, emb, "other.md", "# Other\n\nTenant b secret warranty text.")
    session.execute(text("UPDATE chunks SET access_label = 'team_lead' WHERE text LIKE '%ZX9QK%'"))
    session.commit()
    yield session, a, b, emb
    session.close()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


def _q(pg, query, tenant=None, **kw):
    _, a, _, emb = pg
    base = {"tenant_id": (tenant or a).id, "text": query, "vector": emb.embed([query])[0], "k": 5}
    return SearchQuery(**{**base, **kw})


def test_pg_vector_mode_returns_nearest(pg):
    hits = PgHybridStore(pg[0]).search(_q(pg, "tents warranty defects", mode="vector"))
    assert hits[0].doc == "warranty.md"
    assert hits[0].scores["vector"] > hits[-1].scores["vector"]


def test_pg_keyword_mode_uses_tsvector(pg):
    hits = PgHybridStore(pg[0]).search(_q(pg, "original packaging", mode="keyword"))
    assert [h.doc for h in hits] == ["returns.md"]
    assert hits[0].scores["keyword"] > 0


def test_pg_hybrid_contains_best_of_both(pg):
    hits = PgHybridStore(pg[0]).search(_q(pg, "warranty packaging", mode="hybrid", k=5))
    docs = [h.doc for h in hits]
    assert "warranty.md" in docs and "returns.md" in docs
    assert all("fused" in h.scores for h in hits)


def test_pg_tenant_isolation(pg):
    _, a, b, _ = pg
    hits_a = PgHybridStore(pg[0]).search(_q(pg, "tenant secret warranty", mode="hybrid"))
    assert all(h.doc != "other.md" for h in hits_a)
    hits_b = PgHybridStore(pg[0]).search(_q(pg, "tenant secret warranty", tenant=b, mode="hybrid"))
    assert {h.doc for h in hits_b} == {"other.md"}


def test_pg_label_filter(pg):
    store = PgHybridStore(pg[0])
    assert all(h.doc != "guide.md" for h in store.search(_q(pg, "ZX9QK approve refunds", mode="keyword")))
    lead = store.search(_q(pg, "ZX9QK approve refunds", mode="keyword", allowed_labels=LEAD))
    assert lead[0].doc == "guide.md"
    only = store.search(_q(pg, "refunds returns", mode="hybrid", allowed_labels=LEAD, doc_names=["guide.md"]))
    assert {h.doc for h in only} == {"guide.md"}

"""The retriever port: RRF, the cosine adapter, label and name filters."""

import pytest

from aip.models import Document
from aip.providers.fake import FakeProvider
from aip.rag.retriever import CosineRetriever, SearchQuery, keyword_score, rrf
from aip.rag.service import ingest_document, retrieve
from aip.rag.store import SqlCosineStore
from aip.tenants import create_tenant

DOCS = {
    "returns.md": "# Returns\n\nReturns are accepted within 60 days with the original packaging.",
    "warranty.md": "# Warranty\n\nTents carry a 2 year warranty against defects.",
    "guide.md": "# Team lead guide\n\nApprove refunds above the limit after the order check. Code ZX9QK.",
}


@pytest.fixture
def kb(db_session):
    tenant, _ = create_tenant(db_session, "r", ["search_knowledge_base"])
    emb = FakeProvider()
    for name, text in DOCS.items():
        ingest_document(db_session, tenant.id, emb, name, text)
    # mark the guide as team lead only
    from sqlalchemy import select, update

    from aip.models import Chunk

    guide = db_session.scalar(select(Document).where(Document.name == "guide.md"))
    guide.access_label = "team_lead"
    db_session.execute(update(Chunk).where(Chunk.document_id == guide.id).values(access_label="team_lead"))
    db_session.commit()
    return db_session, tenant, emb


def _q(kb, text, **kw):
    _, tenant, emb = kb
    defaults = {"tenant_id": tenant.id, "text": text, "vector": emb.embed([text])[0], "k": 5}
    return SearchQuery(**{**defaults, **kw})


def test_rrf_orders_by_fused_rank():
    fused = rrf([["a", "b", "c"], ["b", "c", "a"]], k=60)
    assert [item for item, _ in fused][0] == "b"
    assert {item for item, _ in fused} == {"a", "b", "c"}
    assert fused[0][1] > fused[1][1] >= fused[2][1]


def test_rrf_constant_changes_order():
    lists = [["x", "p", "q", "y"], ["r", "s", "t", "y"]]
    assert rrf(lists, k=1)[0][0] == "x"  # a small k rewards one top rank
    assert rrf(lists, k=1000)[0][0] == "y"  # a large k rewards agreement between lists


def test_cosine_retriever_vector_matches_old_store(kb):
    db, tenant, emb = kb
    old = SqlCosineStore(db).search(tenant.id, emb.embed(["warranty defects"])[0], 3)
    old = [h for h in old if h.doc != "guide.md"]
    new = CosineRetriever(db).search(_q(kb, "warranty defects", k=5))
    assert [(h.doc, h.chunk) for h in new][: len(old)] == [(h.doc, h.chunk) for h in old]
    assert [h.score for h in new][: len(old)] == [h.score for h in old]
    assert [h.doc for h in retrieve(db, tenant.id, emb, "warranty defects", 3)][0] == "warranty.md"


def test_keyword_mode_finds_exact_term(kb):
    db, _, _ = kb
    hits = CosineRetriever(db).search(_q(kb, "original packaging", mode="keyword"))
    assert hits and hits[0].doc == "returns.md"
    assert hits[0].scores["keyword"] == 1.0 and hits[0].rank_by["keyword"] == 1
    assert keyword_score("the of", "anything") == 0.0


def test_hybrid_mode_fuses_both_lists(kb):
    db, _, _ = kb
    hits = CosineRetriever(db).search(_q(kb, "warranty defects tents", mode="hybrid"))
    assert hits[0].doc == "warranty.md"
    assert "fused" in hits[0].scores and {"vector", "keyword"} <= set(hits[0].rank_by)


def test_label_filter_drops_team_lead_docs(kb):
    db, _, _ = kb
    agent = CosineRetriever(db).search(_q(kb, "ZX9QK approve refunds", mode="keyword"))
    assert all(h.doc != "guide.md" for h in agent)
    lead = CosineRetriever(db).search(
        _q(kb, "ZX9QK approve refunds", mode="keyword", allowed_labels=frozenset({"public", "team_lead"}))
    )
    assert lead[0].doc == "guide.md" and lead[0].label == "team_lead"


def test_doc_name_filter(kb):
    db, _, _ = kb
    hits = CosineRetriever(db).search(_q(kb, "returns warranty", doc_names=["warranty.md"]))
    assert {h.doc for h in hits} == {"warranty.md"}

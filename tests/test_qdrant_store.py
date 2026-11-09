"""The Qdrant adapter, tested with the in-memory client. Skips when qdrant-client is missing."""

import pytest

pytest.importorskip("qdrant_client")

from qdrant_client import QdrantClient  # noqa: E402

from aip.providers.fake import FakeProvider  # noqa: E402
from aip.rag.qdrant_store import QdrantStore  # noqa: E402
from aip.rag.retriever import SearchQuery  # noqa: E402

DIM = 256
DOCS = {
    "returns.md": ("Returns are accepted within sixty days with the original packaging.", "public"),
    "warranty.md": ("Tents carry a two year warranty against defects in material.", "public"),
    "guide.md": ("Approve refunds above the limit after the order check. Code ZX9QK.", "team_lead"),
}


@pytest.fixture
def store():
    emb = FakeProvider()
    qs = QdrantStore(QdrantClient(":memory:"), "chunks", DIM)
    for doc_id, (name, (text, label)) in enumerate(DOCS.items(), start=1):
        qs.add(1, doc_id, name, [text], emb.embed([text]), label)
    qs.add(
        2,
        10,
        "other.md",
        ["Tenant two warranty secret text."],
        emb.embed(["Tenant two warranty secret text."]),
    )
    return qs, emb


def _q(emb, text, **kw):
    base = {"tenant_id": 1, "text": text, "vector": emb.embed([text])[0], "k": 5}
    return SearchQuery(**{**base, **kw})


def test_qdrant_vector_search_nearest(store):
    qs, emb = store
    hits = qs.search(_q(emb, "tents warranty defects", mode="vector"))
    assert hits[0].doc == "warranty.md" and hits[0].scores["vector"] > 0


def test_qdrant_tenant_filter(store):
    qs, emb = store
    assert all(h.doc != "other.md" for h in qs.search(_q(emb, "tenant two warranty secret", mode="hybrid")))
    other = qs.search(_q(emb, "tenant two warranty secret", tenant_id=2, mode="vector"))
    assert {h.doc for h in other} == {"other.md"}


def test_qdrant_label_filter(store):
    qs, emb = store
    assert all(h.doc != "guide.md" for h in qs.search(_q(emb, "ZX9QK approve refunds", mode="keyword")))
    lead = qs.search(
        _q(emb, "ZX9QK approve refunds", mode="keyword", allowed_labels=frozenset({"public", "team_lead"}))
    )
    assert lead[0].doc == "guide.md"


def test_qdrant_hybrid_uses_rrf(store):
    qs, emb = store
    hits = qs.search(_q(emb, "warranty original packaging", mode="hybrid"))
    assert {"warranty.md", "returns.md"} <= {h.doc for h in hits}
    assert all("fused" in h.scores for h in hits)
    assert hits[0].scores["fused"] >= hits[-1].scores["fused"]

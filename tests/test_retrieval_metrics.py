"""Retrieval metrics and the runner."""

import textwrap

from aip.evals.retrieval import (
    heading_of,
    ingest_corpus,
    mrr,
    parse_front_matter,
    recall_at_k,
    ref_of,
    run_retrieval_eval,
)
from aip.providers.fake import FakeProvider
from aip.rag.retriever import CosineRetriever
from aip.tenants import create_tenant


def test_recall_at_k_basic():
    relevant = {"a.md#X", "b.md#Y"}
    ranked = ["c.md#Z", "a.md#X", "d.md#W", "b.md#Y"]
    assert recall_at_k(relevant, ranked, 2) == 0.5
    assert recall_at_k(relevant, ranked, 4) == 1.0
    assert recall_at_k({"a.md#X"}, [], 5) == 0.0


def test_recall_no_relevant_is_excluded_from_mean():
    assert recall_at_k(set(), ["a.md#X"], 5) is None
    assert mrr(set(), ["a.md#X"]) is None


def test_mrr_basic():
    assert mrr({"b"}, ["a", "b", "c"]) == 0.5
    assert mrr({"z"}, ["a", "b"]) == 0.0
    assert mrr({"a", "b"}, ["x", "b", "a"]) == 0.5


def test_helpers():
    assert parse_front_matter("---\nlabel: team_lead\n---\n\n# T\n\nbody") == ("team_lead", "# T\n\nbody")
    assert parse_front_matter("# T\n\nbody")[0] == "public"
    assert heading_of("## Return window\nBody text") == "Return window"
    assert ref_of("r.md", "# Title\nx") == "r.md#Title"


def test_runner_on_tiny_corpus_sqlite(db_session, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "returns.md").write_text(
        "---\nlabel: public\n---\n\n# Returns\n\n## Return window\n\nReturns are accepted within sixty days."
    )
    (corpus / "guide.md").write_text(
        "---\nlabel: team_lead\n---\n\n# Guide\n\n## Lead approval\n\nCode ZX9QK needs a lead sign-off."
    )
    golden = tmp_path / "golden.yaml"
    golden.write_text(
        textwrap.dedent(
            """\
            labels_reviewed_by: pending owner review
            queries:
              - id: q1
                query: return window sixty days
                role: agent
                relevant: ["returns.md#Return window"]
                kind: plain
              - id: q2
                query: ZX9QK lead sign-off
                role: team_lead
                relevant: ["guide.md#Lead approval"]
                kind: team_lead
              - id: q3
                query: ZX9QK lead sign-off
                role: agent
                relevant: []
                kind: none
            """
        )
    )
    emb = FakeProvider()
    tenant, _ = create_tenant(db_session, "m", ["search_knowledge_base"])
    db_session.commit()
    assert ingest_corpus(db_session, tenant.id, emb, corpus) == 2
    report = run_retrieval_eval(
        golden,
        corpus,
        db_session,
        tenant.id,
        emb,
        CosineRetriever(db_session),
        ["vector", "keyword", "hybrid"],
        3,
    )
    assert report["queries"] == 3
    for mode in ("vector", "keyword", "hybrid"):
        row = report["modes"][mode]
        assert row["recall@3"] == 1.0 and row["mrr"] == 1.0
        assert row["scored_queries"] == 2 and row["no_answer_queries"] == 1
        assert row["p95_ms"] >= row["p50_ms"] >= 0
    # the agent never gets the team lead file
    agent_none = report["per_query"]["keyword"][2]
    assert not any(ref.startswith("guide.md") for ref in agent_none["ranked"])

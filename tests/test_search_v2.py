"""/v2/search: modes, filters and provenance. /v1/search is unchanged."""

import json

import pytest
from tests.contract import RESPONSES, canonical, run_v1_search_cases

SECRET = "# Lead guide\n\nZX9QK approve any refund above the limit after the order check."


@pytest.fixture
def v2(env):
    t = env.tenant()
    env.add_kb(t, "returns.md", "# Returns\n\nReturns are accepted within sixty days with the order number.")
    env.add_kb(t, "shipping.md", "# Shipping\n\nStandard shipping takes three to five business days.")
    r = env.client.post(
        "/v1/documents",
        json={"name": "guide.md", "text": SECRET, "access_label": "team_lead"},
        headers=t["headers"],
    )
    assert r.status_code == 201
    lead = env.client.post(
        f"/admin/tenants/{t['id']}/keys",
        json={"role": "team_lead", "label": "Lead"},
        headers={"X-Admin-Token": "admin-secret"},
    ).json()["api_key"]
    env.t, env.lead = t, {"X-API-Key": lead}
    return env


def _post(env, headers, **body):
    return env.client.post("/v2/search", json={"query": "returns sixty days", **body}, headers=headers)


def test_v2_returns_provenance_fields(v2):
    r = _post(v2, v2.t["headers"], k=3)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["api_version"] == "2" and body["mode"] == "hybrid"
    hit = body["hits"][0]
    assert set(hit) == {
        "doc_id", "doc", "chunk", "label", "source_uri", "ingested_at", "score", "scores", "ranks", "text"
    }  # fmt: skip
    assert hit["doc"] == "returns.md" and hit["label"] == "public"
    assert "fused" in hit["scores"] and {"vector", "keyword"} >= set(hit["ranks"])
    assert hit["ingested_at"]


def test_v2_label_filter_subset_of_role(v2):
    query = {"query": "ZX9QK approve refund", "mode": "keyword"}
    agent = v2.client.post("/v2/search", json=query, headers=v2.t["headers"]).json()
    assert all(h["doc"] != "guide.md" for h in agent["hits"])
    lead = v2.client.post("/v2/search", json=query, headers=v2.lead).json()
    assert lead["hits"][0]["doc"] == "guide.md"
    only_public = v2.client.post("/v2/search", json={**query, "labels": ["public"]}, headers=v2.lead).json()
    assert all(h["doc"] != "guide.md" for h in only_public["hits"])


def test_v2_label_escalation_is_403(v2):
    r = _post(v2, v2.t["headers"], labels=["team_lead"])
    assert r.status_code == 403


def test_v2_doc_name_filter(v2):
    r = _post(v2, v2.t["headers"], query="shipping returns days", doc_names=["shipping.md"], mode="vector")
    assert {h["doc"] for h in r.json()["hits"]} == {"shipping.md"}


def test_v1_unchanged():
    stored = json.loads(RESPONSES.read_text())
    for want, got in zip(stored, run_v1_search_cases(), strict=True):
        assert canonical(got) == canonical(want)

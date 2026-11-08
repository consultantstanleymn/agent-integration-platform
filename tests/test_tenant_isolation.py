def test_tenants_cannot_see_each_others_documents_or_chunks(env):
    a, b = env.tenant("a"), env.tenant("b")
    env.add_kb(a, "secret.md", "# Plans\n\nProject Falcon launches in June.")
    assert env.client.get("/v1/documents", headers=b["headers"]).json() == []
    hits = env.client.post("/v1/search", json={"query": "Project Falcon"}, headers=b["headers"]).json()[
        "hits"
    ]
    assert hits == []
    hits = env.client.post("/v1/search", json={"query": "Project Falcon"}, headers=a["headers"]).json()[
        "hits"
    ]
    assert hits and hits[0]["doc"] == "secret.md"


def test_tenant_cannot_read_or_delete_other_tenants_data(env):
    a, b = env.tenant("a"), env.tenant("b")
    tid = env.client.post("/v1/tickets", json={"subject": "private"}, headers=a["headers"]).json()["id"]
    assert env.client.get(f"/v1/tickets/{tid}", headers=b["headers"]).status_code == 404
    env.add_kb(a)
    doc_id = env.client.get("/v1/documents", headers=a["headers"]).json()[0]["id"]
    assert env.client.delete(f"/v1/documents/{doc_id}", headers=b["headers"]).status_code == 404
    assert env.client.delete(f"/v1/documents/{doc_id}", headers=a["headers"]).status_code == 204


def test_audit_log_is_per_tenant(env):
    a, b = env.tenant("a"), env.tenant("b")
    env.add_kb(a)
    actions_b = {x["action"] for x in env.client.get("/v1/audit", headers=b["headers"]).json()}
    assert "document.ingest" not in actions_b


def test_agent_cannot_look_up_other_tenants_ticket(env):
    a = env.tenant("a")
    b = env.tenant("b")
    tid = env.client.post("/v1/tickets", json={"subject": "private"}, headers=a["headers"]).json()["id"]
    r = env.client.post("/v1/agent/run", json={"question": f"status of T-{tid}"}, headers=b["headers"]).json()
    assert "not found" in r["answer"] or "could not find" in r["answer"]

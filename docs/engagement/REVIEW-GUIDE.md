# Review guide: 60-minute walkthrough

Fictional customer. Any match with a real company is not intended.

This guide helps a reviewer read the Agent Integration Platform in one hour. The scenario is OutdoorCo support cases (see `SCENARIO.md`). Every command runs on one laptop with a fake model. No command calls a real cloud service or a hosted model.

## Plan for the hour

| Minutes | Step |
|---|---|
| 0 to 5 | Pitch (next section) |
| 5 to 20 | Demo, steps 1 to 6 |
| 20 to 35 | Read the code path: graph, authority, write tool |
| 35 to 50 | Questions (20 questions below) |
| 50 to 60 | Remaining risks and open items |

## The 5-minute pitch

OutdoorCo has 40 support agents. Tickets about returns, warranty and shipping damage arrive by webhook. The platform finds the right policy and proposes a resolution. The resolution is a refund, a replacement, store credit or an escalation.

The platform acts alone for small amounts. The approval limit is 100.00. The platform pauses above that limit and waits for a team lead. The pause survives a process restart. Every decision is an audit row.

The written authority file decides what the agent refuses. A test reads that file, so the file and the code cannot drift apart without a test failing. The agent has one write path: an MCP tool named `create_return_authorization`. The tool checks the tenant and the amount.

Documents have access labels. An agent key never receives a team-lead document. A tenant never receives the chunks of another tenant.

Evidence is local. The README lists what ran and what did not. Nothing in this repo claims a GitHub Actions result, a real AWS call or a hosted-model call.

## Demo steps

Run from the repo root. The venv must exist (`.venv`). Port 25100 must be free.

1. Run the whole case demo. It creates a tenant, loads two documents, searches as an agent and as a team lead, runs a small refund, a large refund with approval, and a refused request:

   ```bash
   nice -n 10 scripts/demo_case.sh
   ```

   Saved output: `evidence/demo-transcript.txt`.

2. Check the case graph shape and the single write tool:

   ```bash
   .venv/bin/pytest tests/test_graph_build.py tests/test_mcp_server.py tests/test_graph_write_paths.py -q
   ```

3. Check the pause and the resume after a restart:

   ```bash
   .venv/bin/pytest tests/test_hitl.py tests/test_graph_resume.py -q
   ```

4. Check the budgets, the authority file and the boundary at the approval limit:

   ```bash
   .venv/bin/pytest tests/test_budgets.py tests/test_authority.py -q
   ```

5. Run the evals in replay mode (62 cases, no network):

   ```bash
   .venv/bin/aip eval run --suite cases --replay --min-pass-rate 0.95
   ```

6. Run the cloud verifier and the teardown against a local moto server on port 25150:

   ```bash
   nice -n 10 scripts/aws_moto_roundtrip.sh
   ```

Run the fast gate at any time:

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy src && .venv/bin/pytest -q
```

## Code path to read (15 minutes)

1. `src/aip/graph/build.py`: the node list and the edges.
2. `src/aip/graph/state.py`: the typed state.
3. `src/aip/graph/nodes.py`: `check_authority` and the other nodes.
4. `src/aip/graph/authority.py` and `docs/engagement/AUTHORITY.md`: the rules and the machine-readable block.
5. `src/aip/graph/service.py`: start, decision, resume and expiry.
6. `src/aip/mcp_server/server.py`: the one write tool.
7. `src/aip/rag/retriever.py` and `src/aip/rag/pg_hybrid.py`: the retrieval port and the hybrid search.

## 20 likely questions

**1. Why a state graph and not a free agent loop?**
The steps and the pause points are fixed. A reviewer can list every path. See `src/aip/graph/build.py` and `tests/test_graph_build.py`.

**2. How many write paths does the agent have?**
One. The MCP tool `create_return_authorization`. A static test checks that `aip.graph` has no other write call. See `tests/test_graph_write_paths.py` and `docs/engagement/adr/0001-mcp-single-write-tool.md`.

**3. What happens when an amount equals the approval limit?**
The platform acts alone. The check is "greater than" the limit. `tests/test_authority.py` asserts the amount at the limit and the amount one unit above it. See `check_authority` in `src/aip/graph/nodes.py`.

**4. Who sets the approval limit?**
The authority file. The code reads the block at the end of `docs/engagement/AUTHORITY.md`. A test reads the same block.

**5. What is the hard cap?**
500.00. The agent refuses any amount above it, and a team lead cannot raise it through the agent. See `docs/engagement/AUTHORITY.md`.

**6. How does a pause survive a restart?**
A checkpointer saves the graph state. SQLite is the default. Postgres is available. See `tests/test_graph_resume.py`, `tests/test_graph_resume_pg.py` and `docs/engagement/RISK-DECISION.md`.

**7. What if nobody decides?**
A pending approval expires to rejected after a set time. See `tests/test_expiry.py` and `src/aip/graph/service.py`.

**8. How are decisions recorded?**
Every decision is an audit row. The audit table is append-only (migration `migrations/versions/0003_append_only_audit.py`, test `tests/test_audit_append_only.py`).

**9. What if a ticket says "ignore previous instructions"?**
Text in a ticket or document is data. The authority file lists forbidden phrases, and the agent refuses them. See `tests/test_injection.py` and `docs/engagement/THREAT-MODEL.md`.

**10. How do budgets work?**
Each run has a step budget and a token budget. When one is spent, the run ends as `budget_exceeded` with no side effect. See `tests/test_budgets.py`.

**11. How do you evaluate the agent without a model call?**
The eval suite replays stored cassettes. The cassettes are synthetic. They come from the fake provider in the hosted wire format. See `src/aip/providers/replay.py`, `scripts/make_synthetic_cassettes.py` and `docs/engagement/EVAL-REPORT.md`.

**12. What does the eval gate do?**
It exits non-zero when the pass rate is under the threshold. The threshold is 0.95. See `tests/test_eval_cases.py`.

**13. How does hybrid retrieval work?**
Vector search and Postgres full-text search run separately. Reciprocal rank fusion merges the two lists. See `src/aip/rag/pg_hybrid.py` and `docs/engagement/evidence/retrieval-report.md`.

**14. How do you keep team-lead documents from an agent?**
Each document has an access label. The filter runs in search, in `/v2/search` and in the case graph. See `tests/test_access_labels.py` and `tests/test_tenant_isolation.py`.

**15. Why pgvector and not Qdrant?**
Both ran behind the same port on the same golden set. They tie in vector mode (recall@5 0.9583). Pgvector adds no new service. See `docs/engagement/adr/0004-vector-store-choice.md` and `docs/engagement/evidence/qdrant-vs-pgvector.md`.

**16. What happens when the model provider is slow or down?**
Calls have a timeout and a retry budget. After repeated failures a breaker opens and a second adapter answers. After 30 seconds one probe call tests the primary. See `docs/engagement/adr/0002-llm-failover-rule.md` and `tests/test_resilient_provider.py`.

**17. Does v2 search break v1 clients?**
No test shows a difference. A stored-response contract test pins the `/v1/search` bytes. See `tests/test_contract_v1_search.py` and `docs/engagement/adr/0003-rest-versioning.md`.

**18. What stops a duplicate document upload?**
`POST /v1/documents` accepts `Idempotency-Key`. The same key and body give one document. A different body gives 422. See `tests/test_documents_idempotency.py`.

**19. How do you know the cloud adapters work?**
The S3, SQS and Secrets Manager adapters pass the same tests against moto. The verifier checks public access block, encryption, redrive policy, one message, one DLQ redrive, the secret and the IAM policy. See `tests/test_aws_adapters.py`, `scripts/aws_verify.py` and `evidence/aws-verifier-moto.txt`. No real AWS run happened.

**20. What is the performance?**
A Locust run with 20 users for 2 minutes and a fake model with a 200 ms delay. The numbers are measured on one laptop and do not predict production. See `docs/engagement/LOADTEST.md`.

## Remaining risks

1. **The golden set is not reviewed.** The 48 queries have labels that wait for the owner review (`labels_reviewed_by: pending owner review`). Treat the retrieval numbers as unconfirmed.
2. **The cassettes are synthetic.** The eval replay proves the gate and the parsing. It does not prove the quality of a real hosted model.
3. **The image digest is local.** It is a podman digest from this laptop. It is not a registry digest. The push and the signing step in `.github/workflows/release.yml` are authored and not run.
4. **No GitHub Actions run was started or checked.** The workflow files pass `actionlint` only.
5. **The MCP package is pinned below version 2** (`mcp>=1.2,<2` in `pyproject.toml`). `src/aip/mcp_server/server.py` reads the private attribute `_tool_manager`. A change in the package can break it.
6. **Keyword recall is low.** Keyword mode alone gives recall@5 0.4318. The fusion with vector search hides this weakness.
7. **The RRF k test shows no effect.** The four values (10, 30, 60, 100) give the same result on this small corpus. The corpus is too small to tune k.
8. **SQLite checkpoints suit one process.** More than one API process needs Postgres. See `docs/engagement/RISK-DECISION.md`.
9. **The load test used a fake model.** No hosted model was called.
10. **Tenant webhook secrets sit in the database in plain text.** See "Limits" in `README.md`.

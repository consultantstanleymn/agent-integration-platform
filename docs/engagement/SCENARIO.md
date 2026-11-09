Fictional customer. Any match with a real company is not intended.

# Engagement scenario: OutdoorCo support cases

## Customer

OutdoorCo is an online retailer of outdoor gear. It has 40 support agents. Tickets arrive from its helpdesk by webhook. The tickets cover returns, warranty claims and shipping damage.

## Goal

The platform must do these steps for each case:

1. Classify the case.
2. Find the correct policy.
3. Propose a resolution: refund, replacement, store credit, or escalation.
4. Ask a team lead before any refund or credit above a set limit.
5. Record the action and each decision.

The platform must refuse any request that is outside its written authority. Some policy documents are for team leads only. A caller without the team-lead role must never receive them.

## Scope

**Case workflow**
- A state graph with a typed state model. Nodes: classify, retrieve, propose, check_authority, human_approval, act, record.
- One write path: a single tool server with one write tool, `create_return_authorization`. The tool has a JSON schema, a tenant scope and an amount limit.
- Human approval: the graph pauses above the approval limit. A team lead approves or rejects through `POST /v1/cases/{id}/decision`. Approval survives a process restart.
- Budgets: each run has a step budget and a token budget.

**Retrieval**
- Hybrid search with pgvector and Postgres full-text, fused by reciprocal rank fusion, with metadata filters.
- The search sits behind a `Retriever` port. The old store stays available through an adapter.
- Access labels: a caller without the team-lead role never receives a team-lead document. A tenant never receives another tenant's chunk.

**Cloud adapters**
- S3 for documents, SQS with a dead-letter queue for ingest jobs, and Secrets Manager for the LLM key, all behind ports.
- The same tests pass against a local emulator. A move to real AWS changes config only.

**Operations**
- Tracing with GenAI attributes, a load proof of the case path, and a shared rate limit with a query-embedding cache.

## Out of scope

- A real AWS run. The cloud verifier runs against a local emulator. Real AWS resources are authored and not run.
- Real hosted model quality. Tests use fake and mocked providers. The hosted adapter runs against a mocked HTTP server.
- Multi-region deployment, disaster recovery and autoscaling.

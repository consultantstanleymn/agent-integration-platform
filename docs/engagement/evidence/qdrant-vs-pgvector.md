# Evidence: Qdrant against pgvector on the same golden set
label: VERIFIED LOCALLY
date: 2026-11-09 10:01 UTC
code commit: 5579056e6ea287b277e803aa7f6d460a5adce7dc
working tree clean at run time: yes
machine: MacBook Pro (mid 2015), 8 logical CPUs, 16 GB RAM, Fedora 44, podman 5.8.7 (measured on one laptop)
command: podman compose --profile qdrant up -d qdrant; aip eval retrieval --store qdrant --qdrant-url http://127.0.0.1:25133 --embedder local_onnx --modes vector,keyword,hybrid --k 5 --json
notes: The pgvector rows come from the command in retrieval-report.md. Each store ran twice. Raw runs are in qdrant-vs-pgvector-runs.json. Qdrant 1.13.4 in a local podman container. Measured on one laptop.
---
# Qdrant against pgvector on the same golden set

All numbers were measured on one laptop. Same corpus (15 files), same chunks, same embedder (`BAAI/bge-small-en-v1.5`, 384 dimensions), same 48 golden queries, k = 5. Each store ran twice in a row.

## Quality and latency

| Store | Mode | recall@5 | MRR | p50 ms (run 1 / run 2) | p95 ms (run 1 / run 2) |
|---|---|---|---|---|---|
| pgvector (PostgreSQL 16.10, pgvector 0.8.0) | vector | 0.9583 | 0.8614 | 2.04 / 1.38 | 2.37 / 2.01 |
| Qdrant 1.13.4 (container) | vector | 0.9583 | 0.8614 | 3.26 / 3.05 | 5.14 / 3.37 |
| pgvector | hybrid (RRF 60) | 0.9659 | 0.8557 | 3.00 / 2.53 | 3.17 / 3.10 |
| Qdrant | hybrid (RRF 60) | 0.8864 | 0.7973 | 12.15 / 10.50 | 13.48 / 11.64 |

Noise between two runs of the same store: recall and MRR did not change at all. Latency moved by 0.2 to 1.7 ms.

How to read the table:

- **Vector mode is a tie.** Both stores return the same recall and MRR. Qdrant was 1 to 1.5 ms slower at p50 on this machine. Both are far below the cost of a model call.
- **Hybrid mode is not a fair test of the vector databases.** Qdrant has no full-text index in this adapter. Its keyword list comes from a Python scorer over the filtered points (a scroll), and its fused result is lower (0.8864). pgvector uses the PostgreSQL full-text index. The difference comes from the keyword method, not from the vector search.
- The Python keyword scorer alone (Qdrant keyword row in `qdrant-vs-pgvector-runs.json`) has recall 0.8182. PostgreSQL full-text search with `websearch_to_tsquery` alone has 0.4318, because it needs every query term. Hybrid on PostgreSQL still ends higher.

## Operations

| Question | pgvector | Qdrant |
|---|---|---|
| Memory after the runs (`podman stats`) | 32 MB for the whole PostgreSQL container | 95 MB for the Qdrant container |
| Moving parts | None added: PostgreSQL already holds cases, audit rows and checkpoints | One more service to run, secure and upgrade |
| Backup | One `pg_dump` covers documents, chunks and vectors | A separate snapshot, kept in step with the database |
| Tenant filter | SQL `WHERE tenant_id = ...` in the same query | A payload filter on `tenant_id` |
| Keyword search | Built in (`tsvector`, GIN index) | Not in this adapter |
| Access label filter | SQL `WHERE access_label = ANY(...)` | Payload filter on `label` |

## Decision

Keep pgvector as the default store (ADR 0004). Qdrant did not win recall: the vector-mode difference is 0.0000, which is inside the noise between two runs. Qdrant stays as an adapter behind the same port, tested with the in-memory client and run once against a container.

## Limits

- Measured on one laptop with 15 documents. At millions of vectors, the answer can differ.
- The Qdrant server (1.13.4) and the Python client (1.20.0) differ by more than one minor version. The client warns about it. The check was turned off for the run.
- Labels in the golden set are not reviewed by the owner.

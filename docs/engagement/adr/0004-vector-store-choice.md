# ADR 0004: Vector store choice

Status: accepted.

## Context

Retrieval needs a vector index with a tenant filter and an access label filter. Two stores sit behind the same `Retriever` port: PostgreSQL with pgvector and full-text search, and Qdrant. The platform already needs PostgreSQL for cases, the audit log and graph checkpoints.

## Options

1. **pgvector in PostgreSQL.** No new service. Full-text search is built in. One backup covers everything.
2. **Qdrant as a separate service.** A dedicated vector database with payload filters. One more service to run, secure, back up and upgrade.
3. **Keep the Python cosine scan.** It works for thousands of chunks and not for millions. It stays as the fallback for SQLite.

## Decision

Use option 1 as the default on PostgreSQL. Keep option 3 for SQLite. Keep option 2 as an adapter, not as the default.

The comparison on the same golden set (`docs/engagement/evidence/qdrant-vs-pgvector.md`) showed the same recall and MRR in vector mode: 0.9583 and 0.8614 for both. The difference is 0.0000, inside the noise between two runs. Qdrant was not faster on this machine. It needed 95 MB of memory against 32 MB for the PostgreSQL container, and it adds a service.

The rule for a change: move to Qdrant only if it wins recall by more than the noise between two runs, or if the vector count grows past what one PostgreSQL index can serve with the needed latency.

## Consequences

- One database to operate for documents, vectors, cases and audit rows.
- The access label and tenant filters sit in one SQL query.
- Hybrid search uses the PostgreSQL full-text index. The Qdrant adapter has only a weak keyword path.
- The adapter stays tested, so a later move is a configuration change plus a data load.
- The result comes from 15 documents and 48 queries with labels that the owner has not reviewed. Repeat the comparison on real data before a large move.

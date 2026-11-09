# ADR 0003: REST versioning with a new path

Status: accepted.

## Context

Search needs new inputs (mode, filters) and a richer answer (provenance, scores, ranks). Callers of `POST /v1/search` already depend on the old answer. A change must not break them.

## Options

1. **Change /v1/search in place.** Old callers break when a field moves or a new field is required.
2. **A version header.** One path serves two shapes. Caches, logs and API docs do not show the version. Tests and proxies must look at a header.
3. **A new path, `POST /v2/search`.** Each version has its own request and response shape and its own tests.

## Decision

Use option 3. `/v2/search` has `mode` (vector, keyword or hybrid; default hybrid), `doc_names`, `labels` and `k` up to 20. The answer holds `api_version`, `mode` and hits with `doc_id`, `doc`, `chunk`, `label`, `source_uri`, `ingested_at`, `score`, `scores`, `ranks` and `text`.

`labels` must be a subset of what the caller role can read. A request for another label gets 403.

A contract test, `tests/test_contract_v1_search.py`, keeps `/v1/search` byte compatible. The test uses stored responses from the code before the retrieval work.

## Support policy for v1

- `/v1` stays supported. No `Deprecation` header is sent.
- A change that breaks the v1 contract needs a new major path, not an edit to v1.
- Retirement of v1 needs an announced date and a release note. No date is set today.

## Consequences

- Two handlers exist for search. Both use the same retriever port, so the cost is small.
- Clients must opt in to the new fields by changing the path.

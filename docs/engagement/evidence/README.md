# Evidence index

Each row names a file in this folder, what it shows, its label, the date and the short code commit of the run. The first lines of every text file hold the full header: label, date, code commit, machine, command and notes. All numbers are measured on one laptop.

| File | What it shows | Label | Date | Code commit |
|---|---|---|---|---|
| `aws-hcl-static-checks.txt` | Static checks on the AWS Terraform files | PARTLY RUN | 2026-11-09 08:09 UTC | `2d851e7` |
| `aws-teardown-moto.txt` | Teardown and an empty check against a moto server | PARTLY RUN | 2026-11-09 08:12 UTC | `6c8cf78` |
| `aws-verifier-moto.txt` | Cloud verifier against a moto server | PARTLY RUN | 2026-11-09 08:12 UTC | `6c8cf78` |
| `baseline-local.txt` | Baseline before any change: lint, types, 80 tests, eval 9/9 | VERIFIED LOCALLY | 2026-11-09 06:12 UTC | `c619f22` |
| `demo-transcript.txt` | Case run demo on a fresh SQLite database | VERIFIED LOCALLY | 2026-11-09 09:02 UTC | `8ebb153` |
| `embed-cache-hit-ratio.txt` | Embedding cache hit ratio over two retrieval runs (local Redis) | VERIFIED LOCALLY | 2026-11-09 10:00 UTC | `079a207` |
| `eval-cases-replay.json` | Raw JSON report of the case eval replay | see companion file | | |
| `eval-cases-replay.txt` | Case evals in replay mode: 62 cases, gate 0.95 | VERIFIED LOCALLY | 2026-11-09 08:58 UTC | `67a80db` |
| `full-gate-local.txt` | Full gate: lint, types, 304 tests with coverage and PostgreSQL, evals | VERIFIED LOCALLY | 2026-11-09 11:19 UTC | `79ac102` |
| `graph-resume-after-restart.txt` | Case graph pauses for approval and resumes after a restart (SQLite checkpointer) | VERIFIED LOCALLY | 2026-11-09 06:34 UTC | `4affb25` |
| `graph-resume-postgres.txt` | Case graph resumes after a restart on a Postgres checkpointer | VERIFIED LOCALLY | 2026-11-09 06:37 UTC | `2060c1b` |
| `ingest-dlq-redrive.txt` | Async ingest, poison message to DLQ, redrive, no duplicate (memory queue and moto SQS) | VERIFIED LOCALLY | 2026-11-09 08:08 UTC | `bd5ebde` |
| `jaeger-agent-trace.json` | Jaeger JSON of one case run and its approval (13 spans) | see companion file | | |
| `jaeger-agent-trace.txt` | One case run and its approval as a trace in Jaeger | VERIFIED LOCALLY | 2026-11-09 11:18 UTC | `79ac102` |
| `llm-failover-test.txt` | Failover from primary to secondary provider and back (slow fake, manual clock) | VERIFIED LOCALLY | 2026-11-09 07:56 UTC | `0f5cad5` |
| `local-embedder-check.txt` | Local ONNX embedder: 384 dimensions and a similarity check | VERIFIED LOCALLY | 2026-11-09 08:35 UTC | `f91b5fb` |
| `localstack-profile-config.txt` | LocalStack compose profile, config check only | AUTHORED, NOT RUN | 2026-11-09 08:14 UTC | `d5733b5` |
| `locust-case-path.html` | Static page built from the Locust CSV files (see locust-case-path.txt) | see companion file | | |
| `locust-case-path.txt` | Locust run of the case path: numbers behind locust-case-path.html | VERIFIED LOCALLY | 2026-11-09 10:00 UTC | `43cd436` |
| `migration-0006-up-down.txt` | Migration 0006 upgrade, downgrade and upgrade again on PostgreSQL | VERIFIED LOCALLY | 2026-11-09 10:00 UTC | `079a207` |
| `pg-hybrid-tests.txt` | PostgreSQL hybrid retrieval tests (vector, keyword, fusion, tenant and label filters) | VERIFIED LOCALLY | 2026-11-09 10:00 UTC | `079a207` |
| `qdrant-vs-pgvector-runs.json` | Raw JSON of the Qdrant and pgvector runs | see companion file | | |
| `qdrant-vs-pgvector.md` | Qdrant against pgvector on the same golden set | VERIFIED LOCALLY | 2026-11-09 10:01 UTC | `5579056` |
| `release-local-build.txt` | Local image build, digest, SBOM, image scan, release manifest and acceptance gate | PARTLY RUN | 2026-11-09 11:19 UTC | `79ac102` |
| `retrieval-report.md` | Retrieval report: vector, keyword and hybrid search, tuning and a miss attribution | VERIFIED LOCALLY | 2026-11-09 10:01 UTC | `5579056` |
| `retrieval-runs.json` | Raw JSON of the retrieval runs | see companion file | | |
| `sbom-image.cdx.json` | CycloneDX SBOM of the container image | see companion file | | |
| `sbom-source.cdx.json` | CycloneDX SBOM of the source dependencies | see companion file | | |
| `security-gate-local.txt` | Security gate commands run locally | PARTLY RUN | 2026-11-09 07:49 UTC | `4278c31` |

Labels: VERIFIED LOCALLY means the command ran on this laptop at the stated commit. PARTLY RUN names the part that ran. AUTHORED, NOT RUN means the file is written and checked for syntax only.

# Load test of the case path

All numbers were measured on one laptop (MacBook Pro mid 2015, 8 logical CPUs, 16 GB RAM). They describe this code with a fake model that answers after a fixed delay. They do not describe production capacity.

## Setup

| Item | Value |
|---|---|
| Tool | Locust 2.47.0 (`load/locustfile.py`) |
| Users | 20 |
| Spawn rate | 2 users per second |
| Duration | 2 minutes |
| Requests | `POST /v1/cases` small refund (weight 3), `POST /v1/cases` large refund that waits for approval (weight 1), `POST /v2/search` hybrid (weight 2). Text comes from a generator seeded with 42. |
| API | uvicorn with 2 workers on port 25100 |
| Database and checkpoints | PostgreSQL 16 with pgvector in a local podman container |
| Model | `LLM_PROVIDER=fake_slow`: the fake provider plus a fixed 200 ms per model call. A case makes two model calls. |
| Embeddings | fake hashed embedder |
| Load average | 1.48 at the start, 1.47 at the end (`/proc/loadavg`, 1 minute) |

## Result

| Request | Count | Failures | p50 ms | p95 ms | Max ms | Requests per second |
|---|---|---|---|---|---|---|
| POST /v1/cases small | 542 | 0 | 1 900 | 2 900 | 3 519 | 4.53 |
| POST /v1/cases large | 181 | 0 | 1 500 | 2 600 | 3 036 | 1.51 |
| POST /v2/search | 356 | 0 | 10 | 26 | 120 | 2.97 |
| All requests | 1 079 | 0 (0.00 percent) | 770 | 2 900 | 3 519 | 9.01 |

The page `docs/engagement/evidence/locust-case-path.html` shows the same numbers with charts over time. It is built from the Locust CSV files, because the built-in Locust HTML report is a minified bundle that the banned-word scan flags. The text copy with the command and the header is `docs/engagement/evidence/locust-case-path.txt`.

## What happened before this run

The first run showed every case request hanging until the database stopped. The cause was the checkpoint setup: each worker and each request thread ran the setup statements at the same time. The fix opens the checkpointer once under a lock, takes a PostgreSQL advisory lock for the setup, and opens the checkpointer at startup. A second run with 12 parallel case requests finished in about 2.2 seconds each. The run above used the fixed code. The failed first run is not part of the numbers.

## What the numbers do not mean

- A case took 1.5 to 1.9 seconds at the median. The two model delays add up to 0.4 seconds. The rest comes from the graph steps, the Postgres checkpoint writes and the queue of 20 users on 2 workers. This test did not break the extra time down.
- A hosted model is slower and varies more than a fixed 200 ms delay.
- 9 requests per second on a laptop with a fake model is not a capacity claim. It shows the case path holds up with no errors under 20 users, with approvals pending in the database.
- Search latency is for 3 small documents.
- The run lasted two minutes. Long-run effects such as memory growth were not tested.

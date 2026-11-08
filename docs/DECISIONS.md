# Design decisions

Each entry: the choice, why, and what we gave up.

1. **Sync SQLAlchemy and sync httpx, not async.** FastAPI runs sync endpoints in a thread pool. The code is shorter
   and easier to explain. Cost: fewer concurrent requests per process than a fully async design.
2. **Provider interface with a fake implementation.** Every test and the CI eval run use `FakeProvider`: no network,
   no cost, no flakiness. Cost: the fake cannot tell you whether a prompt works on a real model.
3. **Vectors as JSON in a normal table, cosine in Python.** Runs on SQLite and Postgres without extensions. The
   `VectorStore` protocol is the seam for pgvector. Cost: it does not scale past thousands of chunks per tenant.
4. **Hand-written agent loop, no framework.** About 100 lines. You can read it and explain it. Cost: no built-in
   retries, memory or planning.
5. **Citations are checked, not trusted.** An answer that cites a chunk that was not retrieved is withheld. This
   catches a common hallucination. Cost: a model that cites in another format fails validation.
6. **Tool allow-list per tenant, enforced in the loop.** The model only sees allowed tools, and a call to any other
   name is denied and audited. Cost: tenants are configured by an admin, not by themselves.
7. **HMAC over `timestamp.body`.** Signing the timestamp lets us reject replays outside five minutes. Idempotency keys
   handle replays inside the window and sender retries.
8. **Idempotency committed with the work.** The handler adds rows, and `run_once` commits them together with the key.
   A crash leaves neither. The unique constraint settles races.
9. **Webhook retries: 5xx, 429 and network errors only.** A 4xx means the request is wrong and retrying cannot help.
   Backoff is exponential (0.5s, 1s, 2s). No jitter, to keep it simple and testable.
10. **Alembic migrations run in the container entrypoint.** Simple for one service. With several replicas, run migrations
    as a separate one-off task. See RUNBOOK.
11. **Terraform: ECS Fargate, public subnets, no NAT.** Cheapest working layout for a sketch. Tasks get public IPs but the
    security group only allows traffic from the load balancer. Production should use private subnets and a NAT or VPC
    endpoints. The file has not been applied.
12. **OAuth client credentials only.** It is the flow for machine-to-machine access, which is what the connector needs.
    No authorization-code flow, no user login.
13. **Anthropic has no embeddings API**, so embeddings have their own setting (`EMBEDDING_PROVIDER`).

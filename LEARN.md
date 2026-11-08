# LEARN.md: the concepts in this project

Each section: what it is, where it is in the code, and what to say in an interview.
The last section lists what is simplified, so you can say it before someone else finds it.

## 1. LLM provider adapter

**What.** One small interface (`chat(messages, tools)` and `embed(texts)`) and several implementations.
The rest of the code never knows which model it talks to. **Where.** `src/aip/providers/`.
**Interview.** "I put the model behind an interface. Tests use a deterministic fake, so CI needs no network or key.
OpenAI-compatible covers Ollama, vLLM and OpenAI. Anthropic is a second adapter with a different wire format:
tool results are content blocks in a user message, not a `tool` role. The adapters translate."

## 2. RAG (retrieval augmented generation)

**What.** The model does not know the customer's documents. So we search them first and give the best passages
to the model. Steps: split documents into chunks, turn each chunk into a vector (embedding), store them, embed the
question, find the nearest chunks by cosine similarity. **Where.** `src/aip/rag/`.
**Interview.** "Chunks keep their markdown heading so they keep context. Every query filters by tenant inside the
SQL. The vector store sits behind an interface; today it scores in Python, and pgvector is the next step. I also
return citations so a person can check the source. When nothing scores above a threshold, the agent says it does
not know instead of guessing."

## 3. Tool use (function calling) and the agent loop

**What.** The model can answer with "call tool X with these arguments" instead of text. Our code runs the tool,
sends the result back, and asks again, until the model writes a final answer. **Where.** `src/aip/agent/loop.py`,
`tools.py`. **Interview.** "The loop is about 100 lines with no framework. Guardrails: a maximum number of steps; a
per-tenant allow-list, so the model only sees tools the tenant may use and any other call is denied and audited;
pydantic models validate tool arguments; the final answer is validated, and an answer that cites a chunk we never
retrieved is withheld. The model is untrusted input. Prompt injection cannot be fully stopped, so we limit what the
agent is able to do."

## 4. Webhooks and HMAC signatures

**What.** A webhook is an HTTP call from system A to system B when something happens. Anyone can call a public URL,
so the sender signs the body with a shared secret (HMAC-SHA256). The receiver recomputes the signature and compares.
**Where.** `src/aip/integrations/webhooks.py`. **Interview.** "I sign `timestamp.body`, not just the body. The
receiver rejects timestamps older than five minutes, so a captured request cannot be replayed later. The compare
is constant time. Unknown tenant and bad signature return the same error, so ids cannot be probed. Outbound
delivery retries 5xx, 429 and network errors with exponential backoff and does not retry other 4xx."

## 5. Idempotency

**What.** Networks fail and senders retry. Idempotency means the same request twice has the same effect as once.
The client sends an `Idempotency-Key`. We store the response under (tenant, key) and return it on repeats.
**Where.** `src/aip/integrations/idempotency.py`. **Interview.** "The work and the key are committed in one
transaction, so a crash cannot leave one without the other. A unique constraint settles two requests arriving at
the same time. Keys are per tenant. The response carries `Idempotent-Replay: true` so the client knows."

## 6. OAuth2 client credentials

**What.** A machine-to-machine login. Our service sends its client id and secret to the token URL and gets a short
lived access token. It uses the token as a bearer header. **Where.** `src/aip/integrations/oauth.py`, `crm.py`,
`mock_crm/app.py`. **Interview.** "The helper caches the token and refreshes it 30 seconds before expiry. If the API
answers 401 the connector drops the token and retries once. It is the right flow when no human is logging in. For
user data I would need the authorization code flow, which I did not build."

## 7. Auth, tenancy and rate limiting

**What.** Each customer is a tenant with an API key. We store only the SHA-256 hash. Every table has `tenant_id`,
every query filters on it, and other tenants' rows look like "not found". **Where.** `security.py`, `api/deps.py`,
`tests/test_tenant_isolation.py`. **Interview.** "A fast hash is fine for 256-bit random keys; it would be wrong for
human passwords. Tenant isolation is tested, not assumed: I try to read, search and delete across tenants."

## 8. Evals

**What.** Automatic tests for model behaviour. A YAML list of questions with facts the answer must contain and the
document it must cite. A script scores each case and prints a pass rate. **Where.** `src/aip/evals/`, `evals/`,
`prompts/`. **Interview.** "Prompts are files in git with a version. The same test set runs for every prompt version, so a
change is measured, not guessed. CI runs it with the fake provider, which proves the pipeline works. It does not
prove a real model is good. For that I run the same set against a real provider before releases. The set is small
(9 cases) and I would grow it from real failures."

## 9. Observability and tracing

**What.** Logs, metrics and traces tell you what the system did. We write JSON logs with a request id and a trace id
on every line, expose Prometheus metrics at `/metrics`, and log small spans with durations. **Where.**
`observability.py`, `api/app.py`. **Interview.** "A trace id from the `traceparent` header is reused, so our logs can
join the customer's. We do not export spans to a collector. That is the next step with OpenTelemetry. Logs are
redacted for emails, phone numbers, card numbers and keys. Metric labels use the route template, not the raw path, to
avoid unbounded labels."

## 10. Audit log

**What.** An append-only record of who did what: tenant, action, request id, trace id, redacted detail. **Where.**
`audit.py`, table `audit_log`. **Interview.** "It answers the customer security question 'show me every access to my
data'. It is not tamper-proof yet: a real one would ship to write-once storage."

## 11. Migrations, containers and infrastructure

**What.** Alembic versions the schema. A multi-stage Dockerfile builds wheels in one stage and ships a small
non-root image. Terraform describes ECS Fargate, RDS, Secrets Manager and CloudWatch. **Interview.** "I ran the
migrations on real Postgres and SQLite, and built and started the image. The Terraform has never been applied, and I
could not run `terraform validate` locally; CI is set up to run it. I would tell a customer exactly that."

## 12. Working with customers (the FDE part)

`docs/ONBOARDING.md` is the 30-minute integration path. `docs/RUNBOOK.md` is what the operator does when it breaks.
`docs/DEMO.md` is the script. **Interview.** "I wrote the docs as if for a customer engineer: copy-paste commands,
what each error code means, and a go-live checklist. The best first fix for a wrong answer is usually the knowledge
base, not the prompt."

## What is simplified (say this yourself)

- Embeddings in tests are hashed words, not meaning. Retrieval quality on real text is untested.
- Vector search is a Python loop, not an index.
- The fake model is a rule set. It proves the loop, guardrails and plumbing, not model quality.
- OpenAI-compatible and Anthropic adapters are tested against mocked HTTP only. Never called live.
- One process, in-memory rate limit, sync code, no queue. Webhook retries block the request.
- Webhook secrets are stored in plain text. No key rotation. Admin is one shared token.
- Tracing is logs with ids, not OpenTelemetry export.
- Terraform is unapplied and unvalidated locally; compose was not run end to end.
- Eval set is 9 cases; the fake provider passes it by design.

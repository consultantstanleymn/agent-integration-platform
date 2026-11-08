# Runbook

For the operator. Commands assume the docker-compose stack or a shell with the same environment variables.

## Health and signals

| Signal | Where | Normal |
|---|---|---|
| Liveness | `GET /health` (runs `SELECT 1`) | `{"status":"ok"}` |
| Metrics | `GET /metrics` | `aip_http_requests_total`, `aip_http_request_seconds`, `aip_agent_runs_total{status}`, `aip_tool_calls_total{tool,outcome}`, `aip_llm_calls_total{provider}` |
| Logs | stdout, one JSON object per line | fields `request_id`, `trace_id`, `tenant_id` |
| Audit | `GET /v1/audit` (tenant), table `audit_log` (operator) | one row per action |

Follow one request: take the `X-Request-Id` or `X-Trace-Id` response header and search the logs for it.
The agent logs spans (`agent.run`, `llm.chat`, `tool.<name>`) with the same ids and a duration.

## Alerts to set (suggested, only the 5xx alarm exists in Terraform)

- 5xx count high.
- `aip_agent_runs_total{status="provider_error"}` rising: the LLM is down or the key is wrong.
- `aip_agent_runs_total{status="invalid_output"}` rising: the model stopped citing correctly. Check the prompt version.
- `aip_agent_runs_total{status="max_steps"}` rising: a tool loop.

## Common problems

**All agent runs return `provider_error`.** Check `LLM_PROVIDER` and the key variable. Check the endpoint is reachable
from the container. The log line `provider error` shows the exception class, never the key.

**Answers say "could not find" for things in the knowledge base.** Run `POST /v1/search` with the same words.
If the hit is missing, the document is missing or the embeddings are poor. If you changed `EMBEDDING_PROVIDER`,
re-upload every document: old vectors from another model are not comparable.

**Customer webhook returns 401.** Wrong secret, or clock skew over 5 minutes. Check time on both sides.

**429 from the API.** Per-tenant limit (`RATE_LIMIT_PER_MINUTE`). The `Retry-After` header says when to retry.

**Outbound webhooks fail.** The tool result says "failed after N attempts (status X)". Status `None` means a network
error. The URL must not be private unless `ALLOW_PRIVATE_WEBHOOK_TARGETS=true` (demo only).

**Container exits at start.** The entrypoint runs `alembic upgrade head`. Read the first lines of the log. A wrong
`DATABASE_URL` is the usual cause.

## Routine tasks

- **Create a tenant:** `POST /admin/tenants` or `aip create-tenant NAME --tools a,b`.
- **Rotate a tenant key:** not supported yet. Create a new tenant or add a rotation endpoint (see SECURITY.md).
- **Change prompt version:** set `PROMPT_VERSION` and restart. Run `aip eval run --prompt-version X` first.
- **Migrations:** `alembic upgrade head`. To roll back one step: `alembic downgrade -1`. Take a database snapshot first.
- **Deploy (AWS):** build and push the image, then apply `infra/` (never applied by the author; review the plan).

## Incident basics

1. Check `/health` and the last deploy. Roll back the image first if the incident started after a deploy.
2. Look at `aip_agent_runs_total` by status to see whether the LLM, validation or tools fail.
3. Find affected tenants through `tenant_id` in logs. Tell the customer contact.
4. After the fix, write what happened and what you will change. No blame.

Known limits: one process rate limiter, no restore test, no on-call setup. See SECURITY.md.

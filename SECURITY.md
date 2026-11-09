# Security notes

This is a demo platform. It applies sound basics. It is **not** SOC 2 certified and has not had a security review
or penetration test. This file lists what exists and what does not.

## Assets and threats

| Asset | Main threat | Control in this repo |
|---|---|---|
| Tenant knowledge base and tickets | Another tenant reads them | `tenant_id` on every row, filter in every query, tests in `tests/test_tenant_isolation.py` |
| API keys | Leak from the database | Only a SHA-256 hash is stored. The key is shown once at creation |
| LLM provider keys | Leak in logs or DB | Env vars only, `SecretStr` in settings, never logged or stored |
| Inbound webhook endpoint | Forged or replayed events | HMAC-SHA256 over `timestamp.body`, 5 minute window, constant-time compare, idempotency keys |
| Outbound webhooks | SSRF (calling internal services) | URL validation blocks non-http(s), localhost and private literal IPs |
| Agent | Prompt injection makes it misuse tools | Per-tenant tool allow-list, max steps, validated arguments, answers may cite only retrieved chunks |
| Logs | PII in logs | Redaction of emails, phones, SSNs, card numbers, keys, bearer tokens |
| Service | Abuse, traffic spikes | Per-tenant rate limit (in memory) |

## Controls list (SOC 2 style, honest status)

| Area | Control | Status |
|---|---|---|
| Access control | Per-tenant API keys, admin token for tenant creation | Done (basic) |
| Access control | SSO, MFA, role-based access | Not done |
| Least privilege | Tool allow-list per tenant; IAM roles split (execution vs task) in Terraform | Done (basic) |
| Encryption in transit | TLS at the load balancer (Terraform) | Defined, never applied |
| Encryption at rest | RDS `storage_encrypted = true` | Defined, never applied |
| Secrets management | Env vars; Secrets Manager in Terraform | Done / defined |
| Secret rotation | Rotation of API keys, webhook secrets, admin token | Not done |
| Logging and audit | Audit table (tenant, request id, trace id, action) and JSON logs | Done |
| Audit log integrity | Append-only database triggers (migration 0003). Export and retention policy are not done | Partial |
| Monitoring | Prometheus metrics, one CloudWatch alarm | Basic |
| Change management | CI runs lint, types, tests, evals on each PR | Done (never run on GitHub yet) |
| Vulnerability management | Dependency scanning, container scanning | Not done |
| Data retention and deletion | Document delete endpoint | Partial. No tenant offboarding, no retention schedule |
| Backups | RDS 7 day retention (Terraform) | Defined, restore never tested |
| Data residency, DPA, subprocessors list | Contract and policy work | Not done |
| Incident response | See docs/RUNBOOK.md | Sketch only |

## Known gaps

- **Webhook secrets in plain text** in the `tenants` table. A database leak exposes them.
- **SSRF guard is partial.** DNS names that resolve to private addresses (DNS rebinding) are not blocked.
- **Rate limit** is per process and in memory. Several replicas multiply the limit.
- **Prompt injection** cannot be fully prevented. The controls limit the damage; they do not remove the risk.
- **PII redaction** uses regular expressions. It misses names and addresses. Question text and answers are stored
  redacted, but the knowledge base itself is stored as uploaded.
- **Admin API** uses one shared token. There is no per-admin identity.
- Idempotency keys expire after 72 hours by default. Rows from before the expiry column have no expiry; `aip idempotency purge` removes only keys that have one.
- A database owner can still drop the audit trigger. The trigger stops application code and ordinary users, not the owner.

Report issues to the repository owner.

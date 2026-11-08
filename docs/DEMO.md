# Five-minute demo script

Story: "Acme Dental" has a support knowledge base, a ticketing system and a CRM. We connect them in five minutes
and show the agent answering with citations, with guardrails and an audit trail.

All output below is **real**, captured on 2026-11-07 from a local run with the `FakeProvider`
(SQLite, API on a random port, mock CRM on another). Ports, ids and the secrets are from a throwaway database
and are shortened here. The fake model is a rule-based stand-in, so wording is plain. With a real model
the answers read better and need the eval harness to check them.

## Setup (30 seconds)

```bash
export DATABASE_URL=sqlite:///./demo.db ADMIN_TOKEN=demo-admin ALLOW_PRIVATE_WEBHOOK_TARGETS=true
export CRM_BASE_URL=http://127.0.0.1:9001 CRM_CLIENT_ID=demo-client CRM_CLIENT_SECRET=demo-secret
.venv/bin/alembic upgrade head
.venv/bin/uvicorn mock_crm.app:app --port 9001 &
.venv/bin/uvicorn --factory aip.api.app:app_factory --port 8000 &
export API=http://127.0.0.1:8000
```

(With Docker: `docker compose up --build`, then use `API=http://localhost:8000`, `ADMIN_TOKEN=change-me-demo-admin-token`.
Compose was not run end to end by the author. The image build and a container health check were.)

## 1. Create a tenant (the operator)

```bash
curl -s -X POST $API/admin/tenants -H "X-Admin-Token: demo-admin" -H "Content-Type: application/json" \
  -d '{"name":"acme-dental","allowed_tools":["search_knowledge_base","lookup_ticket","create_ticket","call_customer_webhook"],"webhook_url":"http://127.0.0.1:9001/hooks/receive"}'
```
```json
{
    "id": 1,
    "name": "acme-dental",
    "api_key": "aip_tf3A...(shown once)",
    "webhook_secret": "whsec_s_HL...",
    "note": "Store the api_key now. It is not shown again."
}
```

Say: "Only the hash of the key is stored. The tenant also gets an allow-list of tools."

```bash
export KEY=aip_...   WHSEC=whsec_...
```

## 2. Load the knowledge base

```bash
for f in evals/kb/*.md; do curl -s -X POST $API/v1/documents/upload -H "X-API-Key: $KEY" -F "file=@$f"; echo; done
```
```
{"name":"password-reset.md","chunks":2}
{"name":"refund-policy.md","chunks":2}
{"name":"shipping.md","chunks":2}
{"name":"sla.md","chunks":2}
```

## 3. Retrieval

```bash
curl -s -X POST $API/v1/search -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"query":"how long is the password reset link valid","k":2}'
```
```json
{"hits": [
  {"doc": "password-reset.md", "chunk": 0, "score": 0.6047,
   "text": "# Password reset\nTo reset a password, open the sign in page and choose Forgot password. We send a reset link that expires after 60 minutes."},
  {"doc": "refund-policy.md", "chunk": 1, "score": 0.1054, "text": "## How to request a refund\n..."}
]}
```
(Pretty-printed output shortened.)

## 4. Agent answer with a citation

```bash
curl -s -X POST $API/v1/agent/run -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"question":"How long do customers have to request a full refund?"}'
```
```json
{
    "run_id": 1,
    "status": "ok",
    "answer": "Customers can request a full refund within 30 days of purchase. After 30 days we offer account credit only. [refund-policy.md#0]",
    "citations": [{"doc": "refund-policy.md", "chunk": 0, "score": 0.4256}],
    "steps": [{"tool": "search_knowledge_base", "args": {"query": "How long do customers have to request a full refund?", "k": 3}, "outcome": "ok"}],
    "prompt_version": "triage_v1"
}
```

Say: "The answer cites a chunk. The platform checks that the chunk was really retrieved in this run."

Unknown topic. The agent must not guess:

```bash
curl -s -X POST $API/v1/agent/run -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"question":"Do you sell gift cards for the quantum widget?"}'
```
```json
{"run_id": 2, "status": "ok", "answer": "I could not find this in the knowledge base.", "citations": [], "steps": [...]}
```

## 5. Tools: tickets and webhooks

Questions sent to `/v1/agent/run` (answers printed as `status | answer`):

```
"Create a ticket about the broken label printer"        ->  ok | I created ticket T-1 (the broken label printer).
"What is the status of ticket T-1?"                      ->  ok | Ticket T-1 is open: the broken label printer.
"Notify the customer webhook that the refund was approved" ->  ok | I sent the agent.notification event to the customer webhook (status 200).
```

The mock CRM received the signed outbound call:

```bash
curl -s localhost:9001/hooks/received
```
```json
[{"body": "{\"event\":\"agent.notification\",\"data\":{\"text\":\"Notify the customer webhook that the refund was approved\"}}", "idempotency_key": null}]
```

## 6. Inbound signed webhook, replay and forgery

```bash
BODY='{"event":"ticket.created","subject":"Refund question","body":"How long do I have to ask for a full refund?"}'
TS=$(date +%s)
SIG="sha256=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$WHSEC" -hex | sed 's/^.* //')"
curl -s -i -X POST $API/v1/webhooks/inbound/1 -H "X-Timestamp: $TS" -H "X-Signature: $SIG" \
  -H "Idempotency-Key: evt-1001" -H "Content-Type: application/json" -d "$BODY"
```

First call, then the same call again (filtered to status line, replay header and body):

```
HTTP/1.1 200 OK
{"ticket_id": 2, "triage": {"status": "ok", "answer": "Open a ticket with the order number. Refunds are paid back to the original payment method in 5 to 7 business days. [refund-policy.md#1]", "citations": [{"doc": "refund-policy.md", "chunk": 1, "score": 0.3928}]}}
HTTP/1.1 200 OK
idempotent-replay: true
{"ticket_id": 2, "triage": {... same body ...}}
```

Same ticket id, no second ticket, no second agent run. Now a forged signature:

```
HTTP/1.1 401 Unauthorized
{"detail":"invalid signature"}
```

Say: "The rejected call is in the audit log. The signature covers the timestamp, so an old capture cannot be replayed."

## 7. Connector: pull from the customer's CRM (OAuth2 client credentials)

```bash
curl -s -X POST $API/v1/connectors/crm/customers/c-200/ingest -H "X-API-Key: $KEY"
# {"document":"crm-customer-c-200.md","chunks":1}
curl -s -X POST $API/v1/agent/run -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"question":"Who is the account manager for Blue Harbor Clinic?"}'
```
```
ok | Plan: standard. Account status: past_due. Account manager: Marcus Lee.  Notes: Invoice is 20 days overdue. [crm-customer-c-200.md#0]
```

## 8. Audit, metrics, logs

```bash
curl -s "$API/v1/audit?limit=6" -H "X-API-Key: $KEY"     # action, request id prefix, trace id prefix
```
```
agent.run 4d31e0b7 f766f78d
tool.call 4d31e0b7 f766f78d
connector.crm.ingest 3a0a60e8 9de99310
webhook.rejected 52d2b36c 23d8d08e
webhook.inbound 8ac2b979 6942dbe7
agent.run 8ac2b979 6942dbe7
```
```bash
curl -s $API/metrics | grep -E "^aip_(agent_runs|tool_calls)_total"
```
```
aip_agent_runs_total{status="ok"} 7.0
aip_tool_calls_total{outcome="ok",tool="search_knowledge_base"} 4.0
aip_tool_calls_total{outcome="ok",tool="create_ticket"} 1.0
aip_tool_calls_total{outcome="ok",tool="lookup_ticket"} 1.0
aip_tool_calls_total{outcome="ok",tool="call_customer_webhook"} 1.0
```

One structured log line per request (from a separate local run; the question contained an email, which is not logged):

```json
{"ts": "2026-11-07T20:04:05-0500", "level": "INFO", "logger": "aip.http", "msg": "request", "request_id": "4fe776703310446e9ff7e64efc3c613f", "trace_id": "57faa906414dc6a087419d80a9808f07", "tenant_id": 1, "method": "POST", "path": "/v1/agent/run", "status": 200, "duration_ms": 13.58}
```

## 9. Evals

```bash
.venv/bin/aip eval run
```
```
Eval report  provider=fake  prompt=triage_v1
  [PASS] refund-window
  ... (9 cases)
Pass rate: 9/9 = 100.0%
```

Say: "With the fake provider this checks the plumbing: retrieval, citations, tool use. It cannot grade a real
model. To compare prompts on a real model, run `aip eval run --prompt-version triage_v2` with `LLM_PROVIDER` set."

## What was not shown

Real LLM calls, the Docker stack end to end, and the AWS deployment. See README "Limits".

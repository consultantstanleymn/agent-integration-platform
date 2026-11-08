# Customer onboarding: connect in 30 minutes

Audience: a customer engineer at the customer's company. You need `curl` and your support documents.
The platform operator gives you the base URL and creates your tenant.

## Before the call (operator, 5 minutes)

1. Create the tenant with the admin token. Choose the tools the customer may use.

   ```bash
   curl -s -X POST $BASE/admin/tenants -H "X-Admin-Token: $ADMIN_TOKEN" -H "Content-Type: application/json" \
     -d '{"name":"acme","allowed_tools":["search_knowledge_base","lookup_ticket"],"webhook_url":"https://acme.example.com/hooks/aip"}'
   ```

2. Send `api_key` and `webhook_secret` to the customer through a secure channel. They are shown once.

## Step 1: check access (2 minutes)

```bash
export KEY=<api_key>; export BASE=<base url>
curl -s $BASE/health
curl -s $BASE/v1/documents -H "X-API-Key: $KEY"      # expect: []
```

401 means a wrong key. 429 means you hit the rate limit: wait for `Retry-After`.

## Step 2: load the knowledge base (5 minutes)

```bash
for f in docs/*.md; do curl -s -X POST $BASE/v1/documents/upload -H "X-API-Key: $KEY" -F "file=@$f"; echo; done
```

Supported files: `.md`, `.txt`, `.pdf` (text PDFs only, no scanned images). Uploading the same file name replaces it.

## Step 3: test retrieval and the agent (5 minutes)

```bash
curl -s -X POST $BASE/v1/search -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{"query":"refund window"}'
curl -s -X POST $BASE/v1/agent/run -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{"question":"How long do customers have to ask for a refund?"}'
```

Check that `citations` points at the right document. If the answer is "I could not find this", the knowledge base
lacks the answer. Add a document. Do not tune the model first.

## Step 4: send events from your ticketing system (10 minutes)

Your system sends a signed POST for each new ticket to `POST $BASE/v1/webhooks/inbound/<tenant id>`.

Headers: `X-Timestamp` (unix seconds), `X-Signature` (`sha256=` plus hex HMAC-SHA256 of `"<timestamp>.<raw body>"`
with your `webhook_secret`), `Idempotency-Key` (a unique id per event, for example your ticket id).

Python sender:

```python
import hashlib, hmac, json, time, requests
body = json.dumps({"event": "ticket.created", "subject": "Refund", "body": "How long do I have?"})
ts = str(int(time.time()))
sig = "sha256=" + hmac.new(SECRET.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
requests.post(f"{BASE}/v1/webhooks/inbound/{TENANT_ID}", data=body,
              headers={"X-Timestamp": ts, "X-Signature": sig, "Idempotency-Key": "ticket-1001",
                       "Content-Type": "application/json"})
```

The response holds the new `ticket_id` and the agent's `triage` answer with citations. Retrying the same
`Idempotency-Key` returns the same response with the header `Idempotent-Replay: true`. No second ticket is made.

| Status | Meaning |
|---|---|
| 401 | Bad signature, old timestamp (over 5 minutes), or unknown tenant |
| 400 | Missing `Idempotency-Key` |
| 422 | Body is not a valid `ticket.created` event |

## Step 5: receive events from us (optional, 3 minutes)

If the tenant has a `webhook_url` and `call_customer_webhook` is allowed, the agent can send events. We sign them
the same way. Verify with the same formula and reject timestamps older than 5 minutes. We retry 5xx, 429 and network
errors with backoff. A 4xx stops retries.

## Go-live checklist

- [ ] Knowledge base reviewed by the support lead. No secrets or personal data in the documents.
- [ ] 10 real questions run, answers and citations checked.
- [ ] Webhook signature check works in your receiver, with a bad signature rejected.
- [ ] You can see your calls in `GET /v1/audit`.
- [ ] You know who to call (operator) and the limits: see `docs/RUNBOOK.md`.

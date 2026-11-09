# Runbook for the engagement features

The base runbook is [docs/RUNBOOK.md](../RUNBOOK.md). This file adds the procedures for the case graph, the queue, failover and the eval gate. Commands assume a shell with the same environment variables as the API.

## Stuck approvals

Symptom: cases stay `awaiting_approval`.

1. List them: `GET /v1/cases?status=awaiting_approval` with a tenant key.
2. A team lead decides: `POST /v1/cases/{thread_id}/decision` with `{"decision": "approve"|"reject", "reason": "..."}`.
3. To clear old ones: `aip cases expire --older-than-hours 24`. Each expired case ends `rejected` with the audit row `case.expired`.
4. If a decision returns 409, the case is no longer waiting. Read it with `GET /v1/cases/{thread_id}`.

Schedule `aip cases expire` (for example once an hour). The platform does not run it by itself.

## Dead-letter queue and redrive

Symptom: `aip queue depth` prints a number above 0.

1. Read the number: `aip queue depth`.
2. Find the cause. A common cause is a missing blob. Check the worker log for the failed job.
3. Fix the cause (for example put the blob back).
4. Move the jobs back: `aip queue redrive --max 10`. The idempotency key makes a redriven job safe.
5. Run the worker: `aip worker run-once`. Check `aip queue depth` is 0.

## Provider failover alarm

Symptom: the counter `aip_llm_failover_total` rises.

1. Look at the `from` and `to` labels on `/metrics`.
2. Check the primary provider status and your key. A timeout shows as `ProviderError timeout` in the logs.
3. The breaker opens after five failures in a row and probes the primary again after 30 seconds. No action is needed if the primary recovers.
4. If the secondary also fails, cases end with status `provider_error`. Fix the provider or set `LLM_PROVIDER=fake` only for a demo.

## Budget exceeded spikes

Symptom: many cases end `budget_exceeded`.

1. Read the limits: `CASE_MAX_STEPS` and `CASE_MAX_TOKENS`.
2. Check a few case rows: `GET /v1/cases/{thread_id}` shows `steps_used`, `tokens_used` and the usage block.
3. If tickets are long, raise `CASE_MAX_TOKENS`. If the model loops, find the cause before you raise `CASE_MAX_STEPS`.
4. No action is taken for a case that ends `budget_exceeded`. Send it to a person.

## Eval gate failure

Symptom: `aip eval run --suite cases --replay --min-pass-rate 0.95` exits 1.

1. Read the message. "corrupted cassette" means a file in `evals/cassettes/` is damaged. Restore it from git.
2. "cassette missing" fails one case. It means a prompt, the retrieved text or the graph changed.
3. If the change is intended, refresh the cassettes: `python scripts/make_synthetic_cassettes.py`. Read the git diff before you commit it.
4. A pass rate below 0.95 with no error is a real regression. Read the failed case lines in the output.

## Cassette refresh

Cassettes are synthetic. Run `python scripts/make_synthetic_cassettes.py`, then `aip eval run --suite cases --replay`. Commit the changed files and the new hashes in `docs/engagement/EVAL-REPORT.md`. Record mode for a real provider is blocked unless `AIP_ALLOW_RECORD=1` is set. Do not use it without a spend cap.

## Key rotation for tenant keys

1. Create a new key: `POST /admin/tenants/{tenant_id}/keys` with the admin token and `{"role": "agent"|"team_lead", "label": "..."}`. The key is shown once.
2. Give the new key to the client. Wait until the client uses it.
3. Remove the old key from the database. There is no delete route yet, so use SQL on `tenant_keys` and write the change in the audit log by hand.
4. The legacy tenant key (set at tenant creation) cannot be rotated without a new tenant. This is a known gap.

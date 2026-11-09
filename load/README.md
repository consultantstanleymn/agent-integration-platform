# Load test

This folder holds a Locust test of the case path. It sends three kinds of requests: small cases, large cases that wait for approval, and `/v2/search`.

## Run it

```bash
scripts/locust_run.sh
```

The script starts Postgres (a local container on port 25132), starts the API with two workers on port 25100, makes a tenant, runs Locust for two minutes with 20 users, and stops everything. It writes `docs/engagement/evidence/locust-case-path.html`.

The fake provider waits `FAKE_LLM_DELAY_MS` per call (default 200 ms) when `LLM_PROVIDER=fake_slow`. Use this setting for load tests only. It stands in for the delay of a hosted model.

## What the numbers mean

They show how this code behaves on one laptop with a fake model that answers after a fixed delay. They do not show production capacity or hosted model speed. See `docs/engagement/LOADTEST.md`.

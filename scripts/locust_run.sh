#!/usr/bin/env bash
# Run the Locust case-path test against the API with two workers and Postgres. Measured on one laptop.
# Needs: podman, the project venv, jq, and locust (set LOCUST to its path if it is not on PATH).
# Ports: API 25100, Postgres 25132. Output: docs/engagement/evidence/locust-case-path.html
set -euo pipefail
cd "$(dirname "$0")/.."
LOCUST="${LOCUST:-locust}"  # path to the locust program if it is not on PATH
PY=.venv/bin
OUT_HTML="docs/engagement/evidence/locust-case-path.html"
CSV_PREFIX="${CSV_PREFIX:-/tmp/locust-agt}"
API_PID=""
cleanup() {
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  scripts/pg-test.sh stop >/dev/null 2>&1 || true
}
trap cleanup EXIT

for port in 25100 25132; do
  if ss -ltn | grep -q ":$port "; then echo "port $port is in use, stop" >&2; exit 1; fi
done

echo "load average at start: $(cut -d' ' -f1-3 /proc/loadavg)"
scripts/pg-test.sh start
PG="$(scripts/pg-test.sh url)"
export DATABASE_URL="$PG" CHECKPOINT_URL="$PG"
export ADMIN_TOKEN="load-demo-admin" RATE_LIMIT_PER_MINUTE=1000000
export LLM_PROVIDER=fake_slow FAKE_LLM_DELAY_MS="${FAKE_LLM_DELAY_MS:-200}" EMBEDDING_PROVIDER=fake
$PY/alembic upgrade head >/dev/null
$PY/uvicorn --factory aip.api.app:app_factory --host 127.0.0.1 --port 25100 --workers 2 >/tmp/locust-api.log 2>&1 &
API_PID=$!
for _ in $(seq 1 40); do
  curl -sf http://127.0.0.1:25100/health >/dev/null && break
  sleep 1
done

API=http://127.0.0.1:25100
KEY=$(curl -sf -X POST "$API/admin/tenants" -H "X-Admin-Token: $ADMIN_TOKEN" -H 'Content-Type: application/json' -d '{"name":"load-co"}' | jq -r .api_key)
for doc in returns warranty shipping; do
  curl -sf -X POST "$API/v1/documents" -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
    -d "{\"name\":\"$doc.md\",\"text\":\"# $doc\\n\\nThe $doc policy: return window sixty days, warranty zipper two years, shipping damage photo.\"}" >/dev/null
done

mkdir -p "$(dirname "$OUT_HTML")"
LOAD_API_KEY="$KEY" nice -n 10 "$LOCUST" -f load/locustfile.py --headless -u 20 -r 2 -t 2m \
  --host "$API" --html "$OUT_HTML" --csv "$CSV_PREFIX" --only-summary
echo "load average at end: $(cut -d' ' -f1-3 /proc/loadavg)"
echo "--- aggregated csv"
cat "${CSV_PREFIX}_stats.csv"

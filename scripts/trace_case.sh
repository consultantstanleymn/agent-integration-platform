#!/usr/bin/env bash
# Run one case through the API with tracing on, and save the Jaeger trace as JSON.
# Needs podman, the project venv and the local Jaeger image. Ports: API 25100, Jaeger UI 25186, OTLP 25118.
# Usage: scripts/trace_case.sh [output.json]
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-docs/engagement/evidence/jaeger-agent-trace.json}"
PY=.venv/bin
WORK="$(mktemp -d)"
API_PID=""
cleanup() {
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  podman compose -f docker-compose.yml --profile obs down >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

for port in 25100 25186 25118; do
  if ss -ltn | grep -q ":$port "; then echo "port $port is in use, stop" >&2; exit 1; fi
done

podman compose -f docker-compose.yml --profile obs up -d jaeger >/dev/null 2>&1
for _ in $(seq 1 30); do
  curl -sf "http://127.0.0.1:25186/" >/dev/null && break
  sleep 1
done

export DATABASE_URL="sqlite:///$WORK/app.db"
export CHECKPOINT_URL="sqlite:///$WORK/cp.sqlite"
export ADMIN_TOKEN="trace-demo-admin"
export OTEL_EXPORTER_OTLP_ENDPOINT="http://127.0.0.1:25118"
export LLM_PROVIDER=fake EMBEDDING_PROVIDER=fake
export OTEL_BSP_SCHEDULE_DELAY=500  # send spans every 0.5 s so a short run is exported
$PY/alembic upgrade head >/dev/null
$PY/uvicorn --factory aip.api.app:app_factory --host 127.0.0.1 --port 25100 >"$WORK/api.log" 2>&1 &
API_PID=$!
for _ in $(seq 1 30); do
  curl -sf http://127.0.0.1:25100/health >/dev/null && break
  sleep 1
done

API=http://127.0.0.1:25100
TENANT=$(curl -sf -X POST "$API/admin/tenants" -H "X-Admin-Token: $ADMIN_TOKEN" -H 'Content-Type: application/json' -d '{"name":"trace-co"}')
KEY=$(echo "$TENANT" | jq -r .api_key)
TID=$(echo "$TENANT" | jq -r .id)
LEAD=$(curl -sf -X POST "$API/admin/tenants/$TID/keys" -H "X-Admin-Token: $ADMIN_TOKEN" -H 'Content-Type: application/json' -d '{"role":"team_lead","label":"Lead"}' | jq -r .api_key)
curl -sf -X POST "$API/v1/documents" -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"name":"returns.md","text":"# Returns\n\nReturns are accepted within 60 days with an order number."}' >/dev/null
# shellcheck disable=SC2016  # the dollar sign in the ticket text is literal
THREAD=$(curl -sf -X POST "$API/v1/cases" -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"subject":"Return","body":"Please refund $150.00 for order ORD-5001."}' | jq -r .thread_id)
curl -sf -X POST "$API/v1/cases/$THREAD/decision" -H "X-API-Key: $LEAD" -H 'Content-Type: application/json' \
  -d '{"decision":"approve","reason":"checked"}' >/dev/null
sleep 6
mkdir -p "$(dirname "$OUT")"
curl -sf "http://127.0.0.1:25186/api/traces?service=agent-integration-platform&limit=5" -o "$OUT"
echo "spans in the first trace: $(jq '.data[0].spans | length' "$OUT")"

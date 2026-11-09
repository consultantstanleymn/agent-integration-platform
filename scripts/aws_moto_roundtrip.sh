#!/usr/bin/env bash
# Full cloud verifier round trip against a local moto server (no real AWS).
# Steps: start moto on port 25150, seed, export the IAM policy, verify,
# dry-run teardown, teardown --yes, verify --expect-empty, stop moto by PID.
# Needs the project venv. Exit code is non-zero if any step fails.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin
PORT=25150
EP="http://127.0.0.1:$PORT"
WORK="$(mktemp -d)"
MOTO_PID=""
cleanup() {
  [ -n "$MOTO_PID" ] && kill "$MOTO_PID" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT
if ss -ltn | grep -q ":$PORT "; then echo "port $PORT is in use, stop" >&2; exit 1; fi
export AWS_ACCESS_KEY_ID=testing AWS_SECRET_ACCESS_KEY=testing AWS_DEFAULT_REGION=us-east-1

nice -n 10 "$PY/moto_server" -H 127.0.0.1 -p "$PORT" >"$WORK/moto.log" 2>&1 &
MOTO_PID=$!
for _ in $(seq 1 30); do curl -s "$EP/" >/dev/null 2>&1 && break; sleep 1; done

echo "## seed"
SEED="$($PY/python scripts/aws_seed_moto.py --endpoint-url "$EP")"
echo "$SEED"
BUCKET=$(echo "$SEED" | $PY/python -c 'import json,sys; print(json.load(sys.stdin)["bucket"])')
QUEUE=$(echo "$SEED" | $PY/python -c 'import json,sys; print(json.load(sys.stdin)["queue_url"])')
DLQ=$(echo "$SEED" | $PY/python -c 'import json,sys; print(json.load(sys.stdin)["dlq_url"])')
SECRET=$(echo "$SEED" | $PY/python -c 'import json,sys; print(json.load(sys.stdin)["secret_id"])')
ARGS=(--endpoint-url "$EP" --bucket "$BUCKET" --queue-url "$QUEUE" --dlq-url "$DLQ" --secret-id "$SECRET")

$PY/python scripts/aws_policy_export.py --bucket "$BUCKET" --queue "${QUEUE##*/}" --dlq "${DLQ##*/}" --secret "$SECRET" >"$WORK/policy.json"

echo "## verify"
$PY/python scripts/aws_verify.py "${ARGS[@]}" --policy-file "$WORK/policy.json"
echo "exit code: 0"

echo "## teardown dry run"
$PY/python scripts/aws_teardown.py "${ARGS[@]}"
echo "## teardown --yes"
$PY/python scripts/aws_teardown.py "${ARGS[@]}" --yes
echo "## verify --expect-empty"
$PY/python scripts/aws_verify.py "${ARGS[@]}" --expect-empty
echo "exit code: 0"

#!/usr/bin/env bash
set -euo pipefail

CONTAINER="aip-pg-test"
IMAGE="docker.io/pgvector/pgvector:0.8.0-pg16"
PORT="25132"
DB_USER="aip"
DB_PASSWORD="aip"
DB_NAME="aip_test"

usage() {
    echo "usage: $0 {start|stop|url}" >&2
    exit 2
}

start() {
    if podman container exists "${CONTAINER}"; then
        podman start "${CONTAINER}" >/dev/null
    else
        podman run -d --name "${CONTAINER}" \
            -p "127.0.0.1:${PORT}:5432" \
            -e "POSTGRES_USER=${DB_USER}" \
            -e "POSTGRES_PASSWORD=${DB_PASSWORD}" \
            -e "POSTGRES_DB=${DB_NAME}" \
            "${IMAGE}" >/dev/null
    fi
    # The image starts a temporary server for its first set-up and then restarts. Only the final server
    # listens on TCP, so test TCP twice in a row.
    local ok=0
    for _ in {1..90}; do
        if podman exec "${CONTAINER}" pg_isready -h 127.0.0.1 -U "${DB_USER}" -d "${DB_NAME}" >/dev/null 2>&1; then
            ok=$((ok + 1))
            if [ "${ok}" -ge 2 ]; then
                return 0
            fi
        else
            ok=0
        fi
        sleep 1
    done
    echo "postgres did not become ready" >&2
    exit 1
}

stop() {
    if podman container exists "${CONTAINER}"; then
        podman stop "${CONTAINER}" >/dev/null
        podman rm "${CONTAINER}" >/dev/null
    fi
}

url() {
    echo "postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:${PORT}/${DB_NAME}"
}

case "${1:-}" in
    start) start ;;
    stop) stop ;;
    url) url ;;
    *) usage ;;
esac

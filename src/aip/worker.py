"""Ingest worker: read jobs from the queue, fetch the text from the blob store, ingest it.

A job that fails stays in the queue. The queue delivers it again, and after the maximum number of
receives it moves to the dead-letter queue. A redrive puts it back. The idempotency key makes a
redriven job safe: a job that already worked does not make a second document.
"""

import hashlib
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from aip.audit import audit
from aip.integrations.idempotency import run_once as run_idempotent
from aip.ports import Ports
from aip.providers.base import Embedder
from aip.rag.service import ingest_document

log = logging.getLogger("aip.worker")


def job_key(tenant_id: int, blob_key: str) -> str:
    return "ingest-" + hashlib.sha256(f"{tenant_id}:{blob_key}".encode()).hexdigest()[:32]


def run_once(
    ports: Ports,
    session_factory: Callable[[], Session],
    embedder: Embedder,
    max_messages: int = 5,
) -> dict[str, int]:
    processed = failed = 0
    for job in ports.queue.receive(max_messages=max_messages):
        body: dict[str, Any] = job.body
        try:
            text = ports.blob.get(body["blob_key"]).decode()
            with session_factory() as db:

                def ingest(db: Session = db, body: dict[str, Any] = body, text: str = text) -> dict[str, Any]:
                    n = ingest_document(db, body["tenant_id"], embedder, body["name"], text)
                    audit(db, body["tenant_id"], "document.ingest", name=body["name"], chunks=n)
                    return {"name": body["name"], "chunks": n}

                run_idempotent(db, body["tenant_id"], str(body["idempotency_key"]), ingest)
            ports.queue.delete(job)
            processed += 1
        except Exception as exc:  # noqa: BLE001  any failure leaves the job for a retry and the DLQ
            log.warning("ingest job failed", extra={"fields": {"job": job.id, "error": type(exc).__name__}})
            failed += 1
    return {"processed": processed, "failed": failed}

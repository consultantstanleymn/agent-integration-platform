"""Retrieval evaluation: recall@k, MRR and latency per search mode over a golden query set.

A relevant reference is ``<file>#<heading>``. A returned chunk matches when its first line is a
heading with that text. Queries with no relevant reference (the corpus has no answer) are counted
but left out of the recall and MRR means.
"""

import math
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from aip.providers.base import Embedder
from aip.rag.retriever import Retriever, SearchQuery
from aip.rag.service import ingest_document, labels_for_role

FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def parse_front_matter(text: str) -> tuple[str, str]:
    """Return ``(label, body)``. A file without front matter is public."""
    match = FRONT_MATTER.match(text)
    if not match:
        return "public", text
    meta = yaml.safe_load(match.group(1)) or {}
    return str(meta.get("label", "public")), text[match.end() :].lstrip("\n")


def heading_of(chunk_text: str) -> str:
    first = chunk_text.lstrip().splitlines()[0] if chunk_text.strip() else ""
    return first.lstrip("#").strip() if first.startswith("#") else ""


def ref_of(doc: str, chunk_text: str) -> str:
    return f"{doc}#{heading_of(chunk_text)}"


def recall_at_k(relevant: set[str], ranked: list[str], k: int) -> float | None:
    """Share of relevant references found in the top k. ``None`` when nothing is relevant."""
    if not relevant:
        return None
    return len(relevant & set(ranked[:k])) / len(relevant)


def mrr(relevant: set[str], ranked: list[str]) -> float | None:
    """Reciprocal rank of the first relevant result. ``None`` when nothing is relevant."""
    if not relevant:
        return None
    for position, ref in enumerate(ranked, start=1):
        if ref in relevant:
            return 1.0 / position
    return 0.0


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def load_golden(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text())
    return list(data["queries"])


def ingest_corpus(db: Session, tenant_id: int, embedder: Embedder, corpus_dir: Path) -> int:
    count = 0
    for path in sorted(corpus_dir.glob("*.md")):
        label, body = parse_front_matter(path.read_text())
        ingest_document(db, tenant_id, embedder, path.name, body, label)
        count += 1
    db.commit()
    return count


def run_retrieval_eval(
    golden_path: Path,
    corpus_dir: Path,
    db: Session,
    tenant_id: int,
    embedder: Embedder,
    retriever: Retriever,
    modes: list[str],
    k: int = 5,
) -> dict[str, Any]:
    """Run every golden query in every mode against an already ingested corpus."""
    golden = load_golden(golden_path)
    result: dict[str, Any] = {"k": k, "queries": len(golden), "modes": {}, "per_query": {}}
    for mode in modes:
        rows: list[dict[str, Any]] = []
        times: list[float] = []
        for item in golden:
            vector = embedder.embed([item["query"]])[0]
            query = SearchQuery(
                tenant_id=tenant_id,
                text=item["query"],
                vector=vector,
                k=k,
                mode=mode,  # type: ignore[arg-type]
                allowed_labels=labels_for_role(item.get("role", "agent")),
            )
            start = time.perf_counter()
            hits = retriever.search(query)
            times.append((time.perf_counter() - start) * 1000)
            ranked = [ref_of(h.doc, h.text) for h in hits]
            relevant = set(item.get("relevant") or [])
            rows.append(
                {
                    "id": item["id"],
                    "kind": item.get("kind", ""),
                    "relevant": sorted(relevant),
                    "ranked": ranked,
                    "recall": recall_at_k(relevant, ranked, k),
                    "mrr": mrr(relevant, ranked),
                }
            )
        scored = [r for r in rows if r["recall"] is not None]
        result["per_query"][mode] = rows
        result["modes"][mode] = {
            f"recall@{k}": round(sum(r["recall"] for r in scored) / len(scored), 4) if scored else 0.0,
            "mrr": round(sum(r["mrr"] for r in scored) / len(scored), 4) if scored else 0.0,
            "p50_ms": round(percentile(times, 50), 3),
            "p95_ms": round(percentile(times, 95), 3),
            "scored_queries": len(scored),
            "no_answer_queries": len(rows) - len(scored),
        }
    return result


RetrieverFactory = Callable[[Session], Retriever]


def prepare_database(engine: Any, url: str) -> None:
    """Make a fresh schema. SQLite uses the ORM tables. PostgreSQL is reset and migrated to head."""
    from sqlalchemy import text

    from aip.models import Base

    if engine.dialect.name != "postgresql":
        Base.metadata.create_all(engine)
        return
    import os

    if os.environ.get("AIP_ALLOW_SCHEMA_RESET") != "1":
        raise SystemExit(
            "refusing to reset a PostgreSQL schema: set AIP_ALLOW_SCHEMA_RESET=1 for a test database"
        )
    from alembic import command
    from alembic.config import Config

    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    root = Path(__file__).resolve().parents[3]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    os.environ["DATABASE_URL"] = url
    command.upgrade(cfg, "head")

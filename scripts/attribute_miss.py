"""Find which stage lost a relevant chunk for one golden query.

Stages, in order: parse, chunk, filter, vector, keyword, fusion. The script prints the facts for each
stage and the first stage that lost the chunk. Usage:
  python scripts/attribute_miss.py q07 --embedder fake|local_onnx --db-url sqlite:// --k 5 [--rrf-k 60]
"""

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aip.db import make_engine, make_session_factory  # noqa: E402
from aip.evals.retrieval import (  # noqa: E402
    ingest_corpus,
    load_golden,
    parse_front_matter,
    prepare_database,
    ref_of,
)
from aip.providers.fake import FakeProvider  # noqa: E402
from aip.rag.chunking import chunk_text  # noqa: E402
from aip.rag.retriever import SearchQuery  # noqa: E402
from aip.rag.service import build_retriever, labels_for_role  # noqa: E402
from aip.tenants import create_tenant  # noqa: E402

BIG = 1000


def rank_of(hits: list[Any], ref: str) -> int | None:
    for position, hit in enumerate(hits, start=1):
        if ref_of(hit.doc, hit.text) == ref:
            return position
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("query_id")
    parser.add_argument("--embedder", choices=["fake", "local_onnx"], default="fake")
    parser.add_argument("--db-url", default="sqlite://")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--golden", default=str(ROOT / "evals/retrieval/golden.yaml"))
    parser.add_argument("--corpus", default=str(ROOT / "evals/retrieval/corpus"))
    args = parser.parse_args(argv)

    item = next(q for q in load_golden(Path(args.golden)) if q["id"] == args.query_id)
    print(f"query {item['id']} ({item['kind']}, role {item['role']}): {item['query']}")
    if args.embedder == "local_onnx":
        from aip.providers.local_onnx import LocalOnnxEmbedder

        embedder: Any = LocalOnnxEmbedder()
    else:
        embedder = FakeProvider()

    engine = make_engine(args.db_url)
    prepare_database(engine, args.db_url, 384 if args.embedder == "local_onnx" else 256)
    with make_session_factory(engine)() as db:
        tenant, _ = create_tenant(db, "attribution", ["search_knowledge_base"])
        db.commit()
        ingest_corpus(db, tenant.id, embedder, Path(args.corpus))
        retriever = build_retriever(db, "auto", args.rrf_k)
        allowed = labels_for_role(item["role"])
        vector = embedder.embed([item["query"]])[0]
        runs: dict[str, list[Any]] = {}
        for mode in ("vector", "keyword", "hybrid"):
            query = SearchQuery(tenant.id, item["query"], vector, BIG, mode, allowed)  # type: ignore[arg-type]
            runs[mode] = retriever.search(query)
        for ref in item["relevant"]:
            doc, _, heading = ref.partition("#")
            print(f"\nrelevant: {ref}")
            path = Path(args.corpus) / doc
            label, body = parse_front_matter(path.read_text()) if path.exists() else ("", "")
            print(f"  parse: file found={path.exists()} label={label}")
            chunks = [
                c for c in chunk_text(body) if c.lstrip().startswith("#") and heading in c.splitlines()[0]
            ]
            print(f"  chunk: {len(chunks)} chunk(s) carry the heading")
            print(f"  filter: role {item['role']} may read label {label}: {label in allowed}")
            v, kw, hy = (rank_of(runs[m], ref) for m in ("vector", "keyword", "hybrid"))
            print(f"  vector rank: {v}   keyword rank: {kw}   hybrid (fused) rank: {hy}   k={args.k}")
            lost = None
            if not path.exists():
                lost = "parse"
            elif not chunks:
                lost = "chunk"
            elif label not in allowed:
                lost = "filter"
            elif v is None or v > args.k:
                lost = "vector"
            elif kw is None or kw > args.k:
                lost = "keyword"
            elif hy is None or hy > args.k:
                lost = "fusion"
            print(f"  first stage that lost it: {lost or 'none (found within k in every stage)'}")
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())

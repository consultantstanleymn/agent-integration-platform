"""Command line: create a tenant, run the eval harness, print a report."""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from aip.config import Settings
from aip.db import install_audit_triggers, make_engine, make_session_factory
from aip.graph.actions import build_action_port
from aip.graph.authority import load_authority
from aip.graph.service import CaseService
from aip.graph.state import Ticket
from aip.models import Tenant
from aip.providers import build_chat_provider, build_embedder
from aip.providers.pricing import load_prices
from aip.tenants import ALL_TOOLS, create_tenant


def _retrieval_command(args: argparse.Namespace) -> int:
    from aip.evals.retrieval import (
        ingest_corpus,
        ingest_corpus_qdrant,
        prepare_database,
        run_retrieval_eval,
    )
    from aip.providers.fake import FakeProvider
    from aip.rag.retriever import Retriever
    from aip.rag.service import build_retriever
    from aip.tenants import create_tenant

    engine = make_engine(args.db_url)
    prepare_database(engine, args.db_url)
    embedder: Any
    if args.embedder == "local_onnx":
        from aip.providers.local_onnx import LocalOnnxEmbedder

        embedder = LocalOnnxEmbedder()
    else:
        embedder = FakeProvider()
    settings = Settings()
    cached = bool(settings.redis_url and settings.embed_cache)
    if cached:
        import redis

        from aip.providers.cache import CachedEmbedder

        embedder = CachedEmbedder(embedder, redis.Redis.from_url(settings.redis_url), model=args.embedder)
    try:
        with make_session_factory(engine)() as db:
            tenant, _ = create_tenant(db, "retrieval-eval", ["search_knowledge_base"])
            db.commit()
            retriever: Retriever
            if args.store == "qdrant":
                from qdrant_client import QdrantClient

                from aip.rag.qdrant_store import QdrantStore

                client = (
                    QdrantClient(args.qdrant_url)
                    if args.qdrant_url != ":memory:"
                    else QdrantClient(":memory:")
                )
                dim = len(embedder.embed(["dimension probe"])[0])
                qstore = QdrantStore(client, "retrieval-eval", dim, args.rrf_k)
                ingest_corpus_qdrant(qstore, tenant.id, embedder, Path(args.corpus))
                retriever = qstore
            else:
                ingest_corpus(db, tenant.id, embedder, Path(args.corpus))
                retriever = build_retriever(db, args.retriever, args.rrf_k)
            report = run_retrieval_eval(
                Path(args.golden),
                Path(args.corpus),
                db,
                tenant.id,
                embedder,
                retriever,
                [m for m in args.modes.split(",") if m],
                args.k,
            )
    finally:
        engine.dispose()
    if cached:
        from aip.providers.cache import EMBED_CACHE_HITS, EMBED_CACHE_MISSES

        hits, misses = EMBED_CACHE_HITS._value.get(), EMBED_CACHE_MISSES._value.get()
        report["embed_cache"] = {
            "hits": hits,
            "misses": misses,
            "hit_ratio": round(hits / (hits + misses), 4) if hits + misses else 0.0,
        }
    report["config"] = {
        "embedder": args.embedder,
        "store": args.store,
        "retriever": type(retriever).__name__,
        "rrf_k": args.rrf_k,
        "db": make_url(args.db_url).get_backend_name(),
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for mode, row in report["modes"].items():
            print(mode, row)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aip")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("create-tenant", help="create a tenant and print its API key once")
    t.add_argument("name")
    t.add_argument("--tools", default="search_knowledge_base,lookup_ticket")
    t.add_argument("--webhook-url")

    c = sub.add_parser("case", help="start a case for a tenant")
    csub = c.add_subparsers(dest="case_cmd", required=True)
    cs = csub.add_parser("start")
    cs.add_argument("--tenant-id", type=int, required=True)
    cs.add_argument("--subject", required=True)
    cs.add_argument("--body", default="")
    cs.add_argument("--order-id", default=None)

    ce = sub.add_parser("cases", help="case maintenance")
    cesub = ce.add_subparsers(dest="cases_cmd", required=True)
    ex = cesub.add_parser("expire", help="reject approvals that wait too long")
    ex.add_argument("--older-than-hours", type=float, default=24.0)

    ip = sub.add_parser("idempotency", help="idempotency key maintenance")
    ipsub = ip.add_subparsers(dest="idem_cmd", required=True)
    ipsub.add_parser("purge", help="delete expired idempotency keys")

    wk = sub.add_parser("worker", help="ingest worker")
    wksub = wk.add_subparsers(dest="worker_cmd", required=True)
    wksub.add_parser("run-once", help="process one batch of ingest jobs")
    qu = sub.add_parser("queue", help="ingest queue maintenance")
    qusub = qu.add_subparsers(dest="queue_cmd", required=True)
    qusub.add_parser("depth", help="print the dead-letter queue depth")
    rd = qusub.add_parser("redrive", help="move dead-letter jobs back to the queue")
    rd.add_argument("--max", type=int, default=10)

    e = sub.add_parser("eval", help="run or report evals")
    esub = e.add_subparsers(dest="eval_cmd", required=True)
    r = esub.add_parser("run")
    r.add_argument("--suite", choices=["qa", "cases"], default="qa")
    r.add_argument("--testset", default=None)
    r.add_argument("--json", action="store_true", help="print the case report as JSON")
    r.add_argument("--replay", action="store_true", help="answer from stored cassettes, no provider")
    r.add_argument("--cassettes", default="evals/cassettes")
    r.add_argument("--prompt-version", default=None)
    r.add_argument("--out", default="evals/results/latest.json")
    r.add_argument("--min-pass-rate", type=float, default=0.0)
    rt = esub.add_parser("retrieval", help="recall, MRR and latency per search mode")
    rt.add_argument("--modes", default="vector,keyword,hybrid")
    rt.add_argument("--k", type=int, default=5)
    rt.add_argument("--db-url", default="sqlite://")
    rt.add_argument("--embedder", choices=["fake", "local_onnx"], default="fake")
    rt.add_argument("--rrf-k", type=int, default=60)
    rt.add_argument("--retriever", choices=["auto", "cosine", "pg"], default="auto")
    rt.add_argument(
        "--store", choices=["pg", "qdrant"], default="pg", help="pg: the SQL store. qdrant: Qdrant"
    )
    rt.add_argument("--qdrant-url", default=":memory:", help="http://127.0.0.1:25133 or :memory:")
    rt.add_argument("--golden", default="evals/retrieval/golden.yaml")
    rt.add_argument("--corpus", default="evals/retrieval/corpus")
    rt.add_argument("--json", action="store_true")
    rep = esub.add_parser("report")
    rep.add_argument("file", nargs="?", default="evals/results/latest.json")

    args = p.parse_args(argv)
    if args.cmd == "create-tenant":
        tools = [x for x in args.tools.split(",") if x]
        bad = set(tools) - set(ALL_TOOLS)
        if bad:
            print(f"unknown tools: {sorted(bad)}", file=sys.stderr)
            return 2
        s = Settings()
        factory = make_session_factory(make_engine(s.database_url))
        with factory() as db:
            tenant, key = create_tenant(db, args.name, tools, args.webhook_url)
            db.commit()
            print(json.dumps({"id": tenant.id, "api_key": key, "webhook_secret": tenant.webhook_secret}))
        return 0

    if args.cmd in ("worker", "queue"):
        from aip.ports import build_ports
        from aip.worker import run_once as worker_run_once

        s = Settings()
        ports = build_ports(s)
        if args.cmd == "worker":
            engine = make_engine(s.database_url)
            try:
                result = worker_run_once(ports, make_session_factory(engine), build_embedder(s))
            finally:
                engine.dispose()
            print(json.dumps(result))
        elif args.queue_cmd == "depth":
            print(json.dumps({"dlq_depth": ports.queue.dlq_depth()}))
        else:
            print(json.dumps({"redriven": ports.queue.redrive(args.max)}))
        return 0

    if args.cmd == "idempotency":
        from aip.integrations.idempotency import purge_expired

        engine = make_engine(Settings().database_url)
        try:
            with make_session_factory(engine)() as db:
                print(json.dumps({"purged": purge_expired(db)}))
        finally:
            engine.dispose()
        return 0

    if args.cmd in ("case", "cases"):
        s = Settings()
        engine = make_engine(s.database_url)
        install_audit_triggers(engine)
        factory = make_session_factory(engine)
        _authority = load_authority(s.authority_path)
        service = CaseService(
            checkpoint_url=s.checkpoint_url,
            provider=build_chat_provider(s),
            embedder=build_embedder(s),
            approval_limit_minor=s.approval_limit_minor,
            max_steps=s.case_max_steps,
            max_tokens=s.case_max_tokens,
            authority=_authority,
            prices=load_prices(s.llm_price_per_mtok_json),
            action_port=build_action_port(s.action_port, str(s.database_url), _authority.hard_cap_minor),
        )
        if args.cmd == "cases":
            try:
                with factory() as db:
                    count = service.expire_pending(
                        db, datetime.now(UTC), timedelta(hours=args.older_than_hours)
                    )
                print(json.dumps({"expired": count}))
            finally:
                service.close()
                engine.dispose()
            return 0
        try:
            with factory() as db:
                case_tenant = db.get(Tenant, args.tenant_id)
                if not case_tenant:
                    print("tenant not found", file=sys.stderr)
                    return 2
                ticket = Ticket(subject=args.subject, body=args.body, order_id=args.order_id)
                state = service.start(db, case_tenant, "agent", ticket)
                print(
                    json.dumps(
                        {
                            "thread_id": state.thread_id,
                            "status": state.status,
                            "proposal": state.proposal.model_dump() if state.proposal else None,
                            "needs_approval": state.needs_approval,
                        }
                    )
                )
        finally:
            service.close()
            engine.dispose()
        return 0

    from aip.evals.runner import format_report, run_evals

    if args.eval_cmd == "retrieval":
        return _retrieval_command(args)
    if args.eval_cmd == "run":
        if args.suite == "cases":
            from aip.evals.cases import format_case_report, run_case_suite

            chat = None
            if args.replay:
                from aip.providers.base import ProviderError
                from aip.providers.replay import RecordReplayProvider, verify_cassettes

                try:
                    verify_cassettes(Path(args.cassettes))
                except ProviderError as exc:
                    print(f"replay gate failed: {exc}", file=sys.stderr)
                    return 1
                chat = RecordReplayProvider(None, Path(args.cassettes), "replay")
            report = run_case_suite(Path(args.testset or "evals/cases.yaml"), Settings(), chat=chat)
            print(json.dumps(report, indent=2) if args.json else format_case_report(report))
            return 0 if report["pass_rate"] >= args.min_pass_rate else 1
        report = run_evals(Path(args.testset or "evals/testset.yaml"), Settings(), args.prompt_version)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        print(format_report(report))
        return 0 if report["pass_rate"] >= args.min_pass_rate else 1
    print(format_report(json.loads(Path(args.file).read_text())))
    return 0


if __name__ == "__main__":
    sys.exit(main())

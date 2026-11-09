"""Command line: create a tenant, run the eval harness, print a report."""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aip.config import Settings
from aip.db import install_audit_triggers, make_engine, make_session_factory
from aip.graph.actions import build_action_port
from aip.graph.authority import load_authority
from aip.graph.service import CaseService
from aip.graph.state import Ticket
from aip.models import Tenant
from aip.providers import build_chat_provider, build_embedder
from aip.tenants import ALL_TOOLS, create_tenant


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

    e = sub.add_parser("eval", help="run or report evals")
    esub = e.add_subparsers(dest="eval_cmd", required=True)
    r = esub.add_parser("run")
    r.add_argument("--suite", choices=["qa", "cases"], default="qa")
    r.add_argument("--testset", default=None)
    r.add_argument("--prompt-version", default=None)
    r.add_argument("--out", default="evals/results/latest.json")
    r.add_argument("--min-pass-rate", type=float, default=0.0)
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

    if args.eval_cmd == "run":
        if args.suite == "cases":
            from aip.evals.cases import format_case_report, run_case_suite

            report = run_case_suite(Path(args.testset or "evals/cases.yaml"), Settings())
            print(format_case_report(report))
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

"""Command line: create a tenant, run the eval harness, print a report."""

import argparse
import json
import sys
from pathlib import Path

from aip.config import Settings
from aip.db import make_engine, make_session_factory
from aip.tenants import ALL_TOOLS, create_tenant


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aip")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("create-tenant", help="create a tenant and print its API key once")
    t.add_argument("name")
    t.add_argument("--tools", default="search_knowledge_base,lookup_ticket")
    t.add_argument("--webhook-url")

    e = sub.add_parser("eval", help="run or report evals")
    esub = e.add_subparsers(dest="eval_cmd", required=True)
    r = esub.add_parser("run")
    r.add_argument("--testset", default="evals/testset.yaml")
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

    from aip.evals.runner import format_report, run_evals

    if args.eval_cmd == "run":
        report = run_evals(Path(args.testset), Settings(), args.prompt_version)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        print(format_report(report))
        return 0 if report["pass_rate"] >= args.min_pass_rate else 1
    print(format_report(json.loads(Path(args.file).read_text())))
    return 0


if __name__ == "__main__":
    sys.exit(main())

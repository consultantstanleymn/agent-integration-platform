from pathlib import Path

from sqlalchemy import create_engine, inspect

from aip.agent.loop import AgentResult, Citation
from aip.cli import main
from aip.config import Settings
from aip.evals.runner import Case, run_evals, score_case
from aip.models import Base

ROOT = Path(__file__).resolve().parents[1]


def test_eval_suite_passes_with_fake_provider():
    report = run_evals(ROOT / "evals/testset.yaml", Settings(prompts_dir=ROOT / "prompts"))  # type: ignore[arg-type]
    assert report["total"] >= 9 and report["pass_rate"] == 1.0, report


def test_eval_runs_with_prompt_v2():
    report = run_evals(ROOT / "evals/testset.yaml", Settings(prompts_dir=ROOT / "prompts"), "triage_v2")  # type: ignore[arg-type]
    assert report["prompt_version"] == "triage_v2" and report["pass_rate"] == 1.0


def test_scoring_detects_missing_fact_and_wrong_citation():
    res = AgentResult(
        status="ok",
        answer="Refunds take 90 days",
        citations=[Citation(doc="shipping.md", chunk=0, score=1)],
    )
    s = score_case(
        Case(id="x", question="q", expected_facts=["30 days"], expected_source="refund-policy.md"),
        res,
    )
    assert not s["passed"] and s["missing_facts"] == ["30 days"] and not s["citation_ok"]


def test_scoring_expects_no_citation_when_no_source():
    res = AgentResult(
        status="ok", answer="could not find", citations=[Citation(doc="a.md", chunk=0, score=1)]
    )
    assert not score_case(Case(id="x", question="q", expected_facts=["could not find"]), res)["passed"]


def test_cli_eval_run_and_report(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    out = tmp_path / "r.json"
    assert main(["eval", "run", "--out", str(out), "--min-pass-rate", "0.9"]) == 0
    assert "Pass rate: 9/9" in capsys.readouterr().out
    assert main(["eval", "report", str(out)]) == 0
    assert "Pass rate" in capsys.readouterr().out


def test_cli_eval_fails_below_threshold(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    bad = tmp_path / "ts.yaml"
    bad.write_text(
        f"kb_dir: {ROOT / 'evals/kb'}\ncases:\n  - id: w\n    question: How long is a refund window?\n"
        "    expected_facts: ['999 days']\n"
    )
    assert (
        main(
            [
                "eval",
                "run",
                "--testset",
                str(bad),
                "--out",
                str(tmp_path / "o.json"),
                "--min-pass-rate",
                "1",
            ]
        )
        == 1
    )


def test_cli_create_tenant(tmp_path, monkeypatch, capsys):
    db = tmp_path / "t.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    Base.metadata.create_all(create_engine(f"sqlite:///{db}"))
    assert main(["create-tenant", "acme", "--tools", "create_ticket"]) == 0
    assert capsys.readouterr().out.count("aip_") == 1
    assert main(["create-tenant", "x", "--tools", "bogus"]) == 2


def test_alembic_migration_matches_models(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    db = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("DATABASE_URL", db)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    tables = set(inspect(create_engine(db)).get_table_names())
    assert set(Base.metadata.tables) <= tables

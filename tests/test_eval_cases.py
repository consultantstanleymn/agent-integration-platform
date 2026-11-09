"""The case eval suite and its scoring."""

from collections import Counter
from pathlib import Path

from aip.cli import main
from aip.config import Settings
from aip.evals.cases import CaseEval, load_suite, run_case_suite, score

CASES = Path(__file__).resolve().parents[1] / "evals" / "cases.yaml"


def test_cases_yaml_has_60_or_more_and_all_categories():
    suite = load_suite(CASES)
    counts = Counter(c.category for c in suite.cases)
    assert len(suite.cases) >= 60
    assert set(counts) == {"answer", "refusal", "injection", "approval", "budget"}
    assert counts["answer"] >= 20 and counts["refusal"] >= 12 and counts["injection"] >= 12
    assert counts["approval"] >= 10 and counts["budget"] >= 6
    assert len({c.id for c in suite.cases}) == len(suite.cases)


def test_case_suite_passes_with_fake_provider():
    report = run_case_suite(CASES, Settings())
    failed = [c for c in report["cases"] if not c["passed"]]
    assert failed == []
    assert report["pass_rate"] == 1.0


def test_scoring_detects_wrong_status():
    case = CaseEval(
        id="x", category="answer", ticket={"subject": "s", "body": "b"}, expect_status="completed"
    )
    assert score(case, "completed", None, False)["passed"] is True
    result = score(case, "refused", None, False)
    assert result["passed"] is False
    assert "status refused" in result["problems"][0]


def test_cli_cases_suite_exit_code(capsys, monkeypatch):
    monkeypatch.chdir(CASES.parent.parent)
    assert main(["eval", "run", "--suite", "cases", "--min-pass-rate", "0.95"]) == 0
    assert "Pass rate: 62/62" in capsys.readouterr().out


def test_cli_eval_cases_json_output(capsys, monkeypatch):
    import json

    monkeypatch.chdir(CASES.parent.parent)
    assert main(["eval", "run", "--suite", "cases", "--replay", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"] == 62 and report["pass_rate"] == 1.0
    assert report["cases"][0]["id"]

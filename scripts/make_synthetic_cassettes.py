"""Write synthetic cassettes: run the case suite with the fake provider in record mode.

The files say ``"synthetic": true``. No hosted model response is recorded.
"""

from pathlib import Path

from aip.config import Settings
from aip.evals.cases import run_case_suite
from aip.providers.fake import FakeProvider
from aip.providers.replay import RecordReplayProvider

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    out = ROOT / "evals" / "cassettes"
    for old in out.glob("*.json"):
        old.unlink()
    provider = RecordReplayProvider(FakeProvider(), out, "record", synthetic=True)
    report = run_case_suite(ROOT / "evals" / "cases.yaml", Settings(), chat=provider)
    print(f"wrote {len(list(out.glob('*.json')))} cassettes, pass rate {report['pass_rate']}")


if __name__ == "__main__":
    main()

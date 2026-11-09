"""Release gate. Exit 1 unless the manifest is complete and the eval evidence matches it.

Checks: every manifest field is present and not empty, the image field holds @sha256:, the case
eval JSON has a pass rate of at least 0.95, and its eval set hash equals the manifest hash.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = (
    "git_sha",
    "image",
    "alembic_head",
    "config_hash",
    "prompt_version",
    "prompt_sha256",
    "eval_set_sha256",
    "cassettes_sha256",
    "created_at",
)
MIN_PASS_RATE = 0.95


def check(manifest_path: Path, eval_path: Path) -> list[str]:
    problems: list[str] = []
    manifest = json.loads(manifest_path.read_text())
    for field in FIELDS:
        if not manifest.get(field):
            problems.append(f"manifest field missing or empty: {field}")
    if "@sha256:" not in str(manifest.get("image", "")):
        problems.append("image must be referenced by digest (name@sha256:...)")
    report = json.loads(eval_path.read_text())
    if report.get("pass_rate", 0) < MIN_PASS_RATE:
        problems.append(f"eval pass rate {report.get('pass_rate')} is below {MIN_PASS_RATE}")
    expected = (manifest.get("eval_set_sha256") or {}).get("cases")
    if not expected or report.get("eval_set_sha256") != expected:
        problems.append("eval set hash in the eval JSON does not match the manifest")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(ROOT / "release" / "manifest.json"))
    parser.add_argument(
        "--eval", default=str(ROOT / "docs" / "engagement" / "evidence" / "eval-cases-replay.json")
    )
    args = parser.parse_args(argv)
    problems = check(Path(args.manifest), Path(args.eval))
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if not problems:
        print("ok: acceptance gate passed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

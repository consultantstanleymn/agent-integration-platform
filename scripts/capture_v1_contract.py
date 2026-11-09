"""Capture the /v1/search responses of the current code into tests/contract/v1_search_responses.json.

Refuses to overwrite existing fixtures unless --force is given.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tests.contract import RESPONSES, run_v1_search_cases  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if RESPONSES.exists() and not args.force:
        print(f"refusing to overwrite {RESPONSES}; pass --force to replace it", file=sys.stderr)
        return 1
    bodies = run_v1_search_cases()
    RESPONSES.write_text(json.dumps(bodies, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(bodies)} responses to {RESPONSES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

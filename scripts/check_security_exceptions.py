"""Check security/exceptions.json. Standard library only.

Exit 1 when an item lacks a field, an item has an expired date, or the date is
more than 90 days ahead. Usage: python3 scripts/check_security_exceptions.py security/exceptions.json
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

REQUIRED = ("id", "tool", "reason", "owner", "expires", "review_note")
MAX_DAYS = 90


def check(path: Path, today: date | None = None) -> list[str]:
    today = today or date.today()
    problems: list[str] = []
    items = json.loads(path.read_text())
    if not isinstance(items, list):
        return ["file must hold a JSON list"]
    for index, item in enumerate(items):
        label = item.get("id", f"item {index}") if isinstance(item, dict) else f"item {index}"
        if not isinstance(item, dict):
            problems.append(f"{label}: not an object")
            continue
        for name in REQUIRED:
            if not item.get(name):
                problems.append(f"{label}: missing field {name}")
        raw = item.get("expires")
        if raw:
            try:
                expires = date.fromisoformat(raw)
            except ValueError:
                problems.append(f"{label}: expires is not an ISO date")
                continue
            if expires < today:
                problems.append(f"{label}: exception expired on {raw}")
            elif expires > today + timedelta(days=MAX_DAYS):
                problems.append(f"{label}: expires more than {MAX_DAYS} days ahead")
    return problems


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "security/exceptions.json")
    problems = check(path)
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if not problems:
        print(f"ok: {path}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

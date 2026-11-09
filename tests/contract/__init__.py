"""Frozen API contracts. The fixtures in this folder come from the code before the retrieval work."""

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
CASES = HERE / "v1_search_cases.json"
RESPONSES = HERE / "v1_search_responses.json"


def run_v1_search_cases() -> list[dict[str, Any]]:
    """Run every stored request against the current code, over the conftest KB. Returns the bodies."""
    from tests.conftest import Env

    cases = json.loads(CASES.read_text())
    env = Env()
    try:
        tenant = env.tenant()
        env.add_kb(tenant)
        bodies = []
        for case in cases:
            response = env.client.post("/v1/search", json=case, headers=tenant["headers"])
            assert response.status_code == 200, response.text
            bodies.append(response.json())
        return bodies
    finally:
        env.client.close()
        env.engine.dispose()


def canonical(body: Any) -> str:
    return json.dumps(body, sort_keys=True)

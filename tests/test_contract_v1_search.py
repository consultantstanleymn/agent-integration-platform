"""The /v1/search response stays byte compatible with the stored fixtures."""

import json

from tests.contract import RESPONSES, canonical, run_v1_search_cases


def test_v1_search_byte_compatible():
    stored = json.loads(RESPONSES.read_text())
    current = run_v1_search_cases()
    assert len(stored) == len(current) == 5
    for want, got in zip(stored, current, strict=True):
        assert canonical(got).encode() == canonical(want).encode()
        assert set(got) == {"hits"}

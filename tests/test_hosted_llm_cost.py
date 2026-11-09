"""Hosted adapters parse token usage from mocked HTTP, and costs follow configured prices."""

import json

import httpx

from aip.config import Settings
from aip.graph.service import CaseService
from aip.providers.anthropic import AnthropicProvider
from aip.providers.base import Message, Usage
from aip.providers.fake import FakeProvider
from aip.providers.openai_compat import OpenAICompatProvider
from aip.providers.pricing import estimate_cost_usd, load_prices

MSG = [Message(role="user", content="hi")]


def _client(payload: dict) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))


def test_anthropic_usage_parsed_from_mocked_http():
    body = {"content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 120, "output_tokens": 30}}
    reply = AnthropicProvider("k", "model-a", client=_client(body)).chat(MSG, [])
    assert reply.usage == Usage(input_tokens=120, output_tokens=30)


def test_openai_usage_parsed_from_mocked_http():
    body = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 80, "completion_tokens": 20},
    }
    provider = OpenAICompatProvider("http://x.test/v1", "k", "model-b", "e", client=_client(body))
    assert provider.chat(MSG, []).usage == Usage(input_tokens=80, output_tokens=20)


def test_cost_estimate_uses_configured_price():
    table = load_prices(json.dumps({"model-a": {"input": 2.0, "output": 10.0}}))
    cost = estimate_cost_usd("model-a", Usage(input_tokens=1_000_000, output_tokens=500_000), table)
    assert cost == 7.0


def test_cost_none_without_price():
    assert estimate_cost_usd("model-a", Usage(input_tokens=10, output_tokens=10), {}) is None
    assert (
        estimate_cost_usd(
            "other", Usage(input_tokens=10), load_prices('{"model-a": {"input": 1, "output": 1}}')
        )
        is None
    )
    assert Settings().llm_price_per_mtok_json == ""


class _PricedFake(FakeProvider):
    model = "priced-model"


def test_case_records_usage_and_cost(env, tmp_path):
    svc = CaseService(
        checkpoint_url=f"sqlite:///{tmp_path / 'cp.sqlite'}",
        provider=_PricedFake(),
        embedder=env.app.state.embedder,
        approval_limit_minor=10000,
        prices=load_prices('{"priced-model": {"input": 1.0, "output": 2.0}}'),
    )
    env.app.state.case_service = svc
    t = env.tenant()
    env.add_kb(t)
    body = {"subject": "Return", "body": "Please refund $20.00 for order ORD-1."}
    thread = env.client.post("/v1/cases", json=body, headers=t["headers"]).json()["thread_id"]
    row = env.client.get(f"/v1/cases/{thread}", headers=t["headers"]).json()
    usage = row["proposal"]["usage"]
    assert usage["input_tokens"] > 0 and usage["output_tokens"] > 0
    assert usage["cost_usd"] > 0
    assert row["tokens_used"] == usage["input_tokens"] + usage["output_tokens"]
    svc.close()

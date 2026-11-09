"""Fake provider support for the classify and propose prompts."""

import json
import math

import pytest

from aip.graph.nodes import CLASSIFY_PROMPT, PROPOSE_PROMPT
from aip.providers.base import Message
from aip.providers.fake import FakeProvider


def _classify(text: str) -> str:
    messages = [
        Message(role="system", content=CLASSIFY_PROMPT),
        Message(role="user", content=text),
    ]
    response = FakeProvider().chat(messages, [])
    return json.loads(response.content)["category"]


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("I would like a refund for my boots.", "refund"),
        ("The jacket is broken and still under warranty.", "warranty"),
        ("The box arrived damaged during shipping.", "damage"),
        ("Please delete my account and password.", "account"),
        ("What are your store opening hours?", "other"),
    ],
)
def test_fake_classify_categories(text: str, category: str) -> None:
    assert _classify(text) == category


def test_fake_propose_parses_amount_and_order() -> None:
    text = (
        "Ticket: Return request\n"
        "The boots did not fit. Please refund $45.50 for order ORD-1234.\n\n"
        "Policy:\n"
        "[doc:returns.md] Refunds are allowed within 30 days.\n"
        "[doc:shipping.md] Shipping damage is handled separately."
    )
    messages = [
        Message(role="system", content=PROPOSE_PROMPT),
        Message(role="user", content=text),
    ]

    response = FakeProvider().chat(messages, [])
    data = json.loads(response.content)

    assert data["action"] == "refund"
    assert data["amount_minor"] == 4550
    assert data["order_id"] == "ORD-1234"
    assert data["citations"] == ["returns.md", "shipping.md"]


def test_fake_usage_counts_are_deterministic() -> None:
    messages = [
        Message(role="system", content=CLASSIFY_PROMPT),
        Message(role="user", content="The item is broken."),
    ]
    provider = FakeProvider()

    first = provider.chat(messages, [])
    second = provider.chat(messages, [])

    assert first.usage == second.usage
    assert first.usage.input_tokens == math.ceil(len(CLASSIFY_PROMPT + messages[1].content) / 4)
    assert first.usage.output_tokens == math.ceil(len(first.content) / 4)

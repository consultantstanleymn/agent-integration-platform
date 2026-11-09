"""Retry budget, circuit breaker and provider timeouts."""

import httpx
import pytest

from aip.providers.anthropic import AnthropicProvider
from aip.providers.base import Message, ProviderError
from aip.providers.openai_compat import OpenAICompatProvider
from aip.providers.resilience import CircuitBreaker, RetryBudget


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_retry_budget_caps_retries_to_ratio():
    clock = Clock()
    budget = RetryBudget(ratio=0.2, min_per_window=1, window_s=60, clock=clock)
    for _ in range(50):
        budget.record_attempt()
    used = 0
    while budget.can_retry():
        budget.record_retry()
        used += 1
    assert used == 10  # 20 percent of 50 attempts


def test_retry_budget_allows_minimum():
    budget = RetryBudget(ratio=0.2, min_per_window=3, clock=Clock())
    budget.record_attempt()
    allowed = 0
    while budget.can_retry():
        budget.record_retry()
        allowed += 1
    assert allowed == 3


def test_breaker_opens_after_threshold():
    breaker = CircuitBreaker(failure_threshold=3, reset_after_s=30, clock=Clock())
    for _ in range(2):
        breaker.on_failure()
    assert breaker.state == "closed" and breaker.allow()
    breaker.on_failure()
    assert breaker.state == "open" and not breaker.allow()


def test_breaker_half_open_after_reset():
    clock = Clock()
    breaker = CircuitBreaker(failure_threshold=1, reset_after_s=30, clock=clock)
    breaker.on_failure()
    clock.now = 29
    assert not breaker.allow()
    clock.now = 31
    assert breaker.state == "half_open"
    assert breaker.allow()  # one probe
    assert not breaker.allow()  # a second call waits for the probe
    breaker.on_failure()
    assert breaker.state == "open"


def test_breaker_closes_on_success():
    clock = Clock()
    breaker = CircuitBreaker(failure_threshold=1, reset_after_s=10, clock=clock)
    breaker.on_failure()
    clock.now = 11
    assert breaker.allow()
    breaker.on_success()
    assert breaker.state == "closed" and breaker.allow()


def _timeout_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("kind", ["anthropic", "openai"])
def test_provider_timeout_maps_to_provider_error(kind):
    client = _timeout_client()
    provider = (
        AnthropicProvider("k", "m", client=client)
        if kind == "anthropic"
        else OpenAICompatProvider("http://x.test/v1", "k", "m", "e", client=client)
    )
    with pytest.raises(ProviderError, match="^timeout$"):
        provider.chat([Message(role="user", content="hi")], [])

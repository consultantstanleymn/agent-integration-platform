"""Failover between two providers, and the switch back."""

from prometheus_client import REGISTRY
from tests.fakes_slow import ManualClock, SlowFakeProvider

from aip.providers.base import Message
from aip.providers.resilience import CircuitBreaker, RetryBudget
from aip.providers.resilient import ResilientProvider

MSG = [Message(role="user", content="hi")]


def _setup(**budget):
    clock = ManualClock()
    primary = SlowFakeProvider("primary", clock)
    secondary = SlowFakeProvider("secondary", clock)
    provider = ResilientProvider(
        primary,
        secondary,
        RetryBudget(clock=clock, **budget),
        CircuitBreaker(failure_threshold=2, reset_after_s=30, clock=clock),
    )
    return clock, primary, secondary, provider


def _counter(from_name, to_name):
    value = REGISTRY.get_sample_value("aip_llm_failover_total", {"from": from_name, "to": to_name})
    return value or 0.0


def test_uses_primary_when_healthy():
    _, primary, secondary, provider = _setup()
    assert provider.chat(MSG, []).content == "answer from primary"
    assert secondary.calls == 0 and primary.calls == 1
    assert provider.name == "resilient(primary|secondary)"


def test_switches_to_secondary_after_timeouts():
    _, primary, secondary, provider = _setup(min_per_window=3)
    primary.delay_s = 5.0  # slower than its timeout
    assert provider.chat(MSG, []).content == "answer from secondary"
    assert primary.calls == 2  # first try plus one retry inside the budget
    assert provider.breaker.state == "open"
    before = primary.calls
    assert provider.chat(MSG, []).content == "answer from secondary"
    assert primary.calls == before  # open breaker skips the primary


def test_switches_back_after_reset():
    clock, primary, secondary, provider = _setup(min_per_window=3)
    primary.delay_s = 5.0
    provider.chat(MSG, [])
    assert provider.breaker.state == "open"
    primary.delay_s = 0.1  # the primary recovers
    clock.advance(31)
    assert provider.breaker.state == "half_open"
    assert provider.chat(MSG, []).content == "answer from primary"  # the probe succeeds
    assert provider.breaker.state == "closed"
    assert provider.chat(MSG, []).content == "answer from primary"


def test_failover_counter_increments():
    _, primary, _, provider = _setup()
    primary.fail = True
    start = _counter("primary", "secondary")
    provider.chat(MSG, [])
    assert _counter("primary", "secondary") == start + 1


def test_budget_exhausted_goes_straight_to_secondary():
    _, primary, secondary, provider = _setup(ratio=0.0, min_per_window=0)
    primary.fail = True
    assert provider.chat(MSG, []).content == "answer from secondary"
    assert primary.calls == 1  # no retry: the budget has none left

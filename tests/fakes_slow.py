"""A fake provider that is slow on demand. It uses a manual clock, so no test sleeps."""

from aip.providers.base import LLMResponse, ProviderError


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SlowFakeProvider:
    """``delay_s`` is the simulated answer time. A delay above ``timeout_s`` raises a timeout."""

    def __init__(
        self, name: str, clock: ManualClock, delay_s: float = 0.1, timeout_s: float = 1.0, fail: bool = False
    ) -> None:
        self.name = name
        self.clock = clock
        self.delay_s = delay_s
        self.timeout_s = timeout_s
        self.fail = fail
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        self.clock.advance(min(self.delay_s, self.timeout_s))
        if self.fail or self.delay_s > self.timeout_s:
            raise ProviderError("timeout")
        return LLMResponse(content=f"answer from {self.name}")

"""Fail over from a primary chat provider to a secondary one, and switch back after recovery.

Rule: a failed primary call is retried once when the retry budget allows it. If the call still
fails, the secondary answers. After enough failures the breaker opens and the primary is skipped.
When the breaker turns half open, one probe call goes to the primary. A good probe closes the
breaker, and the primary serves again.
"""

from prometheus_client import Counter

from aip.providers.base import ChatProvider, LLMResponse, Message, ProviderError, ToolSpec
from aip.providers.resilience import CircuitBreaker, RetryBudget

LLM_FAILOVER = Counter(
    "aip_llm_failover_total",
    "Chat calls answered by the secondary provider",
    ["from_provider", "to_provider"],
)


class ResilientProvider:
    def __init__(
        self,
        primary: ChatProvider,
        secondary: ChatProvider,
        budget: RetryBudget,
        breaker: CircuitBreaker,
        max_attempts: int = 2,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.budget = budget
        self.breaker = breaker
        self.max_attempts = max_attempts
        self.name = f"resilient({primary.name}|{secondary.name})"

    def _try_primary(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse | None:
        attempts = 0
        while attempts < self.max_attempts:
            if attempts > 0:
                if not self.budget.can_retry():
                    return None
                self.budget.record_retry()
            attempts += 1
            self.budget.record_attempt()
            try:
                response = self.primary.chat(messages, tools)
            except ProviderError:
                self.breaker.on_failure()
                if not self.breaker.allow():
                    return None
                continue
            self.breaker.on_success()
            return response
        return None

    def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        if self.breaker.allow():
            response = self._try_primary(messages, tools)
            if response is not None:
                return response
        LLM_FAILOVER.labels(**{"from": self.primary.name, "to": self.secondary.name}).inc()
        return self.secondary.chat(messages, tools)

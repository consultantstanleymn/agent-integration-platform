"""Retry budget and circuit breaker for provider calls. Both take an injected clock."""

import time
from collections import deque
from collections.abc import Callable
from typing import Literal

Clock = Callable[[], float]


class RetryBudget:
    """Allow retries up to ``ratio`` of the attempts in a window, with a small fixed minimum."""

    def __init__(
        self,
        ratio: float = 0.2,
        min_per_window: int = 3,
        window_s: float = 60,
        clock: Clock = time.monotonic,
    ) -> None:
        self.ratio = ratio
        self.min_per_window = min_per_window
        self.window_s = window_s
        self.clock = clock
        self._attempts: deque[float] = deque()
        self._retries: deque[float] = deque()

    def _trim(self) -> None:
        cutoff = self.clock() - self.window_s
        for q in (self._attempts, self._retries):
            while q and q[0] <= cutoff:
                q.popleft()

    def record_attempt(self) -> None:
        self._attempts.append(self.clock())

    def record_retry(self) -> None:
        self._retries.append(self.clock())

    def can_retry(self) -> bool:
        self._trim()
        allowed = max(self.min_per_window, int(len(self._attempts) * self.ratio))
        return len(self._retries) < allowed


class CircuitBreaker:
    """Closed, open or half open. Opens after ``failure_threshold`` failures in a row."""

    def __init__(
        self, failure_threshold: int = 5, reset_after_s: float = 30, clock: Clock = time.monotonic
    ) -> None:
        self.failure_threshold = failure_threshold
        self.reset_after_s = reset_after_s
        self.clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_out = False

    @property
    def state(self) -> Literal["closed", "open", "half_open"]:
        if self._opened_at is None:
            return "closed"
        if self.clock() - self._opened_at >= self.reset_after_s:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        state = self.state
        if state == "closed":
            return True
        if state == "half_open" and not self._probe_out:
            self._probe_out = True  # one probe call at a time
            return True
        return False

    def on_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._probe_out = False

    def on_failure(self) -> None:
        self._failures += 1
        self._probe_out = False
        if self._opened_at is not None or self._failures >= self.failure_threshold:
            self._opened_at = self.clock()  # a failed probe re-opens the breaker

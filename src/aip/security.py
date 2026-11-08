"""API keys, rate limiting. Keys are random, shown once, and only their SHA-256 hash is stored."""

import hashlib
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Callable


def new_api_key() -> str:
    return "aip_" + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    # Fast hash is fine: keys are 256-bit random, not human passwords.
    return hashlib.sha256(key.encode()).hexdigest()


class RateLimiter:
    """Sliding window, per key, in memory. One process only (see SECURITY.md for the limit)."""

    def __init__(self, limit: int, window: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> float:
        """Return 0 if allowed, else the seconds to wait."""
        now = self._clock()
        q = self._hits[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return max(q[0] + self.window - now, 0.001)
        q.append(now)
        return 0.0

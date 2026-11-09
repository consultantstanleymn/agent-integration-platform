"""The fake_slow provider is the fake provider plus a fixed delay. For load tests only."""

import time

from aip.config import Settings
from aip.providers import build_chat_provider
from aip.providers.base import Message
from aip.providers.fake import FakeProvider


def test_fake_slow_waits_and_matches_fake():
    provider = build_chat_provider(Settings(llm_provider="fake_slow", fake_llm_delay_ms=50))
    messages = [Message(role="system", content="CLASSIFY"), Message(role="user", content="please refund me")]
    start = time.perf_counter()
    slow = provider.chat(messages, [])
    assert time.perf_counter() - start >= 0.05
    assert slow.content == FakeProvider().chat(messages, []).content
    assert provider.name == "fake_slow"

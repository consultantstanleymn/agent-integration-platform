from aip.config import Settings
from aip.providers.anthropic import AnthropicProvider
from aip.providers.base import ChatProvider, Embedder, ProviderError
from aip.providers.fake import FakeProvider, SlowFakeProvider
from aip.providers.openai_compat import OpenAICompatProvider
from aip.providers.resilience import CircuitBreaker, RetryBudget
from aip.providers.resilient import ResilientProvider


def build_chat_provider(s: Settings) -> ChatProvider:
    primary = _build_chat(s, s.llm_provider)
    if not s.llm_fallback_provider:
        return primary
    secondary = _build_chat(s, s.llm_fallback_provider)
    return ResilientProvider(primary, secondary, RetryBudget(), CircuitBreaker())


def _build_chat(s: Settings, provider: str) -> ChatProvider:
    if provider == "fake":
        return FakeProvider()
    if provider == "fake_slow":
        return SlowFakeProvider(s.fake_llm_delay_ms)
    if provider == "openai_compat":
        return OpenAICompatProvider(
            s.openai_base_url,
            s.openai_api_key.get_secret_value(),
            s.openai_model,
            s.openai_embed_model,
            timeout_s=s.llm_timeout_seconds,
        )
    if provider == "anthropic":
        key = s.anthropic_api_key.get_secret_value()
        if not key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        return AnthropicProvider(key, s.anthropic_model, timeout_s=s.llm_timeout_seconds)
    raise ProviderError(f"unknown LLM_PROVIDER: {provider}")


def build_embedder(s: Settings) -> Embedder:
    embedder = _build_embedder(s)
    if s.redis_url and s.embed_cache:
        import redis

        from aip.providers.cache import CachedEmbedder

        return CachedEmbedder(embedder, redis.Redis.from_url(s.redis_url), model=s.embedding_provider)
    return embedder


def _build_embedder(s: Settings) -> Embedder:
    if s.embedding_provider == "fake":
        return FakeProvider()
    if s.embedding_provider == "local_onnx":
        from aip.providers.local_onnx import LocalOnnxEmbedder

        return LocalOnnxEmbedder(s.local_embed_model)
    if s.embedding_provider == "openai_compat":
        return OpenAICompatProvider(
            s.openai_base_url,
            s.openai_api_key.get_secret_value(),
            s.openai_model,
            s.openai_embed_model,
        )
    raise ProviderError(f"unknown EMBEDDING_PROVIDER: {s.embedding_provider}")


__all__ = ["build_chat_provider", "build_embedder"]

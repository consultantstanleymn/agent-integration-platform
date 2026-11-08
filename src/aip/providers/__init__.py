from aip.config import Settings
from aip.providers.anthropic import AnthropicProvider
from aip.providers.base import ChatProvider, Embedder, ProviderError
from aip.providers.fake import FakeProvider
from aip.providers.openai_compat import OpenAICompatProvider


def build_chat_provider(s: Settings) -> ChatProvider:
    if s.llm_provider == "fake":
        return FakeProvider()
    if s.llm_provider == "openai_compat":
        return OpenAICompatProvider(
            s.openai_base_url,
            s.openai_api_key.get_secret_value(),
            s.openai_model,
            s.openai_embed_model,
        )
    if s.llm_provider == "anthropic":
        key = s.anthropic_api_key.get_secret_value()
        if not key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        return AnthropicProvider(key, s.anthropic_model)
    raise ProviderError(f"unknown LLM_PROVIDER: {s.llm_provider}")


def build_embedder(s: Settings) -> Embedder:
    if s.embedding_provider == "fake":
        return FakeProvider()
    if s.embedding_provider == "openai_compat":
        return OpenAICompatProvider(
            s.openai_base_url,
            s.openai_api_key.get_secret_value(),
            s.openai_model,
            s.openai_embed_model,
        )
    raise ProviderError(f"unknown EMBEDDING_PROVIDER: {s.embedding_provider}")


__all__ = ["build_chat_provider", "build_embedder"]

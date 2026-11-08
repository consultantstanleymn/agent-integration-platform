"""Provider-neutral message types and the two interfaces the rest of the code uses."""

from typing import Any, Protocol

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: str  # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)  # on assistant messages
    tool_call_id: str | None = None  # on tool messages
    name: str | None = None  # tool name, on tool messages


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


class LLMResponse(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ChatProvider(Protocol):
    name: str

    def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse: ...


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class ProviderError(RuntimeError):
    pass

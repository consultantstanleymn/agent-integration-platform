"""Anthropic Messages API provider (plain HTTP, no SDK). Chat only: no embeddings API exists."""

from typing import Any

import httpx

from aip.providers.base import LLMResponse, Message, ProviderError, ToolCall, ToolSpec, Usage

API_URL = "https://api.anthropic.com/v1/messages"


def to_wire(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Split out the system prompt and map our messages to Anthropic content blocks."""
    system = "\n".join(m.content for m in messages if m.role == "system")
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            continue
        if m.role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)  # parallel results go in one user turn
            else:
                out.append({"role": "user", "content": [block]})
        elif m.role == "assistant" and m.tool_calls:
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            blocks += [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in m.tool_calls
            ]
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": m.role, "content": m.content})
    return system, out


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        client: httpx.Client | None = None,
        url: str = API_URL,
        timeout_s: float = 20,
    ) -> None:
        self.model = model
        self.url = url
        self._key = api_key
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout_s, connect=5))

    def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        system, wire = to_wire(messages)
        body: dict[str, Any] = {"model": self.model, "max_tokens": 1024, "messages": wire}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
            ]
        headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01"}
        try:
            r = self._client.post(self.url, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
            text = "".join(b["text"] for b in data["content"] if b["type"] == "text")
            calls = [
                ToolCall(id=b["id"], name=b["name"], arguments=b.get("input") or {})
                for b in data["content"]
                if b["type"] == "tool_use"
            ]
            raw = data.get("usage") or {}
            usage = Usage(
                input_tokens=int(raw.get("input_tokens", 0)), output_tokens=int(raw.get("output_tokens", 0))
            )
            return LLMResponse(content=text, tool_calls=calls, usage=usage)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout") from exc
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"anthropic request failed: {type(exc).__name__}") from exc

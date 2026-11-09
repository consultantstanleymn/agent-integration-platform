"""OpenAI-compatible HTTP provider. Works with Ollama (/v1), vLLM, OpenAI, and similar."""

import json
from typing import Any

import httpx

from aip.providers.base import LLMResponse, Message, ProviderError, ToolCall, ToolSpec, Usage


def to_wire_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        elif m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in m.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": m.role, "content": m.content})
    return out


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        embed_model: str,
        client: httpx.Client | None = None,
        timeout_s: float = 20,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.embed_model = embed_model
        self._key = api_key  # held in memory only, never logged or stored
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout_s, connect=5))

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        try:
            r = self._client.post(f"{self.base_url}{path}", json=body, headers=headers)
            r.raise_for_status()
            data: dict[str, Any] = r.json()
            return data
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"openai_compat request failed: {type(exc).__name__}") from exc

    def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        body: dict[str, Any] = {"model": self.model, "messages": to_wire_messages(messages)}
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        data = self._post("/chat/completions", body)
        try:
            msg = data["choices"][0]["message"]
            calls = [
                ToolCall(
                    id=c["id"],
                    name=c["function"]["name"],
                    arguments=json.loads(c["function"].get("arguments") or "{}"),
                )
                for c in (msg.get("tool_calls") or [])
            ]
            raw = data.get("usage") or {}
            usage = Usage(
                input_tokens=int(raw.get("prompt_tokens", 0)),
                output_tokens=int(raw.get("completion_tokens", 0)),
            )
            return LLMResponse(content=msg.get("content") or "", tool_calls=calls, usage=usage)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("openai_compat: unexpected response shape") from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        data = self._post("/embeddings", {"model": self.embed_model, "input": texts})
        try:
            return [list(map(float, d["embedding"])) for d in data["data"]]
        except (KeyError, TypeError) as exc:
            raise ProviderError("openai_compat: unexpected embeddings shape") from exc

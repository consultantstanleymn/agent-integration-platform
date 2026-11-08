import json

import httpx
import pytest

from aip.config import Settings
from aip.providers import build_chat_provider, build_embedder
from aip.providers.anthropic import AnthropicProvider, to_wire
from aip.providers.base import Message, ProviderError, ToolCall, ToolSpec
from aip.providers.fake import FakeProvider, hash_embed
from aip.providers.openai_compat import OpenAICompatProvider

SPEC = ToolSpec(name="t", description="d", parameters={"type": "object", "properties": {}})
HISTORY = [
    Message(role="system", content="sys"),
    Message(role="user", content="hi"),
    Message(role="assistant", tool_calls=[ToolCall(id="c1", name="t", arguments={"a": 1})]),
    Message(role="tool", name="t", tool_call_id="c1", content='{"ok":1}'),
]


def mock(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fake_is_deterministic():
    assert hash_embed("refund within 30 days") == hash_embed("refund within 30 days")
    a = FakeProvider().chat([Message(role="user", content="what is T-5?")], [])
    assert a.content  # no tools offered -> refusal text


def test_openai_compat_request_and_response_mapping():
    seen = {}

    def handler(req):
        seen["url"], seen["auth"], seen["body"] = (
            str(req.url),
            req.headers.get("authorization"),
            json.loads(req.content),
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c9",
                                    "type": "function",
                                    "function": {"name": "t", "arguments": '{"x": 2}'},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    p = OpenAICompatProvider("http://ollama.test/v1/", "k", "m", "e", client=mock(handler))
    r = p.chat(HISTORY, [SPEC])
    assert seen["url"] == "http://ollama.test/v1/chat/completions" and seen["auth"] == "Bearer k"
    assert seen["body"]["tools"][0]["function"]["name"] == "t"
    assert seen["body"]["messages"][3] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": '{"ok":1}',
    }
    assert r.tool_calls[0].arguments == {"x": 2}


def test_openai_compat_no_auth_header_without_key_and_embeddings():
    def handler(req):
        assert "authorization" not in req.headers
        return httpx.Response(200, json={"data": [{"embedding": [1, 2]}, {"embedding": [3, 4]}]})

    p = OpenAICompatProvider("http://x.test/v1", "", "m", "e", client=mock(handler))
    assert p.embed(["a", "b"]) == [[1.0, 2.0], [3.0, 4.0]]


def test_openai_compat_errors_become_provider_error_without_leaking_key():
    p = OpenAICompatProvider(
        "http://x.test", "topsecret", "m", "e", client=mock(lambda r: httpx.Response(500))
    )
    with pytest.raises(ProviderError) as e:
        p.chat(HISTORY, [])
    assert "topsecret" not in str(e.value)
    bad = OpenAICompatProvider(
        "http://x.test", "", "m", "e", client=mock(lambda r: httpx.Response(200, json={}))
    )
    with pytest.raises(ProviderError):
        bad.chat(HISTORY, [])


def test_anthropic_wire_mapping():
    system, wire = to_wire(HISTORY)
    assert system == "sys"
    assert wire[1]["content"][0] == {"type": "tool_use", "id": "c1", "name": "t", "input": {"a": 1}}
    assert wire[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "c1", "content": '{"ok":1}'}],
    }


def test_anthropic_request_and_response():
    seen = {}

    def handler(req):
        seen["h"], seen["body"] = req.headers, json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "tool_use", "id": "u1", "name": "t", "input": {"q": 1}},
                ]
            },
        )

    r = AnthropicProvider("key1", "model-x", client=mock(handler)).chat(HISTORY, [SPEC])
    assert seen["h"]["x-api-key"] == "key1" and seen["h"]["anthropic-version"]
    assert seen["body"]["tools"][0]["input_schema"] == SPEC.parameters and seen["body"]["system"] == "sys"
    assert r.content == "hello" and r.tool_calls[0].arguments == {"q": 1}


def test_anthropic_http_error():
    p = AnthropicProvider("k", "m", client=mock(lambda r: httpx.Response(529)))
    with pytest.raises(ProviderError):
        p.chat(HISTORY, [])


def test_factory_reads_keys_from_settings_only():
    assert build_chat_provider(Settings(llm_provider="fake")).name == "fake"  # type: ignore[arg-type]
    with pytest.raises(ProviderError):
        build_chat_provider(Settings(llm_provider="anthropic"))  # type: ignore[arg-type]
    assert build_chat_provider(Settings(llm_provider="anthropic", anthropic_api_key="k")).name == "anthropic"  # type: ignore[arg-type]
    assert build_chat_provider(Settings(llm_provider="openai_compat")).name == "openai_compat"  # type: ignore[arg-type]
    with pytest.raises(ProviderError):
        build_embedder(Settings(embedding_provider="anthropic"))  # type: ignore[arg-type]


def test_secret_not_in_settings_repr():
    assert "topsecret" not in repr(Settings(openai_api_key="topsecret"))  # type: ignore[arg-type]

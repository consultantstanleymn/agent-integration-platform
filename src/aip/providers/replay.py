"""Record and replay chat responses so evals run with no network.

A cassette is one JSON file named by the SHA-256 of the request (messages plus
tool specs). Replay reads it. Record mode calls an inner provider and writes it,
but only when ``AIP_ALLOW_RECORD=1`` is set and the inner provider is not the fake.
The synthetic maker script uses ``synthetic=True`` to record the fake provider.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from aip.providers.base import ChatProvider, LLMResponse, Message, ProviderError, ToolSpec


def request_key(messages: list[Message], tools: list[ToolSpec]) -> str:
    payload = {
        "messages": [m.model_dump(mode="json") for m in messages],
        "tools": [t.model_dump(mode="json") for t in tools],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_cassettes(cassette_dir: Path) -> int:
    """Check every cassette parses and matches its file name. Returns the count."""
    files = sorted(cassette_dir.glob("*.json"))
    if not files:
        raise ProviderError(f"no cassettes in {cassette_dir}")
    for f in files:
        try:
            data = json.loads(f.read_text())
            LLMResponse.model_validate(data["response"])
            digest = data["request_digest"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError(f"corrupted cassette: {f.name}") from exc
        if digest != f.stem:
            raise ProviderError(f"cassette digest does not match its name: {f.name}")
    return len(files)


class RecordReplayProvider:
    def __init__(
        self,
        inner: ChatProvider | None,
        cassette_dir: Path,
        mode: Literal["replay", "record"] = "replay",
        *,
        synthetic: bool = False,
    ) -> None:
        if mode == "record":
            if inner is None:
                raise ProviderError("record mode needs an inner provider")
            if not synthetic:
                if os.environ.get("AIP_ALLOW_RECORD") != "1":
                    raise ProviderError("record mode is blocked: set AIP_ALLOW_RECORD=1")
                if inner.name == "fake":
                    raise ProviderError("record mode needs a real inner provider, not the fake")
        self.inner = inner
        self.cassette_dir = cassette_dir
        self.mode = mode
        self.synthetic = synthetic
        self.name = "replay" if mode == "replay" else f"record:{inner.name if inner else 'none'}"

    def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        key = request_key(messages, tools)
        path = self.cassette_dir / f"{key}.json"
        if self.mode == "replay":
            if not path.exists():
                raise ProviderError(f"cassette missing: {key}")
            return LLMResponse.model_validate(json.loads(path.read_text())["response"])
        assert self.inner is not None
        response = self.inner.chat(messages, tools)
        self.cassette_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "provider": self.inner.name,
            "model": getattr(self.inner, "model", "fake" if self.synthetic else "unknown"),
            "request_digest": key,
            "response": response.model_dump(mode="json"),
            "synthetic": self.synthetic,
        }
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        return response

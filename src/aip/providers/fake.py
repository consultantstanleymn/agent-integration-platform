"""Deterministic provider for tests, evals and the demo. No network, no randomness.

Chat policy (a tiny rule-based "model"):
  1. If the last message is a tool result, write the final answer from it.
  2. Otherwise read the user text and pick one tool by keyword.
Embeddings: hashed bag of words, L2 normalised. Similar words give a high cosine score.
"""

import hashlib
import json
import math
import re
from decimal import Decimal

from aip.providers.base import LLMResponse, Message, ToolCall, ToolSpec, Usage

DIM = 256
MIN_SCORE = 0.15  # below this the fake says it found nothing
_STOP = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "of",
    "to",
    "and",
    "in",
    "on",
    "for",
    "i",
    "my",
    "do",
    "how",
    "what",
    "can",
    "it",
    "with",
    "you",
    "we",
    "our",
    "be",
    "this",
    "that",
    "me",
}


def hash_embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        if tok in _STOP:
            continue
        vec[int.from_bytes(hashlib.sha256(tok.encode()).digest()[:4], "big") % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FakeProvider:
    name = "fake"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [hash_embed(t) for t in texts]

    def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        last = messages[-1]
        system = next((m.content for m in messages if m.role == "system"), "")
        if system.startswith("CLASSIFY"):
            content = json.dumps({"category": self._category(self._user_text(messages))})
            return LLMResponse(content=content, usage=self._usage(messages, content))
        if system.startswith("PROPOSE"):
            content = json.dumps(self._proposal(self._user_text(messages)))
            return LLMResponse(content=content, usage=self._usage(messages, content))
        if last.role == "tool":
            content = self._final(last)
            return LLMResponse(content=content, usage=self._usage(messages, content))
        names = {t.name for t in tools}
        text = last.content
        want, args = self._route(text)
        if want not in names:
            content = f"I am not allowed to use the tool '{want}' here."
            return LLMResponse(content=content, usage=self._usage(messages, content))
        return LLMResponse(
            tool_calls=[ToolCall(id="call_1", name=want, arguments=args)],
            usage=self._usage(messages, ""),
        )

    @staticmethod
    def _user_text(messages: list[Message]) -> str:
        return "\n".join(m.content for m in messages if m.role == "user")

    @staticmethod
    def _usage(messages: list[Message], content: str) -> Usage:
        input_text = "".join(m.content for m in messages)
        return Usage(
            input_tokens=math.ceil(len(input_text) / 4),
            output_tokens=math.ceil(len(content) / 4),
        )

    @staticmethod
    def _category(text: str) -> str:
        low = text.lower()
        if re.search(r"\b(refund|money back)\b", low):
            return "refund"
        if re.search(r"\b(damaged|arrived broken)\b", low):
            return "damage"
        if re.search(r"\b(warranty|broken|defect)\b", low):
            return "warranty"
        if re.search(r"\b(delete|account|password)\b", low):
            return "account"
        return "other"

    @staticmethod
    def _proposal(text: str) -> dict[str, object]:
        full = text
        text = text.split("\n\nPolicy:")[0]  # read the ticket only; policy text must not change the plan
        category = FakeProvider._category(text)
        action = {"refund": "refund", "warranty": "replacement", "damage": "replacement"}.get(
            category, "escalate"
        )
        amount = 0
        price = re.search(r"\$([0-9]+(?:\.[0-9]{1,2})?)", text)
        if price:
            amount = int(round(Decimal(price.group(1)) * 100))
        order = re.search(r"ORD-\d+", text)
        citations = re.findall(r"(?m)^\s*\[doc:([^\]#\s]+)", full)
        return {
            "action": action,
            "amount_minor": amount,
            "order_id": order.group(0) if order else None,
            "reason": f"{action} under policy",
            "citations": citations,
        }

    @staticmethod
    def _route(text: str) -> tuple[str, dict[str, object]]:
        low = text.lower()
        m = re.search(r"\bt-(\d+)\b", low) or re.search(r"\bticket\s+#?(\d+)\b", low)
        if m:
            return "lookup_ticket", {"ticket_id": int(m.group(1))}
        if re.search(r"\b(create|open|file|raise)\b.*\bticket\b", low):
            subject = re.split(r"\babout\b", text, maxsplit=1, flags=re.I)[-1].strip()
            return "create_ticket", {"subject": subject[:200] or "Customer request", "body": text}
        if re.search(r"\b(notify|webhook)\b", low):
            return "call_customer_webhook", {
                "event": "agent.notification",
                "payload": {"text": text},
            }
        return "search_knowledge_base", {"query": text, "k": 3}

    @staticmethod
    def _final(tool_msg: Message) -> str:
        try:
            data = json.loads(tool_msg.content)
        except json.JSONDecodeError:
            return "The tool returned an unreadable result."
        if "error" in data:
            return f"The tool failed: {data['error']}"
        if tool_msg.name == "search_knowledge_base":
            hits = data.get("hits", [])
            if not hits or hits[0]["score"] < MIN_SCORE:
                return "I could not find this in the knowledge base."
            top = hits[0]
            body = " ".join(ln for ln in top["text"].splitlines() if not ln.startswith("#"))
            return f"{body.strip()} [{top['doc']}#{top['chunk']}]"
        if tool_msg.name == "lookup_ticket":
            return f"Ticket T-{data['id']} is {data['status']}: {data['subject']}."
        if tool_msg.name == "create_ticket":
            return f"I created ticket T-{data['id']} ({data['subject']})."
        if tool_msg.name == "call_customer_webhook":
            return f"I sent the {data['event']} event to the customer webhook (status {data['status_code']})."
        return "Done."

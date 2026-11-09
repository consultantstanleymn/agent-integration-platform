"""The written authority boundary of the case agent.

``docs/engagement/AUTHORITY.md`` holds one fenced block marked ``yaml authority``.
This module parses that block and judges a ticket plus a proposal against it.
"""

import re
from pathlib import Path

import yaml
from pydantic import BaseModel

from aip.graph.state import Proposal, Ticket

DEFAULT_PATH = Path("docs/engagement/AUTHORITY.md")
_BLOCK = re.compile(r"```yaml authority\n(.*?)```", re.S)

# Phrases that signal each forbidden intent. The intent list in the file selects which ones apply.
_INTENT_PHRASES: dict[str, tuple[str, ...]] = {
    "delete_account": ("delete the customer account", "delete my account", "delete the account"),
    "change_price": ("change the price", "change price"),
    "share_customer_data": ("share customer data", "send me the customer list", "customer list"),
    "bypass_approval": ("approve this yourself", "skip approval", "bypass approval"),
}


class Authority(BaseModel):
    allowed_actions: list[str]
    approval_limit_minor: int
    hard_cap_minor: int
    forbidden_intents: list[str] = []
    forbidden_patterns: list[str] = []


def load_authority(path: Path | str = DEFAULT_PATH) -> Authority:
    text = Path(path).read_text(encoding="utf-8")
    match = _BLOCK.search(text)
    if match is None:
        raise ValueError(f"no 'yaml authority' block in {path}")
    return Authority.model_validate(yaml.safe_load(match.group(1)))


def fallback_authority(approval_limit_minor: int) -> Authority:
    """Authority used when no file is given. Only the approval limit is configurable."""
    return Authority(
        allowed_actions=["refund", "replacement", "store_credit", "escalate"],
        approval_limit_minor=approval_limit_minor,
        hard_cap_minor=max(approval_limit_minor * 5, approval_limit_minor),
    )


def evaluate(authority: Authority, ticket: Ticket, proposal: Proposal | None) -> tuple[bool, str | None]:
    """Return ``(True, None)`` when allowed, else ``(False, reason)``."""
    text = f"{ticket.subject}\n{ticket.body}".lower()
    for phrase in authority.forbidden_patterns:
        if phrase.lower() in text:
            return False, "forbidden_pattern"
    for intent in authority.forbidden_intents:
        if any(p in text for p in _INTENT_PHRASES.get(intent, ())):
            return False, f"forbidden_intent:{intent}"
    if proposal is None:
        return True, None
    if proposal.action not in authority.allowed_actions:
        return False, "action_not_allowed"
    if proposal.amount_minor > authority.hard_cap_minor:
        return False, "over_hard_cap"
    if proposal.action == "refund" and not (proposal.order_id or ticket.order_id):
        return False, "refund_without_order"
    return True, None

"""The redactor port masks personal data in audit rows and case records."""

import json

from sqlalchemy import select

from aip.audit import audit
from aip.db import make_session_factory
from aip.graph.service import CaseService
from aip.models import AuditLog, Case
from aip.providers.base import LLMResponse
from aip.redaction import RegexRedactor, default_redactor


def test_regex_redactor_masks_email_and_phone():
    out = RegexRedactor().redact({"a": "mail bob@example.com or call 415-555-0199", "n": [1, "x@y.io"]})
    assert "bob@example.com" not in json.dumps(out)
    assert "415-555-0199" not in json.dumps(out)
    assert "x@y.io" not in json.dumps(out)
    assert out["n"][0] == 1


def test_audit_detail_is_redacted(env):
    with make_session_factory(env.engine)() as db:
        audit(db, None, "t.event", note="reach me at bob@example.com")
        db.commit()
        row = db.scalar(select(AuditLog).where(AuditLog.action == "t.event"))
        assert "bob@example.com" not in json.dumps(row.detail)


class _EmailProvider:
    """Proposes a refund and puts an email address into the reason text."""

    def chat(self, messages, tools):
        if messages[0].content.startswith("CLASSIFY"):
            return LLMResponse(content='{"category": "refund"}')
        reason = "Contact bob@example.com about ORD-1"
        content = json.dumps(
            {"action": "escalate", "amount_minor": 0, "order_id": None, "reason": reason, "citations": []}
        )
        return LLMResponse(content=content)


def test_case_row_ticket_text_is_redacted(env, tmp_path):
    svc = CaseService(
        checkpoint_url=f"sqlite:///{tmp_path / 'cp.sqlite'}",
        provider=_EmailProvider(),
        embedder=env.app.state.embedder,
        approval_limit_minor=10000,
    )
    env.app.state.case_service = svc
    t = env.tenant()
    env.add_kb(t)
    body = {"subject": "Help", "body": "Mail bob@example.com please."}
    assert env.client.post("/v1/cases", json=body, headers=t["headers"]).status_code == 201
    with make_session_factory(env.engine)() as db:
        row = db.scalar(select(Case))
        assert "bob@example.com" not in json.dumps(row.proposal)
        assert "bob@example.com" not in json.dumps([a.detail for a in db.scalars(select(AuditLog)).all()])
    svc.close()


def test_custom_redactor_can_be_injected(env):
    class Stars:
        def redact(self, value):
            return "***"

    assert default_redactor().redact("plain") == "plain"
    with make_session_factory(env.engine)() as db:
        audit(db, None, "t.custom", redactor=Stars(), secret="abc")
        db.commit()
        assert db.scalar(select(AuditLog).where(AuditLog.action == "t.custom")).detail == "***"

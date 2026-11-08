from sqlalchemy.orm import Session

from aip.models import Ticket


def create_ticket(db: Session, tenant_id: int, subject: str, body: str = "") -> Ticket:
    t = Ticket(tenant_id=tenant_id, subject=subject, body=body)
    db.add(t)
    db.flush()
    return t


def get_ticket(db: Session, tenant_id: int, ticket_id: int) -> Ticket | None:
    t = db.get(Ticket, ticket_id)
    return t if t and t.tenant_id == tenant_id else None  # other tenants' tickets look missing

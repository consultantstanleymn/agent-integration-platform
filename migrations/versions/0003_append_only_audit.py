"""audit_log becomes append-only

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for event in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER IF NOT EXISTS audit_log_no_{event.lower()} BEFORE {event} ON audit_log "
                "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;"
            )
    else:
        op.execute(
            "CREATE OR REPLACE FUNCTION audit_log_block() RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION 'audit_log is append-only'; END; $$ LANGUAGE plpgsql;"
        )
        op.execute(
            "CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log "
            "FOR EACH ROW EXECUTE FUNCTION audit_log_block();"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
        op.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
    else:
        op.execute("DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log")
        op.execute("DROP FUNCTION IF EXISTS audit_log_block()")

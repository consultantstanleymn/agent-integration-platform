"""return authorizations and the case action result

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "return_authorizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("ra_number", sa.String(length=32), nullable=False),
        sa.Column("order_id", sa.String(length=40), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
    )
    op.create_index(op.f("ix_return_authorizations_tenant_id"), "return_authorizations", ["tenant_id"])
    op.create_index(
        op.f("ix_return_authorizations_ra_number"), "return_authorizations", ["ra_number"], unique=True
    )
    with op.batch_alter_table("cases") as batch:
        batch.add_column(sa.Column("action_result", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cases") as batch:
        batch.drop_column("action_result")
    op.drop_index(op.f("ix_return_authorizations_ra_number"), table_name="return_authorizations")
    op.drop_index(op.f("ix_return_authorizations_tenant_id"), table_name="return_authorizations")
    op.drop_table("return_authorizations")

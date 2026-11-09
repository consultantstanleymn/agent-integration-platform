"""cases and tenant keys with roles

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tenant_keys_tenant_id"), "tenant_keys", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_tenant_keys_key_hash"), "tenant_keys", ["key_hash"], unique=True)
    op.create_table(
        "cases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=True),
        sa.Column("proposal", sa.JSON(), nullable=True),
        sa.Column("decision", sa.JSON(), nullable=True),
        sa.Column("steps_used", sa.Integer(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_cases_tenant_id"), "cases", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_cases_thread_id"), "cases", ["thread_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_cases_thread_id"), table_name="cases")
    op.drop_index(op.f("ix_cases_tenant_id"), table_name="cases")
    op.drop_table("cases")
    op.drop_index(op.f("ix_tenant_keys_key_hash"), table_name="tenant_keys")
    op.drop_index(op.f("ix_tenant_keys_tenant_id"), table_name="tenant_keys")
    op.drop_table("tenant_keys")

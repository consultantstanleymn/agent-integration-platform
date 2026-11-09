"""retrieval columns: access labels, source uri, and on PostgreSQL a vector, a tsvector and indexes

Revision ID: 0006
Revises: 0005

All dialects get documents.access_label, documents.source_uri and chunks.access_label.
PostgreSQL also gets chunks.embedding_vec (pgvector, size from the embedding_dim setting), chunks.tsv
(a generated tsvector) with a GIN index, and an HNSW index on embedding_vec. These PostgreSQL columns are
not in the ORM models: only the new retrieval adapter reads them.
"""

import sqlalchemy as sa
from alembic import op

from aip.config import Settings

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(
            sa.Column("access_label", sa.String(length=30), nullable=False, server_default="public")
        )
        batch.add_column(sa.Column("source_uri", sa.String(length=500), nullable=True))
    with op.batch_alter_table("chunks") as batch:
        batch.add_column(
            sa.Column("access_label", sa.String(length=30), nullable=False, server_default="public")
        )
    if op.get_bind().dialect.name != "postgresql":
        return
    dim = int(Settings().embedding_dim)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(f"ALTER TABLE chunks ADD COLUMN embedding_vec vector({dim})")
    op.execute(
        "ALTER TABLE chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED"
    )
    op.execute("CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv)")
    op.execute("CREATE INDEX ix_chunks_embedding_vec ON chunks USING hnsw (embedding_vec vector_cosine_ops)")
    op.execute(
        "UPDATE chunks SET embedding_vec = (embedding::text)::vector "
        f"WHERE json_array_length(embedding) = {dim}"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_vec")
        op.execute("DROP INDEX IF EXISTS ix_chunks_tsv")
        op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS tsv")
        op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS embedding_vec")
    with op.batch_alter_table("chunks") as batch:
        batch.drop_column("access_label")
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("source_uri")
        batch.drop_column("access_label")

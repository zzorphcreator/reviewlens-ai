"""add review chunks for rag

Revision ID: 20260427_0003
Revises: 20260427_0002
Create Date: 2026-04-27 15:55:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260427_0003"
down_revision: Union[str, Sequence[str], None] = "20260427_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "review_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("review_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["review_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_id", "chunk_index", name="uq_review_chunks_review_index"),
    )
    op.execute("ALTER TABLE review_chunks ADD COLUMN embedding vector(1536)")
    op.create_index("review_chunks_source_idx", "review_chunks", ["source_id"])
    op.create_index("review_chunks_review_idx", "review_chunks", ["review_id"])
    op.execute(
        "CREATE INDEX review_chunks_embedding_idx "
        "ON review_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS review_chunks_embedding_idx")
    op.drop_index("review_chunks_review_idx", table_name="review_chunks")
    op.drop_index("review_chunks_source_idx", table_name="review_chunks")
    op.drop_table("review_chunks")

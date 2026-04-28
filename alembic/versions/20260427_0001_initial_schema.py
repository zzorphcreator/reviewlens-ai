"""initial review ingestion schema

Revision ID: 20260427_0001
Revises:
Create Date: 2026-04-27 11:05:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260427_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "review_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("platform", sa.String(length=64), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "ingest_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["review_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ingest_jobs_status_idx", "ingest_jobs", ["status", "started_at"])

    op.create_table(
        "reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("author", sa.String(length=255), nullable=False),
        sa.Column("rating", sa.Numeric(precision=3, scale=1), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["review_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "fingerprint", name="uq_reviews_source_fingerprint"),
    )
    op.create_index("reviews_rating_idx", "reviews", ["rating"])
    op.create_index("reviews_reviewed_at_idx", "reviews", ["reviewed_at"])
    op.create_index("reviews_source_idx", "reviews", ["source_id"])


def downgrade() -> None:
    op.drop_index("reviews_source_idx", table_name="reviews")
    op.drop_index("reviews_reviewed_at_idx", table_name="reviews")
    op.drop_index("reviews_rating_idx", table_name="reviews")
    op.drop_table("reviews")
    op.drop_index("ingest_jobs_status_idx", table_name="ingest_jobs")
    op.drop_table("ingest_jobs")
    op.drop_table("review_sources")

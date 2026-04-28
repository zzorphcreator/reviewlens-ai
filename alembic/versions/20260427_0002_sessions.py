"""add review sessions and chat messages

Revision ID: 20260427_0002
Revises: 20260427_0001
Create Date: 2026-04-27 13:35:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260427_0002"
down_revision: Union[str, Sequence[str], None] = "20260427_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "review_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("review_sessions_created_at_idx", "review_sessions", ["created_at"])

    op.create_table(
        "review_session_sources",
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["review_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["review_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "source_id"),
        sa.UniqueConstraint("session_id", "source_id", name="uq_review_session_source"),
    )
    op.create_index(
        "review_session_sources_source_idx",
        "review_session_sources",
        ["source_id"],
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model_used", sa.String(length=128), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["review_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "chat_messages_session_created_idx",
        "chat_messages",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("chat_messages_session_created_idx", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("review_session_sources_source_idx", table_name="review_session_sources")
    op.drop_table("review_session_sources")
    op.drop_index("review_sessions_created_at_idx", table_name="review_sessions")
    op.drop_table("review_sessions")

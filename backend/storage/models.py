from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class ReviewSource(Base):
    __tablename__ = "review_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="file")
    embedding_model: Mapped[str] = mapped_column(
        String(128), nullable=False, default="text-embedding-3-small"
    )
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    jobs: Mapped[list[IngestJob]] = relationship(back_populates="source", cascade="all, delete")
    reviews: Mapped[list[Review]] = relationship(back_populates="source", cascade="all, delete")
    session_links: Mapped[list[ReviewSessionSource]] = relationship(
        back_populates="source", cascade="all, delete"
    )


class IngestJob(Base):
    __tablename__ = "ingest_jobs"
    __table_args__ = (Index("ingest_jobs_status_idx", "status", "started_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_sources.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    source: Mapped[ReviewSource] = relationship(back_populates="jobs")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("source_id", "fingerprint", name="uq_reviews_source_fingerprint"),
        Index("reviews_source_idx", "source_id"),
        Index("reviews_rating_idx", "rating"),
        Index("reviews_reviewed_at_idx", "reviewed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_sources.id", ondelete="CASCADE"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    rating: Mapped[float] = mapped_column(Numeric(3, 1), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    source: Mapped[ReviewSource] = relationship(back_populates="reviews")


class ReviewSession(Base):
    __tablename__ = "review_sessions"
    __table_args__ = (Index("review_sessions_created_at_idx", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    source_links: Mapped[list[ReviewSessionSource]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ReviewSessionSource(Base):
    __tablename__ = "review_session_sources"
    __table_args__ = (
        UniqueConstraint("session_id", "source_id", name="uq_review_session_source"),
        Index("review_session_sources_source_idx", "source_id"),
    )

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_sources.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[ReviewSession] = relationship(back_populates="source_links")
    source: Mapped[ReviewSource] = relationship(back_populates="session_links")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("chat_messages_session_created_idx", "session_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model_used: Mapped[str | None] = mapped_column(String(128))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[ReviewSession] = relationship(back_populates="messages")

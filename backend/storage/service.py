from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ingestion.dedupe import review_fingerprint
from backend.ingestion.models import ReviewDocument
from backend.storage.models import (
    ChatMessage,
    IngestJob,
    Review,
    ReviewSession,
    ReviewSessionSource,
    ReviewSource,
    utc_now,
)


async def create_source_with_job(
    session: AsyncSession,
    *,
    name: str,
    platform: str,
    url: str | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[ReviewSource, IngestJob]:
    source = ReviewSource(name=name, platform=platform, url=url, config=config or {})
    session.add(source)
    await session.flush()

    job = IngestJob(source_id=source.id, status="pending")
    session.add(job)
    await session.commit()
    await session.refresh(source)
    await session.refresh(job)
    return source, job


async def get_job(session: AsyncSession, job_id: str) -> IngestJob | None:
    return await session.get(IngestJob, job_id)


async def mark_job_running(session: AsyncSession, job_id: str) -> None:
    job = await session.get(IngestJob, job_id)
    if job is None:
        raise ValueError(f"Job {job_id} does not exist")
    job.status = "running"
    job.error = None
    job.started_at = utc_now()
    await session.commit()


async def update_job_stats(session: AsyncSession, job_id: str, stats: dict[str, Any]) -> None:
    job = await session.get(IngestJob, job_id)
    if job is None:
        raise ValueError(f"Job {job_id} does not exist")
    job.stats = {**(job.stats or {}), **stats}
    await session.commit()


async def mark_job_done(session: AsyncSession, job_id: str, stats: dict[str, Any]) -> None:
    job = await session.get(IngestJob, job_id)
    if job is None:
        raise ValueError(f"Job {job_id} does not exist")
    job.status = "done"
    job.error = None
    job.stats = stats
    job.finished_at = utc_now()
    await session.commit()


async def mark_job_failed(session: AsyncSession, job_id: str, error: str, stats: dict[str, Any]) -> None:
    job = await session.get(IngestJob, job_id)
    if job is None:
        raise ValueError(f"Job {job_id} does not exist")
    job.status = "failed"
    job.error = error
    job.stats = stats
    job.finished_at = utc_now()
    await session.commit()


async def mark_job_cancelled(session: AsyncSession, job_id: str, error: str | None = None) -> None:
    job = await session.get(IngestJob, job_id)
    if job is None:
        raise ValueError(f"Job {job_id} does not exist")
    job.status = "cancelled"
    job.error = error
    job.finished_at = utc_now()
    await session.commit()


async def is_job_cancelled(session: AsyncSession, job_id: str) -> bool:
    job = await session.get(IngestJob, job_id)
    return bool(job and job.status == "cancelled")


async def insert_reviews(
    session: AsyncSession, *, source_id: str, reviews: list[ReviewDocument]
) -> dict[str, int]:
    inserted = 0
    skipped = 0

    for review in reviews:
        fingerprint = review_fingerprint(source_id, review)
        existing = await session.scalar(
            select(Review.id).where(
                Review.source_id == source_id,
                Review.fingerprint == fingerprint,
            )
        )
        if existing:
            skipped += 1
            continue

        payload = {
            "source_id": source_id,
            "fingerprint": fingerprint,
            "author": review.author,
            "rating": Decimal(str(review.rating)),
            "title": review.title,
            "body": review.body,
            "reviewed_at": review.reviewed_at,
            "source_url": review.source_url,
            "raw": review.raw or review.model_dump(mode="json"),
        }
        session.add(Review(**payload))
        inserted += 1

    await session.commit()
    return {"inserted": inserted, "duplicates": skipped}


async def list_reviews(
    session: AsyncSession,
    *,
    source_id: str | None = None,
    source_ids: list[str] | None = None,
    query: str | None = None,
    rating_min: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Review], int]:
    filters = []
    if source_id:
        filters.append(Review.source_id == source_id)
    if source_ids is not None:
        filters.append(Review.source_id.in_(source_ids))
    if query:
        pattern = f"%{query.strip()}%"
        filters.append(
            or_(
                Review.author.ilike(pattern),
                Review.title.ilike(pattern),
                Review.body.ilike(pattern),
            )
        )
    if rating_min is not None:
        filters.append(Review.rating >= rating_min)

    total_stmt = select(func.count()).select_from(Review).where(*filters)
    total = int(await session.scalar(total_stmt) or 0)

    stmt = (
        select(Review)
        .where(*filters)
        .order_by(Review.reviewed_at.desc(), Review.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows), total


async def create_review_session(
    session: AsyncSession,
    *,
    name: str,
    source_ids: list[str],
    config: dict[str, Any] | None = None,
) -> ReviewSession:
    if not source_ids:
        raise ValueError("At least one source_id is required to save a session.")

    unique_source_ids = list(dict.fromkeys(source_ids))
    existing_sources = (
        await session.execute(select(ReviewSource.id).where(ReviewSource.id.in_(unique_source_ids)))
    ).scalars().all()
    missing = sorted(set(unique_source_ids) - set(existing_sources))
    if missing:
        raise ValueError(f"Unknown source_id values: {', '.join(missing)}")

    review_session = ReviewSession(name=name, config=config or {})
    session.add(review_session)
    await session.flush()

    for source_id in unique_source_ids:
        session.add(ReviewSessionSource(session_id=review_session.id, source_id=source_id))

    await session.commit()
    await session.refresh(review_session)
    return review_session


async def list_review_sessions(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ReviewSession], int]:
    total = int(await session.scalar(select(func.count()).select_from(ReviewSession)) or 0)
    stmt = (
        select(ReviewSession)
        .order_by(ReviewSession.updated_at.desc(), ReviewSession.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows), total


async def get_review_session(session: AsyncSession, session_id: str) -> ReviewSession | None:
    return await session.get(ReviewSession, session_id)


async def get_review_session_source_ids(session: AsyncSession, session_id: str) -> list[str] | None:
    review_session = await session.get(ReviewSession, session_id)
    if review_session is None:
        return None
    return list(
        (
            await session.execute(
                select(ReviewSessionSource.source_id)
                .where(ReviewSessionSource.session_id == session_id)
                .order_by(ReviewSessionSource.created_at)
            )
        )
        .scalars()
        .all()
    )


async def list_chat_messages(
    session: AsyncSession,
    *,
    session_id: str,
    limit: int = 200,
) -> list[ChatMessage] | None:
    review_session = await session.get(ReviewSession, session_id)
    if review_session is None:
        return None
    return list(
        (
            await session.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def add_chat_message(
    session: AsyncSession,
    *,
    session_id: str,
    role: str,
    content: str,
    model_used: str | None = None,
    latency_ms: int | None = None,
) -> ChatMessage:
    review_session = await session.get(ReviewSession, session_id)
    if review_session is None:
        raise ValueError(f"Session {session_id} does not exist")
    message = ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        model_used=model_used,
        latency_ms=latency_ms,
    )
    review_session.updated_at = utc_now()
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message


def serialize_job(job: IngestJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "source_id": job.source_id,
        "status": job.status,
        "error": job.error,
        "stats": job.stats,
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "created_at": _iso(job.created_at),
    }


def serialize_review(review: Review) -> dict[str, Any]:
    return {
        "id": review.id,
        "source_id": review.source_id,
        "author": review.author,
        "rating": float(review.rating),
        "title": review.title,
        "body": review.body,
        "reviewed_at": _iso(review.reviewed_at),
        "source_url": review.source_url,
        "raw": review.raw,
    }


async def serialize_review_session(session: AsyncSession, review_session: ReviewSession) -> dict[str, Any]:
    source_ids = (
        await session.execute(
            select(ReviewSessionSource.source_id)
            .where(ReviewSessionSource.session_id == review_session.id)
            .order_by(ReviewSessionSource.created_at)
        )
    ).scalars().all()

    review_count = int(
        await session.scalar(select(func.count()).select_from(Review).where(Review.source_id.in_(source_ids)))
        or 0
    )
    message_count = int(
        await session.scalar(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == review_session.id)
        )
        or 0
    )
    sources = (
        (
            await session.execute(
                select(ReviewSource)
                .where(ReviewSource.id.in_(source_ids))
                .order_by(ReviewSource.created_at)
            )
        )
        .scalars()
        .all()
    )

    return {
        "id": review_session.id,
        "name": review_session.name,
        "source_ids": list(source_ids),
        "sources": [serialize_review_source(source) for source in sources],
        "review_count": review_count,
        "message_count": message_count,
        "config": review_session.config,
        "created_at": _iso(review_session.created_at),
        "updated_at": _iso(review_session.updated_at),
    }


def serialize_review_source(source: ReviewSource) -> dict[str, Any]:
    return {
        "id": source.id,
        "name": source.name,
        "platform": source.platform,
        "url": source.url,
        "config": source.config,
        "created_at": _iso(source.created_at),
    }


def serialize_chat_message(message: ChatMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "session_id": message.session_id,
        "role": message.role,
        "content": message.content,
        "model_used": message.model_used,
        "latency_ms": message.latency_ms,
        "created_at": _iso(message.created_at),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None

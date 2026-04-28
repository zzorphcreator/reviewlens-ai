from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from backend.ingestion.file_processor import parse_review_file
from backend.llm.embeddings import embed_source_reviews
from backend.scrapers.router import scrape_url
from backend.storage.database import SessionLocal
from backend.storage.service import (
    insert_reviews,
    is_job_cancelled,
    mark_job_cancelled,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_stats,
)

logger = logging.getLogger(__name__)
SCRAPE_FAILURE_MESSAGE = (
    "We could not fetch reviews from this URL right now. The review site may be blocking automated "
    "access or returning a page format we cannot parse yet. Try another URL or upload CSV/JSON instead."
)


def import_file_task(job_id: str, source_id: str, file_path: str) -> None:
    asyncio.run(
        import_file_task_async(
            job_id=job_id,
            source_id=source_id,
            file_path=file_path,
            raise_on_failure=True,
        )
    )


async def import_file_task_async(
    job_id: str,
    source_id: str,
    file_path: str,
    *,
    raise_on_failure: bool = False,
) -> None:
    await _import_file(
        job_id=job_id,
        source_id=source_id,
        file_path=Path(file_path),
        raise_on_failure=raise_on_failure,
    )


def scrape_url_task(job_id: str, source_id: str, url: str, page_count: int = 1) -> None:
    asyncio.run(
        scrape_url_task_async(
            job_id=job_id,
            source_id=source_id,
            url=url,
            page_count=page_count,
            raise_on_failure=True,
        )
    )


async def scrape_url_task_async(
    job_id: str,
    source_id: str,
    url: str,
    page_count: int = 1,
    *,
    raise_on_failure: bool = False,
) -> None:
    async with SessionLocal() as session:
        await mark_job_running(session, job_id)
        await update_job_stats(
            session,
            job_id,
            {
                "progress_stage": "queued",
                "current_page": 0,
                "total_pages": page_count,
                "accepted": 0,
                "rejected": 0,
                "inserted": 0,
                "duplicates": 0,
            },
        )

    try:
        async def on_progress(stats: dict) -> None:
            async with SessionLocal() as progress_session:
                if await is_job_cancelled(progress_session, job_id):
                    raise CancelledError()
                await update_job_stats(progress_session, job_id, stats)

        result = await scrape_url(url, page_count=page_count, progress_callback=on_progress)
        async with SessionLocal() as session:
            if await is_job_cancelled(session, job_id):
                await mark_job_cancelled(session, job_id, "Cancelled by user.")
                return
        async with SessionLocal() as session:
            write_stats = await insert_reviews(session, source_id=source_id, reviews=result.reviews)
            await update_job_stats(
                session,
                job_id,
                {
                    "progress_stage": "embedding",
                    "current_provider": "OpenAI embeddings",
                },
            )
            if await is_job_cancelled(session, job_id):
                await mark_job_cancelled(session, job_id, "Cancelled by user.")
                return
            embed_stats = await embed_source_reviews(session, source_id=source_id)
            stats = {
                "accepted": len(result.reviews),
                "rejected": 0,
                "inserted": write_stats["inserted"],
                "duplicates": write_stats["duplicates"],
                **embed_stats,
                "final_url": result.final_url,
                "provider": result.provider,
                "page_count": page_count,
                "attempts": result.attempts,
                "errors": [],
            }
            await mark_job_done(session, job_id, stats)
    except CancelledError:
        async with SessionLocal() as session:
            await mark_job_cancelled(session, job_id, "Cancelled by user.")
        return
    except Exception as exc:
        logger.exception(
            "URL scrape job failed. job_id=%s source_id=%s url=%s page_count=%s",
            job_id,
            source_id,
            url,
            page_count,
        )
        async with SessionLocal() as session:
            await mark_job_failed(
                session,
                job_id,
                SCRAPE_FAILURE_MESSAGE,
                {"accepted": 0, "rejected": 0, "error_type": exc.__class__.__name__},
            )
        if raise_on_failure:
            raise


async def _import_file(
    job_id: str,
    source_id: str,
    file_path: Path,
    *,
    raise_on_failure: bool = False,
) -> None:
    async with SessionLocal() as session:
        await mark_job_running(session, job_id)

    try:
        result = parse_review_file(file_path)
        async with SessionLocal() as session:
            if await is_job_cancelled(session, job_id):
                await mark_job_cancelled(session, job_id, "Cancelled by user.")
                return
            write_stats = await insert_reviews(session, source_id=source_id, reviews=result.reviews)
            await update_job_stats(
                session,
                job_id,
                {
                    "progress_stage": "embedding",
                    "current_provider": "OpenAI embeddings",
                },
            )
            if await is_job_cancelled(session, job_id):
                await mark_job_cancelled(session, job_id, "Cancelled by user.")
                return
            embed_stats = await embed_source_reviews(session, source_id=source_id)
            stats = {
                "accepted": result.accepted_count,
                "rejected": result.rejected_count,
                "inserted": write_stats["inserted"],
                "duplicates": write_stats["duplicates"],
                **embed_stats,
                "errors": [error.model_dump() for error in result.errors[:50]],
            }
            await mark_job_done(session, job_id, stats)
    except CancelledError:
        async with SessionLocal() as session:
            await mark_job_cancelled(session, job_id, "Cancelled by user.")
        return
    except Exception as exc:
        async with SessionLocal() as session:
            await mark_job_failed(session, job_id, str(exc), {"accepted": 0, "rejected": 0})
        if raise_on_failure:
            raise


class CancelledError(Exception):
    pass

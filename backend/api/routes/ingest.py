from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import Settings, get_settings
from backend.core.url_validation import UnsafeUrlError, validate_public_http_url
from backend.storage.database import get_session
from backend.storage.service import create_source_with_job, serialize_job
from backend.workers.queues import scrape_queue
from backend.workers.tasks import scrape_url_task, scrape_url_task_async

router = APIRouter(prefix="/api/ingest", tags=["ingest"])
logger = logging.getLogger(__name__)


def _db_host(database_url: str) -> str:
    try:
        parsed = urlparse(database_url)
    except ValueError:
        return "unknown"
    return parsed.hostname or "unknown"


def _db_name(database_url: str) -> str:
    try:
        parsed = urlparse(database_url)
    except ValueError:
        return "unknown"
    return parsed.path.lstrip("/") or "unknown"


def _redis_host(redis_url: str) -> str:
    try:
        parsed = urlparse(redis_url)
    except ValueError:
        return "unknown"
    return parsed.hostname or "unknown"


def _redis_db(redis_url: str) -> str:
    try:
        parsed = urlparse(redis_url)
    except ValueError:
        return "unknown"
    return parsed.path.lstrip("/") or "0"


class UrlIngestRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    source_name: str | None = Field(default=None, max_length=255)
    page_count: int = Field(default=1, ge=1, le=50)


@router.post("/url", status_code=status.HTTP_202_ACCEPTED)
async def ingest_url(
    payload: UrlIngestRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        url = validate_public_http_url(payload.url)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    source, job = await create_source_with_job(
        session,
        name=payload.source_name or url,
        platform="url",
        url=url,
        config={"tier": "managed", "parser": "generic", "page_count": payload.page_count},
    )
    logger.warning(
        "Created ingest job",
        extra={
            "job_id": job.id,
            "db_host": _db_host(settings.database_url),
            "db_name": _db_name(settings.database_url),
        },
    )

    if settings.queue_mode == "rq":
        try:
            scrape_queue.enqueue(
                scrape_url_task, job.id, source.id, url, payload.page_count, job_timeout=600
            )
        except Exception as exc:
            logger.exception(
                "Failed to enqueue scrape job",
                extra={
                    "job_id": job.id,
                    "queue": scrape_queue.name,
                    "redis_host": _redis_host(settings.redis_url),
                    "redis_db": _redis_db(settings.redis_url),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to enqueue scrape job.",
            ) from exc
        logger.warning(
            "Enqueued scrape job",
            extra={
                "job_id": job.id,
                "queue": scrape_queue.name,
                "queue_count": scrape_queue.count,
                "redis_host": _redis_host(settings.redis_url),
                "redis_db": _redis_db(settings.redis_url),
            },
        )
    else:
        background_tasks.add_task(scrape_url_task_async, job.id, source.id, url, payload.page_count)

    return {
        "job": serialize_job(job),
        "source": {
            "id": source.id,
            "name": source.name,
            "platform": source.platform,
            "url": url,
            "config": source.config,
        },
    }

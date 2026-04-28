from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import Settings, get_settings
from backend.ingestion.file_processor import SUPPORTED_EXTENSIONS
from backend.storage.database import get_session
from backend.storage.service import create_source_with_job, serialize_job
from backend.workers.queues import import_queue
from backend.workers.tasks import import_file_task, import_file_task_async

router = APIRouter(prefix="/api/import", tags=["import"])


@router.post("/file", status_code=status.HTTP_202_ACCEPTED)
async def import_file(
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    source_name: Annotated[str | None, Form()] = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{suffix}'. Use CSV, JSON, JSONL, or NDJSON.",
        )

    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path = settings.upload_dir / f"{uuid.uuid4()}{suffix}"
    size = await _save_upload(file, saved_path, settings.max_upload_bytes)

    source, job = await create_source_with_job(
        session,
        name=source_name or file.filename or "Uploaded reviews",
        platform="file",
        config={"filename": file.filename, "bytes": size},
    )

    if settings.queue_mode == "rq":
        import_queue.enqueue(import_file_task, job.id, source.id, str(saved_path), job_timeout=600)
    else:
        background_tasks.add_task(import_file_task_async, job.id, source.id, str(saved_path))

    return {
        "job": serialize_job(job),
        "source": {
            "id": source.id,
            "name": source.name,
            "platform": source.platform,
            "url": source.url,
            "config": source.config,
        },
    }


async def _save_upload(file: UploadFile, path: Path, max_bytes: int) -> int:
    total = 0
    with path.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File exceeds max upload size of {max_bytes} bytes.",
                )
            output.write(chunk)
    return total

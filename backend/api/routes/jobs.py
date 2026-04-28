from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.storage.database import get_session
from backend.storage.service import get_job, mark_job_cancelled, serialize_job

router = APIRouter(prefix="/api/ingest/jobs", tags=["jobs"])


@router.get("/{job_id}")
async def read_job(job_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    job = await get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return {"job": serialize_job(job)}


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    job = await get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status in {"done", "failed", "cancelled"}:
        return {"job": serialize_job(job)}
    await mark_job_cancelled(session, job_id, "Cancelled by user.")
    job = await get_job(session, job_id)
    return {"job": serialize_job(job)}

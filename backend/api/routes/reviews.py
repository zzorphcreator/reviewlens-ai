from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.storage.database import get_session
from backend.storage.service import get_review_session_source_ids, list_reviews, serialize_review

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


@router.get("")
async def read_reviews(
    source_id: str | None = None,
    source_ids: Annotated[list[str] | None, Query()] = None,
    session_id: str | None = None,
    q: str | None = None,
    rating_min: Annotated[int | None, Query(ge=1, le=5)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(get_session),
) -> dict:
    resolved_source_ids = None
    if session_id:
        resolved_source_ids = await get_review_session_source_ids(session, session_id)
        if resolved_source_ids is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    elif source_ids:
        normalized: list[str] = []
        for entry in source_ids:
            normalized.extend([item.strip() for item in entry.split(",") if item.strip()])
        resolved_source_ids = normalized or None

    reviews, total = await list_reviews(
        session,
        source_id=source_id,
        source_ids=resolved_source_ids,
        query=q,
        rating_min=rating_min,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [serialize_review(review) for review in reviews],
        "total": total,
        "limit": limit,
        "offset": offset,
    }

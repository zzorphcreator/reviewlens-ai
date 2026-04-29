from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.storage.database import get_session
from backend.storage.service import (
    add_chat_message,
    add_chat_messages,
    create_review_session,
    get_review_session,
    list_chat_messages,
    list_review_sessions,
    serialize_chat_message,
    serialize_review_session,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class ChatMessageRequest(BaseModel):
    role: str = Field(pattern="^(user|assistant|system)$")
    content: str = Field(min_length=1)
    model_used: str | None = Field(default=None, max_length=128)
    latency_ms: int | None = Field(default=None, ge=0)


class SaveSessionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    source_ids: list[str] = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    messages: list[ChatMessageRequest] = Field(default_factory=list)


@router.post("", status_code=status.HTTP_201_CREATED)
async def save_session(
    payload: SaveSessionRequest,
    db: AsyncSession = Depends(get_session),
) -> dict:
    try:
        review_session = await create_review_session(
            db,
            name=payload.name,
            source_ids=payload.source_ids,
            config=payload.config,
        )
        if payload.messages:
            await add_chat_messages(db, session_id=review_session.id, messages=payload.messages)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {"session": await serialize_review_session(db, review_session)}


@router.get("")
async def read_sessions(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: AsyncSession = Depends(get_session),
) -> dict:
    sessions, total = await list_review_sessions(db, limit=limit, offset=offset)
    return {
        "items": [await serialize_review_session(db, item) for item in sessions],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{session_id}")
async def read_session(session_id: str, db: AsyncSession = Depends(get_session)) -> dict:
    review_session = await get_review_session(db, session_id)
    if review_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    messages = await list_chat_messages(db, session_id=session_id)
    return {
        "session": await serialize_review_session(db, review_session),
        "messages": [serialize_chat_message(message) for message in messages or []],
    }


@router.post("/{session_id}/messages", status_code=status.HTTP_201_CREATED)
async def save_chat_message(
    session_id: str,
    payload: ChatMessageRequest,
    db: AsyncSession = Depends(get_session),
) -> dict:
    try:
        message = await add_chat_message(
            db,
            session_id=session_id,
            role=payload.role,
            content=payload.content,
            model_used=payload.model_used,
            latency_ms=payload.latency_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return {"message": serialize_chat_message(message)}

from __future__ import annotations

import json
import re
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from backend.config import get_settings
from backend.llm.chat import (
    build_user_prompt,
    elapsed_ms,
    plain_text_response,
    stream_anthropic_chat,
    stream_openai_chat,
)
from backend.llm.embeddings import retrieve_relevant_chunks
from backend.storage.database import get_session
from backend.storage.models import Review, ReviewSource
from backend.storage.service import add_chat_message, get_review_session_source_ids

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    source_ids: list[str] | None = None

    @model_validator(mode="after")
    def require_scope(self) -> "ChatRequest":
        if not self.session_id and not self.source_ids:
            raise ValueError("session_id or source_ids is required.")
        return self


class ChatScopeRequest(BaseModel):
    session_id: str | None = None
    source_ids: list[str] | None = None

    @model_validator(mode="after")
    def require_scope(self) -> "ChatScopeRequest":
        if not self.session_id and not self.source_ids:
            raise ValueError("session_id or source_ids is required.")
        return self


@router.post("")
async def chat(payload: ChatRequest, db: Annotated[AsyncSession, Depends(get_session)]) -> StreamingResponse:
    source_ids = await resolve_source_ids(db, payload)
    chunks = await retrieve_relevant_chunks(db, question=payload.question, source_ids=source_ids)
    total_review_count = await count_scope_reviews(db, source_ids)

    return StreamingResponse(
        stream_chat_response(
            db=db,
            payload=payload,
            chunks=chunks,
            total_review_count=total_review_count,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/suggestions")
async def chat_suggestions(
    payload: ChatScopeRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    source_ids = await resolve_source_ids(db, payload)
    rows = (
        await db.execute(
            select(Review.title, Review.body)
            .where(Review.source_id.in_(source_ids))
            .order_by(Review.reviewed_at.desc(), Review.created_at.desc())
            .limit(250)
        )
    ).all()
    return {"items": derive_questions([" ".join([title or "", body or ""]) for title, body in rows])}


@router.post("/topics")
async def chat_topics(
    payload: ChatScopeRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    source_ids = await resolve_source_ids(db, payload)
    rows = (
        await db.execute(
            select(Review.title, Review.body)
            .where(Review.source_id.in_(source_ids))
            .order_by(Review.reviewed_at.desc(), Review.created_at.desc())
            .limit(400)
        )
    ).all()
    return {"items": derive_topics([" ".join([title or "", body or ""]) for title, body in rows])}


async def stream_chat_response(
    *,
    db: AsyncSession,
    payload: ChatRequest,
    chunks: list[dict],
    total_review_count: int,
):
    started_at = time.perf_counter()
    if not chunks:
        answer = "I do not have embedded review content for the current review scope yet."
        yield sse("token", {"text": answer})
        yield sse(
            "done",
            {
                "answer": answer,
                "model_used": None,
                "latency_ms": 0,
                "review_count": total_review_count,
                "sources": [],
            },
        )
        return

    settings = get_settings()
    prompt = build_user_prompt(
        question=payload.question,
        context_chunks=chunks,
        total_review_count=total_review_count,
    )
    sources = [
        {"review_id": chunk["review_id"], "source_id": chunk["source_id"], "score": float(chunk["score"])}
        for chunk in chunks
    ]
    retrieved_review_count = len({chunk["review_id"] for chunk in chunks})
    answer_parts: list[str] = []
    failures: list[str] = []

    if payload.session_id:
        await add_chat_message(db, session_id=payload.session_id, role="user", content=payload.question)

    providers = []
    if settings.openai_api_key:
        providers.append((settings.openai_chat_model, stream_openai_chat(settings=settings, prompt=prompt)))
    if settings.anthropic_api_key:
        providers.extend(
            (
                model,
                stream_anthropic_chat(settings=settings, model=model, prompt=prompt),
            )
            for model in settings.anthropic_models
        )

    if not providers:
        yield sse("error", {"detail": "OPENAI_API_KEY or ANTHROPIC_API_KEY is required for chat."})
        return

    for model, stream in providers:
        yielded_token = False
        try:
            yield sse(
                "meta",
                {
                    "model_used": model,
                    "review_count": total_review_count,
                    "retrieved_review_count": retrieved_review_count,
                    "sources": sources,
                },
            )
            async for token in stream:
                yielded_token = True
                answer_parts.append(token)
                yield sse("token", {"text": token})

            answer = plain_text_response("".join(answer_parts))
            latency = elapsed_ms(started_at)
            if payload.session_id:
                await add_chat_message(
                    db,
                    session_id=payload.session_id,
                    role="assistant",
                    content=answer,
                    model_used=model,
                    latency_ms=latency,
                )
            yield sse(
                "done",
                {
                    "answer": answer,
                    "model_used": model,
                    "latency_ms": latency,
                    "review_count": total_review_count,
                    "retrieved_review_count": retrieved_review_count,
                    "sources": sources,
                },
            )
            return
        except Exception as exc:  # pragma: no cover - provider failures are environment-specific.
            failures.append(f"{model}: {exc}")
            if yielded_token:
                yield sse("error", {"detail": f"Chat stream failed after partial output: {exc}"})
                return
            answer_parts.clear()

    yield sse("error", {"detail": "All LLM providers failed. " + " | ".join(failures)})


async def resolve_source_ids(db: AsyncSession, payload: ChatRequest | ChatScopeRequest) -> list[str]:
    if payload.session_id:
        source_ids = await get_review_session_source_ids(db, payload.session_id)
        if source_ids is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return source_ids

    unique_source_ids = list(dict.fromkeys(payload.source_ids or []))
    existing_source_ids = (
        await db.execute(select(ReviewSource.id).where(ReviewSource.id.in_(unique_source_ids)))
    ).scalars().all()
    missing = sorted(set(unique_source_ids) - set(existing_source_ids))
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown source_id values: {', '.join(missing)}",
        )
    return unique_source_ids


async def count_scope_reviews(db: AsyncSession, source_ids: list[str]) -> int:
    if not source_ids:
        return 0
    return int(
        await db.scalar(select(func.count()).select_from(Review).where(Review.source_id.in_(source_ids)))
        or 0
    )


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def derive_questions(texts: list[str]) -> list[str]:
    topics = derive_topics(texts)[:4]
    if not topics:
        return [
            "What are the most common positive themes?",
            "What are the most common complaints?",
            "What should the product team fix first?",
        ]

    questions = [
        "What are the most common positive themes?",
        "What are the most common complaints?",
    ]
    for topic in topics[:3]:
        questions.append(f"What do reviewers say about {topic}?")
    questions.append("What should the product team fix first?")
    return questions[:6]


def derive_topics(texts: list[str]) -> list[str]:
    words = re.findall(r"[a-z][a-z0-9'-]{2,}", " ".join(texts).lower())
    counts: dict[str, int] = {}
    for word in words:
        if word not in STOPWORDS:
            counts[word] = counts.get(word, 0) + 1
    return [word for word, _ in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:6]]


STOPWORDS = {
    "about",
    "after",
    "also",
    "because",
    "been",
    "being",
    "from",
    "have",
    "into",
    "just",
    "like",
    "more",
    "much",
    "only",
    "really",
    "some",
    "that",
    "their",
    "there",
    "they",
    "this",
    "very",
    "with",
    "would",
}

from __future__ import annotations

import uuid
from typing import Any

import httpx
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import Settings, get_settings
from backend.storage.models import Review, utc_now


def chunk_review(review: Review, *, max_chars: int = 1800) -> list[str]:
    header = f"Author: {review.author}\nRating: {float(review.rating):.1f}/5\n"
    if review.title:
        header += f"Title: {review.title}\n"
    header += f"Reviewed at: {review.reviewed_at.date().isoformat()}\n\n"
    body = review.body.strip()
    if len(header) + len(body) <= max_chars:
        return [header + body]

    chunks: list[str] = []
    remaining = body
    while remaining:
        segment = remaining[: max_chars - len(header)]
        split_at = max(segment.rfind("\n"), segment.rfind(". "), segment.rfind(" "))
        if split_at > max_chars // 3:
            segment = segment[: split_at + 1]
        chunks.append(header + segment.strip())
        remaining = remaining[len(segment) :].strip()
    return chunks


async def embed_source_reviews(
    session: AsyncSession,
    *,
    source_id: str,
    settings: Settings | None = None,
) -> dict[str, int]:
    settings = settings or get_settings()
    reviews = (
        await session.execute(
            select(Review)
            .where(Review.source_id == source_id)
            .order_by(Review.reviewed_at.desc(), Review.created_at.desc())
        )
    ).scalars().all()

    embedded_reviews = 0
    embedded_chunks = 0
    for review in reviews:
        exists = await session.scalar(
            text("SELECT 1 FROM review_chunks WHERE review_id = :review_id LIMIT 1"),
            {"review_id": review.id},
        )
        if exists:
            continue

        chunks = chunk_review(review)
        vectors = await embed_texts(chunks, settings=settings)
        for index, (content, vector) in enumerate(zip(chunks, vectors), start=0):
            await session.execute(
                text(
                    """
                    INSERT INTO review_chunks (
                        id, review_id, source_id, chunk_index, content,
                        embedding_model, metadata, created_at, embedding
                    )
                    VALUES (
                        :id, :review_id, :source_id, :chunk_index, :content,
                        :embedding_model, CAST(:metadata AS json), :created_at,
                        CAST(:embedding AS vector)
                    )
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "review_id": review.id,
                    "source_id": source_id,
                    "chunk_index": index,
                    "content": content,
                    "embedding_model": settings.embedding_model,
                    "metadata": "{}",
                    "created_at": utc_now(),
                    "embedding": vector_literal(vector),
                },
            )
            embedded_chunks += 1
        embedded_reviews += 1

    await session.commit()
    return {"embedded_reviews": embedded_reviews, "embedded_chunks": embedded_chunks}


async def embed_texts(texts: list[str], *, settings: Settings | None = None) -> list[list[float]]:
    settings = settings or get_settings()
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required for embeddings.")
    if not texts:
        return []

    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        response = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.embedding_model,
                "input": texts,
                "dimensions": settings.embedding_dimensions,
            },
        )
    response.raise_for_status()
    payload = response.json()
    return [item["embedding"] for item in sorted(payload["data"], key=lambda item: item["index"])]


async def retrieve_relevant_chunks(
    session: AsyncSession,
    *,
    question: str,
    source_ids: list[str],
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    if not source_ids:
        return []

    question_vector = (await embed_texts([question], settings=settings))[0]
    stmt = (
        text(
            """
            SELECT
                review_id,
                source_id,
                content,
                metadata,
                1 - (embedding <=> CAST(:embedding AS vector)) AS score
            FROM review_chunks
            WHERE source_id IN :source_ids
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
            """
        )
        .bindparams(bindparam("source_ids", expanding=True))
    )
    rows = (
        await session.execute(
            stmt,
            {
                "embedding": vector_literal(question_vector),
                "source_ids": source_ids,
                "limit": settings.rag_top_k,
            },
        )
    ).mappings().all()
    return [dict(row) for row in rows]


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"

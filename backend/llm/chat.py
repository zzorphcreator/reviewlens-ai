from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
import re

import httpx

from backend.config import Settings, get_settings
from backend.llm.tracing import traceable


STRICT_RAG_SYSTEM_PROMPT = """You are ReviewLens AI.
Answer only from the provided review excerpts.
If the excerpts do not contain enough evidence, say that I can only answer questions about the current review set.
Do not use outside knowledge. Do not answer questions unrelated to the current review set.
Be concise, concrete, and mention patterns across reviews when present.
If the user asks how many reviews are in scope, use the provided review count for the current review set.
Do not count excerpts, chunks, or repeated review IDs as reviews.
Write in plain text only. Do not use Markdown formatting, bullets, headings, bold text, tables, or code fences.
Do not add meta commentary about how you counted excerpts or review IDs unless asked."""


@dataclass
class ChatResult:
    answer: str
    model_used: str
    latency_ms: int


@traceable(name="answer_with_fallback", run_type="chain")
async def answer_with_fallback(
    *,
    question: str,
    context_chunks: list[dict],
    settings: Settings | None = None,
) -> ChatResult:
    settings = settings or get_settings()
    prompt = build_user_prompt(question=question, context_chunks=context_chunks)
    started_at = time.perf_counter()
    failures: list[str] = []

    if settings.openai_api_key:
        try:
            answer = plain_text_response(await call_openai_chat(settings=settings, prompt=prompt))
            return ChatResult(
                answer=answer,
                model_used=settings.openai_chat_model,
                latency_ms=elapsed_ms(started_at),
            )
        except Exception as exc:  # pragma: no cover - provider failures are environment-specific.
            failures.append(f"{settings.openai_chat_model}: {exc}")

    if settings.anthropic_api_key:
        for model in settings.anthropic_models:
            try:
                answer = plain_text_response(
                    await call_anthropic_chat(settings=settings, model=model, prompt=prompt)
                )
                return ChatResult(answer=answer, model_used=model, latency_ms=elapsed_ms(started_at))
            except Exception as exc:  # pragma: no cover - provider failures are environment-specific.
                failures.append(f"{model}: {exc}")

    if failures:
        raise RuntimeError("All LLM providers failed. " + " | ".join(failures))
    raise ValueError("OPENAI_API_KEY or ANTHROPIC_API_KEY is required for chat.")


def build_user_prompt(
    *,
    question: str,
    context_chunks: list[dict],
    total_review_count: int | None = None,
    conversation: list[dict] | None = None,
) -> str:
    excerpts = []
    for index, chunk in enumerate(context_chunks, start=1):
        excerpts.append(
            f"[Excerpt {index} | review_id={chunk['review_id']} | score={float(chunk['score']):.3f}]\n"
            f"{chunk['content']}"
        )
    scope_count = (
        f"Current review set contains {total_review_count} reviews.\n\n"
        if total_review_count is not None
        else ""
    )
    history = ""
    if conversation:
        lines = ["Recent chat (most recent last):"]
        for message in conversation:
            role = message.get("role", "user")
            label = "User" if role == "user" else "Assistant"
            lines.append(f"{label}: {message.get('content', '').strip()}")
        history = "\n".join(lines) + "\n\n"
    return (
        scope_count
        + history
        + "Current review excerpts:\n\n"
        + "\n\n---\n\n".join(excerpts)
        + f"\n\nQuestion: {question}\n\nAnswer from these excerpts only."
    )


@traceable(name="openai_chat_completion", run_type="llm")
async def call_openai_chat(*, settings: Settings, prompt: str) -> str:
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.openai_chat_model,
                "messages": [
                    {"role": "system", "content": STRICT_RAG_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
            },
        )
    response.raise_for_status()
    payload = response.json()
    return payload["choices"][0]["message"]["content"].strip()


@traceable(name="openai_chat_stream", run_type="llm")
async def stream_openai_chat(*, settings: Settings, prompt: str) -> AsyncIterator[str]:
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        async with client.stream(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.openai_chat_model,
                "messages": [
                    {"role": "system", "content": STRICT_RAG_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "stream": True,
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line.removeprefix("data: ").strip()
                if payload == "[DONE]":
                    break
                data = json.loads(payload)
                token = data["choices"][0].get("delta", {}).get("content")
                if token:
                    yield token


@traceable(name="anthropic_chat_completion", run_type="llm")
async def call_anthropic_chat(*, settings: Settings, model: str, prompt: str) -> str:
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.anthropic_api_key or "",
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": model,
                "max_tokens": 900,
                "temperature": 0.1,
                "system": STRICT_RAG_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
    response.raise_for_status()
    payload = response.json()
    return "".join(
        block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text"
    ).strip()


@traceable(name="anthropic_chat_stream", run_type="llm")
async def stream_anthropic_chat(
    *, settings: Settings, model: str, prompt: str
) -> AsyncIterator[str]:
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        async with client.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.anthropic_api_key or "",
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": model,
                "max_tokens": 900,
                "temperature": 0.1,
                "system": STRICT_RAG_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = json.loads(line.removeprefix("data: "))
                if data.get("type") == "content_block_delta":
                    token = data.get("delta", {}).get("text")
                    if token:
                        yield token


def elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def plain_text_response(value: str) -> str:
    text = value.strip()
    text = re.sub(r"```(?:\w+)?\s*", "", text)
    text = text.replace("```", "")
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s{0,3}>\s?", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    text = re.sub(r"(?m)^\s*\d+\.\s+", "", text)
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(\*|_)(.*?)\1", r"\2", text)
    text = text.replace("`", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from pydantic import ValidationError

from backend.ingestion.models import ReviewDocument
from backend.scrapers.models import ScrapeResult


def is_google_play_url(url: str) -> bool:
    parsed = urlparse(url)
    return "play.google.com" in parsed.netloc.lower() and google_play_app_id(url) is not None


async def scrape_google_play_reviews(
    url: str,
    *,
    page_count: int = 1,
    progress_callback=None,
) -> ScrapeResult:
    app_id = google_play_app_id(url)
    if app_id is None:
        raise ValueError("Google Play URL does not contain an app package id.")

    language = google_play_language(url)
    country = google_play_country(url)
    pages = max(1, min(page_count, 10))
    attempts: list[dict[str, str]] = []
    all_reviews: list[ReviewDocument] = []
    continuation_token = None

    for page_number in range(1, pages + 1):
        if progress_callback:
            await progress_callback(
                {
                    "progress_stage": "scraping",
                    "current_page": page_number,
                    "total_pages": pages,
                    "current_url": url,
                    "current_provider": "google-play",
                }
            )

        raw_reviews, continuation_token = await asyncio.to_thread(
            fetch_google_play_review_page,
            app_id,
            language,
            country,
            continuation_token,
        )
        page_reviews = parse_google_play_reviews(raw_reviews, source_url=url)
        attempts.append(
            {
                "page": str(page_number),
                "provider": "google-play",
                "status": "success" if page_reviews else "no_reviews",
                "message": "",
            }
        )
        all_reviews.extend(page_reviews)
        if continuation_token is None:
            break

    return ScrapeResult(
        source_url=url,
        final_url=url,
        provider="google-play",
        reviews=dedupe_reviews(all_reviews),
        attempts=attempts,
    )


def fetch_google_play_review_page(
    app_id: str,
    language: str,
    country: str,
    continuation_token: Any = None,
) -> tuple[list[dict[str, Any]], Any]:
    try:
        from google_play_scraper import Sort, reviews
    except ImportError as exc:
        raise RuntimeError(
            "google-play-scraper is required for Google Play reviews. "
            "Install project dependencies before scraping Google Play URLs."
        ) from exc

    return reviews(
        app_id,
        lang=language,
        country=country,
        sort=Sort.NEWEST,
        count=100,
        continuation_token=continuation_token,
    )


def parse_google_play_reviews(
    payload: list[dict[str, Any]],
    *,
    source_url: str,
) -> list[ReviewDocument]:
    parsed: list[ReviewDocument] = []
    for item in payload:
        body = item.get("content")
        rating = item.get("score")
        if not body or rating is None:
            continue
        try:
            parsed.append(
                ReviewDocument.model_validate(
                    {
                        "author": item.get("userName") or "Anonymous",
                        "rating": float(rating),
                        "title": item.get("title") or "Google Play review",
                        "body": body,
                        "reviewed_at": google_play_date(item.get("at")),
                        "source_url": source_url,
                        "metadata": {
                            "parser": "google_play_scraper",
                            "review_id": item.get("reviewId"),
                            "thumbs_up_count": item.get("thumbsUpCount"),
                            "app_version": item.get("reviewCreatedVersion"),
                        },
                        "raw": json_safe(item),
                    }
                )
            )
        except (TypeError, ValueError, ValidationError):
            continue
    return parsed


def json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    return value


def google_play_app_id(url: str) -> str | None:
    query = parse_qs(urlparse(url).query)
    values = query.get("id")
    return values[0] if values and values[0] else None


def google_play_language(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    values = query.get("hl")
    return values[0].split("-", 1)[0].lower() if values and values[0] else "en"


def google_play_country(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    values = query.get("gl")
    return values[0].lower() if values and values[0] else "us"


def google_play_date(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(tz=UTC)


def dedupe_reviews(reviews: list[ReviewDocument]) -> list[ReviewDocument]:
    seen: set[str] = set()
    unique: list[ReviewDocument] = []
    for review in reviews:
        key = f"{review.author}|{review.reviewed_at.isoformat()}|{review.body[:200]}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(review)
    return unique

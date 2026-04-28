from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from backend.config import Settings, get_settings
from backend.ingestion.models import ReviewDocument
from backend.scrapers.models import ScrapeResult


def is_apple_app_store_url(url: str) -> bool:
    parsed = urlparse(url)
    return "apps.apple.com" in parsed.netloc.lower() and app_id_from_url(url) is not None


async def scrape_apple_app_store_reviews(
    url: str,
    *,
    page_count: int = 1,
    settings: Settings | None = None,
    progress_callback=None,
) -> ScrapeResult:
    settings = settings or get_settings()
    app_id = app_id_from_url(url)
    if app_id is None:
        raise ValueError("Apple App Store URL does not contain an app id.")

    country = country_from_url(url)
    attempts: list[dict[str, str]] = []
    reviews: list[ReviewDocument] = []
    final_url = url

    async with httpx.AsyncClient(timeout=settings.scraper_timeout_seconds) as client:
        for page_number, feed_url in enumerate(
            apple_review_feed_urls(app_id=app_id, country=country, page_count=page_count),
            start=1,
        ):
            if progress_callback:
                await progress_callback(
                    {
                        "progress_stage": "scraping",
                        "current_page": page_number,
                        "total_pages": max(1, min(page_count, 10)),
                        "current_url": feed_url,
                        "current_provider": "apple-rss",
                    }
                )
            response = await client.get(feed_url)
            response.raise_for_status()
            final_url = str(response.url)
            page_reviews = parse_apple_review_feed(response.json(), source_url=url)
            attempts.append(
                {
                    "page": str(page_number),
                    "provider": "apple-rss",
                    "status": "success" if page_reviews else "no_reviews",
                    "message": "",
                }
            )
            reviews.extend(page_reviews)

    return ScrapeResult(
        source_url=url,
        final_url=final_url,
        provider="apple-rss",
        reviews=dedupe_reviews(reviews),
        attempts=attempts,
    )


def apple_review_feed_urls(*, app_id: str, country: str, page_count: int) -> list[str]:
    count = max(1, min(page_count, 10))
    return [
        f"https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}/sortby=mostrecent/json"
        for page in range(1, count + 1)
    ]


def parse_apple_review_feed(payload: dict[str, Any], *, source_url: str) -> list[ReviewDocument]:
    entries = payload.get("feed", {}).get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]

    reviews: list[ReviewDocument] = []
    for entry in entries:
        rating = _label(entry.get("im:rating"))
        body = _label(entry.get("content"))
        if not rating or not body:
            continue
        try:
            reviews.append(
                ReviewDocument.model_validate(
                    {
                        "author": _label(entry.get("author", {}).get("name")) or "Anonymous",
                        "rating": float(rating),
                        "title": _label(entry.get("title")) or "Apple App Store review",
                        "body": body,
                        "reviewed_at": parse_apple_date(_label(entry.get("updated"))),
                        "source_url": source_url,
                        "metadata": {"parser": "apple_app_store_rss"},
                        "raw": entry,
                    }
                )
            )
        except (TypeError, ValueError, ValidationError):
            continue
    return reviews


def app_id_from_url(url: str) -> str | None:
    match = re.search(r"/id(\d+)", urlparse(url).path)
    return match.group(1) if match else None


def country_from_url(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if parts and re.fullmatch(r"[a-z]{2}", parts[0], re.IGNORECASE):
        return parts[0].lower()
    return "us"


def parse_apple_date(value: str) -> datetime:
    if not value:
        return datetime.now(tz=UTC)
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.now(tz=UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def dedupe_reviews(reviews: list[ReviewDocument]) -> list[ReviewDocument]:
    seen: set[str] = set()
    unique: list[ReviewDocument] = []
    for review in reviews:
        key = f"{review.author}|{review.reviewed_at.isoformat()}|{review.title}|{review.body[:200]}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(review)
    return unique


def _label(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("label") or "").strip()
    if value is None:
        return ""
    return str(value).strip()

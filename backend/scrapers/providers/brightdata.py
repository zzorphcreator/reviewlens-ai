from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from backend.config import Settings, get_settings
from backend.core.url_validation import validate_public_http_url
from backend.ingestion.models import ReviewDocument
from backend.scrapers.models import FetchedPage, ScrapeResult
from backend.scrapers.parsers.review_html import (
    dedupe_reviews,
    extraction_diagnostics,
    parse_review_html,
    review_card_debug_fragments,
)
from backend.scrapers.providers.http import DEFAULT_HEADERS
from backend.scrapers.utils.pagination import with_page
from backend.scrapers.utils.platform_detection import platform_from_url, product_name_from_url

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict], Awaitable[None]]


async def fetch_with_brightdata(
    url: str,
    *,
    settings: Settings | None = None,
) -> FetchedPage:
    settings = settings or get_settings()
    if settings.brightdata_api_key and settings.brightdata_zone:
        return await _fetch_with_brightdata_api(url, settings=settings)
    if settings.brightdata_proxy_url:
        return await _fetch_with_brightdata_proxy(url, settings=settings)
    raise ValueError(
        "Bright Data is not configured. Set BRIGHTDATA_API_KEY and BRIGHTDATA_ZONE, "
        "or set BRIGHTDATA_PROXY_URL."
    )


async def scrape_with_brightdata(
    url: str,
    *,
    page_count: int = 1,
    settings: Settings | None = None,
    progress_callback: ProgressCallback | None = None,
) -> ScrapeResult:
    settings = settings or get_settings()
    normalized_url = validate_public_http_url(url)
    if not settings.brightdata_api_key:
        raise ValueError("BRIGHTDATA_API_KEY is not configured.")
    if not settings.brightdata_zone:
        raise ValueError("BRIGHTDATA_ZONE is not configured.")

    requested_pages = max(1, min(page_count, 1000))
    product_name = product_name_from_url(normalized_url)
    all_reviews: list[dict[str, str]] = []
    review_documents: list[ReviewDocument] = []
    pages_fetched = 0
    failures: list[str] = []
    stale_pages = 0
    attempts: list[dict[str, str]] = []

    for page in range(1, requested_pages + 1):
        page_url = with_page(normalized_url, page)
        if progress_callback:
            await progress_callback(
                {
                    "phase": "loading",
                    "provider": "brightdata",
                    "start_page": page,
                    "end_page": page,
                    "requested_pages": requested_pages,
                    "reviews_found": len(all_reviews),
                }
            )
        try:
            html = await _fetch_html(page_url, settings=settings)
        except Exception as exc:
            failures.append(f"page {page}: {exc}")
            logger.error("Bright Data page fetch failed: page=%s url=%s error=%s", page, page_url, exc)
            stale_pages += 1
            attempts.append(_attempt(page=page, status="error", message=str(exc) or repr(exc)))
            if progress_callback:
                await progress_callback(
                    {
                        "phase": "skipped",
                        "provider": "brightdata",
                        "page": page,
                        "requested_pages": requested_pages,
                        "reviews_found": len(all_reviews),
                        "error": str(exc),
                    }
                )
            if stale_pages >= 3:
                break
            continue

        diagnostics = extraction_diagnostics(html, page_url)
        logger.warning("Bright Data extraction diagnostics: page=%s diagnostics=%s", page, diagnostics)
        for idx, fragment in enumerate(review_card_debug_fragments(html), start=1):
            logger.warning("Bright Data review card HTML fragment: page=%s fragment=%s html=%s", page, idx, fragment)
        page_reviews, page_product_name = parse_review_html(html, page_url)
        if not page_reviews:
            logger.warning("Bright Data extraction returned zero reviews: %s", diagnostics)
            attempts.append(_attempt(page=page, status="no_reviews", message="No review HTML extracted."))
        else:
            attempts.append(_attempt(page=page, status="success", message=""))
        pages_fetched += 1
        product_name = page_product_name or product_name
        before = len(all_reviews)
        all_reviews.extend(page_reviews)
        all_reviews = dedupe_reviews(all_reviews)
        added = len(all_reviews) - before
        _log_page_reviews(page=page, page_url=page_url, page_reviews=page_reviews, added=added)
        if progress_callback:
            await progress_callback(
                {
                    "phase": "loaded",
                    "provider": "brightdata",
                    "page": page,
                    "requested_pages": requested_pages,
                    "page_reviews": len(page_reviews),
                    "reviews_found": len(all_reviews),
                }
            )
        if page_reviews and len(all_reviews) > before:
            stale_pages = 0
            continue
        stale_pages += 1
        if stale_pages >= 3:
            break

    if not all_reviews:
        raise ValueError(
            "Bright Data did not return extractable review HTML."
            f" Last error: {failures[-1] if failures else 'parser found zero reviews'}"
        )

    for review in all_reviews:
        document = _review_dict_to_document(review, source_url=normalized_url)
        if document is not None:
            review_documents.append(document)

    if not review_documents:
        raise ValueError("Bright Data returned review HTML, but no reviews could be parsed.")

    return ScrapeResult(
        source_url=normalized_url,
        final_url=normalized_url,
        provider="brightdata",
        reviews=review_documents,
        attempts=attempts,
    )


async def _fetch_with_brightdata_api(url: str, *, settings: Settings) -> FetchedPage:
    response: httpx.Response
    try:
        async with httpx.AsyncClient(timeout=settings.brightdata_timeout_seconds) as client:
            response = await client.post(
                settings.brightdata_api_url,
                headers={
                    "Authorization": f"Bearer {settings.brightdata_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": url,
                    "zone": settings.brightdata_zone,
                    "format": "raw",
                },
            )
    except httpx.TimeoutException as exc:
        logger.warning(
            "Bright Data API timeout: url=%s timeout_seconds=%s",
            url,
            settings.brightdata_timeout_seconds,
        )
        raise ValueError(f"Bright Data timed out while fetching {url}.") from exc
    except httpx.RequestError as exc:
        logger.warning("Bright Data API request failed: url=%s error=%s", url, exc)
        raise ValueError(f"Bright Data request failed for {url}: {exc}") from exc

    if response.status_code >= 400:
        logger.warning(
            "Bright Data API returned error: url=%s status=%s body=%s",
            url,
            response.status_code,
            response.text[:1000],
        )
        response.raise_for_status()

    html = _html_from_brightdata_response(response)
    _maybe_dump_html(url=url, html=html, settings=settings)
    return FetchedPage(
        url=url,
        final_url=url,
        status_code=response.status_code,
        html=html,
        provider="brightdata",
    )


async def _fetch_with_brightdata_proxy(url: str, *, settings: Settings) -> FetchedPage:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        proxy=settings.brightdata_proxy_url,
        timeout=settings.brightdata_timeout_seconds,
        verify=settings.brightdata_verify_ssl,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        _maybe_dump_html(url=url, html=response.text, settings=settings)
        return FetchedPage(
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            html=response.text,
            provider="brightdata",
        )


def _html_from_brightdata_response(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "")
    text = response.text
    if "application/json" not in content_type.lower() and not text.lstrip().startswith("{"):
        return text

    try:
        payload = response.json()
    except json.JSONDecodeError:
        return text

    body = payload.get("body") or payload.get("html") or payload.get("browserHtml")
    if isinstance(body, str) and body.strip():
        return body
    raise ValueError(f"Bright Data JSON response did not include HTML body. Keys: {sorted(payload)}")


def _maybe_dump_html(*, url: str, html: str, settings: Settings) -> None:
    if not settings.brightdata_debug_dump_html:
        return
    parsed = httpx.URL(url)
    slug = parsed.path.strip("/").replace("/", "_") or "page"
    output_dir = Path("debug") / "scrapes"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"brightdata_{slug}.html"
    output_path.write_text(html or "", encoding="utf-8", errors="ignore")
    logger.warning("Bright Data HTML dumped: url=%s path=%s", url, output_path)


async def _fetch_html(url: str, *, settings: Settings) -> str:
    page = await fetch_with_brightdata(url, settings=settings)
    return page.html


def _review_dict_to_document(review: dict[str, str], *, source_url: str) -> ReviewDocument | None:
    reviewed_at = review.get("date") or datetime.now(tz=UTC).isoformat()
    payload = {
        "author": review.get("author") or "Anonymous",
        "rating": review.get("rating"),
        "title": review.get("title") or None,
        "body": review.get("body") or "",
        "reviewed_at": reviewed_at,
        "source_url": source_url,
        "metadata": {"parser": "brightdata_html"},
        "raw": review,
    }
    try:
        return ReviewDocument.model_validate(payload)
    except Exception:
        return None


def _attempt(*, page: int, status: str, message: str) -> dict[str, str]:
    return {"page": str(page), "provider": "brightdata", "status": status, "message": message}


def _log_page_reviews(*, page: int, page_url: str, page_reviews: list[dict[str, str]], added: int) -> None:
    logger.warning(
        "Bright Data parsed page: page=%s url=%s parsed_reviews=%s unique_added=%s",
        page,
        page_url,
        len(page_reviews),
        added,
    )
    for idx, review in enumerate(page_reviews[:5], start=1):
        logger.warning(
            "Bright Data review sample: page=%s sample=%s title=%r rating=%r author=%r date=%r body=%r pros=%r cons=%r",
            page,
            idx,
            (review.get("title") or "")[:120],
            review.get("rating") or "",
            (review.get("author") or "")[:80],
            review.get("date") or "",
            (review.get("body") or "")[:220],
            (review.get("pros") or "")[:160],
            (review.get("cons") or "")[:160],
        )

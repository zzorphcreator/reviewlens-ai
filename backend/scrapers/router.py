from __future__ import annotations

import logging
import re
from pathlib import Path
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from backend.config import Settings, get_settings
from backend.scrapers.models import FetchedPage, ScrapeResult
from backend.scrapers.parsers.generic import parse_generic_reviews
from backend.scrapers.providers.apple_app_store import (
    is_apple_app_store_url,
    scrape_apple_app_store_reviews,
)
from backend.scrapers.providers.brightdata import fetch_with_brightdata, scrape_with_brightdata
from backend.scrapers.providers.google_play import is_google_play_url, scrape_google_play_reviews
from backend.scrapers.providers.http import fetch_html
from backend.scrapers.providers.zyte import fetch_with_zyte

FetchPage = Callable[[str], Awaitable[FetchedPage]]
ProgressCallback = Callable[[dict], Awaitable[None]]

logger = logging.getLogger(__name__)


class ScrapeError(ValueError):
    pass


async def scrape_url(
    url: str,
    *,
    page_count: int = 1,
    fetcher: FetchPage | None = None,
    settings: Settings | None = None,
    progress_callback: ProgressCallback | None = None,
) -> ScrapeResult:
    if fetcher is not None:
        return await _scrape_with_fetcher(
            url,
            fetcher,
            page_count=page_count,
            progress_callback=progress_callback,
        )

    settings = settings or get_settings()
    if is_apple_app_store_url(url):
        return await scrape_apple_app_store_reviews(
            url,
            page_count=page_count,
            settings=settings,
            progress_callback=progress_callback,
        )
    if is_google_play_url(url):
        return await scrape_google_play_reviews(
            url,
            page_count=page_count,
            progress_callback=progress_callback,
        )

    attempts: list[dict[str, str]] = []
    all_reviews = []
    successful_providers: list[str] = []
    final_url = url
    urls = page_urls(url, page_count)
    logger.debug(
        "Scrape started: url=%s pages=%s providers=%s",
        url,
        len(urls),
        settings.scraper_providers,
    )

    if settings.scraper_providers and settings.scraper_providers[0] == "brightdata":
        try:
            return await scrape_with_brightdata(
                url,
                page_count=page_count,
                settings=settings,
                progress_callback=progress_callback,
            )
        except Exception as exc:
            attempts.append(
                {
                    "page": "all",
                    "provider": "brightdata",
                    "status": "error",
                    "message": str(exc) or repr(exc),
                }
            )
            logger.warning("Bright Data scrape failed, falling back: url=%s error=%s", url, exc)
            settings = Settings(
                **{
                    **settings.model_dump(),
                    "scraper_provider_order": ",".join(
                        provider for provider in settings.scraper_providers if provider != "brightdata"
                    ),
                }
            )

    for page_number, page_url in enumerate(urls, start=1):
        page_result = await _scrape_single_page(
            page_url,
            providers=settings.scraper_providers,
            page_number=page_number,
            settings=settings,
            total_pages=len(urls),
            progress_callback=progress_callback,
        )
        attempts.extend(page_result.attempts)
        all_reviews.extend(page_result.reviews)
        final_url = page_result.final_url
        successful_providers.append(page_result.provider)

    if not all_reviews:
        summary = "; ".join(
            f"page {attempt.get('page', '?')} {attempt['provider']}={attempt['status']}: "
            f"{attempt['message']}"
            for attempt in attempts
        )
        raise ScrapeError(f"All scraper providers failed. {summary}")

    return ScrapeResult(
        source_url=url,
        final_url=final_url,
        provider=",".join(dict.fromkeys(successful_providers)),
        reviews=all_reviews,
        attempts=attempts,
    )


async def _scrape_single_page(
    url: str,
    *,
    providers: list[str],
    page_number: int,
    settings: Settings,
    total_pages: int,
    progress_callback: ProgressCallback | None = None,
) -> ScrapeResult:
    attempts: list[dict[str, str]] = []
    for provider in providers:
        try:
            if progress_callback:
                await progress_callback(
                    {
                        "progress_stage": "scraping",
                        "current_page": page_number,
                        "total_pages": total_pages,
                        "current_url": url,
                        "current_provider": provider,
                    }
                )
            logger.debug("Scrape attempt started: provider=%s page=%s url=%s", provider, page_number, url)
            page = await _fetch_with_provider(provider, url)
            if settings.scraper_debug_dump_html:
                _dump_html(page=page_number, provider=provider, url=url, html=page.html)
            logger.debug(
                "Scrape fetch completed: provider=%s page=%s status=%s final_url=%s html_bytes=%s preview=%s",
                provider,
                page_number,
                page.status_code,
                page.final_url,
                len(page.html or ""),
                _html_preview(page.html),
            )
            reviews = parse_generic_reviews(page.html, source_url=page.final_url)
            if reviews:
                sample = reviews[:2]
                logger.debug(
                    "Scrape parse completed: provider=%s page=%s reviews=%s sample_titles=%s",
                    provider,
                    page_number,
                    len(reviews),
                    [review.title for review in sample],
                )
            if not reviews:
                attempts.append(
                    _attempt(
                        page_number=page_number,
                        provider=provider,
                        status="no_reviews",
                        message="No schema.org review data found.",
                    )
                )
                continue
            attempts.append(
                _attempt(page_number=page_number, provider=provider, status="success", message="")
            )
            return ScrapeResult(
                source_url=url,
                final_url=page.final_url,
                provider=page.provider,
                reviews=reviews,
                attempts=attempts,
            )
        except Exception as exc:
            logger.warning(
                "Scrape attempt failed: provider=%s page=%s url=%s error=%s",
                provider,
                page_number,
                url,
                exc,
            )
            attempts.append(
                _attempt(
                    page_number=page_number,
                    provider=provider,
                    status="error",
                    message=str(exc) or repr(exc),
                )
            )

    return ScrapeResult(
        source_url=url,
        final_url=url,
        provider="",
        reviews=[],
        attempts=attempts,
    )


async def _scrape_with_fetcher(
    url: str,
    fetcher: FetchPage,
    *,
    page_count: int,
    progress_callback: ProgressCallback | None = None,
) -> ScrapeResult:
    attempts: list[dict[str, str]] = []
    all_reviews = []
    final_url = url
    provider = "test"
    urls = page_urls(url, page_count)
    settings = get_settings()

    for page_number, page_url in enumerate(urls, start=1):
        if progress_callback:
            await progress_callback(
                {
                    "progress_stage": "scraping",
                    "current_page": page_number,
                    "total_pages": len(urls),
                    "current_url": page_url,
                    "current_provider": provider,
                }
            )
        page = await fetcher(page_url)
        final_url = page.final_url
        provider = page.provider
        if settings.scraper_debug_dump_html:
            _dump_html(page=page_number, provider=provider, url=page_url, html=page.html)
        logger.debug(
            "Scrape fetcher completed: provider=%s page=%s status=%s final_url=%s html_bytes=%s preview=%s",
            provider,
            page_number,
            page.status_code,
            page.final_url,
            len(page.html or ""),
            _html_preview(page.html),
        )
        reviews = parse_generic_reviews(page.html, source_url=page.final_url)
        if reviews:
            sample = reviews[:2]
            logger.debug(
                "Scrape fetcher parse completed: provider=%s page=%s reviews=%s sample_titles=%s",
                provider,
                page_number,
                len(reviews),
                [review.title for review in sample],
            )
        if not reviews:
            attempts.append(
                _attempt(
                    page_number=page_number,
                    provider=page.provider,
                    status="no_reviews",
                    message="No schema.org review data found.",
                )
            )
            continue
        attempts.append(
            _attempt(page_number=page_number, provider=page.provider, status="success", message="")
        )
        all_reviews.extend(reviews)

    if not all_reviews:
        raise ScrapeError("No schema.org review data found on any requested page.")

    return ScrapeResult(
        source_url=url,
        final_url=final_url,
        provider=provider,
        reviews=all_reviews,
        attempts=attempts,
    )


async def _fetch_with_provider(provider: str, url: str) -> FetchedPage:
    if provider == "http":
        return await fetch_html(url)
    if provider == "brightdata":
        return await fetch_with_brightdata(url)
    if provider == "zyte":
        return await fetch_with_zyte(url)
    raise ValueError(f"Unknown scraper provider '{provider}'.")


def page_urls(url: str, page_count: int) -> list[str]:
    count = max(1, min(page_count, 50))
    if count == 1:
        return [url]

    urls = [url]
    parsed = urlparse(url)
    for page_number in range(2, count + 1):
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["page"] = str(page_number)
        urls.append(
            urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    urlencode(query),
                    parsed.fragment,
                )
            )
        )
    return urls


def _attempt(*, page_number: int, provider: str, status: str, message: str) -> dict[str, str]:
    return {
        "page": str(page_number),
        "provider": provider,
        "status": status,
        "message": message,
    }


def _html_preview(html: str | None) -> str:
    if not html:
        return ""
    preview = re.sub(r"\s+", " ", html).strip()
    return preview[:500]


def _dump_html(*, page: int, provider: str, url: str, html: str) -> None:
    output_dir = Path("debug") / "scrapes"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_provider = re.sub(r"[^a-z0-9]+", "_", provider.lower()).strip("_")
    filename = f"{safe_provider}_page_{page}.html"
    output_path = output_dir / filename
    output_path.write_text(html or "", encoding="utf-8", errors="ignore")
    logger.debug("Scrape HTML dumped: page=%s provider=%s url=%s path=%s", page, provider, url, output_path)

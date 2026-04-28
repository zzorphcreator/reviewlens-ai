import logging
import json
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse
from bs4 import BeautifulSoup

import httpx

from app.config import PROJECT_ROOT, Settings
from app.core.url_validation import validate_review_url
from app.scraping.models import ReviewScrapeResult, ReviewScraperError
from app.scraping.parsers.review_html import parse_review_html
from app.scraping.utils.pagination import with_page
from app.scraping.utils.platform_detection import platform_from_url, product_name_from_url
from app.scraping.local_scraper import (
    dedupe_reviews,
    extraction_diagnostics,
    review_card_debug_fragments,
)

logger = logging.getLogger(__name__)


class BrightDataFetcher:
    """Legacy Bright Data provider implementation used by the canonical BrightDataScraper wrapper."""

    batch_size = 3

    def __init__(self, settings: Settings):
        self.settings = settings

    async def scrape(
        self,
        url: str,
        max_pages: int = 5,
        progress_callback: Callable[[dict], Awaitable[None]] | None = None,
    ) -> ReviewScrapeResult:
        normalized_url = validate_review_url(url)
        if not self.settings.brightdata_api_key:
            raise ReviewScraperError("BRIGHTDATA_API_KEY is not configured.")
        if not self.settings.brightdata_zone:
            raise ReviewScraperError("BRIGHTDATA_ZONE is not configured.")

        requested_pages = max(1, min(max_pages, 1000))
        product_name = product_name_from_url(normalized_url)
        all_reviews: list[dict[str, str]] = []
        pages_fetched = 0
        failures: list[str] = []
        stale_pages = 0

        for page in range(1, requested_pages + 1):
            page_url = with_page(normalized_url, page)
            await self._emit_progress(
                progress_callback,
                {
                    "phase": "loading",
                    "provider": "brightdata",
                    "start_page": page,
                    "end_page": page,
                    "requested_pages": requested_pages,
                    "reviews_found": len(all_reviews),
                },
            )
            try:
                html = await self.fetch_html(page_url)
                self._dump_html(page=page, page_url=page_url, html=html)
            except ReviewScraperError as exc:
                failures.append(f"page {page}: {exc}")
                logger.error("Bright Data page fetch failed: page=%s url=%s error=%s", page, page_url, exc)
                stale_pages += 1
                await self._emit_progress(
                    progress_callback,
                    {
                        "phase": "skipped",
                        "provider": "brightdata",
                        "page": page,
                        "requested_pages": requested_pages,
                        "reviews_found": len(all_reviews),
                        "error": str(exc),
                    },
                )
                if stale_pages >= 3:
                    break
                continue

            diagnostics = extraction_diagnostics(html, page_url)
            logger.warning("Bright Data extraction diagnostics: page=%s diagnostics=%s", page, diagnostics)
            logger.warning("Bright Data pagination current page: requested=%s actual=%s", page, self._current_page_number(html))
            for idx, fragment in enumerate(review_card_debug_fragments(html), start=1):
                logger.warning("Bright Data review card HTML fragment: page=%s fragment=%s html=%s", page, idx, fragment)
            page_reviews, page_product_name = parse_review_html(html, page_url)
            if not page_reviews:
                logger.warning("Bright Data extraction returned zero reviews: %s", diagnostics)
            pages_fetched += 1
            product_name = page_product_name or product_name
            before = len(all_reviews)
            all_reviews.extend(page_reviews)
            all_reviews = dedupe_reviews(all_reviews)
            added = len(all_reviews) - before
            self._log_page_reviews(page=page, page_url=page_url, page_reviews=page_reviews, added=added)
            await self._emit_progress(
                progress_callback,
                {
                    "phase": "loaded",
                    "provider": "brightdata",
                    "page": page,
                    "requested_pages": requested_pages,
                    "page_reviews": len(page_reviews),
                    "reviews_found": len(all_reviews),
                },
            )
            if page_reviews and len(all_reviews) > before:
                stale_pages = 0
                continue
            stale_pages += 1
            if stale_pages >= 3:
                break

        if not all_reviews:
            raise ReviewScraperError(
                "Bright Data did not return extractable review HTML."
                f" Last error: {failures[-1] if failures else 'parser found zero reviews'}"
            )

        logger.info(
            "Bright Data scrape completed: url=%s requested_pages=%s pages_fetched=%s reviews=%s",
            normalized_url,
            requested_pages,
            pages_fetched,
            len(all_reviews),
        )
        return ReviewScrapeResult(
            source_url=normalized_url,
            platform=platform_from_url(normalized_url),
            product_name=product_name,
            reviews=all_reviews,
            pages_fetched=pages_fetched,
        )

    async def fetch_html(self, url: str) -> str:
        payload = {
            "url": url,
            "format": "raw",
        }
        payload["zone"] = self.settings.brightdata_zone

        headers = {
            "Authorization": f"Bearer {self.settings.brightdata_api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.brightdata_timeout_seconds) as client:
                response = await client.post(self.settings.brightdata_api_url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            logger.warning(
                "Bright Data API timeout: url=%s timeout_seconds=%s",
                url,
                self.settings.brightdata_timeout_seconds,
            )
            raise ReviewScraperError(f"Bright Data timed out while fetching {url}.") from exc
        except httpx.RequestError as exc:
            logger.warning("Bright Data API request failed: url=%s error=%s", url, exc)
            raise ReviewScraperError(f"Bright Data request failed for {url}: {exc}") from exc

        if response.status_code >= 400:
            logger.error(
                "Bright Data API returned error: url=%s status=%s body=%s",
                url,
                response.status_code,
                response.text[:1000],
            )
            raise ReviewScraperError(f"Bright Data returned HTTP {response.status_code}: {response.text[:500]}")

        logger.warning(
            "Bright Data response received: url=%s status=%s content_type=%s bytes=%s",
            url,
            response.status_code,
            response.headers.get("content-type", ""),
            len(response.content),
        )
        return self._html_from_response(response)

    async def _emit_progress(
        self,
        progress_callback: Callable[[dict], Awaitable[None]] | None,
        payload: dict,
    ) -> None:
        if progress_callback:
            await progress_callback(payload)

    def _html_from_response(self, response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "")
        text = response.text
        if "application/json" not in content_type.lower() and not text.lstrip().startswith("{"):
            self._log_response_preview(text)
            return text

        try:
            payload = response.json()
        except json.JSONDecodeError:
            self._log_response_preview(text)
            return text

        logger.warning("Bright Data JSON response keys: keys=%s", sorted(payload.keys()))
        body = payload.get("body") or payload.get("html") or payload.get("browserHtml")
        if isinstance(body, str) and body.strip():
            self._log_response_preview(body)
            return body
        raise ReviewScraperError(f"Bright Data JSON response did not include HTML body. Keys: {sorted(payload.keys())}")

    def _log_response_preview(self, html: str) -> None:
        preview = " ".join(html[:5000].split())
        logger.warning("Bright Data raw response preview: %s", preview[:2000])

    def _dump_html(self, *, page: int, page_url: str, html: str) -> None:
        if not self.settings.brightdata_debug_dump_html:
            return
        parsed = urlparse(page_url)
        slug = parsed.path.strip("/").replace("/", "_") or "page"
        output_dir = PROJECT_ROOT / "backend" / "app" / "data" / "debug"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"brightdata_{slug}_page_{page}.html"
        output_path.write_text(html, encoding="utf-8", errors="ignore")
        logger.warning("Bright Data HTML dumped: page=%s path=%s url=%s", page, output_path, page_url)

    def _log_page_reviews(
        self,
        *,
        page: int,
        page_url: str,
        page_reviews: list[dict[str, str]],
        added: int,
    ) -> None:
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

    def _current_page_number(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        return soup.select_one(".pagination__page-number--current").get_text(" ", strip=True) if soup.select_one(".pagination__page-number--current") else ""

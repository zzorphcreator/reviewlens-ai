import base64
import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx

from app.config import Settings
from app.core.url_validation import validate_review_url
from app.scraping.models import ReviewScrapeResult, ReviewScraperError
from app.scraping.parsers.review_html import parse_review_html
from app.scraping.utils.pagination import with_page
from app.scraping.utils.platform_detection import platform_from_url, product_name_from_url
from app.scraping.local_scraper import (
    dedupe_reviews,
    extraction_diagnostics,
)

logger = logging.getLogger(__name__)


class ZyteWebsiteBanError(ReviewScraperError):
    """Raised when Zyte reports an upstream website ban before ReviewLens receives parseable HTML."""

    pass


class ZyteFetcher:
    """Legacy Zyte provider implementation used by the canonical ZyteScraper wrapper."""

    batch_size = 8

    def __init__(self, settings: Settings):
        self.settings = settings

    async def scrape(
        self,
        url: str,
        max_pages: int = 5,
        progress_callback: Callable[[dict], Awaitable[None]] | None = None,
    ) -> ReviewScrapeResult:
        normalized_url = validate_review_url(url)
        if not self.settings.zyte_api_key:
            raise ReviewScraperError("ZYTE_API_KEY is not configured. Set URL_FETCH_PROVIDER=local or add a Zyte API key.")

        all_reviews: list[dict[str, str]] = []
        product_name = product_name_from_url(normalized_url)
        pages_fetched = 0
        stale_pages = 0
        page_failures: list[str] = []
        requested_pages = max(1, min(max_pages, 1000))
        batch_size = self._batch_size_for_url(normalized_url)
        stop = False
        for batch_start in range(1, requested_pages + 1, batch_size):
            batch_end = min(batch_start + batch_size, requested_pages + 1)
            await self._emit_progress(
                progress_callback,
                {
                    "phase": "loading",
                    "start_page": batch_start,
                    "end_page": batch_end - 1,
                    "requested_pages": requested_pages,
                    "reviews_found": len(all_reviews),
                },
            )
            page_urls = [with_page(normalized_url, page) for page in range(batch_start, batch_end)]
            pages = await asyncio.gather(
                *(self._fetch_page(page, page_url) for page, page_url in zip(range(batch_start, batch_end), page_urls))
            )

            for page, page_url, html, failure in pages:
                if failure:
                    page_failures.append(f"page {page}: {failure}")
                if not html:
                    stale_pages += 1
                    logger.error("Zyte page fetch failed: page=%s url=%s error=%s", page, page_url, failure or "empty html")
                    await self._emit_progress(
                        progress_callback,
                        {
                            "phase": "skipped",
                            "page": page,
                            "requested_pages": requested_pages,
                            "reviews_found": len(all_reviews),
                            "error": failure or "empty html",
                        },
                    )
                    if stale_pages >= 3:
                        stop = True
                        break
                    continue

                page_reviews, page_product_name = parse_review_html(html, page_url)
                if not page_reviews:
                    logger.warning("Review extraction returned zero reviews: %s", extraction_diagnostics(html, page_url))
                pages_fetched += 1
                product_name = page_product_name or product_name
                before = len(all_reviews)
                all_reviews.extend(page_reviews)
                all_reviews = dedupe_reviews(all_reviews)
                await self._emit_progress(
                    progress_callback,
                    {
                        "phase": "loaded",
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
                    stop = True
                    break

            if stop:
                break

        reviews = all_reviews
        if not reviews:
            logger.error(
                "Zyte scrape failed to extract reviews: url=%s requested_pages=%s pages_fetched=%s failures=%s",
                normalized_url,
                requested_pages,
                pages_fetched,
                page_failures[:5],
            )
            failure_detail = f" Last fetch error: {page_failures[-1]}" if page_failures else ""
            raise ReviewScraperError(
                "Zyte did not return extractable review HTML."
                f"{failure_detail} Check server logs for per-page Zyte fetch diagnostics."
            )

        logger.info(
            "Zyte scrape completed: url=%s requested_pages=%s pages_fetched=%s reviews=%s",
            normalized_url,
            requested_pages,
            pages_fetched,
            len(reviews),
        )

        return ReviewScrapeResult(
            source_url=normalized_url,
            platform=platform_from_url(normalized_url),
            product_name=product_name,
            reviews=reviews,
            pages_fetched=pages_fetched,
        )

    async def _fetch_page(self, page: int, url: str) -> tuple[int, str, str | None, str | None]:
        try:
            return page, url, await self.fetch_html(url), None
        except ReviewScraperError as exc:
            return page, url, None, str(exc)

    def _batch_size_for_url(self, url: str) -> int:
        if "g2.com" in url:
            return max(1, min(self.settings.zyte_g2_batch_size, 3))
        return self.batch_size

    async def _emit_progress(
        self,
        progress_callback: Callable[[dict], Awaitable[None]] | None,
        payload: dict,
    ) -> None:
        if progress_callback:
            await progress_callback(payload)

    def result_from_payload(self, payload: dict) -> ReviewScrapeResult:
        return ReviewScrapeResult(
            source_url=payload["source_url"],
            platform=payload["platform"],
            product_name=payload["product_name"],
            reviews=payload["reviews"],
            pages_fetched=int(payload.get("pages_fetched") or 1),
        )

    async def fetch_html(self, url: str) -> str:
        auth = base64.b64encode(f"{self.settings.zyte_api_key}:".encode("utf-8")).decode("ascii")
        payloads = self._payloads_for_url(url)
        timeout_seconds = max(self.settings.zyte_timeout_seconds, self.settings.g2_scraper_timeout_seconds + 15, 60)
        response = None
        last_error = ""
        for payload in payloads:
            mode = "browserHtml" if payload.get("browserHtml") else "httpResponseBody"
            for attempt in range(2):
                try:
                    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                        response = await client.post(
                            self.settings.zyte_api_url,
                            json=payload,
                            headers={
                                "Authorization": f"Basic {auth}",
                                "Content-Type": "application/json",
                            },
                        )
                    break
                except httpx.TimeoutException as exc:
                    last_error = f"timeout in {mode} mode"
                    logger.warning(
                        "Zyte API timeout: url=%s mode=%s attempt=%s timeout_seconds=%s",
                        url,
                        mode,
                        attempt + 1,
                        timeout_seconds,
                    )
                    if attempt == 1:
                        response = None
                        break
                    await asyncio.sleep(1.5 * (attempt + 1))
                except httpx.RequestError as exc:
                    last_error = f"request error in {mode} mode: {exc}"
                    logger.warning("Zyte API request failed: url=%s mode=%s attempt=%s error=%s", url, mode, attempt + 1, exc)
                    if attempt == 1:
                        response = None
                        break
                    await asyncio.sleep(1.5 * (attempt + 1))

            if response is not None and response.status_code < 400:
                break

        if response is None:
            raise ReviewScraperError(f"Zyte API did not return a response for {url}: {last_error or 'unknown error'}.")

        if response.status_code >= 400:
            logger.error(
                "Zyte API returned error: url=%s status=%s body=%s",
                url,
                response.status_code,
                response.text[:1000],
            )
            if response.status_code == 520 and "Website Ban" in response.text:
                raise ZyteWebsiteBanError(
                    f"G2 blocked Zyte API for {url}. Zyte returned Website Ban 520 before ReviewLens could parse reviews."
                )
            raise ReviewScraperError(f"Zyte API returned HTTP {response.status_code}: {response.text[:500]}")

        data = response.json()
        logger.info(
            "Zyte response received: url=%s status=%s keys=%s",
            url,
            response.status_code,
            sorted(data.keys()),
        )
        html = data.get("browserHtml") or data.get("httpResponseBody")
        if not html:
            raise ReviewScraperError("Zyte API response did not include browserHtml.")

        if data.get("httpResponseBody") and not data.get("browserHtml"):
            try:
                return base64.b64decode(html).decode("utf-8", errors="replace")
            except Exception:
                pass
        return html

    def _payloads_for_url(self, url: str) -> list[dict]:
        if "g2.com" in url:
            return [
                {"url": url, "httpResponseBody": True},
                {"url": url, "browserHtml": self.settings.zyte_browser_html},
            ]
        return [{"url": url, "browserHtml": self.settings.zyte_browser_html}]

from __future__ import annotations

import httpx

from backend.scrapers.models import FetchedPage


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36 ReviewLensAI/0.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


async def fetch_html(url: str, *, timeout_seconds: float = 20.0) -> FetchedPage:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=timeout_seconds,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            raise ValueError(f"Expected HTML response, got '{content_type or 'unknown'}'.")
        return FetchedPage(
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            html=response.text,
            provider="http",
        )

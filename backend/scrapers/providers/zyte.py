from __future__ import annotations

import asyncio
import base64

import httpx

from backend.config import Settings, get_settings
from backend.scrapers.models import FetchedPage


class ZyteWebsiteBanError(ValueError):
    pass


async def fetch_with_zyte(
    url: str,
    *,
    settings: Settings | None = None,
) -> FetchedPage:
    settings = settings or get_settings()
    if not settings.zyte_api_key:
        raise ValueError("Zyte is not configured. Set ZYTE_API_KEY.")

    response = await _post_zyte_with_fallbacks(url, settings=settings)
    payload = response.json()
    html = _html_from_zyte_payload(payload)
    return FetchedPage(
        url=url,
        final_url=payload.get("url") or url,
        status_code=response.status_code,
        html=html,
        provider="zyte",
    )


async def _post_zyte_with_fallbacks(url: str, *, settings: Settings) -> httpx.Response:
    timeout_seconds = max(settings.zyte_timeout_seconds, settings.scraper_timeout_seconds)
    auth = base64.b64encode(f"{settings.zyte_api_key}:".encode("utf-8")).decode("ascii")
    last_error = ""
    response: httpx.Response | None = None

    for payload in _payloads_for_url(url, settings=settings):
        mode = "browserHtml" if payload.get("browserHtml") else "httpResponseBody"
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                    response = await client.post(
                        settings.zyte_api_url,
                        json=payload,
                        headers={
                            "Authorization": f"Basic {auth}",
                            "Content-Type": "application/json",
                        },
                    )
                break
            except httpx.TimeoutException:
                last_error = f"timeout in {mode} mode"
            except httpx.RequestError as exc:
                last_error = f"request error in {mode} mode: {exc}"

            if attempt == 0:
                await asyncio.sleep(1.5)

        if response is not None and response.status_code < 400:
            return response

    if response is None:
        raise ValueError(f"Zyte API did not return a response for {url}: {last_error or 'unknown error'}.")

    if response.status_code == 520 and "Website Ban" in response.text:
        raise ZyteWebsiteBanError(
            f"Zyte reported Website Ban 520 for {url} before ReviewLens could parse reviews."
        )
    response.raise_for_status()
    return response


def _payloads_for_url(url: str, *, settings: Settings) -> list[dict]:
    if "g2.com" in url:
        return [
            {"url": url, "httpResponseBody": True},
            {"url": url, "browserHtml": settings.zyte_browser_html},
        ]
    return [{"url": url, "browserHtml": settings.zyte_browser_html}]


def _html_from_zyte_payload(payload: dict) -> str:
    if payload.get("browserHtml"):
        return payload["browserHtml"]

    if payload.get("httpResponseBody"):
        try:
            return base64.b64decode(payload["httpResponseBody"]).decode("utf-8", errors="replace")
        except Exception as exc:
            raise ValueError("Zyte httpResponseBody was not valid base64 HTML.") from exc

    raise ValueError("Zyte API response did not include browserHtml or httpResponseBody.")

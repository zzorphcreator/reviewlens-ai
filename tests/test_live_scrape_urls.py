import asyncio
import logging
import os
from pathlib import Path

import pytest

from backend.config import get_settings
from backend.scrapers.router import scrape_url


def _skip_if_missing_provider_creds() -> None:
    _load_test_env()
    _configure_debug_logging()
    settings = get_settings()
    providers = settings.scraper_providers
    missing: list[str] = []
    if "brightdata" in providers:
        if not _has_brightdata_creds(settings):
            missing.append("BRIGHTDATA_API_KEY/BRIGHTDATA_ZONE or BRIGHTDATA_PROXY_URL")
    if "zyte" in providers and not settings.zyte_api_key:
        missing.append("ZYTE_API_KEY")
    if missing:
        pytest.skip(f"Missing provider credentials: {', '.join(missing)}")


def _has_brightdata_creds(settings) -> bool:
    if settings.brightdata_api_key and settings.brightdata_zone:
        return True
    if settings.brightdata_proxy_url:
        return True
    return False


def _load_test_env() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if line.lower().startswith("set "):
            line = line[4:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

    get_settings.cache_clear()


def _configure_debug_logging() -> None:
    if logging.getLogger().handlers:
        logging.getLogger().setLevel(logging.DEBUG)
        return
    logging.basicConfig(level=logging.DEBUG)


async def _assert_live_reviews(url: str) -> None:
    _skip_if_missing_provider_creds()
    result = await scrape_url(url, page_count=1)
    assert result.reviews, f"No reviews returned for {url} using {result.provider}"


@pytest.mark.integration
def test_live_scrape_g2() -> None:
    asyncio.run(_assert_live_reviews("https://www.g2.com/products/slack/reviews"))


@pytest.mark.integration
def test_live_scrape_capterra() -> None:
    asyncio.run(_assert_live_reviews("https://www.capterra.com/p/135003/Slack/reviews"))


@pytest.mark.integration
def test_live_scrape_trustradius() -> None:
    asyncio.run(_assert_live_reviews("https://www.trustradius.com/products/slack/reviews"))


@pytest.mark.integration
def test_live_scrape_producthunt() -> None:
    asyncio.run(_assert_live_reviews("https://www.producthunt.com/products/slack/reviews"))


@pytest.mark.integration
def test_live_scrape_shopify() -> None:
    asyncio.run(_assert_live_reviews("https://apps.shopify.com/klaviyo-email-marketing/reviews"))


@pytest.mark.integration
def test_live_scrape_yelp() -> None:
    asyncio.run(_assert_live_reviews("https://www.yelp.com/biz/athenian-fresh-grill-north-arlington"))


@pytest.mark.integration
def test_live_scrape_trustpilot() -> None:
    asyncio.run(_assert_live_reviews("https://www.trustpilot.com/review/slack.com"))


@pytest.mark.integration
def test_live_scrape_tripadvisor() -> None:
    asyncio.run(
        _assert_live_reviews(
            "https://www.tripadvisor.com/Hotel_Review-g60763-d93562-Reviews-The_New_Yorker_A_Wyndham_Hotel-New_York_City_New_York.html"
        )
    )


@pytest.mark.integration
def test_live_scrape_booking() -> None:
    asyncio.run(_assert_live_reviews("https://www.booking.com/hotel/us/the-new-yorker.html"))


@pytest.mark.integration
def test_live_scrape_expedia() -> None:
    asyncio.run(
        _assert_live_reviews(
            "https://www.expedia.com/New-York-Hotels-The-New-Yorker-A-Wyndham-Hotel.h8903.Hotel-Information"
        )
    )


@pytest.mark.integration
def test_live_scrape_glassdoor() -> None:
    asyncio.run(_assert_live_reviews("https://www.glassdoor.com/Reviews/Slack-Reviews-E950758.htm"))


@pytest.mark.integration
def test_live_scrape_google_play() -> None:
    asyncio.run(_assert_live_reviews("https://play.google.com/store/apps/details?id=com.Slack"))


@pytest.mark.integration
def test_live_scrape_app_store() -> None:
    asyncio.run(_assert_live_reviews("https://apps.apple.com/us/app/slack-for-desktop/id803453959"))


@pytest.mark.integration
def test_live_scrape_amazon() -> None:
    asyncio.run(_assert_live_reviews("https://www.amazon.com/dp/B08N5WRWNW"))


@pytest.mark.integration
def test_live_scrape_google_maps() -> None:
    asyncio.run(
        _assert_live_reviews(
            "https://www.google.com/maps/place/Athenian+Fresh+Grill/@40.7919966,-74.1356226,17z/"
        )
    )

import asyncio
import base64
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from backend.config import Settings, get_settings
from backend.scrapers.models import FetchedPage
from backend.scrapers.parsers.generic import parse_generic_reviews
from backend.scrapers.providers.apple_app_store import (
    apple_review_feed_urls,
    parse_apple_review_feed,
)
from backend.scrapers.providers.brightdata import fetch_with_brightdata
from backend.scrapers.providers.brightdata import scrape_with_brightdata
from backend.scrapers.providers.google_play import (
    google_play_app_id,
    google_play_country,
    google_play_language,
    parse_google_play_reviews,
)
from backend.scrapers.providers.zyte import fetch_with_zyte
from backend.scrapers.router import page_urls, scrape_url


FIXTURES = Path(__file__).parent / "fixtures" / "html"


def test_parse_generic_jsonld_reviews() -> None:
    html = (FIXTURES / "schema_reviews.html").read_text(encoding="utf-8")
    reviews = parse_generic_reviews(html, source_url="https://example.com/reviews")

    assert len(reviews) == 2
    assert reviews[0].author == "Ada Lovelace"
    assert reviews[0].rating == 5
    assert reviews[0].metadata == {"parser": "jsonld"}


def test_parse_generic_html_review_cards() -> None:
    html = """
    <html>
      <body>
        <article class="review-card">
          <h3>Fast team collaboration</h3>
          <span class="author">Grace Hopper</span>
          <time datetime="2026-04-21">April 21, 2026</time>
          <span aria-label="4.5 stars"></span>
          <p data-testid="review-body">
            Slack keeps our engineering conversations searchable and async-friendly.
          </p>
        </article>
      </body>
    </html>
    """

    reviews = parse_generic_reviews(html, source_url="https://example.com/reviews")

    assert len(reviews) == 1
    assert reviews[0].author == "Grace Hopper"
    assert reviews[0].rating == 4.5
    assert reviews[0].metadata == {"parser": "html"}


def test_parse_tripadvisor_review_cards() -> None:
    html = """
    <html>
      <body>
        <ul>
          <li>
            <div data-automation="bubbleRatingImage"><title>5 of 5 bubbles</title></div>
            <a href="/Profile/AdaTraveler">AdaTraveler</a>
            <a href="/ShowUserReviews">Excellent location</a>
            The hotel was clean, close to transit, and the staff helped us with every request.
            Date of stay: March 2026
            Read more
          </li>
        </ul>
      </body>
    </html>
    """

    reviews = parse_generic_reviews(html, source_url="https://www.tripadvisor.com/Hotel_Review-test")

    assert len(reviews) == 1
    assert reviews[0].author == "AdaTraveler"
    assert reviews[0].rating == 5
    assert reviews[0].metadata == {"parser": "tripadvisor"}


def test_parse_apple_app_store_rss_reviews() -> None:
    payload = {
        "feed": {
            "entry": [
                {
                    "author": {"name": {"label": "Grace"}},
                    "im:rating": {"label": "4"},
                    "title": {"label": "Useful app"},
                    "content": {"label": "The app is reliable and easy to use."},
                    "updated": {"label": "2026-04-27T12:30:00-07:00"},
                }
            ]
        }
    }

    reviews = parse_apple_review_feed(
        payload,
        source_url="https://apps.apple.com/us/app/example/id123456789",
    )

    assert len(reviews) == 1
    assert reviews[0].author == "Grace"
    assert reviews[0].rating == 4
    assert reviews[0].metadata == {"parser": "apple_app_store_rss"}


def test_apple_review_feed_urls_are_paginated() -> None:
    assert apple_review_feed_urls(app_id="123456789", country="us", page_count=2) == [
        "https://itunes.apple.com/us/rss/customerreviews/page=1/id=123456789/sortby=mostrecent/json",
        "https://itunes.apple.com/us/rss/customerreviews/page=2/id=123456789/sortby=mostrecent/json",
    ]


def test_parse_google_play_reviews() -> None:
    reviews = parse_google_play_reviews(
        [
            {
                "reviewId": "abc123",
                "userName": "Linus",
                "content": "The app is fast and stable.",
                "score": 5,
                "at": datetime(2026, 4, 27, 12, 30, tzinfo=timezone.utc),
                "thumbsUpCount": 12,
                "reviewCreatedVersion": "1.2.3",
            }
        ],
        source_url="https://play.google.com/store/apps/details?id=com.example.app",
    )

    assert len(reviews) == 1
    assert reviews[0].author == "Linus"
    assert reviews[0].rating == 5
    assert reviews[0].metadata["parser"] == "google_play_scraper"
    assert reviews[0].metadata["review_id"] == "abc123"
    assert reviews[0].raw["at"] == "2026-04-27T12:30:00+00:00"


def test_google_play_url_parts() -> None:
    url = "https://play.google.com/store/apps/details?id=com.example.app&hl=en_GB&gl=CA"

    assert google_play_app_id(url) == "com.example.app"
    assert google_play_language(url) == "en"
    assert google_play_country(url) == "ca"


def test_parse_generic_embedded_json_reviews() -> None:
    html = """
    <html>
      <head>
        <script id="__NEXT_DATA__" type="application/json">
          {
            "props": {
              "pageProps": {
                "reviews": [
                  {
                    "title": "Solid product",
                    "reviewText": "Capterra has a ton of automation features.",
                    "rating": 4.2,
                    "reviewerName": "Katherine Johnson",
                    "createdAt": "2024-02-01"
                  }
                ]
              }
            }
          }
        </script>
      </head>
    </html>
    """

    reviews = parse_generic_reviews(html, source_url="https://example.com/reviews")

    assert len(reviews) == 1
    assert reviews[0].author == "Katherine Johnson"
    assert reviews[0].rating == 4.2
    assert reviews[0].metadata == {"parser": "embedded_json"}


def test_parse_generic_embedded_json_script_assignment() -> None:
    html = """
    <html>
      <head>
        <script>
          window.__INITIAL_STATE__ = {
            "reviews": [
              {
                "title": "Great fit",
                "reviewText": "This product is solid for small teams.",
                "rating": {"score": 4.8},
                "reviewerName": "Ada Lovelace",
                "createdAt": "2024-03-05"
              }
            ]
          };
        </script>
      </head>
    </html>
    """

    reviews = parse_generic_reviews(html, source_url="https://example.com/reviews")

    assert len(reviews) == 1
    assert reviews[0].author == "Ada Lovelace"
    assert reviews[0].rating == 4.8
    assert reviews[0].metadata == {"parser": "embedded_json"}


def test_scrape_url_uses_injected_fetcher() -> None:
    asyncio.run(_test_scrape_url_uses_injected_fetcher())


async def _test_scrape_url_uses_injected_fetcher() -> None:
    html = (FIXTURES / "schema_reviews.html").read_text(encoding="utf-8")

    async def fake_fetcher(url: str) -> FetchedPage:
        return FetchedPage(url=url, final_url=url, status_code=200, html=html)

    result = await scrape_url("https://example.com/reviews", fetcher=fake_fetcher)

    assert result.final_url == "https://example.com/reviews"
    assert len(result.reviews) == 2


def test_page_urls_adds_page_query_parameter() -> None:
    assert page_urls("https://example.com/reviews?sort=recent", 3) == [
        "https://example.com/reviews?sort=recent",
        "https://example.com/reviews?sort=recent&page=2",
        "https://example.com/reviews?sort=recent&page=3",
    ]


def test_scrape_url_fetches_multiple_pages() -> None:
    asyncio.run(_test_scrape_url_fetches_multiple_pages())


async def _test_scrape_url_fetches_multiple_pages() -> None:
    html = (FIXTURES / "schema_reviews.html").read_text(encoding="utf-8")
    seen_urls = []

    async def fake_fetcher(url: str) -> FetchedPage:
        seen_urls.append(url)
        return FetchedPage(url=url, final_url=url, status_code=200, html=html)

    result = await scrape_url("https://example.com/reviews", page_count=2, fetcher=fake_fetcher)

    assert seen_urls == ["https://example.com/reviews", "https://example.com/reviews?page=2"]
    assert len(result.reviews) == 4
    assert [attempt["page"] for attempt in result.attempts] == ["1", "2"]


def test_scrape_url_falls_back_by_configured_provider_order(monkeypatch) -> None:
    asyncio.run(_test_scrape_url_falls_back_by_configured_provider_order(monkeypatch))


async def _test_scrape_url_falls_back_by_configured_provider_order(monkeypatch) -> None:
    html = (FIXTURES / "schema_reviews.html").read_text(encoding="utf-8")

    async def blocked_fetcher(url: str) -> FetchedPage:
        raise RuntimeError("blocked")

    async def managed_fetcher(url: str) -> FetchedPage:
        return FetchedPage(
            url=url,
            final_url=url,
            status_code=200,
            html=html,
            provider="brightdata",
        )

    monkeypatch.setattr("backend.scrapers.router.fetch_html", blocked_fetcher)
    monkeypatch.setattr("backend.scrapers.router.fetch_with_brightdata", managed_fetcher)

    result = await scrape_url(
        "https://example.com/reviews",
        settings=Settings(scraper_provider_order="http,brightdata"),
    )

    assert result.provider == "brightdata"
    assert [attempt["status"] for attempt in result.attempts] == ["error", "success"]
    assert len(result.reviews) == 2


def test_brightdata_api_fetcher_posts_to_request_endpoint(monkeypatch) -> None:
    asyncio.run(_test_brightdata_api_fetcher_posts_to_request_endpoint(monkeypatch))


async def _test_brightdata_api_fetcher_posts_to_request_endpoint(monkeypatch) -> None:
    html = (FIXTURES / "schema_reviews.html").read_text(encoding="utf-8")
    captured: dict = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url: str, *, headers: dict, json: dict):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return httpx.Response(
                200,
                json={"body": html},
                headers={"content-type": "application/json"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("backend.scrapers.providers.brightdata.httpx.AsyncClient", FakeAsyncClient)

    page = await fetch_with_brightdata(
        "https://example.com/reviews",
        settings=Settings(
            brightdata_api_key="test-key",
            brightdata_zone="test-zone",
            brightdata_api_url="https://api.brightdata.com/request",
        ),
    )

    assert captured["url"] == "https://api.brightdata.com/request"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"] == {
        "url": "https://example.com/reviews",
        "zone": "test-zone",
        "format": "raw",
    }
    assert page.html == html


def test_zyte_fetcher_prefers_browser_html(monkeypatch) -> None:
    asyncio.run(_test_zyte_fetcher_prefers_browser_html(monkeypatch))


async def _test_zyte_fetcher_prefers_browser_html(monkeypatch) -> None:
    captured: dict = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url: str, *, json: dict, headers: dict):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return httpx.Response(
                200,
                json={"browserHtml": "<html>ok</html>", "url": json["url"]},
                headers={"content-type": "application/json"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("backend.scrapers.providers.zyte.httpx.AsyncClient", FakeAsyncClient)

    page = await fetch_with_zyte(
        "https://example.com/reviews",
        settings=Settings(
            zyte_api_key="test-zyte",
            zyte_api_url="https://api.zyte.com/v1/extract",
            zyte_browser_html=True,
            zyte_timeout_seconds=60,
        ),
    )

    assert captured["url"] == "https://api.zyte.com/v1/extract"
    assert captured["json"] == {"url": "https://example.com/reviews", "browserHtml": True}
    assert page.html == "<html>ok</html>"


def test_zyte_fetcher_decodes_http_response_body(monkeypatch) -> None:
    asyncio.run(_test_zyte_fetcher_decodes_http_response_body(monkeypatch))


async def _test_zyte_fetcher_decodes_http_response_body(monkeypatch) -> None:
    captured: dict = {}
    encoded = base64.b64encode(b"<html>g2</html>").decode("ascii")

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url: str, *, json: dict, headers: dict):
            captured.setdefault("payloads", []).append(json)
            return httpx.Response(
                200,
                json={"httpResponseBody": encoded, "url": json["url"]},
                headers={"content-type": "application/json"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("backend.scrapers.providers.zyte.httpx.AsyncClient", FakeAsyncClient)

    page = await fetch_with_zyte(
        "https://g2.com/reviews",
        settings=Settings(
            zyte_api_key="test-zyte",
            zyte_api_url="https://api.zyte.com/v1/extract",
            zyte_browser_html=True,
            zyte_timeout_seconds=60,
        ),
    )

    assert captured["payloads"][0] == {"url": "https://g2.com/reviews", "httpResponseBody": True}
    assert page.html == "<html>g2</html>"


def test_brightdata_scrape_with_html_parser(monkeypatch) -> None:
    asyncio.run(_test_brightdata_scrape_with_html_parser(monkeypatch))


async def _test_brightdata_scrape_with_html_parser(monkeypatch) -> None:
    html = """
    <html>
      <body>
        <article class="review-card">
          <h3>Fast team collaboration</h3>
          <span class="author">Grace Hopper</span>
          <time datetime="2026-04-21">April 21, 2026</time>
          <span aria-label="4.5 stars"></span>
          <p data-testid="review-body">
            Slack keeps our engineering conversations searchable and async-friendly.
          </p>
        </article>
      </body>
    </html>
    """

    async def fake_fetch_html(url: str, *, settings):
        return html

    monkeypatch.setattr("backend.scrapers.providers.brightdata._fetch_html", fake_fetch_html)

    result = await scrape_with_brightdata(
        "https://www.capterra.com/p/135003/Slack/reviews",
        page_count=1,
        settings=Settings(
            brightdata_api_key="test-key",
            brightdata_zone="test-zone",
        ),
    )

    assert result.provider == "brightdata"
    assert len(result.reviews) == 1
    assert result.reviews[0].author == "Anonymous"
    assert result.reviews[0].metadata == {"parser": "brightdata_html"}


def test_scrape_url_uses_env_provider_order_g2(monkeypatch) -> None:
    asyncio.run(_assert_scrape_url_uses_env_provider_order(monkeypatch, "https://www.g2.com/products/slack/reviews"))


def test_scrape_url_uses_env_provider_order_capterra(monkeypatch) -> None:
    asyncio.run(
        _assert_scrape_url_uses_env_provider_order(monkeypatch, "https://www.capterra.com/p/135003/Slack/reviews")
    )


def test_scrape_url_uses_env_provider_order_trustradius(monkeypatch) -> None:
    asyncio.run(
        _assert_scrape_url_uses_env_provider_order(
            monkeypatch,
            "https://www.trustradius.com/products/slack/reviews",
        )
    )


def test_scrape_url_uses_env_provider_order_producthunt(monkeypatch) -> None:
    asyncio.run(
        _assert_scrape_url_uses_env_provider_order(
            monkeypatch,
            "https://www.producthunt.com/products/slack/reviews",
        )
    )


def test_scrape_url_uses_env_provider_order_shopify(monkeypatch) -> None:
    asyncio.run(
        _assert_scrape_url_uses_env_provider_order(
            monkeypatch,
            "https://apps.shopify.com/klaviyo-email-marketing/reviews",
        )
    )


def test_scrape_url_uses_env_provider_order_yelp(monkeypatch) -> None:
    asyncio.run(
        _assert_scrape_url_uses_env_provider_order(
            monkeypatch,
            "https://www.yelp.com/biz/athenian-fresh-grill-north-arlington",
        )
    )


def test_scrape_url_uses_env_provider_order_trustpilot(monkeypatch) -> None:
    asyncio.run(
        _assert_scrape_url_uses_env_provider_order(
            monkeypatch,
            "https://www.trustpilot.com/review/slack.com",
        )
    )


def test_scrape_url_uses_env_provider_order_tripadvisor(monkeypatch) -> None:
    asyncio.run(
        _assert_scrape_url_uses_env_provider_order(
            monkeypatch,
            "https://www.tripadvisor.com/Hotel_Review-g60763-d93562-Reviews-The_New_Yorker_A_Wyndham_Hotel-New_York_City_New_York.html",
        )
    )


def test_scrape_url_uses_env_provider_order_booking(monkeypatch) -> None:
    asyncio.run(
        _assert_scrape_url_uses_env_provider_order(
            monkeypatch,
            "https://www.booking.com/hotel/us/the-new-yorker.html",
        )
    )


def test_scrape_url_uses_env_provider_order_expedia(monkeypatch) -> None:
    asyncio.run(
        _assert_scrape_url_uses_env_provider_order(
            monkeypatch,
            "https://www.expedia.com/New-York-Hotels-The-New-Yorker-A-Wyndham-Hotel.h8903.Hotel-Information",
        )
    )


def test_scrape_url_uses_env_provider_order_glassdoor(monkeypatch) -> None:
    asyncio.run(
        _assert_scrape_url_uses_env_provider_order(
            monkeypatch,
            "https://www.glassdoor.com/Reviews/Slack-Reviews-E950758.htm",
        )
    )


def test_scrape_url_uses_env_provider_order_google_play(monkeypatch) -> None:
    asyncio.run(
        _assert_scrape_url_uses_env_provider_order(
            monkeypatch,
            "https://play.google.com/store/apps/details?id=com.Slack",
        )
    )


def test_scrape_url_uses_env_provider_order_app_store(monkeypatch) -> None:
    asyncio.run(
        _assert_scrape_url_uses_env_provider_order(
            monkeypatch,
            "https://apps.apple.com/us/app/slack-for-desktop/id803453959",
        )
    )


def test_scrape_url_uses_env_provider_order_amazon(monkeypatch) -> None:
    asyncio.run(_assert_scrape_url_uses_env_provider_order(monkeypatch, "https://www.amazon.com/dp/B08N5WRWNW"))


def test_scrape_url_uses_env_provider_order_google_maps(monkeypatch) -> None:
    asyncio.run(
        _assert_scrape_url_uses_env_provider_order(
            monkeypatch,
            "https://www.google.com/maps/place/Athenian+Fresh+Grill/@40.7919966,-74.1356226,17z/",
        )
    )


async def _assert_scrape_url_uses_env_provider_order(monkeypatch, url: str) -> None:
    html = (FIXTURES / "schema_reviews.html").read_text(encoding="utf-8")
    settings = get_settings()
    provider_order = settings.scraper_providers
    last_provider = provider_order[-1]
    calls: list[str] = []

    async def fake_http(url: str) -> FetchedPage:
        calls.append("http")
        if last_provider == "http":
            return FetchedPage(url=url, final_url=url, status_code=200, html=html, provider="http")
        raise RuntimeError("blocked")

    async def fake_brightdata(url: str) -> FetchedPage:
        calls.append("brightdata")
        if last_provider == "brightdata":
            return FetchedPage(url=url, final_url=url, status_code=200, html=html, provider="brightdata")
        raise RuntimeError("blocked")

    async def fake_zyte(url: str) -> FetchedPage:
        calls.append("zyte")
        if last_provider == "zyte":
            return FetchedPage(url=url, final_url=url, status_code=200, html=html, provider="zyte")
        raise RuntimeError("blocked")

    monkeypatch.setattr("backend.scrapers.router.fetch_html", fake_http)
    monkeypatch.setattr("backend.scrapers.router.fetch_with_brightdata", fake_brightdata)
    monkeypatch.setattr("backend.scrapers.router.fetch_with_zyte", fake_zyte)

    result = await scrape_url(url)

    assert calls == provider_order
    assert result.provider == last_provider

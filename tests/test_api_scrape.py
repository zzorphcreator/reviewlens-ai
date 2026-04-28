from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.scrapers.models import FetchedPage


FIXTURES = Path(__file__).parent / "fixtures" / "html"
pytestmark = pytest.mark.db


def test_ingest_url_endpoint_creates_job_and_reviews(monkeypatch: pytest.MonkeyPatch) -> None:
    html = (FIXTURES / "schema_reviews.html").read_text(encoding="utf-8")

    async def fake_fetch_html(url: str) -> FetchedPage:
        return FetchedPage(url=url, final_url=url, status_code=200, html=html)

    monkeypatch.setattr("backend.scrapers.router.fetch_html", fake_fetch_html)

    with TestClient(app) as client:
        response = client.post(
            "/api/ingest/url",
            json={
                "url": "https://example.com/reviews",
                "source_name": "Schema reviews",
                "page_count": 3,
            },
        )

        assert response.status_code == 202
        payload = response.json()
        job_id = payload["job"]["id"]
        source_id = payload["source"]["id"]

        job_response = client.get(f"/api/ingest/jobs/{job_id}")
        assert job_response.status_code == 200
        job = job_response.json()["job"]
        assert job["status"] == "done"
        assert job["stats"]["page_count"] == 3

        reviews_response = client.get(f"/api/reviews?source_id={source_id}")
        assert reviews_response.status_code == 200
        reviews = reviews_response.json()
        assert reviews["total"] == 2

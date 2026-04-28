from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app


FIXTURES = Path(__file__).parent / "fixtures" / "imports"
pytestmark = pytest.mark.db


def test_import_file_endpoint_creates_job_and_reviews() -> None:
    with TestClient(app) as client:
        with (FIXTURES / "reviews.json").open("rb") as handle:
            response = client.post(
                "/api/import/file",
                data={"source_name": "API fixture"},
                files={"file": ("reviews.json", handle, "application/json")},
            )

        assert response.status_code == 202
        payload = response.json()
        job_id = payload["job"]["id"]
        source_id = payload["source"]["id"]

        job_response = client.get(f"/api/ingest/jobs/{job_id}")
        assert job_response.status_code == 200
        assert job_response.json()["job"]["status"] == "done"

        reviews_response = client.get(f"/api/reviews?source_id={source_id}")
        assert reviews_response.status_code == 200
        reviews = reviews_response.json()
        assert reviews["total"] == 1
        assert reviews["items"][0]["author"] == "Katherine Johnson"

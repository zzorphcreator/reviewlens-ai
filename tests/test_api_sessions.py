from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app


FIXTURES = Path(__file__).parent / "fixtures" / "imports"
pytestmark = pytest.mark.db


def test_save_session_lists_session_and_accepts_chat_messages() -> None:
    with TestClient(app) as client:
        with (FIXTURES / "reviews.json").open("rb") as handle:
            import_response = client.post(
                "/api/import/file",
                data={"source_name": "Session fixture source"},
                files={"file": ("reviews.json", handle, "application/json")},
            )

        assert import_response.status_code == 202
        source_id = import_response.json()["source"]["id"]

        save_response = client.post(
            "/api/sessions",
            json={"name": "Saved fixture session", "source_ids": [source_id]},
        )
        assert save_response.status_code == 201
        saved = save_response.json()["session"]
        assert saved["name"] == "Saved fixture session"
        assert saved["source_ids"] == [source_id]
        assert saved["review_count"] == 1
        assert saved["message_count"] == 0

        message_response = client.post(
            f"/api/sessions/{saved['id']}/messages",
            json={"role": "user", "content": "What did reviewers like?"},
        )
        assert message_response.status_code == 201
        assert message_response.json()["message"]["role"] == "user"

        list_response = client.get("/api/sessions")
        assert list_response.status_code == 200
        sessions = list_response.json()["items"]
        assert any(item["id"] == saved["id"] and item["message_count"] == 1 for item in sessions)

        search_response = client.get(
            "/api/reviews",
            params={
                "session_id": saved["id"],
                "q": "normalized",
                "rating_min": 5,
            },
        )
        assert search_response.status_code == 200
        search_results = search_response.json()
        assert search_results["total"] == 1
        assert search_results["items"][0]["author"] == "Katherine Johnson"

        empty_response = client.get(
            "/api/reviews",
            params={
                "session_id": saved["id"],
                "q": "does-not-exist",
                "rating_min": 1,
            },
        )
        assert empty_response.status_code == 200
        assert empty_response.json()["total"] == 0


def test_save_session_rejects_unknown_source() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/sessions",
            json={"name": "Broken session", "source_ids": ["missing-source-id"]},
        )

    assert response.status_code == 400
    assert "Unknown source_id" in response.json()["detail"]

import asyncio
from pathlib import Path

import pytest

from backend.ingestion.file_processor import parse_review_file
from backend.storage.database import SessionLocal
from backend.storage.service import create_source_with_job, insert_reviews, list_reviews


FIXTURES = Path(__file__).parent / "fixtures" / "imports"
pytestmark = pytest.mark.db


def test_insert_reviews_deduplicates_per_source() -> None:
    asyncio.run(_test_insert_reviews_deduplicates_per_source())


async def _test_insert_reviews_deduplicates_per_source() -> None:
    result = parse_review_file(FIXTURES / "reviews.csv")

    async with SessionLocal() as session:
        source, _ = await create_source_with_job(session, name="Fixture", platform="file")
        first = await insert_reviews(session, source_id=source.id, reviews=result.reviews)
        second = await insert_reviews(session, source_id=source.id, reviews=result.reviews)
        reviews, total = await list_reviews(session, source_id=source.id)

    assert first == {"inserted": 2, "duplicates": 0}
    assert second == {"inserted": 0, "duplicates": 2}
    assert total == 2
    assert len(reviews) == 2

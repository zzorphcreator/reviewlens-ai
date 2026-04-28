from pathlib import Path

from backend.ingestion.file_processor import parse_review_file


FIXTURES = Path(__file__).parent / "fixtures" / "imports"


def test_parse_csv_reviews() -> None:
    result = parse_review_file(FIXTURES / "reviews.csv")

    assert result.accepted_count == 2
    assert result.rejected_count == 0
    assert result.reviews[0].author == "Ada Lovelace"
    assert result.reviews[0].metadata == {"plan": "pro"}


def test_parse_json_reviews() -> None:
    result = parse_review_file(FIXTURES / "reviews.json")

    assert result.accepted_count == 1
    assert result.reviews[0].title == "Great signal"


def test_parse_jsonl_reviews() -> None:
    result = parse_review_file(FIXTURES / "reviews.jsonl")

    assert result.accepted_count == 2
    assert result.reviews[1].rating == 3.5


def test_invalid_rows_return_row_level_errors() -> None:
    result = parse_review_file(FIXTURES / "invalid.csv")

    assert result.accepted_count == 0
    assert result.rejected_count >= 1
    assert any(error.field == "unexpected" for error in result.errors)

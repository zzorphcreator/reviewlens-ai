from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from io import StringIO
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend.ingestion.models import ImportResult, ImportValidationError, ReviewDocument


REQUIRED_FIELDS = {"author", "rating", "body", "reviewed_at"}
OPTIONAL_FIELDS = {"title", "source_url", "metadata"}
ALLOWED_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS
SUPPORTED_EXTENSIONS = {".csv", ".json", ".jsonl", ".ndjson"}


class UnsupportedImportFormat(ValueError):
    pass


def parse_review_file(path: Path) -> ImportResult:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedImportFormat(
            f"Unsupported file type '{suffix}'. Use CSV, JSON, JSONL, or NDJSON."
        )

    content = path.read_text(encoding="utf-8-sig")
    if suffix == ".csv":
        rows = _read_csv(content)
    elif suffix == ".json":
        rows = _read_json(content)
    else:
        rows = _read_jsonl(content)

    return normalize_rows(rows)


def normalize_rows(rows: Iterable[dict[str, Any]]) -> ImportResult:
    reviews: list[ReviewDocument] = []
    errors: list[ImportValidationError] = []

    for index, row in enumerate(rows, start=1):
        row_errors = _validate_columns(row, index)
        if row_errors:
            errors.extend(row_errors)
            continue

        payload = {key: row.get(key) for key in ALLOWED_FIELDS if key in row}
        metadata = payload.get("metadata")
        if isinstance(metadata, str):
            try:
                payload["metadata"] = json.loads(metadata) if metadata.strip() else {}
            except json.JSONDecodeError as exc:
                errors.append(
                    ImportValidationError(row=index, field="metadata", message=f"Invalid JSON: {exc}")
                )
                continue

        try:
            review = ReviewDocument.model_validate({**payload, "raw": row})
        except ValidationError as exc:
            errors.extend(_pydantic_errors(index, exc))
            continue

        reviews.append(review)

    return ImportResult(reviews=reviews, errors=errors)


def _read_csv(content: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(StringIO(content))
    if not reader.fieldnames:
        return []
    return [dict(row) for row in reader]


def _read_json(content: str) -> list[dict[str, Any]]:
    parsed = json.loads(content)
    if isinstance(parsed, dict) and isinstance(parsed.get("reviews"), list):
        parsed = parsed["reviews"]
    if not isinstance(parsed, list):
        raise ValueError("JSON imports must be an array or an object with a 'reviews' array.")
    if not all(isinstance(row, dict) for row in parsed):
        raise ValueError("Every JSON review row must be an object.")
    return parsed


def _read_jsonl(content: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            raise ValueError(f"JSONL line {line_number} must be an object.")
        rows.append(parsed)
    return rows


def _validate_columns(row: dict[str, Any], row_index: int) -> list[ImportValidationError]:
    keys = {str(key).strip() for key in row.keys()}
    missing = REQUIRED_FIELDS - keys
    unknown = keys - ALLOWED_FIELDS
    errors = [
        ImportValidationError(row=row_index, field=field, message="Missing required field")
        for field in sorted(missing)
    ]
    errors.extend(
        ImportValidationError(row=row_index, field=field, message="Unknown field")
        for field in sorted(unknown)
    )
    return errors


def _pydantic_errors(row_index: int, exc: ValidationError) -> list[ImportValidationError]:
    errors: list[ImportValidationError] = []
    for item in exc.errors():
        loc = item.get("loc") or []
        field = str(loc[0]) if loc else None
        errors.append(
            ImportValidationError(row=row_index, field=field, message=str(item.get("msg")))
        )
    return errors

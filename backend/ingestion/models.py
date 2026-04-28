from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ImportValidationError(BaseModel):
    row: int
    field: str | None = None
    message: str


class ReviewDocument(BaseModel):
    author: str = Field(min_length=1, max_length=255)
    rating: float = Field(ge=0, le=5)
    body: str = Field(min_length=1)
    reviewed_at: datetime
    title: str | None = None
    source_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("author", "body", "title", "source_url", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class ImportResult(BaseModel):
    reviews: list[ReviewDocument]
    errors: list[ImportValidationError] = Field(default_factory=list)

    @property
    def accepted_count(self) -> int:
        return len(self.reviews)

    @property
    def rejected_count(self) -> int:
        return len(self.errors)

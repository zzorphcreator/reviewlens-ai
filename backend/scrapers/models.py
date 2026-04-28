from pydantic import BaseModel, Field

from backend.ingestion.models import ReviewDocument


class FetchedPage(BaseModel):
    url: str
    final_url: str
    status_code: int
    html: str
    provider: str = "http"


class ScrapeResult(BaseModel):
    source_url: str
    final_url: str
    provider: str
    reviews: list[ReviewDocument] = Field(default_factory=list)
    attempts: list[dict[str, str]] = Field(default_factory=list)

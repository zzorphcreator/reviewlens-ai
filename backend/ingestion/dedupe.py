from __future__ import annotations

import hashlib

from backend.ingestion.models import ReviewDocument


def review_fingerprint(source_id: str, review: ReviewDocument) -> str:
    reviewed_at = review.reviewed_at.date().isoformat()
    parts = [
        source_id.strip().lower(),
        review.author.strip().lower(),
        f"{review.rating:.1f}",
        review.body.strip().lower(),
        reviewed_at,
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from backend.llm.chat import build_user_prompt, plain_text_response
from backend.llm.embeddings import chunk_review


def test_build_user_prompt_scopes_answer_to_excerpts() -> None:
    prompt = build_user_prompt(
        question="What do users dislike?",
        total_review_count=37,
        context_chunks=[
            {
                "review_id": "review-1",
                "score": 0.91,
                "content": "Rating: 2/5\nThe app is slow during onboarding.",
            }
        ],
    )

    assert "Answer from these excerpts only" in prompt
    assert "Current review set contains 37 reviews." in prompt
    assert "review-1" in prompt
    assert "The app is slow during onboarding." in prompt


def test_chunk_review_includes_review_metadata() -> None:
    review = SimpleNamespace(
        author="Sam",
        rating=Decimal("4.0"),
        title="Good support",
        reviewed_at=datetime(2026, 4, 27, tzinfo=timezone.utc),
        body="Support answered quickly.",
    )

    chunks = chunk_review(review)

    assert chunks == [
        "Author: Sam\n"
        "Rating: 4.0/5\n"
        "Title: Good support\n"
        "Reviewed at: 2026-04-27\n\n"
        "Support answered quickly."
    ]


def test_plain_text_response_removes_markdown_characters() -> None:
    assert (
        plain_text_response(
            """## Summary
- **Slow setup** is mentioned often.
- Users like `search`.

[Docs](https://example.com)
"""
        )
        == "Summary\nSlow setup is mentioned often.\nUsers like search.\n\nDocs"
    )

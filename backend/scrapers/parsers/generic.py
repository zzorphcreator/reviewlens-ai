from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pydantic import ValidationError

from backend.ingestion.models import ReviewDocument
from backend.scrapers.parsers.review_html import parse_review_html


def parse_generic_reviews(html: str, *, source_url: str) -> list[ReviewDocument]:
    if "tripadvisor." in urlparse(source_url).netloc.lower():
        reviews = _parse_tripadvisor_reviews(html, source_url=source_url)
        if reviews:
            return reviews

    soup = BeautifulSoup(html, "html.parser")
    reviews: list[ReviewDocument] = []

    for node in _jsonld_nodes(soup):
        for review_payload in _walk_reviews(node):
            review = _review_from_schema(review_payload, source_url=source_url)
            if review is not None:
                reviews.append(review)

    if reviews:
        return reviews

    reviews = _parse_microdata_reviews(soup, source_url=source_url)
    if reviews:
        return reviews

    reviews = _parse_embedded_json_reviews(soup, source_url=source_url)
    if reviews:
        return reviews

    return _parse_html_reviews(soup, source_url=source_url)


def _parse_tripadvisor_reviews(html: str, *, source_url: str) -> list[ReviewDocument]:
    raw_reviews, _product_name = parse_review_html(html, source_url=source_url)
    reviews: list[ReviewDocument] = []
    seen: set[str] = set()
    for raw in raw_reviews:
        rating = _rating_from_value(raw.get("rating"))
        body = raw.get("body")
        if rating is None or not body:
            continue
        try:
            review = ReviewDocument.model_validate(
                {
                    "author": raw.get("author") or "Anonymous",
                    "rating": rating,
                    "title": raw.get("title") or "Tripadvisor review",
                    "body": body,
                    "reviewed_at": _parse_date(str(raw.get("date") or "")) or datetime.now(tz=UTC),
                    "source_url": source_url,
                    "metadata": {"parser": "tripadvisor"},
                    "raw": raw,
                }
            )
        except ValidationError:
            continue
        key = _review_key(review)
        if key in seen:
            continue
        seen.add(key)
        reviews.append(review)
    return reviews


def _jsonld_nodes(soup: BeautifulSoup) -> Iterable[Any]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        yield parsed


def _walk_reviews(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, list):
        for item in node:
            yield from _walk_reviews(item)
        return

    if not isinstance(node, dict):
        return

    node_type = node.get("@type")
    if _type_matches(node_type, "Review"):
        yield node

    graph = node.get("@graph")
    if graph:
        yield from _walk_reviews(graph)

    review = node.get("review")
    if review:
        yield from _walk_reviews(review)


def _review_from_schema(payload: dict[str, Any], *, source_url: str) -> ReviewDocument | None:
    rating = payload.get("reviewRating") or payload.get("rating")
    if isinstance(rating, dict):
        rating = rating.get("ratingValue") or rating.get("value")

    author = payload.get("author")
    if isinstance(author, dict):
        author = author.get("name")

    body = payload.get("reviewBody") or payload.get("description") or payload.get("text")
    reviewed_at = payload.get("datePublished") or payload.get("dateCreated")

    try:
        return ReviewDocument.model_validate(
            {
                "author": author or "Anonymous",
                "rating": rating,
                "title": payload.get("name") or payload.get("headline"),
                "body": body,
                "reviewed_at": reviewed_at,
                "source_url": source_url,
                "metadata": {"parser": "jsonld"},
                "raw": payload,
            }
        )
    except ValidationError:
        return None


def _parse_microdata_reviews(soup: BeautifulSoup, *, source_url: str) -> list[ReviewDocument]:
    reviews: list[ReviewDocument] = []
    selectors = [
        '[itemtype*="schema.org/Review"]',
        '[itemtype*="https://schema.org/Review"]',
        '[itemtype*="http://schema.org/Review"]',
    ]

    for element in soup.select(",".join(selectors)):
        payload = {
            "author": _prop_text(element, "author") or "Anonymous",
            "rating": _prop_text(element, "ratingValue") or _prop_text(element, "rating"),
            "title": _prop_text(element, "name") or _prop_text(element, "headline"),
            "body": _prop_text(element, "reviewBody") or _prop_text(element, "description"),
            "reviewed_at": _prop_text(element, "datePublished") or _prop_text(element, "dateCreated"),
            "source_url": source_url,
            "metadata": {"parser": "microdata"},
            "raw": {"html": str(element)[:5000]},
        }
        try:
            reviews.append(ReviewDocument.model_validate(payload))
        except ValidationError:
            continue

    return reviews


def _parse_html_reviews(soup: BeautifulSoup, *, source_url: str) -> list[ReviewDocument]:
    candidates = soup.select(
        ",".join(
            [
                '[data-testid*="review" i]',
                '[data-test*="review" i]',
                '[class*="review" i]',
                "article",
            ]
        )
    )
    reviews: list[ReviewDocument] = []
    seen: set[str] = set()

    for element in candidates:
        text = _clean_text(element.get_text(" ", strip=True))
        if len(text) < 80:
            continue

        body = (
            _first_text(element.select_one('[itemprop="reviewBody"]'))
            or _first_text(element.select_one('[data-testid*="review-body" i]'))
            or _review_body_from_text(text)
        )
        if not body or len(body) < 20:
            continue

        rating = _rating_from_node(element, text)
        reviewed_at = _date_from_node(element, text)
        if rating is None or reviewed_at is None:
            continue

        payload = {
            "author": _review_author(element) or "Anonymous",
            "rating": rating,
            "title": _review_title(element) or None,
            "body": body,
            "reviewed_at": reviewed_at,
            "source_url": source_url,
            "metadata": {"parser": "html"},
            "raw": {"html": str(element)[:5000]},
        }
        try:
            review = ReviewDocument.model_validate(payload)
        except ValidationError:
            continue

        key = _review_key(review)
        if key in seen:
            continue
        seen.add(key)
        reviews.append(review)

    return reviews


def _parse_embedded_json_reviews(soup: BeautifulSoup, *, source_url: str) -> list[ReviewDocument]:
    reviews: list[ReviewDocument] = []
    seen: set[str] = set()
    scripts = soup.select('script[id="__NEXT_DATA__"], script[type="application/json"]')

    for script in scripts:
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue

        _collect_embedded_reviews(payload, source_url, reviews, seen)

    # Some sites embed state as JS assignments instead of pure JSON scripts.
    for script in soup.select("script"):
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue
        for marker in _STATE_MARKERS:
            payload = _extract_json_after_marker(raw, marker)
            if payload is None:
                continue
            _collect_embedded_reviews(payload, source_url, reviews, seen)

    return reviews


def _prop_text(element: Any, prop: str) -> str | None:
    match = element.select_one(f'[itemprop="{prop}"]')
    if match is None:
        return None
    return match.get("content") or match.get("datetime") or match.get_text(" ", strip=True)


def _first_text(element: Any) -> str:
    if element is None:
        return ""
    return _clean_text(element.get("content") or element.get_text(" ", strip=True))


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _review_title(element: Any) -> str:
    selectors = [
        '[itemprop="name"]',
        '[itemprop="headline"]',
        '[data-testid*="title" i]',
        "h3",
        "h2",
    ]
    for selector in selectors:
        value = _first_text(element.select_one(selector))
        if value and len(value) <= 180:
            return value
    return ""


def _review_author(element: Any) -> str:
    selectors = [
        '[itemprop="author"]',
        '[data-testid*="reviewer" i]',
        '[class*="author" i]',
        '[class*="user" i]',
    ]
    for selector in selectors:
        value = _first_text(element.select_one(selector))
        if value and len(value) <= 255:
            return value
    return ""


def _review_body_from_text(text: str) -> str:
    body = _extract_between(
        text,
        ["What do you like best?", "What do you like best about"],
        ["What do you dislike?", "Recommendations", "Review collected by"],
    )
    if body:
        return body

    cleaned = re.sub(r"\bRead (?:more|less)\b", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bShow less\b", "", cleaned, flags=re.IGNORECASE)
    return _clean_text(cleaned[:2000])


def _rating_from_node(element: Any, text: str) -> float | None:
    selectors = [
        '[itemprop="reviewRating"] [itemprop="ratingValue"]',
        'meta[itemprop="ratingValue"]',
        '[itemprop="ratingValue"]',
        '[aria-label*="star" i]',
        '[aria-label*="rating" i]',
        '[title*="star" i]',
        '[title*="rating" i]',
        '[alt*="star" i]',
        '[alt*="rating" i]',
        "[data-rating]",
    ]
    for candidate in element.select(",".join(selectors)):
        raw = " ".join(
            str(part)
            for part in [
                candidate.get("content"),
                candidate.get("value"),
                candidate.get("aria-label"),
                candidate.get("title"),
                candidate.get("alt"),
                candidate.get("data-rating"),
                candidate.get_text(" ", strip=True),
            ]
            if part
        )
        rating = _rating_from_text(raw)
        if rating is not None:
            return rating
    return _rating_from_text(text)


def _rating_from_text(value: str) -> float | None:
    match = re.search(r"\b(10|[1-9](?:\.\d)?)\s*(?:out of\s*)10\b", value, re.IGNORECASE)
    if match:
        return float(match.group(1)) / 2

    match = re.search(r"\b([1-5](?:\.\d)?)\s*(?:(?:out\s+)?of\s*)?5\b", value, re.IGNORECASE)
    if match:
        return float(match.group(1))

    match = re.search(r"\b([1-5](?:\.\d)?)\s*(?:star|stars|star rating)\b", value, re.IGNORECASE)
    if match:
        return float(match.group(1))

    return None


def _date_from_node(element: Any, text: str) -> datetime | None:
    tracking_options = element.get("data-track-in-viewport-options")
    if tracking_options:
        try:
            payload = json.loads(tracking_options)
            published_date = _clean_text(str(payload.get("published_date") or ""))
            match = re.fullmatch(r"(20\d{2})(\d{2})(\d{2})", published_date)
            if match:
                return _parse_date(f"{match.group(1)}-{match.group(2)}-{match.group(3)}")
        except json.JSONDecodeError:
            pass

    date_node = element.select_one('meta[itemprop="datePublished"], time[datetime]')
    if date_node:
        date_value = _clean_text(str(date_node.get("content") or date_node.get("datetime") or ""))
        parsed = _parse_date(date_value)
        if parsed is not None:
            return parsed

    return _parse_date(text)


def _parse_date(value: str) -> datetime | None:
    patterns = [
        r"\b(20\d{2}-\d{2}-\d{2})\b",
        r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+20\d{2})\b",
        r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+20\d{2})\b",
    ]
    formats = ["%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"]
    for pattern in patterns:
        match = re.search(pattern, value, re.IGNORECASE)
        if not match:
            continue
        date_text = match.group(1).replace("Sept", "Sep").replace(".", "")
        for date_format in formats:
            try:
                return datetime.strptime(date_text, date_format).replace(tzinfo=UTC)
            except ValueError:
                continue
    return None


def _extract_between(value: str, starts: list[str], ends: list[str]) -> str:
    lowered = value.lower()
    start_pos = -1
    start_len = 0
    for start in starts:
        idx = lowered.find(start.lower())
        if idx >= 0:
            start_pos = idx
            start_len = len(start)
            break
    if start_pos < 0:
        return ""

    end_pos = len(value)
    for end in ends:
        idx = lowered.find(end.lower(), start_pos + start_len)
        if idx >= 0:
            end_pos = min(end_pos, idx)
    return _clean_text(value[start_pos + start_len : end_pos])


def _review_key(review: ReviewDocument) -> str:
    return _clean_text(f"{review.title or ''} {review.body}")[:500].lower()


def _type_matches(node_type: Any, expected: str) -> bool:
    if isinstance(node_type, list):
        return any(_type_matches(item, expected) for item in node_type)
    return isinstance(node_type, str) and node_type.lower() == expected.lower()


def _walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _looks_like_review(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    body_keys = {"reviewText", "review_body", "reviewBody", "body", "text", "description", "comment"}
    rating_keys = {"rating", "ratingValue", "score", "overallRating", "overall_rating"}
    has_body = any(key in payload and payload.get(key) for key in body_keys)
    has_rating = any(key in payload and payload.get(key) is not None for key in rating_keys)
    return has_body and has_rating


def _review_from_embedded_json(
    payload: dict[str, Any], *, source_url: str
) -> ReviewDocument | None:
    author = payload.get("reviewerName") or payload.get("author") or payload.get("user")
    if isinstance(author, dict):
        author = author.get("name") or author.get("fullName")

    body = (
        payload.get("reviewText")
        or payload.get("review_body")
        or payload.get("reviewBody")
        or payload.get("body")
        or payload.get("text")
        or payload.get("description")
        or payload.get("comment")
    )

    title = payload.get("title") or payload.get("headline") or payload.get("summary")
    reviewed_at = (
        payload.get("createdAt")
        or payload.get("reviewDate")
        or payload.get("reviewedAt")
        or payload.get("publishedAt")
        or payload.get("date")
    )

    rating_value = (
        payload.get("rating")
        or payload.get("ratingValue")
        or payload.get("score")
        or payload.get("overallRating")
        or payload.get("overall_rating")
    )
    rating = _rating_from_value(rating_value)

    if rating is None or not body:
        return None

    try:
        return ReviewDocument.model_validate(
            {
                "author": author or "Anonymous",
                "rating": rating,
                "title": title,
                "body": str(body),
                "reviewed_at": _parse_date(str(reviewed_at)) or datetime.now(tz=UTC),
                "source_url": source_url,
                "metadata": {"parser": "embedded_json"},
                "raw": payload,
            }
        )
    except ValidationError:
        return None


def _collect_embedded_reviews(
    payload: Any, source_url: str, reviews: list[ReviewDocument], seen: set[str]
) -> None:
    for item in _walk_json(payload):
        if not _looks_like_review(item):
            continue
        candidate = _review_from_embedded_json(item, source_url=source_url)
        if candidate is None:
            continue
        key = _review_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        reviews.append(candidate)


def _rating_from_value(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("value", "ratingValue", "score", "overallRating", "overall_rating"):
            nested = value.get(key)
            rating = _rating_from_value(nested)
            if rating is not None:
                return rating
    if value is None:
        return None
    return _rating_from_text(str(value))


_STATE_MARKERS = (
    "__NEXT_DATA__",
    "__APOLLO_STATE__",
    "__NUXT__",
    "__INITIAL_STATE__",
    "INITIAL_STATE",
    "reduxState",
)


def _extract_json_after_marker(source: str, marker: str) -> Any | None:
    idx = source.find(marker)
    if idx == -1:
        return None
    brace_start = source.find("{", idx)
    if brace_start == -1:
        return None
    brace_end = _find_matching_brace(source, brace_start)
    if brace_end == -1:
        return None
    snippet = source[brace_start : brace_end + 1]
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        return None


def _find_matching_brace(source: str, start: int) -> int:
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(source)):
        char = source[idx]
        if in_string:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return idx
    return -1

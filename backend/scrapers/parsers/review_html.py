from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from backend.scrapers.utils.pagination import pagination_links
from backend.scrapers.utils.platform_detection import product_name_from_url


def parse_review_html(html: str, source_url: str = "") -> tuple[list[dict[str, str]], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    product_name = (
        first_text(soup.select_one("h1"))
        or first_text(soup.select_one('meta[property="og:title"]'))
        or first_text(soup.select_one("title"))
        or (product_name_from_url(source_url) if source_url else None)
    )
    json_reviews = parse_json_ld_reviews(soup)
    if "apps.shopify.com" in urlparse(source_url).netloc.lower():
        shopify_reviews = parse_shopify_reviews(soup)
        if shopify_reviews:
            return best_review_set(json_reviews, shopify_reviews), product_name
    if "yelp.com" in urlparse(source_url).netloc.lower():
        yelp_reviews = parse_yelp_reviews(soup)
        if yelp_reviews:
            return best_review_set(json_reviews, yelp_reviews), product_name
    if "booking.com" in urlparse(source_url).netloc.lower():
        booking_reviews = parse_booking_reviews(soup)
        if booking_reviews:
            return best_review_set(json_reviews, booking_reviews), product_name
    if "tripadvisor.com" in urlparse(source_url).netloc.lower():
        tripadvisor_reviews = parse_tripadvisor_reviews(soup)
        if tripadvisor_reviews:
            return best_review_set(json_reviews, tripadvisor_reviews), product_name
    html_reviews = parse_html_reviews(soup)
    return best_review_set(json_reviews, html_reviews), product_name


def extraction_diagnostics(html: str, source_url: str = "") -> dict[str, object]:
    soup = BeautifulSoup(html, "html.parser")
    title = first_text(soup.select_one("title")) or first_text(soup.select_one("h1"))
    text = clean_text(soup.get_text(" ", strip=True))
    lowered = text.lower()
    blocker_terms = [
        "captcha",
        "access denied",
        "verify you are human",
        "cloudflare",
        "enable javascript",
        "blocked",
        "unusual traffic",
    ]
    return {
        "url": source_url,
        "html_len": len(html),
        "title": title[:160],
        "json_ld_scripts": len(soup.select('script[type="application/ld+json"]')),
        "review_itemtype_nodes": len(soup.select('[itemtype*="Review"]')),
        "review_itemprop_nodes": len(soup.select('[itemprop*="review"], [itemprop*="Review"]')),
        "review_testid_nodes": len(soup.select('[data-testid*="review"], [data-test*="review"]')),
        "article_nodes": len(soup.select("article")),
        "class_review_nodes": len(soup.select('[class*="review"], [class*="Review"]')),
        "pagination_links": pagination_links(soup, source_url),
        "g2_markers": {
            "what_do_you_like_best": "what do you like best" in lowered,
            "what_do_you_dislike": "what do you dislike" in lowered,
            "review_collected_by": "review collected by" in lowered,
        },
        "blocker_markers": [term for term in blocker_terms if term in lowered],
        "text_sample": text[:300],
    }


def parse_json_ld_reviews(soup: BeautifulSoup) -> list[dict[str, str]]:
    reviews: list[dict[str, str]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in walk_json(payload):
            if not is_review_node(item):
                continue
            reviews.append(
                {
                    "title": clean_text(str(item.get("name") or item.get("headline") or "")),
                    "body": clean_text(str(item.get("reviewBody") or item.get("description") or "")),
                    "rating": rating_from_json_ld(item),
                    "author": author_from_json_ld(item),
                    "date": clean_text(str(item.get("datePublished") or item.get("dateCreated") or "")),
                }
            )
    return [review for review in reviews if review.get("body") or review.get("title")]


def walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def is_review_node(item: dict[str, Any]) -> bool:
    node_type = item.get("@type") or item.get("type")
    if isinstance(node_type, list):
        return any(str(part).lower() == "review" for part in node_type)
    return str(node_type).lower() == "review"


def rating_from_json_ld(item: dict[str, Any]) -> str:
    rating = item.get("reviewRating") or item.get("rating")
    if isinstance(rating, dict):
        return clean_text(str(rating.get("ratingValue") or rating.get("value") or ""))
    return clean_text(str(rating or ""))


def author_from_json_ld(item: dict[str, Any]) -> str:
    author = item.get("author")
    if isinstance(author, dict):
        return clean_text(str(author.get("name") or ""))
    return clean_text(str(author or ""))


def parse_html_reviews(soup: BeautifulSoup) -> list[dict[str, str]]:
    candidates = soup.select(
        '[itemtype*="Review"], [itemprop="review"], [data-testid*="review"], [data-test*="review"], article'
    )
    reviews: list[dict[str, str]] = []
    for node in candidates:
        text = clean_text(node.get_text(" ", strip=True))
        if len(text) < 80:
            continue
        body = (
            first_text(node.select_one('[itemprop="reviewBody"]'))
            or first_text(node.select_one('[data-testid*="review-body"]'))
            or extract_between(
                text,
                ["What do you like best?", "What do you like best about"],
                ["What do you dislike?", "Review collected by"],
            )
            or text
        )
        title = (
            first_text(node.select_one('[itemprop="name"]'))
            or first_text(node.select_one("h3"))
            or first_text(node.select_one("h2"))
            or "G2 review"
        )
        pros = extract_between(
            text,
            ["What do you like best?", "What do you like best about"],
            ["What do you dislike?", "Recommendations"],
        )
        cons = extract_between(
            text,
            ["What do you dislike?", "What problems"],
            ["What problems", "Recommendations", "Review collected by"],
        )
        reviews.append(
            {
                "title": clean_text(title),
                "body": clean_text(body),
                "pros": clean_text(pros),
                "cons": clean_text(cons),
                "rating": rating_from_node(node, text),
                "author": first_text(node.select_one('[itemprop="author"], [data-testid*="reviewer"]'))
                or "",
                "date": date_from_node(node, text),
            }
        )
    return dedupe_reviews(reviews)


def parse_shopify_reviews(soup: BeautifulSoup) -> list[dict[str, str]]:
    reviews: list[dict[str, str]] = []
    cards = soup.select('[data-merchant-review][data-review-content-id]')
    for card in cards:
        body_node = card.select_one(
            '[data-truncate-review]:not([data-reply-id]) [data-truncate-content-copy]'
        ) or card.select_one('[data-truncate-review]:not([data-reply-id])')
        body = clean_text(body_node.get_text(" ", strip=True)) if body_node else ""
        if not body:
            continue
        text = clean_text(card.get_text(" ", strip=True))
        rating_source = first_text(card.select_one('[aria-label*="out of 5" i]')) or first_text(
            card.select_one('[aria-label*="star" i]')
        )
        rating = rating_from_text(rating_source or "") or rating_from_node(card, text)
        author = (
            first_text(card.select_one("span[title]"))
            or first_text(card.select_one(".tw-text-heading-xs"))
            or ""
        )
        reviews.append(
            {
                "title": "",
                "body": body,
                "pros": "",
                "cons": "",
                "rating": rating,
                "author": author,
                "date": date_from_text(text),
            }
        )
    return dedupe_reviews(reviews)


def parse_yelp_reviews(soup: BeautifulSoup) -> list[dict[str, str]]:
    reviews: list[dict[str, str]] = []
    for script in soup.select("script"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        script_type = (script.get("type") or "").lower()
        if script_type == "application/json":
            for payload in extract_json_payloads(raw):
                for item in walk_json(payload):
                    review = yelp_review_from_json(item)
                    if review:
                        reviews.append(review)
            continue
        lowered = raw.lower()
        if "review" not in lowered or "rating" not in lowered:
            continue
        for payload in extract_json_payloads(raw):
            for item in walk_json(payload):
                review = yelp_review_from_json(item)
                if review:
                    reviews.append(review)
    return dedupe_reviews(reviews)


def parse_booking_reviews(soup: BeautifulSoup) -> list[dict[str, str]]:
    reviews: list[dict[str, str]] = []
    cards = soup.select('[data-testid="review-card"], [data-testid^="review-card"]')
    if not cards:
        cards = soup.select('[data-testid="review-card-container"]')
    for card in cards:
        text = clean_text(card.get_text(" ", strip=True))
        if len(text) < 60:
            continue
        title = first_text(card.select_one('[data-testid="review-title"]')) or ""
        pros = first_text(card.select_one('[data-testid="review-positive-text"]')) or ""
        cons = first_text(card.select_one('[data-testid="review-negative-text"]')) or ""
        body = pros or cons or text
        rating_raw = first_text(card.select_one('[data-testid="review-score"]'))
        rating = rating_from_text(rating_raw or "")
        if not rating and rating_raw:
            rating = rating_from_numeric_text(rating_raw)
        author = (
            first_text(card.select_one('[data-testid="review-author"]'))
            or first_text(card.select_one('[data-testid="reviewer-name"]'))
            or first_text(card.select_one('[data-testid="reviewer"]'))
            or ""
        )
        date_value = (
            first_text(card.select_one('time[datetime]'))
            or first_text(card.select_one('[data-testid="review-date"]'))
            or date_from_text(text)
        )
        reviews.append(
            {
                "title": clean_text(title),
                "body": clean_text(body),
                "pros": clean_text(pros),
                "cons": clean_text(cons),
                "rating": rating,
                "author": author,
                "date": date_value,
            }
        )
    return dedupe_reviews(reviews)


def extract_json_payloads(raw: str) -> list[Any]:
    payloads: list[Any] = []
    cleaned = raw.strip()
    if cleaned.startswith("{") or cleaned.startswith("["):
        parsed = _safe_json_load(cleaned)
        if parsed is not None:
            payloads.append(parsed)
            return payloads

    markers = [
        "window.__INITIAL_STATE__",
        "__INITIAL_STATE__",
        "window.__PRELOADED_STATE__",
        "__PRELOADED_STATE__",
        "window.__NEXT_DATA__",
        "__NEXT_DATA__",
        "window.__APOLLO_STATE__",
        "__APOLLO_STATE__",
    ]
    for marker in markers:
        idx = cleaned.find(marker)
        if idx < 0:
            continue
        candidate = extract_balanced_json(cleaned, idx)
        if not candidate:
            continue
        parsed = _safe_json_load(candidate)
        if parsed is not None:
            payloads.append(parsed)
    return payloads


def extract_balanced_json(raw: str, start_idx: int) -> str:
    start_obj = raw.find("{", start_idx)
    start_arr = raw.find("[", start_idx)
    if start_obj == -1:
        start_obj = None
    if start_arr == -1:
        start_arr = None
    if start_obj is None and start_arr is None:
        return ""
    if start_obj is None or (start_arr is not None and start_arr < start_obj):
        start = start_arr
        open_char = "["
        close_char = "]"
    else:
        start = start_obj
        open_char = "{"
        close_char = "}"

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == open_char:
            depth += 1
            continue
        if ch == close_char:
            depth -= 1
            if depth == 0:
                return raw[start : idx + 1]
    return ""


def _safe_json_load(value: str) -> Any | None:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def yelp_review_from_json(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    review_id = item.get("reviewId") or item.get("id") or ""
    rating = extract_rating_value(item.get("rating") or item.get("ratingValue") or item.get("reviewRating"))
    comment = extract_text_value(item.get("comment") or item.get("reviewText") or item.get("text"))
    if not comment or not rating:
        return None
    user = item.get("user") or item.get("author") or {}
    if isinstance(user, dict):
        author = (
            user.get("displayName")
            or user.get("name")
            or user.get("nickname")
            or user.get("userNickname")
            or ""
        )
    else:
        author = str(user or "")
    reviewed_at = normalize_yelp_date(
        item.get("timeCreated")
        or item.get("createdAt")
        or item.get("localizedDate")
        or item.get("date")
        or item.get("publishedDate")
    )
    return {
        "title": "",
        "body": clean_text(str(comment)),
        "pros": "",
        "cons": "",
        "rating": clean_text(str(rating)),
        "author": clean_text(str(author)) or "Anonymous",
        "date": reviewed_at,
    }


def extract_text_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ["text", "content", "value", "raw", "markup"]:
            if key in value:
                return extract_text_value(value[key])
    if isinstance(value, list):
        parts = [extract_text_value(item) for item in value]
        return " ".join(part for part in parts if part)
    return ""


def extract_rating_value(value: Any) -> str:
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ["ratingValue", "value", "rating"]:
            if key in value:
                return extract_rating_value(value[key])
    if isinstance(value, str):
        return value
    return ""


def parse_tripadvisor_reviews(soup: BeautifulSoup) -> list[dict[str, str]]:
    reviews: list[dict[str, str]] = []
    for rating_node in soup.select('[data-automation="bubbleRatingImage"]'):
        card = tripadvisor_review_card(rating_node)
        if not card:
            continue
        text = clean_text(card.get_text(" ", strip=True))
        if len(text) < 80:
            continue
        rating = rating_from_text(first_text(rating_node.select_one("title")) or rating_node.get("title") or text)
        title = tripadvisor_review_title(card)
        body = tripadvisor_review_body(card, title)
        if not body:
            continue
        reviews.append(
            {
                "title": title or "Tripadvisor review",
                "body": body,
                "pros": "",
                "cons": "",
                "rating": rating,
                "author": tripadvisor_review_author(card),
                "date": tripadvisor_review_date(text),
            }
        )
    return dedupe_reviews(reviews)


def tripadvisor_review_card(node):
    current = node
    for _ in range(10):
        current = current.parent
        if not current:
            return None
        text = clean_text(current.get_text(" ", strip=True))
        if current.name == "li" and len(text) >= 120 and ("Read more" in text or "Date of stay" in text):
            return current
    return None


def tripadvisor_review_title(card) -> str:
    for selector in ["[data-test-target*='review-title']", "a[href*='ShowUserReviews']", "q", "h3", "h2"]:
        value = first_text(card.select_one(selector))
        if value and len(value) <= 180:
            return value.strip('"')
    return ""


def tripadvisor_review_body(card, title: str) -> str:
    text = clean_text(card.get_text(" ", strip=True))
    rating_match = re.search(r"\b[1-5](?:\.\d)?\s+of\s+5\s+bubbles\b", text, re.IGNORECASE)
    if rating_match:
        text = text[rating_match.end() :].strip()
    if title and text.lower().startswith(title.lower()):
        text = text[len(title) :].strip()
    text = re.sub(r"\bRead more\b.*$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\bDate of stay:.*$", "", text, flags=re.IGNORECASE).strip()
    text = clean_text(text)
    return text if len(text) <= 1200 else text[:1197] + "..."


def tripadvisor_review_author(card) -> str:
    for selector in ["a[href*='/Profile/']", "[class*='member']", "[class*='user']"]:
        value = first_text(card.select_one(selector))
        if value and len(value) <= 80:
            return value
    return ""


def tripadvisor_review_date(text: str) -> str:
    match = re.search(
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+(20\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return ""
    month_lookup = {
        "jan": "01",
        "feb": "02",
        "mar": "03",
        "apr": "04",
        "may": "05",
        "jun": "06",
        "jul": "07",
        "aug": "08",
        "sep": "09",
        "sept": "09",
        "oct": "10",
        "nov": "11",
        "dec": "12",
    }
    month = month_lookup.get(match.group(1).lower()[:4], "")
    if not month:
        return ""
    return f"{match.group(2)}-{month}-01"


def review_card_debug_fragments(html: str, limit: int = 2) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates = soup.select(
        '[itemtype*="Review"], [itemprop="review"], [data-testid*="review"], [data-test*="review"], article'
    )
    fragments: list[str] = []
    for node in candidates[:limit]:
        fragments.append(clean_text(str(node))[:2500])
    return fragments


def best_review_set(json_reviews: list[dict[str, str]], html_reviews: list[dict[str, str]]) -> list[dict[str, str]]:
    if not json_reviews:
        return html_reviews
    if not html_reviews:
        return json_reviews
    json_score = review_set_score(json_reviews)
    html_score = review_set_score(html_reviews)
    return html_reviews if html_score >= json_score else json_reviews


def review_set_score(reviews: list[dict[str, str]]) -> int:
    scored = len(reviews) * 10
    scored += sum(3 for review in reviews if review.get("rating"))
    scored += sum(2 for review in reviews if review.get("author"))
    scored += sum(2 for review in reviews if review.get("date"))
    scored += sum(1 for review in reviews if review.get("pros") or review.get("cons"))
    return scored


def first_text(node) -> str:
    if not node:
        return ""
    return clean_text(node.get("content") or node.get_text(" ", strip=True))


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def rating_from_text(value: str) -> str:
    match = re.search(r"\b(10|[1-9](?:\.\d)?)\s*(?:out of\s*)10\b", value, re.IGNORECASE)
    if match:
        return f"{float(match.group(1)) / 2:.1f}"
    match = re.search(r"\b([1-5](?:\.\d)?)\s*(?:(?:out\s+)?of\s*)?5\b", value, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"\b([1-5](?:\.\d)?)\s*(?:star|stars|star rating)\b", value, re.IGNORECASE)
    return match.group(1) if match else ""


def rating_from_node(node, text: str) -> str:
    rating_meta = node.select_one('[itemprop="reviewRating"] [itemprop="ratingValue"], meta[itemprop="ratingValue"]')
    if rating_meta:
        rating = rating_from_text(str(rating_meta.get("content") or rating_meta.get("value") or ""))
        if rating:
            return rating

    selectors = [
        '[itemprop="ratingValue"]',
        '[itemprop="reviewRating"]',
        '[aria-label*="star" i]',
        '[aria-label*="rating" i]',
        '[title*="star" i]',
        '[title*="rating" i]',
        '[alt*="star" i]',
        '[alt*="rating" i]',
        '[data-rating]',
    ]
    for candidate in node.select(",".join(selectors)):
        raw = " ".join(
            str(part)
            for part in [
                candidate.get("content"),
                candidate.get("aria-label"),
                candidate.get("title"),
                candidate.get("alt"),
                candidate.get("data-rating"),
                candidate.get_text(" ", strip=True),
            ]
            if part
        )
        rating = rating_from_text(raw)
        if rating:
            return rating
    return rating_from_text(text)


def date_from_node(node, text: str) -> str:
    tracking_options = node.get("data-track-in-viewport-options")
    if tracking_options:
        try:
            payload = json.loads(tracking_options)
            published_date = clean_text(str(payload.get("published_date") or ""))
            match = re.fullmatch(r"(20\d{2})(\d{2})(\d{2})", published_date)
            if match:
                return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        except json.JSONDecodeError:
            pass

    date_meta = node.select_one('meta[itemprop="datePublished"], time[datetime]')
    if date_meta:
        date_value = clean_text(str(date_meta.get("content") or date_meta.get("datetime") or ""))
        if date_value:
            return date_value[:10]

    return date_from_text(text)


def date_from_text(value: str) -> str:
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", value)
    if match:
        return match.group(1)
    match = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
        r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+"
        r"(\d{1,2}),\s+(20\d{2})\b",
        value,
        re.IGNORECASE,
    )
    month_lookup = {
        "jan": "01",
        "feb": "02",
        "mar": "03",
        "apr": "04",
        "may": "05",
        "jun": "06",
        "jul": "07",
        "aug": "08",
        "sep": "09",
        "sept": "09",
        "oct": "10",
        "nov": "11",
        "dec": "12",
    }
    if match:
        month_key = match.group(1).strip().lower()[:4]
        month = month_lookup.get(month_key[:3], "")
        if not month:
            return ""
        day = match.group(2).zfill(2)
        return f"{match.group(3)}-{month}-{day}"

    match = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
        r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+"
        r"(20\d{2})\b",
        value,
        re.IGNORECASE,
    )
    if not match:
        return ""
    month_key = match.group(1).strip().lower()[:4]
    month = month_lookup.get(month_key[:3], "")
    if not month:
        return ""
    return f"{match.group(2)}-{month}-01"


def rating_from_numeric_text(value: str) -> str:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", value)
    if not match:
        return ""
    score = float(match.group(1))
    if score > 5:
        score = score / 2
    return f"{score:.1f}".rstrip("0").rstrip(".")


def normalize_yelp_date(value: Any) -> str:
    if isinstance(value, (int, float)):
        timestamp = int(value)
        if timestamp > 1_000_000_000_000:
            timestamp = int(timestamp / 1000)
        return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")
    if isinstance(value, str):
        return date_from_text(value)
    return ""


def extract_between(value: str, starts: list[str], ends: list[str]) -> str:
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
    return clean_text(value[start_pos + start_len : end_pos])


def review_key(review: dict[str, str]) -> str:
    return clean_text(f"{review.get('title', '')} {review.get('body', '')}")[:500].lower()


def dedupe_reviews(reviews: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for review in reviews:
        key = review_key(review)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(review)
    return unique

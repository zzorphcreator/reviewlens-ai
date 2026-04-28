from __future__ import annotations

import json
import re
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
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{4}\b",
        text,
        re.IGNORECASE,
    )
    return match.group(0) if match else ""


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
    return match.group(1) if match else ""


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

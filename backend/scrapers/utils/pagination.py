from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup


def with_page(url: str, page: int) -> str:
    parsed = urlparse(url)
    path = parsed.path
    if "trustradius.com" in parsed.netloc.lower() and "/products/" in path and "/reviews" in path:
        path = re.sub(r"/reviews(?:/all)?/?$", "/reviews/all", path)
    query = parse_qs(parsed.query)
    if page > 1:
        query["page"] = [str(page)]
    else:
        query.pop("page", None)
    return urlunparse(parsed._replace(path=path, query=urlencode(query, doseq=True)))


def pagination_links(soup: BeautifulSoup, source_url: str = "") -> list[str]:
    links: list[str] = []
    for anchor in soup.select('a[href*="page="], a[rel="next"], link[rel="next"]'):
        href = anchor.get("href")
        if not href:
            continue
        links.append(href)
    return links[:10]

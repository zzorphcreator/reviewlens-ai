from __future__ import annotations

from urllib.parse import urlparse


def product_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    parts = [
        part
        for part in parsed.path.split("/")
        if part and part.lower() not in {"products", "reviews", "review"}
    ]
    if not parts:
        return platform_from_url(url)
    return parts[-1].replace("-", " ").replace("_", " ").title()


def platform_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "g2.com" in host:
        return "G2"
    if "capterra.com" in host:
        return "Capterra"
    if "amazon." in host:
        return "Amazon"
    if "google.com" in host or "maps.app.goo.gl" in host:
        return "Google Maps"
    if "trustradius.com" in host:
        return "TrustRadius"
    if "trustpilot.com" in host:
        return "Trustpilot"
    if "producthunt.com" in host:
        return "Product Hunt"
    if "apps.shopify.com" in host:
        return "Shopify App Store"
    if "chromewebstore.google.com" in host:
        return "Chrome Web Store"
    if "play.google.com" in host:
        return "Google Play"
    if "apps.apple.com" in host:
        return "Apple App Store"
    if "tripadvisor.com" in host:
        return "Tripadvisor"
    if "booking.com" in host:
        return "Booking.com"
    if "expedia.com" in host:
        return "Expedia"
    if "glassdoor.com" in host:
        return "Glassdoor"
    if "yelp.com" in host:
        return "Yelp"
    return host.replace("www.", "")

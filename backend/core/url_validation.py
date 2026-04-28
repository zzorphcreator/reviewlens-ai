from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urlunparse


class UnsafeUrlError(ValueError):
    pass


def validate_public_http_url(url: str) -> str:
    candidate = url.strip()
    parsed = urlparse(candidate)

    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("Only http and https URLs are allowed.")
    if not parsed.hostname:
        raise UnsafeUrlError("URL must include a hostname.")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("Credentials in URLs are not allowed.")

    host = parsed.hostname.strip().lower()
    _reject_private_host(host)

    normalized_netloc = host
    if parsed.port:
        normalized_netloc = f"{normalized_netloc}:{parsed.port}"

    return urlunparse(
        (
            parsed.scheme,
            normalized_netloc,
            parsed.path or "/",
            "",
            parsed.query,
            "",
        )
    )


def _reject_private_host(host: str) -> None:
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UnsafeUrlError(f"Could not resolve hostname '{host}'.") from exc
        addresses = [ipaddress.ip_address(info[4][0]) for info in infos]

    if not addresses:
        raise UnsafeUrlError("Hostname did not resolve to an IP address.")

    for address in addresses:
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise UnsafeUrlError("URL resolves to a private or reserved network address.")

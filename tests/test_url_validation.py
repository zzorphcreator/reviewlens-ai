import pytest

from backend.core.url_validation import UnsafeUrlError, validate_public_http_url


def test_validate_public_http_url_normalizes_path() -> None:
    assert validate_public_http_url("https://example.com") == "https://example.com/"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/reviews",
        "https://user:pass@example.com/reviews",
        "http://127.0.0.1/reviews",
        "http://localhost/reviews",
        "http://10.0.0.5/reviews",
        "http://169.254.169.254/latest/meta-data",
    ],
)
def test_validate_public_http_url_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_public_http_url(url)

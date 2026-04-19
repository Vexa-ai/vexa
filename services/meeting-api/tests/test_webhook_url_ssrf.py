"""Regression tests for the SSRF validator on webhook URLs.

Covers CVE-2026-25883 / GHSA-fhr6-8hff-cvg4.

The validator lives in `meeting_api.webhook_url.validate_webhook_url` and is
called at two points:

1. At configuration time — `admin_api/app/main.py::set_user_webhook`
2. At delivery time — `meeting_api/webhooks.py` (`send_completion_webhook`,
   `send_status_webhook`) before `webhook_delivery.deliver(...)`.

These tests fix the validator's contract in place so a future refactor
cannot silently disable SSRF protection for either call site.
"""

import pytest

from meeting_api.webhook_url import validate_webhook_url


SSRF_SAMPLES = [
    # Cloud metadata
    "http://169.254.169.254/latest/meta-data/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://metadata.amazonaws.com/",
    # Loopback
    "http://localhost/internal",
    "http://127.0.0.1/admin",
    "http://[::1]/",
    # Private IPv4 ranges
    "http://10.0.0.1/",
    "http://172.16.0.1/",
    "http://192.168.0.1/",
    # Private IPv6
    "http://[fc00::1]/",
    "http://[fe80::1]/",
    # Multicast
    "http://224.0.0.1/",
    # Internal Vexa Docker service hostnames
    "http://redis:6379/",
    "http://postgres:5432/",
    "http://meeting-api:8080/leak",
    "http://admin-api/",
    "http://api-gateway/",
    "http://transcription-collector/",
    # Scheme rejections
    "file:///etc/passwd",
    "gopher://attacker.com/",
    "javascript:alert(1)",
]


PERMITTED_SAMPLES = [
    # All three IANA-reserved example.* base domains resolve; specific
    # subdomains like `hooks.example.org` intentionally don't, so stick to
    # the bases (or a path under them) for these positive-case tests.
    "https://example.com/webhook",
    "https://example.org/vexa",
    "http://example.net/endpoint",
]


@pytest.mark.parametrize("url", SSRF_SAMPLES)
def test_ssrf_urls_rejected(url):
    with pytest.raises(ValueError):
        validate_webhook_url(url)


@pytest.mark.parametrize("url", PERMITTED_SAMPLES)
def test_public_urls_permitted(url):
    assert validate_webhook_url(url) == url


def test_missing_hostname_rejected():
    with pytest.raises(ValueError):
        validate_webhook_url("http:///path")


def test_invalid_url_rejected():
    with pytest.raises(ValueError):
        validate_webhook_url("not-a-url")

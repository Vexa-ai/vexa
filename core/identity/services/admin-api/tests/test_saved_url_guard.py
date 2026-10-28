"""The URLs a person saves for a server to fetch later — a calendar feed, a webhook — are checked
when they are saved, by the same guard the fetching service applies (``app/ssrf.py``, vendored).
No database, no DNS: a saved URL is checked as written; the fetch resolves and pins."""
import pytest
from fastapi import HTTPException

from admin_api.app import ssrf
from admin_api.app.calendars import validate_ics_url


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/feed.ics", "http://[::1]/feed.ics", "http://[::ffff:127.0.0.1]/feed.ics",
    "http://[::ffff:a9fe:a9fe]/feed.ics", "http://[::10.0.0.1]/feed.ics", "http://[::ffff:0:a9fe:a9fe]/",
    "http://[2002:a9fe:a9fe::1]/", "http://[2001:0:4136:e378:8000:63bf:3fff:fdd2]/",
    "http://[64:ff9b::a9fe:a9fe]/", "http://[64:ff9b:1::a00:1]/", "http://[fd00:ec2::254]/",
    "http://169.254.169.254/latest", "http://100.100.100.200/", "http://10.0.0.5/", "http://2130706433/",
    "http://0x7f.0.0.1/", "http://0177.0.0.1/", "http://localhost/", "http://a.localhost/",
    "http://admin-api:8001/", "http://metadata.google.internal/",
])
def test_an_internal_feed_is_refused(url):
    with pytest.raises(HTTPException) as exc:
        validate_ics_url(url)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("url", [
    "https://calendar.google.com/calendar/ical/x%40vexa.ai/private-abc/basic.ics",
    "https://outlook.office365.com/owa/calendar/abc/calendar.ics",
    "https://[2606:4700::1111]/feed.ics",
])
def test_a_public_feed_is_kept(url):
    assert validate_ics_url(url) == url


def test_a_saved_url_is_checked_without_a_lookup(monkeypatch):
    def no_dns(*a, **k):
        raise AssertionError("a saved URL is checked as written; the fetch resolves")
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", no_dns)
    assert ssrf.validate_url("https://hooks.example.com/x", resolve=False).host == "hooks.example.com"

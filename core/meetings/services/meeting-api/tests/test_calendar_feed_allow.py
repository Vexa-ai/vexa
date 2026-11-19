"""Internal calendar feeds — the operator's ``VEXA_CALENDAR_FEED_ALLOW``.

A feed served inside a corporate network resolves to a private address, which the outbound URL
guard refuses. The operator (never a user) may name internal hosts or networks for calendar feeds
only; everything else stays refused, including for this very fetch: loopback, cloud metadata, other
private ranges, a listed name that re-resolves somewhere it may not go, and customer webhooks.
"""
from __future__ import annotations

import httpx
import pytest

from meeting_api.calendar_sync.adapters import build_ics_client, feed_allowance, fetch_ics
from meeting_api.webhooks import ssrf

FEED = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
DNS = {
    "cal.corp.example": ["10.9.9.9"],
    "other.corp.example": ["10.9.9.9"],
    "net.corp.example": ["10.20.3.4"],
    "mixed.corp.example": ["10.20.3.4", "192.168.1.1"],
    "loop.corp.example": ["127.0.0.1"],
    "meta.corp.example": ["169.254.169.254"],
}
ALLOW = "cal.corp.example loop.corp.example meta.corp.example, 10.20.0.0/16"


def _resolver(host):
    return DNS.get(host, [])


def _client(allow, resolver=_resolver, dials=None):
    def handler(request):
        if dials is not None:
            dials.append(request.url.host)
        return httpx.Response(200, text=FEED)
    transport = ssrf.build_pinned_transport(inner=httpx.MockTransport(handler), resolver=resolver,
                                            allow=allow)
    return httpx.AsyncClient(transport=transport, follow_redirects=False)


async def test_without_an_allowance_an_internal_feed_is_refused_and_never_dialled():
    dials: list = []
    async with _client(feed_allowance(""), dials=dials) as client:
        text, err = await fetch_ics("https://cal.corp.example/team.ics", client=client)
    assert text is None and "blocked/internal" in err and dials == []


@pytest.mark.parametrize("url,dialled", [
    ("https://cal.corp.example/team.ics", "10.9.9.9"),   # a listed host, resolving privately
    ("https://net.corp.example/team.ics", "10.20.3.4"),  # any host, inside a listed network
    ("https://10.20.7.7/team.ics", "10.20.7.7"),          # a literal inside a listed network
])
async def test_an_allowed_internal_feed_is_fetched_at_the_checked_address(url, dialled):
    dials: list = []
    async with _client(feed_allowance(ALLOW), dials=dials) as client:
        text, err = await fetch_ics(url, client=client)
    assert err is None and text == FEED and dials == [dialled]


@pytest.mark.parametrize("url", [
    "https://other.corp.example/x.ics",   # not listed, private
    "https://mixed.corp.example/x.ics",   # one of its addresses is outside the networks
    "https://10.21.0.1/x.ics",            # outside the listed network
    "https://192.168.1.1/x.ics",
    "https://loop.corp.example/x.ics",    # LISTED, but resolves to loopback
    "https://meta.corp.example/x.ics",    # LISTED, but resolves to cloud metadata
    "https://127.0.0.1/x.ics",
    "https://169.254.169.254/latest/meta-data",
    "https://[::ffff:127.0.0.1]/x.ics",
    "https://localhost/x.ics",
    "https://metadata.google.internal/x",
])
async def test_everything_else_stays_refused_with_an_allowance(url):
    dials: list = []
    async with _client(feed_allowance(ALLOW), dials=dials) as client:
        text, err = await fetch_ics(url, client=client)
    assert text is None and dials == [], url


async def test_a_listed_host_that_rebinds_to_metadata_at_connect_is_never_dialled():
    answers = iter([["10.9.9.9"], ["169.254.169.254"]])
    pinned = ssrf.validate_url("https://cal.corp.example/x.ics", lambda h: next(answers),
                               allow=feed_allowance(ALLOW))
    assert pinned.pinned_ips == ["10.9.9.9"]
    dials: list = []
    async with _client(feed_allowance(ALLOW), resolver=lambda h: next(answers), dials=dials) as client:
        text, _ = await fetch_ics("https://cal.corp.example/x.ics", client=client)
    assert text is None and dials == []


def test_the_allowance_never_reaches_customer_webhooks(monkeypatch):
    monkeypatch.setenv("VEXA_CALENDAR_FEED_ALLOW", ALLOW)
    for url in ("https://cal.corp.example/hook", "https://10.20.1.1/hook"):
        with pytest.raises(ssrf.SSRFError):
            ssrf.validate_webhook_url(url, _resolver)


async def test_the_production_client_reads_the_operator_allowance(monkeypatch):
    seen = []
    real = ssrf.build_pinned_transport

    def spy(*args, **kwargs):
        seen.append(kwargs.get("allow"))
        return real(*args, **kwargs)

    monkeypatch.setattr(ssrf, "build_pinned_transport", spy)
    monkeypatch.setenv("VEXA_CALENDAR_FEED_ALLOW", "10.20.0.0/16")
    client = build_ics_client()
    await client.aclose()
    assert [str(n) for n in seen[0].networks] == ["10.20.0.0/16"]


@pytest.mark.parametrize("bad", [
    "0.0.0.0/0", "::/0", "127.0.0.0/8", "169.254.0.0/16", "10.0.0.1/8", "*.corp.example",
    "localhost", "metadata.google.internal", "https://cal.corp.example", "cal.corp.example:443",
    "bad..name",
])
def test_an_unsafe_or_malformed_entry_refuses_the_boot(bad):
    from meeting_api.__main__ import _calendar_feed_allow_at_boot
    from meeting_api.config_preflight import ConfigError

    with pytest.raises(ConfigError, match="VEXA_CALENDAR_FEED_ALLOW"):
        _calendar_feed_allow_at_boot({"VEXA_CALENDAR_FEED_ALLOW": bad})


def test_unset_is_the_rule_with_no_exception():
    assert not feed_allowance("")

"""#1182 — the ICS feed size cap: an operator dial, sized for a real calendar, enforced by streaming.

Every test here fails on the pre-#1182 shape, where ``MAX_ICS_BYTES`` was a 2 MB module constant
with three references all in one file: no env, no compose value, no Helm value, and therefore no
workaround to offer a user whose work calendar exceeded it. A provider's secret iCal address
exports the WHOLE calendar — history included, no time-range parameter — so those feeds failed
ALWAYS, not sometimes.

Offline: the SSRF-pinned transport refuses loopback by design (127.0.0.0/8 is on its blocklist), so
these drive ``fetch_ics`` through the same seam the existing taxonomy test uses — monkeypatching
``webhooks.ssrf.build_pinned_transport`` with an httpx MockTransport.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from meeting_api.calendar_sync import adapters as cal_adapters

URL = "https://calendar.example.com/private-abc/basic.ics"


def _feed(payload_bytes: int) -> str:
    """A VALID ICS feed padded to roughly ``payload_bytes`` — the shape of a long calendar history:
    parseable from the first line, large only because of how many events it carries."""
    head = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
    event = ("BEGIN:VEVENT\r\nUID:{i}@test\r\nDTSTAMP:20200101T000000Z\r\n"
             "DTSTART:20200101T090000Z\r\nSUMMARY:Historic standup {i}\r\n"
             "DESCRIPTION:{pad}\r\nEND:VEVENT\r\n")
    out = [head]
    size = len(head)
    i = 0
    while size < payload_bytes:
        block = event.format(i=i, pad="x" * 400)
        out.append(block)
        size += len(block)
        i += 1
    out.append("END:VCALENDAR\r\n")
    return "".join(out)


def _fetch(monkeypatch, *, body=None, status=200, content=None, headers=None):
    """Run ``fetch_ics`` against a mocked transport. ``content`` takes an async byte iterator for
    the streaming tests; ``body`` takes plain text."""
    def fake_transport():
        def handler(request):
            if content is not None:
                return httpx.Response(status, content=content, headers=headers or {})
            return httpx.Response(status, text=body, headers=headers or {})
        return httpx.MockTransport(handler)

    import meeting_api.webhooks.ssrf as ssrf
    monkeypatch.setattr(ssrf, "build_pinned_transport", fake_transport)
    return asyncio.run(cal_adapters.fetch_ics(URL))


# ── the dial exists, and it is what the fetch obeys ──────────────────────────────────────────────


def test_operator_can_raise_the_cap_and_the_feed_then_imports(monkeypatch):
    """THE defect. A feed past the built-in default must become acceptable by configuration alone —
    no source edit. Pre-#1182 there was no key to set, so the feed was refused whatever the operator
    did."""
    feed = _feed(3 * 1024 * 1024)
    monkeypatch.setenv("CALENDAR_MAX_ICS_BYTES", str(8 * 1024 * 1024))
    text, err = _fetch(monkeypatch, body=feed)
    assert err is None, f"a feed inside the configured cap must import: {err}"
    assert text is not None and text.startswith("BEGIN:VCALENDAR")


def test_operator_can_lower_the_cap(monkeypatch):
    """The dial turns both ways — an operator on a tight memory budget can tighten it."""
    monkeypatch.setenv("CALENDAR_MAX_ICS_BYTES", "4096")
    text, err = _fetch(monkeypatch, body=_feed(64 * 1024))
    assert text is None
    assert "too large" in (err or "")


def test_default_admits_a_work_calendar_with_years_of_history(monkeypatch):
    """With NOTHING configured, a multi-megabyte work calendar must import. The old 2 MB default
    made this feed fail always — the reporter's exact case."""
    monkeypatch.delenv("CALENDAR_MAX_ICS_BYTES", raising=False)
    text, err = _fetch(monkeypatch, body=_feed(5 * 1024 * 1024))
    assert err is None, f"a 5 MB work calendar must import on stock config: {err}"
    assert text is not None and "BEGIN:VEVENT" in text


def test_oversize_reason_names_the_configured_limit_and_the_key(monkeypatch):
    """The refusal is only actionable if it names the limit in force and the knob that moves it.
    The old message hardcoded 'over 2 MB' and named nothing."""
    monkeypatch.setenv("CALENDAR_MAX_ICS_BYTES", str(1024 * 1024))
    text, err = _fetch(monkeypatch, body=_feed(3 * 1024 * 1024))
    assert text is None
    assert "1 MB" in (err or ""), f"the reason must name the CONFIGURED cap: {err}"
    assert "CALENDAR_MAX_ICS_BYTES" in (err or ""), f"the reason must name the dial: {err}"


@pytest.mark.parametrize("raw", ["", "   ", "not-a-number", "0", "-1"])
def test_unusable_values_fall_back_to_the_default(monkeypatch, raw):
    """A typo must not silently become a 0-byte cap that refuses every feed, and must not raise
    inside a sweep that is required never to."""
    monkeypatch.setenv("CALENDAR_MAX_ICS_BYTES", raw)
    assert cal_adapters.max_ics_bytes() == cal_adapters.DEFAULT_MAX_ICS_BYTES


def test_default_is_read_at_call_time_not_import_time(monkeypatch):
    """config.v1's rule: env state is computed at call time, so a value set after import still
    applies (and the sync-now edge and the sweep can never disagree)."""
    monkeypatch.setenv("CALENDAR_MAX_ICS_BYTES", "12345")
    assert cal_adapters.max_ics_bytes() == 12345


# ── the cap now bounds MEMORY, because the body streams ──────────────────────────────────────────


def test_oversize_body_is_abandoned_mid_download(monkeypatch):
    """The old check was ``len(resp.content) > MAX_ICS_BYTES`` — the whole body was already resident
    before the size was consulted, so the cap described memory it had not protected. Streaming makes
    the claim true: a feed far past the cap must stop being read, not be buffered and then judged."""
    served = {"chunks": 0}
    chunk = b"BEGIN:VCALENDAR\r\n" + b"x" * (64 * 1024)

    async def endless():
        # 4 GB if fully drained. Any implementation that buffers before judging hangs or dies here.
        for _ in range(65536):
            served["chunks"] += 1
            yield chunk

    monkeypatch.setenv("CALENDAR_MAX_ICS_BYTES", str(1024 * 1024))
    text, err = _fetch(monkeypatch, content=endless())
    assert text is None
    assert "too large" in (err or "")
    assert served["chunks"] < 64, (
        f"the download must stop at the cap, not drain the body: {served['chunks']} chunks read")


# ── the better message is no longer hidden behind the size test ──────────────────────────────────


def test_oversize_html_page_gets_the_teachable_reason_not_too_large(monkeypatch):
    """Reported alongside the cap: the size test ran BEFORE the HTML test, so a login or error page
    bigger than the cap was reported as 'the feed is too large' and the message that actually helps
    — copy the Secret address in iCal format — was unreachable."""
    monkeypatch.setenv("CALENDAR_MAX_ICS_BYTES", str(64 * 1024))
    # Deliberately past the OLD hardcoded 2 MB as well as the cap set here, so this fails on the
    # pre-#1182 ordering no matter what the env says (which that code ignored anyway).
    page = "<!DOCTYPE html><html><body>" + ("<p>sign in</p>" * 240000) + "</body></html>"
    assert len(page.encode()) > 2 * 1024 * 1024
    text, err = _fetch(monkeypatch, body=page)
    assert text is None
    assert "web page" in (err or ""), f"an oversize HTML page must still teach the fix: {err}"
    assert "too large" not in (err or "")


# ── no regression in the rest of the taxonomy ────────────────────────────────────────────────────


def test_small_feed_shorter_than_the_sniff_window_still_validates(monkeypatch):
    """A tiny feed never reaches the sniff threshold mid-stream — it must still be sniffed at the
    end, so both the accept and the reject paths hold below 4 KB."""
    ok_text, ok_err = _fetch(monkeypatch, body="BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    assert ok_err is None and ok_text is not None
    bad_text, bad_err = _fetch(monkeypatch, body="not a calendar at all")
    assert bad_text is None and "BEGIN:VCALENDAR" in (bad_err or "")


def test_leading_whitespace_does_not_starve_the_sniff(monkeypatch):
    """The sniff matches a 200-CHARACTER head; it buffers 4 KB first so padding cannot push
    BEGIN:VCALENDAR out of the window and produce a false 'not an ICS calendar'."""
    text, err = _fetch(monkeypatch, body="\r\n" * 300 + _feed(32 * 1024))
    assert err is None, f"leading whitespace must not defeat the sniff: {err}"
    assert text is not None


def test_status_and_redirect_reasons_are_unchanged(monkeypatch):
    """Streaming reads headers first, so the pre-body refusals must behave exactly as before."""
    _, err404 = _fetch(monkeypatch, body="", status=404)
    assert "HTTP 404" in (err404 or "")
    _, err302 = _fetch(monkeypatch, body="", status=302)
    assert "redirects" in (err302 or "")


def test_declared_charset_is_honoured(monkeypatch):
    """``resp.text`` used to do this decoding; the manual buffer must not regress a non-UTF-8 feed
    into replacement characters."""
    feed = "BEGIN:VCALENDAR\r\nSUMMARY:café\r\nEND:VCALENDAR\r\n"
    text, err = _fetch(monkeypatch, content=feed.encode("latin-1"),
                       headers={"Content-Type": "text/calendar; charset=latin-1"})
    assert err is None
    assert "café" in (text or "")


# ── the promise, end to end: a real work calendar connects and imports a meeting ─────────────────


async def test_work_calendar_with_years_of_history_imports_its_next_meeting(monkeypatch):
    """The user-visible outcome, not just the fetch verdict: a feed whose SIZE comes entirely from
    history the parser will discard must still reach the parser and produce a joinable planned row.

    This is the shape #1182 describes — a secret iCal address exporting years of past events plus
    the meetings that matter — and pre-#1182 it returned no rows at all, on every sweep, forever.
    """
    from datetime import datetime, timedelta, timezone

    from meeting_api.calendar_sync import runner
    from meeting_api.collector.fakes import InMemoryTranscriptStore

    now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    soon = (now + timedelta(hours=3)).strftime("%Y%m%dT%H%M%SZ")

    # ~4 MB of history — past the old 2 MB refusal — then the one upcoming meeting with a link.
    history = "".join(
        f"BEGIN:VEVENT\r\nUID:past-{i}@corp\r\nDTSTAMP:20200101T000000Z\r\n"
        f"DTSTART:20200106T090000Z\r\nSUMMARY:Standup {i}\r\n"
        f"DESCRIPTION:{'notes ' * 60}\r\nEND:VEVENT\r\n"
        for i in range(9000)
    )
    upcoming = (f"BEGIN:VEVENT\r\nUID:next@corp\r\nDTSTAMP:20260801T000000Z\r\nDTSTART:{soon}\r\n"
                f"SUMMARY:Architecture review\r\n"
                f"LOCATION:https://meet.google.com/abc-defg-hij\r\nEND:VEVENT\r\n")
    feed = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//corp//EN\r\n" + history + upcoming + "END:VCALENDAR\r\n"
    assert len(feed.encode()) > 2 * 1024 * 1024, "the fixture must exceed the limit under test"

    def fake_transport():
        return httpx.MockTransport(lambda request: httpx.Response(200, text=feed))

    import meeting_api.webhooks.ssrf as ssrf
    monkeypatch.setattr(ssrf, "build_pinned_transport", fake_transport)
    monkeypatch.delenv("CALENDAR_MAX_ICS_BYTES", raising=False)  # stock config must suffice

    store = InMemoryTranscriptStore()
    stamp = await runner.run_user_sync(store, {"user_id": 7, "ics_url": URL, "auto_join": True},
                                       now=now)

    assert stamp["last_error"] is None, f"the sync must not fail on a real work calendar: {stamp}"
    assert stamp["counts"]["created"] == 1, f"the upcoming meeting must import: {stamp}"
    (row,) = await store.list_meetings(7)
    assert row["status"] == "scheduled"
    assert row["data"]["title"] == "Architecture review"
    assert row["data"]["auto_join"] is True

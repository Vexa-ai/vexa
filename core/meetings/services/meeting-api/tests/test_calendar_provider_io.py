"""provider_io — fetching a window of events and refreshing a token, offline.

Driven through a stub client rather than a live API. What is pinned here is the stuff that bites in
production: the query parameters the readers depend on, pagination, and — most of all — that every
failure comes back as a sentence the person who connected the calendar can act on, never a raised
exception that kills the sweep for everyone else.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meeting_api.calendar_sync.provider_io import (
    GOOGLE_SCOPES,
    MAX_PAGES,
    fetch_google_events,
    fetch_microsoft_events,
    refresh_access_token,
)

START = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
END = START + timedelta(days=14)


class Response:
    def __init__(self, status=200, body=None, boom=False):
        self.status_code = status
        self._body = body if body is not None else {}
        self._boom = boom

    def json(self):
        if self._boom:
            raise ValueError("not json")
        return self._body


class StubClient:
    """Records what was asked for and replays queued responses."""

    def __init__(self, *responses):
        self._queue = list(responses)
        self.calls: list[dict] = []

    async def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params or {}, "headers": headers or {}})
        return self._next()

    async def post(self, url, data=None):
        self.calls.append({"url": url, "data": data or {}})
        return self._next()

    def _next(self):
        if not self._queue:
            raise AssertionError("stub client ran out of responses")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class DeadClient:
    async def get(self, *a, **k):
        raise ConnectionError("down")

    async def post(self, *a, **k):
        raise ConnectionError("down")


# ------------------------------------------------------------------ the request the readers need

async def test_google_asks_the_provider_to_expand_recurrences_and_show_cancellations():
    """events_from_google depends on both. Drop either and recurring meetings or retirements break,
    silently and only in production."""
    client = StubClient(Response(body={"items": [{"id": "a"}]}))

    items, reason = await fetch_google_events(
        client, access_token="tok", calendar_id="primary", window_start=START, window_end=END)

    assert reason is None and items == [{"id": "a"}]
    params = client.calls[0]["params"]
    assert params["singleEvents"] == "true"
    assert params["showDeleted"] == "true"
    assert params["timeMin"] == START.isoformat()
    assert params["timeMax"] == END.isoformat()
    assert client.calls[0]["headers"]["Authorization"] == "Bearer tok"


async def test_a_calendar_id_cannot_escape_into_the_url():
    """Calendar ids are stored user input. Un-escaped, one containing ../ or a query would point
    the authenticated request somewhere else."""
    client = StubClient(Response(body={"items": []}))

    await fetch_google_events(client, access_token="tok",
                              calendar_id="../../tokeninfo?x=", window_start=START, window_end=END)

    url = client.calls[0]["url"]
    assert url.startswith("https://www.googleapis.com/calendar/v3/calendars/")
    assert "../" not in url and "?" not in url


async def test_microsoft_pins_the_timezone_so_the_readers_fallback_is_not_load_bearing():
    """Without this header Graph answers in Windows zone ids, which are not IANA and do not load."""
    client = StubClient(Response(body={"value": []}))

    await fetch_microsoft_events(client, access_token="tok", window_start=START, window_end=END)

    assert client.calls[0]["headers"]["Prefer"] == 'outlook.timezone="UTC"'


# ------------------------------------------------------------------------------------ pagination

async def test_google_follows_every_page():
    client = StubClient(
        Response(body={"items": [{"id": "1"}], "nextPageToken": "p2"}),
        Response(body={"items": [{"id": "2"}]}),
    )

    items, reason = await fetch_google_events(
        client, access_token="tok", calendar_id="primary", window_start=START, window_end=END)

    assert reason is None
    assert [i["id"] for i in items] == ["1", "2"]
    assert client.calls[1]["params"]["pageToken"] == "p2"


async def test_graph_follows_its_next_link_without_re_sending_our_params():
    """The nextLink already carries the full query; sending ours alongside duplicates them."""
    client = StubClient(
        Response(body={"value": [{"id": "1"}],
                       "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/calendarView?$skip=250"}),
        Response(body={"value": [{"id": "2"}]}),
    )

    items, reason = await fetch_microsoft_events(
        client, access_token="tok", window_start=START, window_end=END)

    assert reason is None and [i["id"] for i in items] == ["1", "2"]
    assert client.calls[1]["params"] == {}


async def test_a_paging_link_to_another_host_is_refused():
    """The nextLink is remote input and it is followed with an Authorization header attached."""
    client = StubClient(Response(body={"value": [], "@odata.nextLink": "https://evil.example/steal"}))

    items, reason = await fetch_microsoft_events(
        client, access_token="tok", window_start=START, window_end=END)

    assert items is None
    assert "don't trust" in reason
    assert len(client.calls) == 1, "the second request must never have been made"


async def test_endless_pagination_stops_rather_than_eating_the_sweep():
    pages = [Response(body={"items": [], "nextPageToken": f"p{i}"}) for i in range(MAX_PAGES + 2)]
    client = StubClient(*pages)

    items, reason = await fetch_google_events(
        client, access_token="tok", calendar_id="primary", window_start=START, window_end=END)

    assert items is None
    assert "more events than we can read" in reason
    assert len(client.calls) == MAX_PAGES


# -------------------------------------------------------------- failures are sentences, not raises

async def test_an_expired_authorization_tells_the_user_to_reconnect():
    for status in (401, 403):
        items, reason = await fetch_google_events(
            StubClient(Response(status=status)), access_token="tok", calendar_id="primary",
            window_start=START, window_end=END)
        assert items is None
        assert "reconnect" in reason


async def test_a_deleted_calendar_says_so_rather_than_saying_401():
    items, reason = await fetch_google_events(
        StubClient(Response(status=404)), access_token="tok", calendar_id="gone",
        window_start=START, window_end=END)
    assert items is None
    assert "no longer exists" in reason


async def test_rate_limiting_and_provider_outages_read_as_transient():
    for status in (429, 500, 503):
        _, reason = await fetch_microsoft_events(
            StubClient(Response(status=status)), access_token="tok",
            window_start=START, window_end=END)
        assert "retry" in reason


async def test_an_unreachable_provider_never_raises_into_the_sweep():
    """One user's dead network must not stall every other user's calendar."""
    items, reason = await fetch_google_events(
        DeadClient(), access_token="tok", calendar_id="primary",
        window_start=START, window_end=END)
    assert items is None and "couldn't reach" in reason


async def test_an_unreadable_body_is_a_reason_not_a_traceback():
    items, reason = await fetch_google_events(
        StubClient(Response(boom=True)), access_token="tok", calendar_id="primary",
        window_start=START, window_end=END)
    assert items is None and "couldn't read" in reason


# ----------------------------------------------------------------------------------- token refresh

async def test_a_refresh_returns_the_new_access_token():
    client = StubClient(Response(body={"access_token": "new", "expires_in": 3599}))

    token, reason = await refresh_access_token(
        client, provider="google", refresh_token="r", client_id="cid", client_secret="sec")

    assert reason is None
    assert token == {"access_token": "new", "expires_in": 3599}
    assert client.calls[0]["data"]["grant_type"] == "refresh_token"


async def test_a_revoked_grant_is_terminal_and_says_reconnect():
    """invalid_grant means revoked, password-changed, or expired through disuse. Retrying it on a
    loop hammers the identity provider with a credential that will never work again."""
    client = StubClient(Response(status=400, body={"error": "invalid_grant"}))

    token, reason = await refresh_access_token(
        client, provider="google", refresh_token="r", client_id="cid", client_secret="sec")

    assert token is None
    assert "reconnect" in reason


async def test_microsoft_refresh_resends_the_scopes_it_needs():
    """Graph drops offline_access on refresh unless it is asked for again — lose it and the NEXT
    refresh has no refresh token to use."""
    client = StubClient(Response(body={"access_token": "new"}))

    await refresh_access_token(client, provider="microsoft", refresh_token="r",
                               client_id="cid", client_secret="sec")

    assert "offline_access" in client.calls[0]["data"]["scope"]


async def test_a_refresh_against_a_dead_network_is_a_reason_not_a_raise():
    token, reason = await refresh_access_token(
        DeadClient(), provider="google", refresh_token="r", client_id="c", client_secret="s")
    assert token is None and "couldn't reach" in reason


# ------------------------------------------------------------------------------------------ scopes

def test_we_ask_for_read_only_scopes_and_nothing_wider():
    """A wider scope is a bigger consent prompt, a slower Google review, and more to lose if a
    token leaks. We never write to a calendar."""
    assert all(s.endswith(".readonly") for s in GOOGLE_SCOPES)
    assert not any("events" == s.rsplit("/", 1)[-1] for s in GOOGLE_SCOPES)

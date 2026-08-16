"""L3 seam — the two adapters, driven against fake HTTP.

Both adapters are exercised through ``httpx.MockTransport``, so these assert the SHIPPED wire
behaviour (paths, params, headers, bodies, error translation) with no Mailpit and no gateway. The
load-bearing ones: the mailroom sends the caller's API key on every control-plane call, and a
refusal comes back as ``{"error": ...}`` rather than an exception that would kill the poll loop.
"""
from __future__ import annotations

import json

import httpx
import pytest

from vexa_mailroom.adapters import MailpitSource, MeetingApiClient


def _mailpit(messages: list[dict], raws: dict[str, bytes], seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v1/messages":
            start = int(request.url.params.get("start", 0))
            limit = int(request.url.params.get("limit", 50))
            return httpx.Response(200, json={"total": len(messages),
                                             "messages": messages[start:start + limit]})
        if request.url.path.endswith("/raw"):
            mid = request.url.path.split("/")[-2]
            if mid in raws:
                return httpx.Response(200, content=raws[mid])
            return httpx.Response(404)
        return httpx.Response(404)                            # pragma: no cover
    return httpx.MockTransport(handler)


async def test_mailpit_returns_new_messages_in_arrival_order():
    messages = [                                              # Mailpit lists newest FIRST
        {"ID": "c", "Created": "2026-08-16T09:03:00Z"},
        {"ID": "b", "Created": "2026-08-16T09:02:00Z"},
        {"ID": "a", "Created": "2026-08-16T09:01:00Z"},
    ]
    raws = {m["ID"]: f"raw-{m['ID']}".encode() for m in messages}
    seen: list[httpx.Request] = []
    source = MailpitSource("http://mailpit:8025", transport=_mailpit(messages, raws, seen))

    out = await source.fetch_new(since=None, limit=10)
    assert [m.id for m in out] == ["a", "b", "c"]             # oldest first, as the loop needs
    assert [m.raw for m in out] == [b"raw-a", b"raw-b", b"raw-c"]


async def test_mailpit_stops_at_the_cursor():
    messages = [
        {"ID": "c", "Created": "2026-08-16T09:03:00Z"},
        {"ID": "b", "Created": "2026-08-16T09:02:00Z"},
        {"ID": "a", "Created": "2026-08-16T09:01:00Z"},
    ]
    raws = {m["ID"]: b"x" for m in messages}
    source = MailpitSource("http://mailpit:8025", transport=_mailpit(messages, raws, []))
    out = await source.fetch_new(since="2026-08-16T09:02:00Z", limit=10)
    assert [m.id for m in out] == ["c"]


async def test_mailpit_skips_a_message_whose_body_vanished():
    """A message deleted between the list and the fetch is skipped, not fatal."""
    messages = [{"ID": "a", "Created": "2026-08-16T09:01:00Z"}]
    source = MailpitSource("http://mailpit:8025", transport=_mailpit(messages, {}, []))
    assert await source.fetch_new(since=None, limit=10) == []


def _api(record: list[httpx.Request], status: int = 201, body: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        record.append(request)
        if request.method == "DELETE":
            return httpx.Response(status if status != 201 else 204)
        return httpx.Response(status, json=body if body is not None else {"id": 7, "status": "scheduled"})
    return httpx.MockTransport(handler)


async def test_create_posts_the_planned_meeting_with_the_api_key():
    seen: list[httpx.Request] = []
    api = MeetingApiClient("http://gateway:8000", "key-123", transport=_api(seen))
    row = await api.create_planned_meeting(workspace_id="ws-mk-dev",
                                           meeting_url="https://meet.google.com/abc-defg-hij",
                                           title="Quarterly sync",
                                           scheduled_at="2026-08-18T14:00:00+00:00")
    assert row["id"] == 7
    request = seen[-1]
    assert (request.method, request.url.path) == ("POST", "/meetings")
    assert request.headers["x-api-key"] == "key-123"
    assert json.loads(request.content) == {
        "meeting_url": "https://meet.google.com/abc-defg-hij",
        "auto_join": True,
        "title": "Quarterly sync",
        "scheduled_at": "2026-08-18T14:00:00+00:00",
        "workspace_id": "ws-mk-dev",
    }


async def test_update_patches_only_what_changed():
    seen: list[httpx.Request] = []
    api = MeetingApiClient("http://gateway:8000", "key-123", transport=_api(seen, status=200))
    await api.update_planned_meeting(7, scheduled_at="2026-08-19T11:30:00+00:00")
    assert (seen[-1].method, seen[-1].url.path) == ("PATCH", "/meetings/7")
    assert json.loads(seen[-1].content) == {"scheduled_at": "2026-08-19T11:30:00+00:00"}


@pytest.mark.parametrize("status,expected", [(200, True), (204, True), (404, True), (409, False)])
async def test_cancel_translates_the_control_plane_verdict(status, expected):
    api = MeetingApiClient("http://gateway:8000", "key-123", transport=_api([], status=status))
    assert await api.cancel_planned_meeting(7) is expected


async def test_a_refusal_becomes_an_error_dict_not_an_exception():
    api = MeetingApiClient("http://gateway:8000", "key-123",
                           transport=_api([], status=409, body={"detail": "already exists"}))
    row = await api.create_planned_meeting(workspace_id=None, meeting_url="https://meet.google.com/abc-defg-hij",
                                           title=None, scheduled_at=None)
    assert row["error"].startswith("409")
    assert "already exists" in row["error"]


async def test_transport_failure_is_an_error_dict_too():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)
    api = MeetingApiClient("http://gateway:8000", "key-123", transport=httpx.MockTransport(boom))
    assert (await api.create_planned_meeting(workspace_id=None, meeting_url="u", title=None,
                                             scheduled_at=None))["error"].startswith("transport:")
    assert await api.cancel_planned_meeting(7) is False

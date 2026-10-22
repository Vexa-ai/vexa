"""The sensors role (ADR-0050): flows' calendar sensor reads a person's calendar CHANGES through the
broker — read-only, the token never leaves, the request signed for exactly the person it reads,
refused to every other role, audited — and the incremental read itself: sync tokens, 410 as resync,
cancellations as changes, meeting links only from meeting hosts."""
import time
from datetime import datetime, timezone
from unittest.mock import patch

import httpx

from credential_broker import providers
from credential_broker.calendar_sync import calendar_changes, video_link
from conftest import OWNER

PATH = "/api/internal/sensor/calendar-changes"


def _calendar(connection, ready, store, *, actor=OWNER):
    cid = connection(provider="google_calendar", actor=actor)
    ready(cid)
    store.put(cid, {"owner": actor, "value": {"access_token": "SECRET", "expires_at": time.time() + 300}})
    return cid


def test_the_sensor_reads_changes_for_the_person_it_is_signed_for(signed, connection, ready, store):
    cid = _calendar(connection, ready, store)
    assert signed("agent", "POST", "/api/sensor-grants", {}, actor=OWNER).status_code == 200
    answer = {"items": [{"event_id": "e1", "status": "confirmed", "self_response": "accepted",
                         "organizer_self": False, "calendar_id": "primary"}],
              "cursor": "tok-2", "resync_required": False}
    with patch.object(providers, "calendar_changes", return_value=answer) as read:
        r = signed("sensors", "POST", PATH, {"owner": OWNER, "cursor": "tok-1"}, actor=OWNER)
    assert r.status_code == 200, r.text
    assert r.json() == {"connection_id": cid, **answer}
    assert read.call_args.args[0]["access_token"] == "SECRET" and read.call_args.args[1] == "tok-1"
    assert "SECRET" not in r.text


def test_a_sensor_key_cannot_read_another_person(signed, connection, ready, store):
    _calendar(connection, ready, store)
    r = signed("sensors", "POST", PATH, {"owner": OWNER}, actor="someone-else")
    assert r.status_code == 403


def test_every_other_role_is_refused(signed, connection, ready, store):
    _calendar(connection, ready, store)
    for role in ("agent", "human", "git", "bots"):
        assert signed(role, "POST", PATH, {"owner": OWNER}, actor=OWNER).status_code == 403, role


def test_no_ready_calendar_is_a_409(signed):
    assert signed("sensors", "POST", PATH, {"owner": OWNER}, actor=OWNER).status_code == 409


def test_incremental_read_sync_token_resync_and_cancellation():
    seen = []

    def respond(req):
        seen.append(dict(req.url.params))
        assert req.method == "GET" and req.url.host == "www.googleapis.com"
        if req.url.params.get("syncToken") == "stale":
            return httpx.Response(410, json={})
        if "pageToken" not in req.url.params:
            return httpx.Response(200, json={"items": [
                {"id": "a", "etag": "1", "status": "confirmed", "summary": "Sync",
                 "start": {"dateTime": "2027-03-01T10:00:00+01:00"}, "end": {"dateTime": "2027-03-01T10:30:00+01:00"},
                 "hangoutLink": "https://meet.google.com/abc-defg-hij",
                 "attendees": [{"email": "x@y", "self": True, "responseStatus": "declined"}]}],
                "nextPageToken": "p2"})
        return httpx.Response(200, json={"items": [{"id": "b", "status": "cancelled"}], "nextSyncToken": "tok-1"})
    now = datetime(2027, 3, 1, tzinfo=timezone.utc)
    with httpx.Client(transport=httpx.MockTransport(respond)) as c:
        first = calendar_changes({"access_token": "x"}, None, http=c, now=now)
        stale = calendar_changes({"access_token": "x"}, "stale", http=c)
    assert first["cursor"] == "tok-1" and not first["resync_required"]
    a, b = first["items"]
    assert a["video_url"] == "https://meet.google.com/abc-defg-hij" and a["self_response"] == "declined"
    assert a["start"] == "2027-03-01T10:00:00+01:00"
    assert b == {"event_id": "b", "status": "cancelled", "etag": "", "updated": "", "self_response": "organizer",
                 "organizer_self": False, "calendar_id": "primary"}
    assert seen[0]["showDeleted"] == "true" and "timeMin" in seen[0] and "syncToken" not in seen[0]
    assert stale == {"items": [], "cursor": None, "resync_required": True}


def test_a_meeting_link_comes_only_from_a_meeting_host():
    assert video_link({"location": "https://zoom.us/j/123?pwd=x"}) == "https://zoom.us/j/123?pwd=x"
    assert video_link({"description": "join http://meet.google.com/x or https://evil.test/meet.google.com"}) is None
    assert video_link({"location": "https://user:pw@meet.google.com/x"}) is None

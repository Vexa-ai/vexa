"""Google Calendar changes: an opt-in permission, idempotent by request id, held to the forwarded
connection policy, and only on events the person can change. Google is mocked with httpx's
MockTransport, the same injection the read tests use."""
import json
import time
from unittest.mock import patch

import httpx
import pytest

from credential_broker import identity_token, providers
from conftest import GATEWAY_KEY, OWNER

WRITE = {"access_token": "TOKEN", "expires_at": time.time() + 500, "scope": providers.CALENDAR_WRITE_SCOPE}
CREATE = {"request_id": "fixture-event-0001", "action": "calendar.event.create", "title": "Review",
          "start": {"date_time": "2026-10-20T10:00:00", "time_zone": "Europe/Lisbon"},
          "end": {"date_time": "2026-10-20T10:30:00", "time_zone": "Europe/Lisbon"},
          "attendees": ["ada@example.test"]}
CREATED = {"status": "event_created", "event_id": "0a1b2c3d4e", "calendar_id": "primary", "html_link": "",
           "meet_link": "", "notified": False}


@pytest.fixture
def calendar(connection, ready, store):
    def make(value=None):
        cid = connection("google_calendar")
        store.put(cid, {"owner": OWNER, "value": value or WRITE})
        ready(cid)
        return cid
    return make


def path(cid):
    return f"/api/connections/{cid}/calendar-event"


def test_without_the_write_permission_nothing_is_written(signed, calendar, broker):
    cid = calendar({**WRITE, "scope": "https://www.googleapis.com/auth/calendar.events.readonly"})
    before = broker.sql("SELECT setup_request FROM connections WHERE id=?", (cid,), one=True)["setup_request"]
    with patch.object(providers, "write_calendar_event") as write:
        r = signed("agent", "POST", path(cid), CREATE).json()
    write.assert_not_called()
    assert r["status"] == "permission_required" and r["permission"] == "calendar_write"
    assert "Enable calendar changes" in r["instruction"]
    # The panel opens on a changed setup request, as it does for drafts.
    assert broker.sql("SELECT setup_request FROM connections WHERE id=?", (cid,), one=True)["setup_request"] != before


def test_an_idempotent_retry_returns_the_first_result(signed, calendar):
    cid = calendar()
    with patch.object(providers, "write_calendar_event", return_value=CREATED) as write:
        first = signed("agent", "POST", path(cid), CREATE).json()
        again = signed("agent", "POST", path(cid), CREATE).json()
        assert first == again == CREATED and write.call_count == 1
        assert signed("agent", "POST", path(cid), {**CREATE, "title": "Other"}).status_code == 409


def test_an_unknown_outcome_is_not_retried(signed, calendar):
    from credential_broker.faults import UpstreamFault
    cid = calendar()
    with patch.object(providers, "write_calendar_event", side_effect=UpstreamFault("provider", "unreachable", "x")):
        assert signed("agent", "POST", path(cid), CREATE).status_code == 503
    r = signed("agent", "POST", path(cid), CREATE)
    assert r.status_code == 409 and "check your calendar before retrying" in r.json()["detail"]


@pytest.mark.parametrize("change,policy,key", [
    ({"notify": True}, {}, "calendar_notify_attendees"),
    ({"calendar_id": "team@group.calendar.google.com"}, {}, "calendar_ids"),
    ({}, {"attendee_domains": ["inside.test"]}, "calendar_attendee_domains"),
])
def test_each_policy_key_refuses_by_name(signed, calendar, change, policy, key):
    cid = calendar()
    with patch.object(providers, "write_calendar_event", return_value=CREATED) as write:
        r = signed("agent", "POST", path(cid), {**CREATE, **change, "policy": policy})
    write.assert_not_called()
    assert r.status_code == 409 and r.json()["detail"].startswith(f"Refused by policy {key}:")


def test_policy_that_allows_it_lets_it_through(signed, calendar):
    cid = calendar()
    policy = {"notify_attendees": True, "calendars": None, "attendee_domains": ["example.test"]}
    with patch.object(providers, "write_calendar_event", return_value={**CREATED, "notified": True}) as write:
        r = signed("agent", "POST", path(cid), {**CREATE, "notify": True, "calendar_id": "team@group.calendar.google.com",
                                                "policy": policy})
    assert r.status_code == 200 and write.call_args.kwargs["notify"] is True


def test_a_notified_change_leaves_its_own_audit_row(signed, calendar, broker):
    """#1783: asking Google to email the attendees is mail sent in the person's name, so it is
    recorded as its own act — which change, how many attendees — beside the change itself."""
    cid = calendar()
    policy = {"notify_attendees": True, "calendars": None, "attendee_domains": None}
    with patch.object(providers, "write_calendar_event", return_value={**CREATED, "notified": True}):
        assert signed("agent", "POST", path(cid), {**CREATE, "notify": True, "policy": policy}).status_code == 200
        quiet = {**CREATE, "request_id": "fixture-event-0002", "policy": policy}
        assert signed("agent", "POST", path(cid), quiet).status_code == 200
    rows = broker.sql("SELECT action, outcome, operation_id FROM audit WHERE connection=? AND "
                      "action='calendar.attendees_notified'", (cid,), rows=True)
    assert [(r["outcome"], r["operation_id"]) for r in rows] == [("sent:1", "fixture-event-0001")]


def _worker(regime="autonomous"):
    return identity_token.sign(GATEWAY_KEY, {"sub": OWNER, "scopes": ["bot", "tx"],
                                             "delegation": {"regime": regime, "workspaces": "*"}})


def test_a_background_routine_is_refused_whatever_policy_it_forwards(signed, calendar):
    """A change to a calendar needs a person in the loop. The policy in the body is the caller's
    word, so it never opens a write to a worker running without one (ADR-0040)."""
    cid = calendar()
    with patch.object(providers, "write_calendar_event", return_value=CREATED) as write:
        for body in (CREATE, {**CREATE, "policy": {"unattended": True}}):
            r = signed("agent", "POST", path(cid), body, identity=_worker())
            assert r.status_code == 403 and r.json()["detail"] == "This session runs without a person in the loop"
        assert not write.called
        assert signed("agent", "POST", path(cid), CREATE, identity=_worker("human")).status_code == 200


def test_create_takes_no_event_id_and_update_needs_one(signed, calendar):
    cid = calendar()
    assert signed("agent", "POST", path(cid), {**CREATE, "event_id": "0a1b2c3d4e"}).status_code == 422
    assert signed("agent", "POST", path(cid), {**CREATE, "request_id": "fixture-event-0009",
                                               "action": "calendar.event.update"}).status_code == 422


# ── the provider adapter, against a mocked Google ────────────────────────────────────────────────

def google(events: dict, seen: list):
    def respond(req):
        seen.append(req)
        url = str(req.url)
        eid = req.url.path.rsplit("/", 1)[-1]
        if req.method == "GET":
            return httpx.Response(200, json=events[eid]) if eid in events else httpx.Response(404, json={})
        if req.method == "DELETE":
            return httpx.Response(204)
        body = json.loads(req.content)
        return httpx.Response(200, json={"id": body.get("id", eid), "htmlLink": "https://calendar.test/e",
                                         "hangoutLink": "https://meet.test/x" if "conferenceData" in body else ""})
    return httpx.Client(transport=httpx.MockTransport(respond))


def test_create_is_sent_with_no_attendee_mail_and_a_request_derived_id():
    seen = []
    with google({}, seen) as c:
        out = providers.write_calendar_event(WRITE, "calendar.event.create", "primary", request_key="u:r1",
                                             fields={k: CREATE[k] for k in ("title", "start", "end", "attendees")},
                                             conference=True, http=c)
    req = seen[-1]
    assert req.method == "POST" and req.url.params["sendUpdates"] == "none"
    assert req.url.params["conferenceDataVersion"] == "1"
    body = json.loads(req.content)
    assert body["id"] == providers.event_id_for("u:r1") and body["attendees"] == [{"email": "ada@example.test"}]
    assert out["status"] == "event_created" and out["meet_link"] and out["notified"] is False


def test_update_and_cancel_of_an_event_that_does_not_resolve_are_refused():
    with google({}, []) as c:
        for action in ("calendar.event.update", "calendar.event.cancel"):
            with pytest.raises(providers.ProviderError, match="not found"):
                providers.write_calendar_event(WRITE, action, "primary", event_id="0a1b2c3d4e",
                                               fields={"title": "x"}, request_key="u:r", http=c)


def test_an_event_the_person_does_not_organize_cannot_be_changed():
    foreign = {"id": "0a1b2c3d4e", "status": "confirmed", "organizer": {"email": "x@other.test"}}
    with google({"0a1b2c3d4e": foreign}, []) as c:
        with pytest.raises(providers.ProviderError, match="cannot change"):
            providers.write_calendar_event(WRITE, "calendar.event.update", "primary", event_id="0a1b2c3d4e",
                                           fields={"title": "x"}, request_key="u:r", http=c)
        with pytest.raises(providers.ProviderError, match="organizes"):
            providers.write_calendar_event(WRITE, "calendar.event.cancel", "primary", event_id="0a1b2c3d4e",
                                           request_key="u:r", http=c)


def test_cancel_deletes_the_organizers_event_quietly_unless_notify():
    own = {"id": "0a1b2c3d4e", "status": "confirmed", "organizer": {"self": True}}
    for notify, sent in ((False, "none"), (True, "all")):
        seen = []
        with google({"0a1b2c3d4e": own}, seen) as c:
            out = providers.write_calendar_event(WRITE, "calendar.event.cancel", "primary", event_id="0a1b2c3d4e",
                                                 notify=notify, request_key="u:r", http=c)
        assert seen[-1].method == "DELETE" and seen[-1].url.params["sendUpdates"] == sent
        assert out == {"status": "event_cancelled", "event_id": "0a1b2c3d4e", "calendar_id": "primary",
                       "notified": notify}


def test_update_patches_only_the_fields_given():
    own = {"id": "0a1b2c3d4e", "status": "confirmed", "organizer": {"self": True},
           "start": {"dateTime": "2026-10-20T10:00:00+01:00"}, "end": {"dateTime": "2026-10-20T10:30:00+01:00"}}
    seen = []
    with google({"0a1b2c3d4e": own}, seen) as c:
        providers.write_calendar_event(WRITE, "calendar.event.update", "primary", event_id="0a1b2c3d4e",
                                       fields={"title": "Moved", "start": None, "end": None, "description": None,
                                               "location": None, "attendees": None}, request_key="u:r", http=c)
    assert seen[-1].method == "PATCH" and json.loads(seen[-1].content) == {"summary": "Moved"}


def test_times_must_be_qualified_and_ordered():
    with google({}, []) as c:
        for start, end in (({"date_time": "2026-10-20T10:00:00"}, {"date_time": "2026-10-20T11:00:00"}),
                           ({"date_time": "2026-10-20T10:00:00Z"}, {"date_time": "2026-10-20T09:00:00Z"})):
            with pytest.raises(providers.ProviderError):
                providers.write_calendar_event(WRITE, "calendar.event.create", "primary", request_key="u:r",
                                               fields={"title": "x", "start": start, "end": end}, http=c)


def test_the_write_grant_is_asked_for_incrementally(signed, connection, broker):
    cid = connection("google_calendar")
    url = signed("human", "POST", f"/api/connections/{cid}/authorize", {"grant": "calendar_write"}).json()["authorize_url"]
    from urllib.parse import parse_qs, urlsplit
    query = parse_qs(urlsplit(url).query)
    assert providers.CALENDAR_WRITE_SCOPE in query["scope"][0].split()
    assert query["include_granted_scopes"] == ["true"]
    assert broker.sql("SELECT grant_name FROM oauth_states WHERE connection=?", (cid,), one=True)["grant_name"] == "calendar_write"
    plain = signed("human", "POST", f"/api/connections/{cid}/authorize", {}).json()["authorize_url"]
    plain_query = parse_qs(urlsplit(plain).query)
    assert providers.CALENDAR_WRITE_SCOPE not in plain_query["scope"][0].split()
    assert "include_granted_scopes" not in plain_query
    gmail = connection("google_email")
    assert signed("human", "POST", f"/api/connections/{gmail}/authorize", {"grant": "calendar_write"}).status_code == 409


def test_the_list_reports_the_write_permission(signed, connection, ready, store, broker):
    cid = connection("google_calendar")
    store.put(cid, {"owner": OWNER, "value": WRITE})
    ready(cid)
    with patch.object(providers, "write_calendar_event", return_value=CREATED):
        signed("agent", "POST", path(cid), CREATE)
    row = next(c for c in signed("agent", "GET", "/api/connections").json()["connections"] if c["id"] == cid)
    assert row["permissions"] == ["calendar_write"]


def test_consent_that_did_not_grant_the_write_scope_is_refused():
    read_only = "https://www.googleapis.com/auth/calendar.events.readonly"
    def respond(req):
        return httpx.Response(200, json={"access_token": "a", "token_type": "Bearer", "expires_in": 3600,
                                         "scope": read_only})
    client = {"client_id": "c", "client_secret": "s"}
    with httpx.Client(transport=httpx.MockTransport(respond)) as c:
        assert providers.tokens("google_calendar", client, code="x", verifier="v", redirect="r", http=c)["scope"] == read_only
        with pytest.raises(providers.ProviderError, match="not granted"):
            providers.tokens("google_calendar", client, code="x", verifier="v", redirect="r", http=c, grant="calendar_write")

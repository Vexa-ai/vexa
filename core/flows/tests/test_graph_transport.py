"""Microsoft Graph transport, driven entirely by a fake HTTP layer.

Nothing here touches a tenant — there is no M365 credential in the vault, so this suite IS the
validation of the Graph wiring for now (see the PR body's honest rung). It drives the four
transport contracts: cursor durability, threading, ICS attachments, and no internal polling."""
from __future__ import annotations

import base64
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows import SqliteDB  # noqa: E402
from flows_integrations import mail_transport as mt  # noqa: E402
from flows_integrations.graph_transport import GraphTransport  # noqa: E402
from flows_integrations.ics import parse_ics  # noqa: E402
from flows_integrations.mailbox import handle, route  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures_exchange"
ENV = {"VEXA_GRAPH_TENANT_ID": "t-1", "VEXA_GRAPH_CLIENT_ID": "c-1",
       "VEXA_GRAPH_CLIENT_SECRET": "s-1", "VEXA_GRAPH_MAILBOX": "vexa@oenb.at"}
GRAPH = "https://graph.microsoft.com/v1.0"


class FakeGraph:
    """A Graph double: canned pages/attachments/drafts, every call recorded."""

    def __init__(self, *, pages=None, attachments=None, draft_status=201, expires_in=3600):
        self.pages = pages or {}                # url (or "*") → response dict
        self.attachments = attachments or {}    # message id → list of attachment dicts
        self.draft_status = draft_status
        self.expires_in = expires_in
        self.calls: list[tuple[str, str, dict, dict]] = []
        self.tokens = 0
        self.sent: list[str] = []

    def __call__(self, method, url, headers, data):
        body = json.loads(data) if (data and headers.get("content-type") == "application/json") else {}
        self.calls.append((method, url, headers, body))
        if url.endswith("/oauth2/v2.0/token"):
            self.tokens += 1
            form = urllib.parse.parse_qs(data.decode())
            assert form["grant_type"] == ["client_credentials"]
            assert form["scope"] == ["https://graph.microsoft.com/.default"]
            return 200, json.dumps({"access_token": f"tok-{self.tokens}",
                                    "expires_in": self.expires_in})
        assert headers.get("Authorization", "").startswith("Bearer tok-"), "unauthenticated call"
        if "/attachments" in url:
            mid = url.split("/messages/")[1].split("/")[0]
            return 200, json.dumps({"value": self.attachments.get(mid, [])})
        if url.endswith("/send"):
            self.sent.append(url.split("/messages/")[1].split("/")[0])
            return 202, ""
        if method == "POST" and url.endswith("/messages"):
            if self.draft_status >= 400 and "internetMessageHeaders" in body:
                return self.draft_status, json.dumps({"error": {"code": "ErrorInvalidHeader"}})
            return 201, json.dumps({"id": "draft-1",
                                    "internetMessageId": "<made@oenb.at>", "echo": body})
        for key, page in self.pages.items():
            if key == "*" or key in url:
                return 200, json.dumps(page)
        return 200, json.dumps({"value": []})


def tp(fake, **env) -> GraphTransport:
    return GraphTransport(http=fake, env={**ENV, **env})


def msg(i, when, **extra):
    return {"id": f"m{i}", "receivedDateTime": when,
            "internetMessageId": f"<m{i}@oenb.at>", "subject": f"subject {i}",
            "from": {"emailAddress": {"address": "Anna.Bank@OeNB.at"}},
            "body": {"contentType": "text", "content": f"body {i}"},
            "hasAttachments": False, **extra}


# ------------------------------------------------------------------------------------- auth
def test_missing_env_is_a_loud_error():
    with pytest.raises(ValueError, match="VEXA_GRAPH_MAILBOX"):
        GraphTransport(http=FakeGraph(), env={**ENV, "VEXA_GRAPH_MAILBOX": ""})


def test_token_is_client_credentials_and_cached():
    f = FakeGraph(pages={"*": {"value": [msg(1, "2026-08-24T09:00:00Z")]}})
    t = tp(f)
    list(t.fetch("2026-08-24T08:00:00Z"))
    list(t.fetch("2026-08-24T08:00:00Z"))
    assert f.tokens == 1, "a token per request would rate-limit the tenant"


def test_token_refreshes_when_it_expires():
    f = FakeGraph(pages={"*": {"value": []}}, expires_in=0)   # 0 - 60s slack → always stale
    t = tp(f)
    list(t.fetch("2026-08-24T08:00:00Z"))
    list(t.fetch("2026-08-24T08:00:00Z"))
    assert f.tokens == 2


# ------------------------------------------------------------------------------------ poll
def test_poll_filters_on_the_cursor_orders_ascending_and_pages():
    f = FakeGraph(pages={
        "%24filter": {"value": [msg(1, "2026-08-24T09:00:00Z"), msg(2, "2026-08-24T09:05:00Z")],
                    "@odata.nextLink": f"{GRAPH}/page2"},
        "/page2": {"value": [msg(3, "2026-08-24T09:09:00Z")]}})
    got = list(tp(f).fetch("2026-08-24T08:00:00Z"))
    assert [m.message_id for m in got] == ["<m1@oenb.at>", "<m2@oenb.at>", "<m3@oenb.at>"]
    first = [c for c in f.calls if "%24filter" in c[1]][0][1]
    assert "receivedDateTime+gt+2026-08-24T08%3A00%3A00Z" in first
    assert "%24orderby=receivedDateTime+asc" in first
    assert "internetMessageHeaders" in first, "headers must be $select-ed or threading dies"


def test_every_message_carries_a_resumable_cursor():
    """C1: the position is per-message, so a crash mid-batch resumes at the next one and never
    re-reads the mailbox from the top."""
    f = FakeGraph(pages={"*": {"value": [msg(1, "2026-08-24T09:00:00Z"),
                                         msg(2, "2026-08-24T09:05:00Z")]}})
    db = SqliteDB()
    seen = []
    for m in tp(f).fetch("2026-08-24T08:00:00Z"):
        seen.append(m.cursor)
        mt.write_cursor(db, m.cursor)
    assert seen == ["2026-08-24T09:00:00Z", "2026-08-24T09:05:00Z"]
    assert mt.read_cursor(db) == "2026-08-24T09:05:00Z"


def test_headers_body_and_sender_are_normalised_for_routing():
    f = FakeGraph(pages={"*": {"value": [msg(1, "2026-08-24T09:00:00Z", internetMessageHeaders=[
        {"name": "In-Reply-To", "value": "<ours@vexa.ai>"},
        {"name": "Auto-Submitted", "value": "auto-replied"}])]}})
    m = next(iter(tp(f).fetch("2026-08-24T08:00:00Z")))
    assert m.frm == "anna.bank@oenb.at"
    assert m.headers["In-Reply-To"] == "<ours@vexa.ai>"
    assert m.headers["Auto-Submitted"] == "auto-replied"
    body_pref = [c for c in f.calls if "%24select" in c[1]][0][2]
    assert body_pref["Prefer"] == 'outlook.body-content-type="text"'


def test_delta_mode_anchors_at_the_tail_and_advances_the_delta_link():
    dl1, dl2 = f"{GRAPH}/delta?$deltatoken=one", f"{GRAPH}/delta?$deltatoken=two"
    f = FakeGraph(pages={"delta?$select=id": {"value": [], "@odata.deltaLink": dl1}})
    t = tp(f, VEXA_GRAPH_USE_DELTA="1")
    assert t.tail_cursor() == dl1                    # first boot: no history replay
    f.pages = {"$deltatoken=one": {"value": [msg(1, "2026-08-24T09:00:00Z"),
                                             {"id": "m9", "@removed": {"reason": "deleted"}},
                                             msg(2, "2026-08-24T09:05:00Z")],
                                   "@odata.deltaLink": dl2}}
    got = list(t.fetch(dl1))
    assert [m.message_id for m in got] == ["<m1@oenb.at>", "<m2@oenb.at>"], "deletions are not mail"
    assert got[0].cursor == dl1, "a delta link is only valid once the page-set is drained"
    assert got[-1].cursor == dl2


# ------------------------------------------------------------------------------------- ICS
def att(path: str, ctype="text/calendar", name="invite.ics"):
    return [{"name": name, "contentType": ctype,
             "contentBytes": base64.b64encode(FIX.joinpath(path).read_bytes()).decode()}]


def test_ics_attachment_is_fetched_decoded_and_parses_to_the_exact_instant():
    import calendar as cal
    import time as _t
    f = FakeGraph(pages={"*": {"value": [msg(1, "2026-08-24T09:00:00Z", hasAttachments=True)]}},
                  attachments={"m1": att("outlook-w-europe.ics")})
    m = next(iter(tp(f).fetch("2026-08-24T08:00:00Z")))
    ev = parse_ics(m.ics)
    assert ev["start"] == cal.timegm(_t.strptime("20300315T130000", "%Y%m%dT%H%M%S"))
    assert ev["organizer"] == "anna.bank@oenb.at"


def test_utf16_attachment_decodes():
    raw = base64.b64decode(FIX.joinpath("outlook-utf16le.ics.b64").read_text())
    f = FakeGraph(pages={"*": {"value": [msg(1, "2026-08-24T09:00:00Z", hasAttachments=True)]}},
                  attachments={"m1": [{"name": "invite.ics", "contentType": "text/calendar",
                                       "contentBytes": base64.b64encode(raw).decode()}]})
    m = next(iter(tp(f).fetch("2026-08-24T08:00:00Z")))
    assert parse_ics(m.ics)["url"] == "https://meet.google.com/abc-defg-hij"


def test_exchange_meeting_request_without_a_mime_part_is_synthesized():
    """Exchange lifts the calendar data out of MIME into `eventMessageRequest` properties —
    there is no `.ics` attachment to read at all."""
    f = FakeGraph(pages={"*": {"value": [msg(1, "2026-08-24T09:00:00Z",
        meetingMessageType="meetingRequest",
        startDateTime={"dateTime": "2030-03-15T14:00:00.0000000",
                       "timeZone": "W. Europe Standard Time"},
        location={"displayName": "https://meet.google.com/abc-defg-hij"},
        body={"contentType": "text", "content": "Join https://meet.google.com/abc-defg-hij"})]}})
    m = next(iter(tp(f).fetch("2026-08-24T08:00:00Z")))
    import calendar as cal
    import time as _t
    ev = parse_ics(m.ics)
    assert ev["start"] == cal.timegm(_t.strptime("20300315T130000", "%Y%m%dT%H%M%S"))
    assert ev["organizer"] == "anna.bank@oenb.at"
    assert ev["url"] == "https://meet.google.com/abc-defg-hij"


def test_a_plain_message_is_never_synthesized_into_an_invite():
    f = FakeGraph(pages={"*": {"value": [msg(1, "2026-08-24T09:00:00Z")]}})
    assert next(iter(tp(f).fetch("2026-08-24T08:00:00Z"))).ics is None


# ------------------------------------------------------------------------------- outbound
def test_send_is_draft_then_send_so_the_real_message_id_comes_back():
    f = FakeGraph()
    mid = tp(f).send("anna.bank@oenb.at", "Vexa will join", "body", in_reply_to="<prev@vexa.ai>")
    assert mid == "<made@oenb.at>", "mail_thread keys on this — a fabricated id breaks routing"
    draft = [c for c in f.calls if c[0] == "POST" and c[1].endswith("/messages")][0][3]
    assert draft["toRecipients"] == [{"emailAddress": {"address": "anna.bank@oenb.at"}}]
    assert draft["body"] == {"contentType": "Text", "content": "body"}
    assert {"name": "In-Reply-To", "value": "<prev@vexa.ai>"} in draft["internetMessageHeaders"]
    assert f.sent == ["draft-1"]


def test_a_tenant_that_rejects_reserved_headers_still_sends_and_still_threads():
    f = FakeGraph(draft_status=400)
    mid = tp(f).send("anna.bank@oenb.at", "s", "b", in_reply_to="<prev@vexa.ai>")
    assert mid == "<made@oenb.at>"
    drafts = [c[3] for c in f.calls if c[0] == "POST" and c[1].endswith("/messages")]
    assert len(drafts) == 2 and "internetMessageHeaders" not in drafts[1]
    assert f.sent == ["draft-1"]


def test_rsvp_rides_as_an_imip_calendar_attachment():
    f = FakeGraph()
    tp(f).send_calendar_reply("anna.bank@oenb.at", "Accepted: x", "text",
                              "BEGIN:VCALENDAR\r\nMETHOD:REPLY\r\nEND:VCALENDAR\r\n")
    draft = [c for c in f.calls if c[0] == "POST" and c[1].endswith("/messages")][0][3]
    a = draft["attachments"][0]
    assert a["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert a["contentType"].startswith("text/calendar; method=REPLY")
    assert base64.b64decode(a["contentBytes"]).decode().startswith("BEGIN:VCALENDAR")


def test_send_failure_is_raised_not_swallowed():
    class Broken(FakeGraph):
        def __call__(self, method, url, headers, data):
            if method == "POST" and url.endswith("/messages"):
                return 403, json.dumps({"error": {"code": "ErrorAccessDenied"}})
            return super().__call__(method, url, headers, data)
    with pytest.raises(RuntimeError, match="graph draft 403"):
        tp(Broken()).send("a@b.c", "s", "b")


# ------------------------------------------------------------- the whole edge, end to end
def test_a_graph_delivered_outlook_invite_admits_once_and_dedups():
    from fixtures import rig                                   # the offline engine rig
    db, reg, clock, _world = rig()
    f = FakeGraph(pages={"*": {"value": [msg(1, "2026-08-24T09:00:00Z", hasAttachments=True)]}},
                  attachments={"m1": att("outlook-w-europe.ics")})
    t = tp(f)
    cursor = "2026-08-24T08:00:00Z"
    admissions = []
    for _round in range(2):                                    # a redelivery after a crash
        for m in t.fetch(cursor):
            out = handle(db, reg, clock, t.address(), m, lambda e: None, lambda u: False,
                         lambda e: "1")
            admissions.append(out)
            cursor = m.cursor
            mt.write_cursor(db, cursor)
    assert admissions[0][0] == "invite" and admissions[0][1] >= 1
    assert admissions[1][1] == 0, "the same ICS UID must never admit twice"
    assert mt.read_cursor(db) == "2026-08-24T09:00:00Z"


def test_a_graph_delivered_reply_routes_by_thread_not_by_sender():
    from flows_steps.emailx import register_thread
    db = SqliteDB()
    register_thread(db, "<ours@vexa.ai>", "7", "onboarding")
    f = FakeGraph(pages={"*": {"value": [msg(1, "2026-08-24T09:00:00Z", internetMessageHeaders=[
        {"name": "In-Reply-To", "value": "<ours@vexa.ai>"}],
        **{"from": {"emailAddress": {"address": "someone.else@oenb.at"}}})]}})
    m = next(iter(tp(f).fetch("2026-08-24T08:00:00Z")))
    kind, payload = route(db, "vexa@oenb.at", m.frm, m.headers, m.ics, lambda e: None,
                          lambda u: False)
    assert (kind, payload) == ("thread_reply", {"uid": "7", "session": "onboarding"})

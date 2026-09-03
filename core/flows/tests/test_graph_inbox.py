"""The Microsoft Graph mailbox, whole, against a FAKE HTTP LAYER.

⚠ **NO LIVE TENANT HAS EVER ANSWERED THIS CODE.** There is no M365 credential in the vault, so
every response below is one we wrote, shaped from the documented Graph resources. That is the
rung: the wiring is proven, the tenant is not. Say it plainly rather than let a green suite imply
otherwise — `_synthesize_ics` (the `eventMessageRequest` path, where Exchange lifts the calendar
data out of MIME into message properties) is the least-verified piece in the whole port.

What IS proven here is everything that does not need a tenant: the token shape and its caching,
the query construction, paging, the four transport contracts (C1 durable cursor anchored at the
tail · C2 the real Message-ID comes back from a send · C3 the ICS attachment is read and decoded ·
C4 nothing sleeps inside a fetch), and — the two that matter most — that an Exchange-delivered
invite arriving over Graph produces the SAME facts as one arriving over IMAP, and that a reply
routes by THREAD and not by sender.

Ported from PR Vexa-ai/vexa#1318 and rebased onto this line's inbox seam, whose watermark +
`mail_seen` cursor replaces #1318's per-message `receivedDateTime gt` (which silently loses the
second of two messages sharing a timestamp — the test below is the reproduction).
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest                                                          # noqa: E402

from flows_integrations.graph_client import GraphClient                # noqa: E402
from flows_integrations.graph_inbox import GraphInbox, synthesize_ics  # noqa: E402
from sqlite_double import SqliteDB                                     # noqa: E402

ENV = {"VEXA_GRAPH_TENANT_ID": "t-1", "VEXA_GRAPH_CLIENT_ID": "c-1",
       "VEXA_GRAPH_CLIENT_SECRET": "s-1", "VEXA_GRAPH_MAILBOX": "vexa@oenb.at"}

INVITE_ICS = ("BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
              'DTSTART;TZID="W. Europe Standard Time":20300315T140000\r\n'
              "UID:graph-fixture-0001@oenb.at\r\n"
              "ORGANIZER;CN=Anna Bank:mailto:Anna.Bank@oenb.at\r\n"
              "SUMMARY:Quarterly risk review\r\n"
              "LOCATION:https://meet.google.com/abc-defg-hij\r\n"
              "END:VEVENT\r\nEND:VCALENDAR\r\n")


class FakeHttp:
    """A tenant that does exactly what the documentation says and nothing else."""

    def __init__(self, pages=None, attachments=None, token_expires=3600):
        self.calls: list[tuple[str, str, dict, bytes | None]] = []
        self.pages = pages or {}
        self.attachments = attachments or {}
        self.token_expires = token_expires
        self.tokens_issued = 0
        self.sent: list[dict] = []
        self.reject_headers = False

    def __call__(self, method, url, headers, data):
        self.calls.append((method, url, headers, data))
        if url.endswith("/oauth2/v2.0/token"):
            self.tokens_issued += 1
            return 200, json.dumps({"access_token": f"tok-{self.tokens_issued}",
                                    "expires_in": self.token_expires})
        if "/attachments" in url:
            mid = url.split("/messages/")[1].split("/")[0]
            return 200, json.dumps({"value": self.attachments.get(mid, [])})
        if method == "GET":
            for key, body in self.pages.items():
                if key in url:
                    return 200, json.dumps(body)
            return 200, json.dumps({"value": []})
        if method == "POST" and url.endswith("/messages"):
            body = json.loads(data)
            if self.reject_headers and body.get("internetMessageHeaders"):
                return 400, json.dumps({"error": {"code": "ErrorInvalidHeader"}})
            self.sent.append(body)
            return 201, json.dumps({"id": "draft-1",
                                    "internetMessageId": "<real-id@oenb.at>"})
        if method == "POST" and url.endswith("/send"):
            return 202, ""
        raise AssertionError(f"unexpected {method} {url}")


def client(http) -> GraphClient:
    return GraphClient(http=http, env=ENV)


def msg(i, when, *, frm="anna.bank@oenb.at", subject="hello", body="hi",
        headers=None, attachments=False, extra=None):
    m = {"id": i, "receivedDateTime": when, "internetMessageId": f"<{i}@oenb.at>",
         "subject": subject, "from": {"emailAddress": {"address": frm}},
         "body": {"content": body}, "hasAttachments": attachments,
         "internetMessageHeaders": [{"name": k, "value": v}
                                    for k, v in (headers or {}).items()]}
    m.update(extra or {})
    return m


def ics_attachment(text: str) -> dict:
    return {"name": "invite.ics", "contentType": "text/calendar; method=REQUEST",
            "contentBytes": base64.b64encode(text.encode()).decode()}


def db_with_schema() -> SqliteDB:
    """`SqliteDB` applies `schema.sql`, which already carries `mail_cursor.token` and `mail_seen`
    — the Graph inbox adds NO schema of its own, which is half the reason it reuses this seam."""
    return SqliteDB()


# ── configuration ────────────────────────────────────────────────────────────────────────────
def test_a_half_configured_graph_mailbox_refuses_by_name():
    """`required-explicit` cannot express "required only when Graph is selected" (config.v1 has no
    conditional), so the refusal happens where the mailbox is constructed — and it names the KEYS,
    never the values, exactly as `emailx.creds` does for a half-configured SMTP pair."""
    import flows_config
    partial = dict(ENV, VEXA_GRAPH_CLIENT_SECRET="")
    with pytest.raises(flows_config.ConfigError) as e:
        GraphClient(http=FakeHttp(), env=partial)
    assert "VEXA_GRAPH_CLIENT_SECRET" in str(e.value)
    assert "s-1" not in str(e.value)


# ── auth ─────────────────────────────────────────────────────────────────────────────────────
def test_the_token_is_client_credentials_and_is_cached():
    http = FakeHttp(pages={"/messages": {"value": []}})
    c = client(http)
    assert c.token() == "tok-1"
    assert c.token() == "tok-1"
    assert http.tokens_issued == 1
    method, url, _, data = http.calls[0]
    form = urllib.parse.parse_qs(data.decode())
    assert form["grant_type"] == ["client_credentials"]
    assert form["scope"] == ["https://graph.microsoft.com/.default"]
    assert url == "https://login.microsoftonline.com/t-1/oauth2/v2.0/token"


def test_a_token_about_to_expire_is_refreshed_rather_than_used():
    """60 s of slack. A token that expires mid-page is a 401 loop, and nothing here sleeps."""
    http = FakeHttp(token_expires=30)
    c = client(http)
    c.token()
    c.token()
    assert http.tokens_issued == 2


def test_a_token_refusal_propagates_rather_than_being_swallowed():
    class Refuses(FakeHttp):
        def __call__(self, method, url, headers, data):
            if url.endswith("/token"):
                return 401, '{"error":"invalid_client"}'
            return super().__call__(method, url, headers, data)
    with pytest.raises(RuntimeError, match="graph token 401"):
        client(Refuses()).token()


# ── the query ────────────────────────────────────────────────────────────────────────────────
def test_the_listing_asks_for_ge_not_gt_and_orders_ascending():
    """`gt` drops the second of two messages sharing a `receivedDateTime`, forever, with no error
    anywhere — Graph's timestamp is second-granular and a burst of invitations will share one.
    The window overlaps on purpose; idempotence comes from `mail_seen`, not from the filter."""
    http = FakeHttp(pages={"/messages": {"value": []}})
    client(http).messages_since("2026-09-03T10:00:00Z")
    url = [u for _, u, _, _ in http.calls if "/mailFolders/inbox/messages" in u][0]
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["$filter"] == ["receivedDateTime ge 2026-09-03T10:00:00Z"]
    assert q["$orderby"] == ["receivedDateTime asc"]
    assert "internetMessageHeaders" in q["$select"][0]
    assert "meetingMessageType" in q["$select"][0]


def test_paging_walks_next_link_and_then_stops():
    """C4: `fetch` returns what is there. No sleeping, no retry loop, no second cursor shape."""
    http = FakeHttp()
    page2 = {"value": [msg("b", "2026-09-03T10:00:02Z")]}

    def responder(method, url, headers, data):
        http.calls.append((method, url, headers, data))
        if url.endswith("/token"):
            return 200, json.dumps({"access_token": "t", "expires_in": 3600})
        if "PAGE2" in url:
            return 200, json.dumps(page2)
        return 200, json.dumps({"value": [msg("a", "2026-09-03T10:00:01Z")],
                                "@odata.nextLink": "https://graph.microsoft.com/PAGE2"})

    c = GraphClient(http=responder, env=ENV)
    got = c.messages_since("2026-09-03T10:00:00Z")
    assert [m["id"] for m in got] == ["a", "b"]


# ── the cursor (C1) ──────────────────────────────────────────────────────────────────────────
def test_first_boot_anchors_at_the_tail_and_never_replays_history():
    http = FakeHttp(pages={"$orderby=receivedDateTime+desc":
                           {"value": [{"receivedDateTime": "2026-09-03T09:00:00Z"}]}})
    box = GraphInbox(client(http), lookback_s=0)
    db = db_with_schema()
    assert box.restore(db) is None
    tail = box.tail_cursor()
    box.anchor(db, tail)
    assert box.restore(db).startswith("2026-09-03T09:00:00")


def test_two_messages_in_the_same_second_are_both_delivered_once():
    """THE REPRODUCTION of what #1318's `gt` cursor would have lost. Both arrive; a restart after
    the first has been committed re-scans the window and yields only the second."""
    when = "2026-09-03T10:00:01Z"
    http = FakeHttp(pages={"/mailFolders/inbox/messages":
                           {"value": [msg("a", when), msg("b", when)]}})
    db = db_with_schema()
    box = GraphInbox(client(http), lookback_s=300)
    box.anchor(db, "2026-09-03T09:59:00Z")
    cursor = box.restore(db)
    first = list(box.fetch(cursor))
    assert [m.ext_id for m in first] == ["a", "b"]
    box.commit(db, first[0])

    fresh = GraphInbox(client(FakeHttp(pages={"/mailFolders/inbox/messages":
                                              {"value": [msg("a", when), msg("b", when)]}})),
                       lookback_s=300)
    again = list(fresh.fetch(fresh.restore(db)))
    assert [m.ext_id for m in again] == ["b"]


def test_the_watermark_never_moves_backwards():
    db = db_with_schema()
    box = GraphInbox(client(FakeHttp()), lookback_s=300)
    box.anchor(db, "2026-09-03T10:00:00Z")
    box.commit(db, box.to_inbound(msg("late", "2026-09-03T10:05:00Z")))
    box.commit(db, box.to_inbound(msg("early", "2026-09-03T10:01:00Z")))
    assert box.restore(db).startswith("2026-09-03T10:05:00")


# ── the facts (C2, C3) ───────────────────────────────────────────────────────────────────────
def test_an_ics_attachment_is_fetched_and_decoded():
    http = FakeHttp(pages={"/mailFolders/inbox/messages":
                           {"value": [msg("m1", "2026-09-03T10:00:01Z", attachments=True)]}},
                    attachments={"m1": [{"name": "logo.png", "contentType": "image/png",
                                         "contentBytes": "AAAA"},
                                        ics_attachment(INVITE_ICS)]})
    box = GraphInbox(client(http), lookback_s=0)
    got = list(box.fetch("2026-09-03T10:00:00Z"))
    assert len(got) == 1
    assert "BEGIN:VEVENT" in got[0].ics


def test_a_utf16_ics_attachment_survives_the_round_trip():
    raw = ("﻿" + INVITE_ICS).encode("utf-16-le")
    http = FakeHttp(pages={"/mailFolders/inbox/messages":
                           {"value": [msg("m1", "2026-09-03T10:00:01Z", attachments=True)]}},
                    attachments={"m1": [{"name": "invite.ics", "contentType": "text/calendar",
                                         "contentBytes": base64.b64encode(raw).decode()}]})
    box = GraphInbox(client(http), lookback_s=0)
    assert "Quarterly risk review" in list(box.fetch("2026-09-03T10:00:00Z"))[0].ics


def test_inbound_headers_are_surfaced_so_threading_is_unchanged():
    """C2. `mail_thread` routes a reply by In-Reply-To; the source may not change that."""
    box = GraphInbox(client(FakeHttp()), lookback_s=0)
    m = box.to_inbound(msg("m2", "2026-09-03T10:00:01Z",
                           headers={"In-Reply-To": "<ours@vexa.ai>",
                                    "References": "<ours@vexa.ai>"}))
    assert m.headers["In-Reply-To"] == "<ours@vexa.ai>"
    assert m.message_id == "<m2@oenb.at>"
    assert m.frm == "anna.bank@oenb.at"


# ── the same facts as IMAP ───────────────────────────────────────────────────────────────────
def test_a_graph_invite_and_an_imap_invite_produce_identical_facts():
    """The seam's whole promise, asserted rather than asserted-about."""
    from flows_integrations.inbox import from_rfc822
    from flows_integrations.mailbox import parse_ics

    mime = ("From: Anna Bank <anna.bank@oenb.at>\r\nSubject: Quarterly risk review\r\n"
            'Content-Type: multipart/mixed; boundary="B"\r\nMIME-Version: 1.0\r\n\r\n'
            "--B\r\nContent-Type: text/plain\r\n\r\nsee attached\r\n"
            "--B\r\nContent-Type: text/calendar; method=REQUEST\r\n\r\n"
            + INVITE_ICS + "\r\n--B--\r\n")
    over_imap = from_rfc822(mime.encode(), cursor="7", ext_id="7")

    http = FakeHttp(pages={"/mailFolders/inbox/messages":
                           {"value": [msg("m1", "2026-09-03T10:00:01Z",
                                          subject="Quarterly risk review",
                                          body="see attached", attachments=True)]}},
                    attachments={"m1": [ics_attachment(INVITE_ICS)]})
    over_graph = list(GraphInbox(client(http), lookback_s=0).fetch("2026-09-03T10:00:00Z"))[0]

    assert over_graph.frm == over_imap.frm
    assert over_graph.subject == over_imap.subject
    assert parse_ics(over_graph.ics, "vexa@oenb.at") == parse_ics(over_imap.ics, "vexa@oenb.at")


# ── outbound (C2) ────────────────────────────────────────────────────────────────────────────
def test_a_send_is_draft_then_send_and_returns_the_real_message_id():
    """`sendMail` is one call and answers 202 with no body — and the Message-ID IS the threading
    contract. A send whose id we never learned is a conversation we can never route."""
    http = FakeHttp()
    mid = client(http).send("anna.bank@oenb.at", "Re: risk review", "on it",
                            in_reply_to="<theirs@oenb.at>")
    assert mid == "<real-id@oenb.at>"
    posts = [u for m, u, _, _ in http.calls if m == "POST" and not u.endswith("/token")]
    assert posts[0].endswith("/messages")
    assert posts[1].endswith("/messages/draft-1/send")
    assert http.sent[0]["internetMessageHeaders"] == [
        {"name": "In-Reply-To", "value": "<theirs@oenb.at>"},
        {"name": "References", "value": "<theirs@oenb.at>"}]


def test_a_tenant_that_rejects_reserved_headers_still_sends():
    """Threading holds without them: what routes the reply is OUR Message-ID, echoed back."""
    http = FakeHttp()
    http.reject_headers = True
    mid = client(http).send("anna.bank@oenb.at", "hi", "text", in_reply_to="<theirs@oenb.at>")
    assert mid == "<real-id@oenb.at>"
    assert "internetMessageHeaders" not in http.sent[0]


def test_the_rsvp_rides_as_an_imip_calendar_attachment():
    http = FakeHttp()
    client(http).send_calendar_reply("anna.bank@oenb.at", "Accepted: x", "body", INVITE_ICS)
    att = http.sent[0]["attachments"][0]
    assert att["contentType"].startswith("text/calendar; method=REPLY")
    assert base64.b64decode(att["contentBytes"]).decode() == INVITE_ICS


def test_a_send_failure_is_raised_not_reported_as_success():
    class Refuses(FakeHttp):
        def __call__(self, method, url, headers, data):
            if method == "POST" and url.endswith("/messages"):
                return 403, '{"error":{"code":"ErrorAccessDenied"}}'
            return super().__call__(method, url, headers, data)
    with pytest.raises(RuntimeError, match="graph draft 403"):
        client(Refuses()).send("a@b.c", "s", "b")


# ── eventMessageRequest (the least-verified piece) ───────────────────────────────────────────
def test_an_exchange_meeting_request_with_no_mime_part_is_synthesized():
    """Exchange strips iMIP: the calendar data is lifted OUT of MIME into message properties and
    there is no `.ics` part to read at all. ⚠ Written from the documented resource shape and
    never seen from a live tenant."""
    m = msg("m3", "2026-09-03T10:00:01Z", subject="Quarterly risk review",
            body="https://meet.google.com/abc-defg-hij",
            extra={"meetingMessageType": "meetingRequest",
                   "startDateTime": {"dateTime": "2030-03-15T14:00:00.0000000",
                                     "timeZone": "W. Europe Standard Time"},
                   "location": {"displayName": "https://meet.google.com/abc-defg-hij"}})
    ics = synthesize_ics(m)
    assert ics is not None
    from flows_integrations.mailbox import parse_ics
    ev = parse_ics(ics, "vexa@oenb.at")
    assert ev is not None
    assert ev["organizer"] == "anna.bank@oenb.at"
    assert ev["url"] == "https://meet.google.com/abc-defg-hij"
    import calendar as cal
    assert ev["start"] == float(cal.timegm((2030, 3, 15, 13, 0, 0, 0, 1, -1)))


def test_a_plain_message_is_never_synthesized_into_an_invite():
    assert synthesize_ics(msg("m4", "2026-09-03T10:00:01Z")) is None
    assert synthesize_ics({"meetingMessageType": "meetingRequest"}) is None


def test_a_newline_in_a_synthesized_value_cannot_open_a_new_property():
    m = msg("m5", "2026-09-03T10:00:01Z",
            subject="risk\r\nATTENDEE;PARTSTAT=ACCEPTED:mailto:attacker@evil.test",
            extra={"meetingMessageType": "meetingRequest",
                   "startDateTime": {"dateTime": "2030-03-15T14:00:00.0000000",
                                     "timeZone": "UTC"}})
    ics = synthesize_ics(m)
    assert "\r\nATTENDEE" not in ics

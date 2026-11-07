"""The service door holds every person-bound flows call to a producer-signed subject ticket.

The flows key opens the door; it no longer lets flows name whoever it likes on the calls that act
for a person (`BOUND`). Each needs a ticket that the producer of that person's fact signed —
meeting-api for a meeting, agent-api for a routine, the mail intake for an invite, this gateway for
a bearer the person presented — naming the subject, the class, the meeting, and unexpired. The
refusals come first: they are the security claim.
"""
from __future__ import annotations

import base64
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway import identity_token, subject_ticket as T
from gateway.service_door import (BOUND, GRANT_RESPONSE_HEADER, create_door_app, ticket_checks)

FLOWS_KEY = "flows-key-0123456789abcdef0123456789abcdef"
INTERNAL = "internal-0123456789abcdef0123456789abcdef"
GATEWAY_KEY = identity_token.generate_signing_key()
KEYS = {i: identity_token.generate_signing_key() for i in (T.MEETINGS, T.AGENT, T.MAIL)}
STRANGER = identity_token.generate_signing_key()
UPSTREAMS = {"agent": "http://agent-api:8100", "identity": "http://admin-api:8001",
             "meetings": "http://meeting-api:8080", "crm": "http://crm-api:8200"}
PEOPLE = {"7": "ann@acme.test", "8": "bob@acme.test"}
MEET = "https://meet.google.com/abc-defg-hij"
NATIVE = "google_meet/abc-defg-hij"


class Upstream:
    def __init__(self):
        self.seen: list[httpx.Request] = []
        self.status = 200
        self.validate = {"user_id": 7, "email": "ann@acme.test", "credential_class": "session"}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path = request.url.path
        if path.startswith("/internal/users/") and path.endswith("/identity"):
            uid = path.split("/")[3]
            if uid in PEOPLE:
                return httpx.Response(200, json={"user_id": int(uid), "email": PEOPLE[uid],
                                                 "max_concurrent": 1})
            return httpx.Response(404, json={})
        if path.startswith("/internal/users/") and path.endswith("/email"):
            uid = path.split("/")[3]
            return (httpx.Response(200, json={"email": PEOPLE[uid]}) if uid in PEOPLE
                    else httpx.Response(404, json={}))
        if path == "/internal/validate":
            return httpx.Response(200, json=self.validate)
        return httpx.Response(self.status, json={"ok": True})

    def forwarded(self) -> list[httpx.Request]:
        return [r for r in self.seen if not (r.url.path.endswith("/identity")
                                             or r.url.path.endswith("/email"))]


@pytest.fixture()
def door(tmp_path):
    for issuer, key in KEYS.items():
        (tmp_path / f"{issuer}.pem").write_bytes(identity_token.public_key_pem(key))
    up = Upstream()
    app = create_door_app(flows_key=FLOWS_KEY, internal_secret=INTERNAL, upstreams=UPSTREAMS,
                          client=httpx.AsyncClient(transport=httpx.MockTransport(up)),
                          identity_key=GATEWAY_KEY,
                          checks=ticket_checks(str(tmp_path), GATEWAY_KEY))
    with TestClient(app) as c:
        yield c, up


def ticket(iss=T.MEETINGS, *, key=None, sub="7", cls=None, ttl=3600, kind=T.EVENT, now=None,
           **kw) -> str:
    cls = cls or sorted(T.ISSUER_CLASSES[iss])
    signer = (key or (GATEWAY_KEY if iss == T.GATEWAY else KEYS[iss])).sign
    return T.mint(signer, iss=iss, sub=sub, cls=cls, evt="e-1", ttl=ttl, kind=kind, now=now, **kw)


def hdr(uid="7", *tickets, **extra) -> dict:
    h = {"X-Vexa-Service-Key": FLOWS_KEY, **extra}
    if uid is not None:
        h["X-User-Id"] = uid
    if tickets:
        h[T.HEADER] = ",".join(tickets)
    return h


def call(c, domain, method, template, headers, *, args=("42", "43"), body=None):
    it = iter(args)
    path = "/".join(next(it) if seg == "{}" else seg for seg in template.split("/"))
    return c.request(method, f"/{domain}{path}", headers=headers,
                     json=body if method in ("POST", "PUT") else None)


#: A ticket that opens each bound row for subject 7, and the body the row needs.
def opener(domain, method, template):
    cls = BOUND[(domain, method, template)]["cls"]
    meeting = BOUND[(domain, method, template)].get("meeting", "")
    body = {"a": 1}
    if cls in (T.ROUTINE_TURN,):
        return ticket(T.AGENT, cls=[cls], kind=T.GRANT), body
    if cls == T.BOT_DISPATCH:
        if meeting == "native_body":
            body = {"meeting_url": MEET}
        return ticket(T.MAIL, cls=[cls], native=NATIVE if meeting == "native" else NATIVE), body
    if cls == T.ROOM_BILLS_TO:
        return ticket(T.MEETINGS, cls=[cls]), {"bills_to": "7", "workspace": "ws-1"}
    if cls == T.TRANSCRIPT_READ:
        return ticket(T.MEETINGS, cls=[cls], mid="42", native="google_meet/42"), body
    return ticket(T.MEETINGS, cls=[cls], mid="42"), body


ROWS = sorted(BOUND)


# ── refusals ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("domain,method,template", ROWS)
def test_a_valid_service_key_and_no_ticket_opens_no_bound_call(door, domain, method, template):
    c, up = door
    body = {"bills_to": "7"} if BOUND[(domain, method, template)].get("subject") else {"a": 1}
    r = call(c, domain, method, template, hdr("7"), body=body)
    assert r.status_code == 403 and r.json()["reason"] == "subject_ticket_required", r.text
    assert up.forwarded() == []


@pytest.mark.parametrize("domain,method,template", ROWS)
def test_a_ticket_for_ann_does_not_open_the_call_for_bob(door, domain, method, template):
    c, up = door
    tok, body = opener(domain, method, template)
    if "bills_to" in body:
        body = {**body, "bills_to": "8"}
    args = ("42", "42") if template.startswith("/transcripts/{}") else ("42", "43")
    if template in ("/transcripts/{}/{}", "/bots/{}/{}"):
        args = ("google_meet", "abc-defg-hij")
    r = call(c, domain, method, template, hdr("8", tok), body=body, args=args)
    assert r.status_code == 403 and r.json()["reason"] == "subject_ticket_refused", r.text
    assert "other_subject" in r.json()["why"]
    assert up.forwarded() == []


@pytest.mark.parametrize("domain,method,template", ROWS)
def test_each_bound_call_opens_for_its_own_person_with_its_own_ticket(door, domain, method,
                                                                       template):
    """The positive control for every refusal above."""
    c, up = door
    tok, body = opener(domain, method, template)
    args = ("42", "43")
    if template in ("/transcripts/{}/{}", "/bots/{}/{}"):
        args = ("google_meet", "abc-defg-hij") if BOUND[(domain, method, template)]["cls"] \
            == T.BOT_DISPATCH else ("google_meet", "42")
    r = call(c, domain, method, template, hdr("7", tok), body=body, args=args)
    assert r.status_code == 200, r.text
    (sent,) = up.forwarded()
    assert T.HEADER.lower() not in {k.lower() for k in sent.headers}


def test_an_expired_ticket_is_refused(door):
    c, up = door
    old = ticket(T.MEETINGS, cls=[T.CHAT_AUTONOMOUS], ttl=60, now=int(time.time()) - 3600)
    r = c.post("/agent/api/chat", json={}, headers=hdr("7", old))
    assert r.status_code == 403 and r.json()["why"] == ["expired"]
    assert up.forwarded() == []


def test_a_once_ticket_dispatches_one_bot_and_its_replay_is_refused(door):
    c, up = door
    tok = ticket(T.MAIL, sub="", addr="ann@acme.test", cls=[T.BOT_DISPATCH], native=NATIVE,
                 once=[T.BOT_DISPATCH])
    up.status = 503                      # a dispatch that failed spends nothing
    assert c.post("/meetings/bots", json={"meeting_url": MEET}, headers=hdr("7", tok)).status_code \
        == 503
    up.status = 200
    assert c.post("/meetings/bots", json={"meeting_url": MEET},
                  headers=hdr("7", tok)).status_code == 200
    r = c.post("/meetings/bots", json={"meeting_url": MEET}, headers=hdr("7", tok))
    assert r.status_code == 403 and r.json()["reason"] == "ticket_replayed"
    assert len([s for s in up.forwarded() if s.url.path == "/bots"]) == 2


def test_a_meeting_bound_ticket_opens_its_meeting_only(door):
    c, up = door
    tok = ticket(T.MEETINGS, cls=[T.TRANSCRIPT_READ], mid="42")
    assert c.get("/meetings/transcripts/by-id/42", headers=hdr("7", tok)).status_code == 200
    r = c.get("/meetings/transcripts/by-id/43", headers=hdr("7", tok))
    assert r.status_code == 403 and r.json()["why"] == ["other_meeting"]
    r = c.post("/meetings/meetings/43/share", json={}, headers=hdr("7", tok))
    assert r.status_code == 403
    mail = ticket(T.MAIL, sub="", addr="ann@acme.test", cls=[T.BOT_DISPATCH], native=NATIVE)
    r = c.post("/meetings/bots", json={"meeting_url": "https://meet.google.com/xyz-wxyz-xyz"},
               headers=hdr("7", mail))
    assert r.status_code == 403 and r.json()["why"] == ["other_meeting"]
    r = c.delete("/meetings/bots/google_meet/xyz-wxyz-xyz", headers=hdr("7", mail))
    assert r.status_code == 403


def test_a_transcript_read_with_no_meeting_to_hold_it_to_is_refused(door):
    c, _ = door
    unbound = ticket(T.MAIL, sub="7", cls=[T.TRANSCRIPT_READ])        # no mid, no native
    r = c.get("/meetings/transcripts/by-id/42", headers=hdr("7", unbound))
    assert r.status_code == 403 and r.json()["why"] == ["meeting_unchecked"]


def test_an_issuer_cannot_open_a_class_it_may_not_grant(door):
    """agent-api's key signs a transcript read: forged by the key holder, refused by policy."""
    c, up = door
    claims = {"v": T.TYPE, "iss": T.AGENT, "kind": T.EVENT, "sub": "7",
              "cls": [T.TRANSCRIPT_READ], "evt": "x", "iat": int(time.time()),
              "exp": int(time.time()) + 600, "jti": "j", "mid": "42"}
    body = "st1." + base64.urlsafe_b64encode(json.dumps(claims, sort_keys=True,
                                                        separators=(",", ":")).encode()
                                             ).rstrip(b"=").decode()
    forged = body + "." + base64.urlsafe_b64encode(KEYS[T.AGENT].sign(body.encode())).rstrip(
        b"=").decode()
    r = c.get("/meetings/transcripts/by-id/42", headers=hdr("7", forged))
    assert r.status_code == 403 and r.json()["why"] == ["issuer_not_allowed"]
    with pytest.raises(ValueError):
        ticket(T.AGENT, cls=[T.TRANSCRIPT_READ])
    assert up.forwarded() == []


def test_a_ticket_signed_by_a_key_the_door_does_not_hold_is_refused(door):
    c, _ = door
    forged = ticket(T.MEETINGS, key=STRANGER, cls=[T.CHAT_AUTONOMOUS])
    r = c.post("/agent/api/chat", json={}, headers=hdr("7", forged))
    assert r.status_code == 403 and r.json()["why"] == ["bad_signature"]


def test_a_class_the_ticket_does_not_list_is_refused(door):
    c, _ = door
    tok = ticket(T.MEETINGS, cls=[T.TRANSCRIPT_READ], mid="42")
    r = c.post("/agent/api/chat", json={}, headers=hdr("7", tok))
    assert r.status_code == 403 and r.json()["why"] == ["class_not_granted"]


def test_bills_to_is_the_ticket_s_person_and_the_header_cannot_disagree(door):
    c, up = door
    tok = ticket(T.MEETINGS, cls=[T.ROOM_BILLS_TO])
    url = "/identity/internal/principals/workspace"
    assert c.post(url, json={"bills_to": "8"}, headers=hdr(None, tok)).status_code == 403
    assert c.post(url, json={"bills_to": "8"}, headers=hdr("7", tok)).status_code == 403
    assert c.post(url, json={"workspace": "w"}, headers=hdr(None, tok)).status_code == 400
    assert c.post(url, json={"bills_to": "7"}, headers=hdr(None, tok)).status_code == 200
    assert len(up.forwarded()) == 1


def test_a_mail_ticket_names_an_address_and_its_attendees_only_for_their_desks(door):
    c, _ = door
    tok = ticket(T.MAIL, sub="", addr="ann@acme.test", peers=["bob@acme.test"],
                 cls=[T.DESK_WRITE, T.CHAT_AUTONOMOUS])
    assert c.post("/agent/api/chat", json={}, headers=hdr("7", tok)).status_code == 200
    assert c.post("/agent/api/chat", json={}, headers=hdr("8", tok)).status_code == 403
    assert c.put("/agent/api/workspace/file", json={}, headers=hdr("8", tok)).status_code == 200
    assert c.put("/agent/api/workspace/file", json={}, headers=hdr("9", tok)).status_code == 403


def test_without_producer_keys_every_bound_call_is_refused(tmp_path):
    up = Upstream()
    app = create_door_app(flows_key=FLOWS_KEY, internal_secret=INTERNAL, upstreams=UPSTREAMS,
                          client=httpx.AsyncClient(transport=httpx.MockTransport(up)),
                          identity_key=GATEWAY_KEY, checks=ticket_checks("", GATEWAY_KEY))
    with TestClient(app) as c:
        r = c.post("/agent/api/chat", json={},
                   headers=hdr("7", ticket(T.MEETINGS, cls=[T.CHAT_AUTONOMOUS])))
        assert r.status_code == 403 and r.json()["why"] == ["unknown_issuer"]
        # unbound rows are untouched
        assert c.get("/identity/internal/users/7/settings",
                     headers=hdr("7")).status_code == 200


# ── the gateway issuer: a person's own bearer ───────────────────────────────────────────────────

def test_a_validated_bearer_answers_a_short_ticket_for_that_person(door):
    c, up = door
    r = c.post("/identity/internal/validate", json={"token": "x"}, headers=hdr(None))
    tok = r.headers[T.HEADER]
    claims = T.read(tok, {T.GATEWAY: lambda m, s: _verify(GATEWAY_KEY, m, s)})
    assert claims["sub"] == "7" and claims["exp"] - claims["iat"] == 600
    assert GRANT_RESPONSE_HEADER not in r.headers
    assert c.put("/agent/api/workspace/file", json={}, headers=hdr("7", tok)).status_code == 200
    assert c.put("/agent/api/workspace/file", json={}, headers=hdr("8", tok)).status_code == 403


def test_only_a_session_arming_its_own_routine_gets_a_grant(door):
    c, up = door
    ask = {"X-Vexa-Ticket-Grant": "user:7/daily"}
    r = c.post("/identity/internal/validate", json={}, headers=hdr(None, **ask))
    grant = T.read(r.headers[GRANT_RESPONSE_HEADER],
                   {T.GATEWAY: lambda m, s: _verify(GATEWAY_KEY, m, s)})
    assert grant["kind"] == T.GRANT and grant["evt"] == "user:7/daily"
    r = c.post("/identity/internal/validate", json={},
               headers=hdr(None, **{"X-Vexa-Ticket-Grant": "user:8/daily"}))
    assert GRANT_RESPONSE_HEADER not in r.headers
    up.validate = {"user_id": 7, "credential_class": "key"}
    r = c.post("/identity/internal/validate", json={}, headers=hdr(None, **ask))
    assert GRANT_RESPONSE_HEADER not in r.headers and T.HEADER in r.headers
    up.validate = {"user_id": 7, "credential_class": "delegation",
                   "delegation": {"shared": True}}
    r = c.post("/identity/internal/validate", json={}, headers=hdr(None, **ask))
    assert T.HEADER not in r.headers and GRANT_RESPONSE_HEADER not in r.headers


def _verify(key, message, signature) -> bool:
    try:
        key.public_key().verify(signature, message)
        return True
    except Exception:  # noqa: BLE001
        return False


# ── the contract's goldens, read by the door's own copy ─────────────────────────────────────────

def _goldens(prefix):
    import pathlib  # noqa: PLC0415
    here = pathlib.Path(__file__).resolve()
    root = next(p for p in here.parents if (p / "contracts" / "subject-ticket.v1").is_dir())
    return sorted((root / "contracts" / "subject-ticket.v1" / "golden").glob(f"{prefix}-*.json"))


def _checks_for(golden: dict) -> dict:
    pub = identity_token.load_verify_key(golden["public_key"].encode())

    def check(m, s):
        try:
            pub.verify(s, m)
            return True
        except Exception:  # noqa: BLE001
            return False
    return {i: check for i in T.ISSUERS}


@pytest.mark.parametrize("path", _goldens("vector"), ids=lambda p: p.stem)
def test_every_contract_vector_verifies_at_the_door(path):
    g = json.loads(path.read_text())
    claims = T.read(g["token"], _checks_for(g), now=g["now"])
    assert claims == g["claims"]


@pytest.mark.parametrize("path", _goldens("refused"), ids=lambda p: p.stem)
def test_every_contract_refusal_is_refused_for_its_reason(path):
    g = json.loads(path.read_text())
    checks = _checks_for(g)
    if g["reason"] == "unknown_issuer":
        checks = {}
    with pytest.raises(T.TicketError) as e:
        T.read(g["token"], checks, now=g["now"])
    assert e.value.reason == g["reason"]

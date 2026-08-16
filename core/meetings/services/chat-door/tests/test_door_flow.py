"""The door end-to-end, in-process: verify → lazy identity → record view → steer → scope."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from chat_door.app import SESSION_COOKIE, create_app
from chat_door.meetings_client import MeetingsClient
from chat_door.tokens import build_magic_link

from conftest import make_meetings_transport

EMAIL = "clicker@example.test"


def link_for(signer, *, subject=EMAIL, meeting_id="126", scope="guest", ttl=600, now=None):
    return signer.issue(kind="link", subject=subject, meeting_id=meeting_id, scope=scope,
                        ttl_seconds=ttl, now=now)


def test_health_reports_fingerprint_not_key(door):
    client, _, _ = door
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert len(body["signing_key_fingerprint"]) == 8
    assert "test-signing-key-not-a-real-secret" not in str(body)


# -- lazy identity --------------------------------------------------------------

def test_every_response_suppresses_referrer_and_caching(door):
    """The verify URL *is* the token, and every page is one person's record."""
    client, signer, _ = door
    verify = client.get("/door/verify", params={"t": link_for(signer)}, follow_redirects=False)
    page = client.get("/door/meeting/126")
    for resp in (verify, page, client.get("/health")):
        assert resp.headers["referrer-policy"] == "no-referrer"
        assert resp.headers["cache-control"] == "no-store"


def test_nothing_is_stored_before_the_first_click(door):
    _, _, store = door
    assert store.get_user(EMAIL) is None


def test_first_click_creates_the_user_and_an_empty_personal_doc(door):
    client, signer, store = door
    resp = client.get("/door/verify", params={"t": link_for(signer)}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/door/meeting/126"

    user = store.get_user(EMAIL)
    assert user is not None
    doc = store.read_instructions(EMAIL)
    assert EMAIL in doc
    # "Empty" means: the doc exists with its header and carries no dated entry yet.
    assert "###" not in doc


def test_creation_happens_on_the_first_click_only(door):
    client, signer, store = door
    client.get("/door/verify", params={"t": link_for(signer)}, follow_redirects=False)
    created_at = store.get_user(EMAIL).created_at
    client.cookies.clear()
    client.get("/door/verify", params={"t": link_for(signer)}, follow_redirects=False)
    assert store.get_user(EMAIL).created_at == created_at


def test_a_failed_verification_creates_nothing(door):
    client, signer, store = door
    expired = link_for(signer, ttl=1, now=int(time.time()) - 60)
    assert client.get("/door/verify", params={"t": expired}).status_code == 401
    assert client.get("/door/verify", params={"t": "garbage"}).status_code == 401
    assert client.get("/door/verify").status_code == 400
    assert store.get_user(EMAIL) is None


# -- single use, and what still works after it ----------------------------------

def test_link_opens_once_and_the_session_carries_on(door):
    client, signer, _ = door
    token = link_for(signer)
    first = client.get("/door/verify", params={"t": token}, follow_redirects=False)
    assert first.status_code == 303
    assert SESSION_COOKIE in first.cookies

    # The same URL, forwarded to anyone else, is dead.
    replay = client.get("/door/verify", params={"t": token}, follow_redirects=False)
    assert replay.status_code == 401
    assert "token_already_used" in replay.text

    # …but this browser stays in, because the session cookie is a different token.
    assert client.get("/door/meeting/126").status_code == 200


# -- the record view ------------------------------------------------------------

def test_record_view_renders_the_transcript(door):
    client, signer, _ = door
    client.get("/door/verify", params={"t": link_for(signer)})
    page = client.get("/door/meeting/126")
    assert page.status_code == 200
    assert "Henry Buisseret" in page.text
    assert "dev v0" in page.text  # the page says what it is
    assert "Steer your next artifact" in page.text


def test_no_session_no_door(door):
    client, _, _ = door
    resp = client.get("/door/meeting/126")
    assert resp.status_code == 401
    assert "no_session" in resp.text


def test_transcript_falls_back_to_the_route_that_exists_today(config, signer, store):
    """`/meetings/{id}/transcript` is in flight; 405 must fall through, not fail the page."""
    meetings = MeetingsClient(
        config.meetings_url, transport=make_meetings_transport(record_route_status=405)
    )
    app = create_app(config, signer=signer, store=store, meetings=meetings)
    with TestClient(app) as client:
        client.get("/door/verify", params={"t": link_for(signer)})
        page = client.get("/door/meeting/126")
        assert page.status_code == 200
        assert "Henry Buisseret" in page.text


def test_empty_transcript_is_stated_not_implied(config, signer, store):
    meetings = MeetingsClient(
        config.meetings_url, transport=make_meetings_transport(segments=[])
    )
    app = create_app(config, signer=signer, store=store, meetings=meetings)
    with TestClient(app) as client:
        client.get("/door/verify", params={"t": link_for(signer)})
        page = client.get("/door/meeting/126")
        assert page.status_code == 200
        assert "empty" in page.text.lower()


# -- scope ----------------------------------------------------------------------

def test_a_session_may_read_only_the_meeting_its_token_named(door):
    client, signer, _ = door
    client.get("/door/verify", params={"t": link_for(signer, meeting_id="126")})
    denied = client.get("/door/meeting/999")
    assert denied.status_code == 403
    assert "out_of_scope_meeting" in denied.text


def test_scope_is_shown_on_the_page(door):
    client, signer, _ = door
    client.get("/door/verify", params={"t": link_for(signer, scope="member")})
    assert "member" in client.get("/door/meeting/126").text


def test_unknown_scope_degrades_to_guest(door):
    client, signer, _ = door
    client.get("/door/verify", params={"t": link_for(signer, scope="superuser")})
    page = client.get("/door/meeting/126")
    assert "guest" in page.text
    assert "superuser" not in page.text


# -- steering -------------------------------------------------------------------

def test_steer_appends_a_dated_entry_and_acknowledges(door):
    client, signer, store = door
    client.get("/door/verify", params={"t": link_for(signer)})
    resp = client.post(
        "/door/steer",
        data={"meeting_id": "126", "text": "next time, focus on decisions not descriptions"},
    )
    assert resp.status_code == 200
    assert "shape your next artifact" in resp.text

    doc = store.read_instructions(EMAIL)
    assert "next time, focus on decisions not descriptions" in doc
    assert "via chat door · meeting 126" in doc


def test_two_steers_both_land(door):
    client, signer, store = door
    client.get("/door/verify", params={"t": link_for(signer)})
    for text in ("shorter please", "name the owner of each commitment"):
        client.post("/door/steer", data={"meeting_id": "126", "text": text})
    doc = store.read_instructions(EMAIL)
    assert doc.count("via chat door") == 2
    assert "shorter please" in doc and "name the owner" in doc


def test_steer_keeps_non_ascii_text_intact(door):
    """Artifacts are multilingual; so are the replies. A browser percent-encodes UTF-8."""
    client, signer, store = door
    client.get("/door/verify", params={"t": link_for(signer)})
    russian = "в следующий раз — только решения, без пересказа"
    client.post("/door/steer", data={"meeting_id": "126", "text": russian})
    assert russian in store.read_instructions(EMAIL)


def test_steer_outside_scope_is_refused(door):
    client, signer, store = door
    client.get("/door/verify", params={"t": link_for(signer, meeting_id="126")})
    resp = client.post("/door/steer", data={"meeting_id": "999", "text": "nope"})
    assert resp.status_code == 403
    assert "nope" not in store.read_instructions(EMAIL)


def test_steer_without_a_session_is_refused(door):
    client, _, store = door
    resp = client.post("/door/steer", data={"meeting_id": "126", "text": "hello"})
    assert resp.status_code == 401
    assert store.get_user(EMAIL) is None


def test_full_loop_link_to_steer(door):
    """The demo path in one test: mail-shaped link → click → read → steer."""
    client, signer, store = door
    url = build_magic_link("http://door.test", link_for(signer))
    assert url.startswith("http://door.test/door/verify?t=")
    token = url.split("t=", 1)[1]

    assert client.get("/door/verify", params={"t": token}, follow_redirects=True).status_code == 200
    client.post("/door/steer", data={"meeting_id": "126", "text": "less narrative, more asks"})
    assert "less narrative, more asks" in store.read_instructions(EMAIL)

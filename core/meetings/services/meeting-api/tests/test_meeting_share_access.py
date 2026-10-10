"""Sharing a meeting: the owner sees who can read it and takes access back (deny tests, P20).

Offline over the in-memory fakes, driving the SHIPPED routes. Three people throughout:
OWNER shares, INVITEE is invited, OUTSIDER is never invited. Every read path is checked for the
outsider (nothing, live or after), for a removed invitee (loses it at once), and for the owner-only
management routes (an invitee cannot list, revoke, remove or change settings).
"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.recordings import build_router, upload_chunk
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage

OWNER, INVITEE, OUTSIDER = 7, 8, 9
INVITEE_EMAIL = "invitee@example.test"
PLAT, NID = "google_meet", "abc-defg-hij"


def _h(uid, email=None):
    h = {"x-user-id": str(uid)}
    if email:
        h["x-user-email"] = email
    return h


def _setup(status="active"):
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=OWNER, platform=PLAT, native_meeting_id=NID, status=status,
                             segments=[{"segment_id": "s1", "text": "hello", "speaker": "A"}])
    return store, mid, TestClient(create_app(store, redis=None))


def _invite(client, mid, email=INVITEE_EMAIL):
    r = client.post(f"/meetings/{mid}/share",
                    json={"mode": "restricted", "allowed_emails": [email]}, headers=_h(OWNER))
    assert r.status_code == 200
    return r.json()


def _can_read(client, mid, uid):
    return client.get(f"/transcripts/by-id/{mid}", headers=_h(uid)).status_code == 200


def _can_subscribe(client, uid):
    r = client.post("/ws/authorize-subscribe",
                    json={"meetings": [{"platform": PLAT, "native_meeting_id": NID}]}, headers=_h(uid))
    return bool(r.json().get("authorized"))


def _can_see_meeting(client, mid, uid):
    return client.get(f"/meetings/{mid}", headers=_h(uid)).status_code == 200


def test_outsider_gets_nothing_live_or_after():
    for status in ("active", "completed"):
        store, mid, client = _setup(status)
        grant = _invite(client, mid)
        client.post("/transcripts/share/accept", json={"token": grant["token"]},
                    headers=_h(INVITEE, INVITEE_EMAIL))
        assert not _can_read(client, mid, OUTSIDER)
        assert not _can_subscribe(client, OUTSIDER)
        assert not _can_see_meeting(client, mid, OUTSIDER)
        # holding the invitee's link does not help: it is restricted to the invitee's address
        r = client.post("/transcripts/share/accept", json={"token": grant["token"]},
                        headers=_h(OUTSIDER, "outsider@example.test"))
        assert r.status_code == 403
        assert not _can_read(client, mid, OUTSIDER)


def test_invitee_reads_after_accepting_and_the_owner_sees_them():
    store, mid, client = _setup()
    grant = _invite(client, mid)
    pending = client.get(f"/meetings/{mid}/access", headers=_h(OWNER)).json()
    assert [i["emails"] for i in pending["invites"]] == [[INVITEE_EMAIL]]
    assert pending["people"] == []
    assert "token" not in str(pending) and "secret_hash" not in str(pending)

    client.post("/transcripts/share/accept", json={"token": grant["token"]},
                headers=_h(INVITEE, INVITEE_EMAIL))
    assert _can_read(client, mid, INVITEE) and _can_subscribe(client, INVITEE)
    view = client.get(f"/meetings/{mid}/access", headers=_h(OWNER)).json()
    assert view["people"] == [{**view["people"][0], "user_id": INVITEE, "email": INVITEE_EMAIL,
                               "role": "viewer"}]
    assert view["invites"] == []  # accepted, no longer pending


def test_removed_invitee_loses_access_immediately_and_cannot_reuse_the_link():
    store, mid, client = _setup()
    grant = _invite(client, mid)
    client.post("/transcripts/share/accept", json={"token": grant["token"]},
                headers=_h(INVITEE, INVITEE_EMAIL))
    assert _can_read(client, mid, INVITEE)

    r = client.delete(f"/meetings/{mid}/viewers/{INVITEE}", headers=_h(OWNER))
    assert r.status_code == 200 and r.json()["people"] == []
    assert not _can_read(client, mid, INVITEE)
    assert not _can_subscribe(client, INVITEE)
    assert not _can_see_meeting(client, mid, INVITEE)
    again = client.post("/transcripts/share/accept", json={"token": grant["token"]},
                        headers=_h(INVITEE, INVITEE_EMAIL))
    assert again.status_code == 403
    assert not _can_read(client, mid, INVITEE)


def test_a_fresh_invite_after_removal_admits_them_again():
    store, mid, client = _setup()
    first = _invite(client, mid)
    client.post("/transcripts/share/accept", json={"token": first["token"]}, headers=_h(INVITEE, INVITEE_EMAIL))
    client.delete(f"/meetings/{mid}/viewers/{INVITEE}", headers=_h(OWNER))
    second = _invite(client, mid)
    ok = client.post("/transcripts/share/accept", json={"token": second["token"]},
                     headers=_h(INVITEE, INVITEE_EMAIL))
    assert ok.status_code == 200 and _can_read(client, mid, INVITEE)


def test_turning_a_link_off_removes_the_people_who_came_through_it():
    store, mid, client = _setup()
    link = client.post(f"/meetings/{mid}/share", json={"mode": "open"}, headers=_h(OWNER)).json()
    client.post("/transcripts/share/accept", json={"token": link["token"]}, headers=_h(INVITEE, INVITEE_EMAIL))
    view = client.get(f"/meetings/{mid}/access", headers=_h(OWNER)).json()
    assert view["links"][0]["joined"] == 1
    r = client.delete(f"/meetings/{mid}/share/{link['id']}", headers=_h(OWNER))
    assert r.status_code == 200 and r.json()["links"] == [] and r.json()["people"] == []
    assert not _can_read(client, mid, INVITEE)
    late = client.post("/transcripts/share/accept", json={"token": link["token"]},
                       headers=_h(OUTSIDER, "outsider@example.test"))
    assert late.status_code == 403


def test_management_routes_are_owner_only_and_leak_no_existence():
    store, mid, client = _setup()
    grant = _invite(client, mid)
    client.post("/transcripts/share/accept", json={"token": grant["token"]}, headers=_h(INVITEE, INVITEE_EMAIL))
    for uid in (INVITEE, OUTSIDER):
        assert client.get(f"/meetings/{mid}/access", headers=_h(uid)).status_code == 404
        assert client.patch(f"/meetings/{mid}/access", json={"recording": True}, headers=_h(uid)).status_code == 404
        assert client.delete(f"/meetings/{mid}/share/{grant['id']}", headers=_h(uid)).status_code == 404
        assert client.delete(f"/meetings/{mid}/viewers/{INVITEE}", headers=_h(uid)).status_code == 404
    # an unknown meeting reads exactly like a foreign one
    assert client.get("/meetings/99999/access", headers=_h(OWNER)).status_code == 404
    assert _can_read(client, mid, INVITEE)  # none of the refused calls changed anything


def test_the_roster_is_never_shipped_to_a_recipient():
    store, mid, client = _setup()
    grant = _invite(client, mid)
    client.post("/transcripts/share/accept", json={"token": grant["token"]}, headers=_h(INVITEE, INVITEE_EMAIL))
    data = client.get(f"/meetings/{mid}", headers=_h(INVITEE)).json()["data"]
    for key in ("share_grants", "transcript_viewers", "share_viewers", "share_removed"):
        assert key not in data


def test_settings_body_is_validated():
    store, mid, client = _setup()
    assert client.patch(f"/meetings/{mid}/access", json={"recording": "yes"}, headers=_h(OWNER)).status_code == 422
    r = client.patch(f"/meetings/{mid}/access", json={"recording": True}, headers=_h(OWNER))
    assert r.status_code == 200 and r.json()["recording"] is True


# ── the recording ──────────────────────────────────────────────────────────────────────────────
def _wav() -> bytes:
    import struct
    data = b"\x00" * 4
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 16000, 32000, 2, 16)
    chunk = struct.pack("<4sI", b"data", len(data)) + data
    return struct.pack("<4sI4s", b"RIFF", 4 + len(fmt) + len(chunk), b"WAVE") + fmt + chunk


def _recording_client(data):
    repo, storage = InMemoryRecordingRepo(), InMemoryStorage()
    repo.seed(meeting_id=1, user_id=OWNER, session_uid="s", status="completed", data=data)
    rec = asyncio.run(upload_chunk(repo, storage, token_meeting_id=1, session_uid="s", data=_wav(),
                                   media_format="wav", chunk_seq=0, is_final=True))
    app = FastAPI()
    app.include_router(build_router(repo, storage, token_secret="t"))
    client = TestClient(app)
    media = client.get(f"/recordings/{rec['recording_id']}", headers=_h(OWNER)).json()["media_files"][0]["id"]
    return client, rec["recording_id"], media


def _recording_status(client, rid, media, uid, ws=None):
    h = _h(uid)
    if ws:
        h["x-user-workspaces"] = ws
    return (client.get(f"/recordings?meeting_id=1", headers=h).json()["total"],
            client.get(f"/recordings/{rid}", headers=h).status_code,
            client.get(f"/recordings/{rid}/media/{media}/raw?type=audio", headers=h).status_code)


def test_recording_needs_the_owners_permission_as_well_as_access():
    client, rid, media = _recording_client({"transcript_viewers": [INVITEE]})
    assert _recording_status(client, rid, media, INVITEE) == (0, 404, 404)  # access, no permission
    client2, rid2, media2 = _recording_client({"transcript_viewers": [INVITEE],
                                               "share_settings": {"recording": True}})
    assert _recording_status(client2, rid2, media2, INVITEE) == (1, 200, 200)
    assert _recording_status(client2, rid2, media2, OUTSIDER) == (0, 404, 404)
    assert _recording_status(client2, rid2, media2, OWNER) == (1, 200, 200)


def test_recording_for_a_workspace_member_follows_the_same_rule():
    data = {"workspace_id": "ws-team", "share_settings": {"recording": True}}
    client, rid, media = _recording_client(data)
    assert _recording_status(client, rid, media, OUTSIDER, ws="ws-team") == (1, 200, 200)
    assert _recording_status(client, rid, media, OUTSIDER, ws="ws-other") == (0, 404, 404)


def test_the_account_wide_list_stays_the_callers_own():
    """Without `meeting_id`, `GET /recordings` is still "my recordings" — a shared meeting's
    recording is reachable by its id and by naming the meeting, never mixed into the list."""
    client, rid, media = _recording_client({"transcript_viewers": [INVITEE],
                                            "share_settings": {"recording": True}})
    assert client.get("/recordings", headers=_h(INVITEE)).json()["total"] == 0


def test_a_recipient_cannot_delete_the_recording():
    client, rid, media = _recording_client({"transcript_viewers": [INVITEE],
                                            "share_settings": {"recording": True}})
    assert client.delete(f"/recordings/{rid}", headers=_h(INVITEE)).status_code == 404


# ── the invite mail (meeting.shared → flows `meeting_share`) ──────────────────────────────────────
def _capture(monkeypatch, landed=True):
    from meeting_api import events
    sent = []

    async def fake_publish(event_type, source_event_id, refs, *, timeout=None):
        sent.append((event_type, source_event_id, refs))
        return landed
    monkeypatch.setattr(events, "publish", fake_publish)
    return sent


def test_an_invite_with_notify_hands_one_fact_per_address_to_flows(monkeypatch):
    sent = _capture(monkeypatch)
    store, mid, client = _setup()
    r = client.post(f"/meetings/{mid}/share", headers={**_h(OWNER), "x-user-email": "owner@example.test"},
                    json={"mode": "restricted", "allowed_emails": [INVITEE_EMAIL], "notify": True,
                          "workspace_invite": "W" * 43})
    assert r.status_code == 200 and r.json()["notified"] == {INVITEE_EMAIL: True}
    [(etype, sid, refs)] = sent
    assert etype == "meeting.shared" and sid.startswith(f"share-{r.json()['id']}-")
    assert INVITEE_EMAIL not in sid, "the dedupe id carries no address"
    assert refs["email"] == INVITEE_EMAIL and refs["uid"] == str(OWNER)
    assert refs["token"] == r.json()["token"] and refs["inviter"] == "owner@example.test"
    assert refs["workspace_invite"] == "W" * 43
    assert "link" not in refs, "flows composes the link from its own UI address"


def test_no_mail_unless_asked_and_never_for_an_open_link(monkeypatch):
    sent = _capture(monkeypatch)
    store, mid, client = _setup()
    assert client.post(f"/meetings/{mid}/share", headers=_h(OWNER),
                       json={"mode": "restricted", "allowed_emails": [INVITEE_EMAIL]}).status_code == 200
    assert client.post(f"/meetings/{mid}/share", headers=_h(OWNER),
                       json={"mode": "open", "notify": True}).status_code == 422
    assert client.post(f"/meetings/{mid}/share", headers=_h(OWNER),
                       json={"mode": "restricted", "allowed_emails": [INVITEE_EMAIL], "notify": True,
                             "workspace_invite": "../../not a token"}).status_code == 422
    assert sent == []


def test_a_mail_that_did_not_land_is_reported_and_the_invite_still_stands(monkeypatch):
    _capture(monkeypatch, landed=False)
    store, mid, client = _setup()
    r = client.post(f"/meetings/{mid}/share", headers=_h(OWNER),
                    json={"mode": "restricted", "allowed_emails": [INVITEE_EMAIL], "notify": True})
    assert r.status_code == 200 and r.json()["notified"] == {INVITEE_EMAIL: False}
    ok = client.post("/transcripts/share/accept", json={"token": r.json()["token"]},
                     headers=_h(INVITEE, INVITEE_EMAIL))
    assert ok.status_code == 200


def test_a_stranger_cannot_mail_from_someone_elses_meeting(monkeypatch):
    sent = _capture(monkeypatch)
    store, mid, client = _setup()
    r = client.post(f"/meetings/{mid}/share", headers=_h(OUTSIDER),
                    json={"mode": "restricted", "allowed_emails": ["x@example.test"], "notify": True})
    assert r.status_code == 404 and sent == []

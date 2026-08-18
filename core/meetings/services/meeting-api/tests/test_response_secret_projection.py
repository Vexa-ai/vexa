"""``meeting.data`` operational state stays off the API response — on every read edge, for every viewer.

The meeting row's ``data`` blob is shared: besides the meeting's own content it carries state other
parts of the system stamped there — the per-user webhook signing config (``bot_spawn`` writes it so
the lifecycle callback can sign deliveries), the transcript-share grants and viewer roster, the
authenticated-session path. None of it is meeting content, and the reads that ship ``data`` authorize
more than the owner: owner **or** transcript-share recipient **or** member of the bound workspace
(the access union ``list_meetings`` documents). So the response edge projects it away.

The share-recipient cases are the load-bearing ones: a second user, holding nothing but a redeemed
transcript link, must receive the transcript and none of the owner's configuration. Every other
surface already reports the webhook config the same way (``GET /user/webhook`` → ``webhook_secret_set``
plus a masked value, never the value).

The projection is a RESPONSE-edge transform and must not disturb the stored row — the delivery path
reads the signing secret out of the meeting row at send time — so the last test signs a real delivery
after a read has been served.
"""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.collector.projection import (
    RESPONSE_OMIT_KEYS,
    is_sensitive_key,
    project_list_data,
    project_response_data,
)

OWNER, VIEWER, STRANGER = 41, 42, 43
PLAT, NID = "google_meet", "xyz-abcd-efg"
SECRET = "whsec-owner-signing-key"
HOOK = "https://hooks.example.com/owner-endpoint"

# What a spawned meeting's row actually holds: real content next to the operational keys.
ROW_DATA = {
    "title": "Quarterly review",
    "notes": "agenda in the doc",
    "constructed_meeting_url": f"https://meet.google.com/{NID}",
    "transcribe_enabled": True,
    # operational state — none of it the reader's
    "webhook_url": HOOK,
    "webhook_secret": SECRET,
    "webhook_events": {"meeting.status_change": True},
    "auth_userdata_path": "s3://vexa-bot-userdata/owner/session.tar",
}

# Keys a reader may legitimately see — asserted present so the projection is proven to be a strip of
# the operational keys and not a blanket erasure of ``data``.
CONTENT_KEYS = ("title", "notes", "constructed_meeting_url", "transcribe_enabled")


def _client(data=None):
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=OWNER, platform=PLAT, native_meeting_id=NID,
                       data=dict(data if data is not None else ROW_DATA))
    return store, TestClient(create_app(store, redis=None))


def _mid(store):
    return next(iter(store._meetings))


def _share_with(client, uid):
    """Owner mints an open share link; ``uid`` redeems it → a share-only reader of this meeting."""
    token = client.post(f"/meetings/{PLAT}/{NID}/share", json={"mode": "open"},
                        headers={"x-user-id": str(OWNER)}).json()["token"]
    r = client.post("/transcripts/share/accept", json={"token": token},
                    headers={"x-user-id": str(uid)})
    assert r.status_code == 200 and r.json()["ok"] is True
    return r


def _assert_clean(data: dict, *, where: str):
    """No omitted key, and nothing credential-SHAPED, survived into a response ``data`` blob."""
    for key in RESPONSE_OMIT_KEYS:
        assert key not in data, f"{where}: {key} rode the response"
    leaked = [k for k in data if is_sensitive_key(k)]
    assert not leaked, f"{where}: credential-shaped keys rode the response: {leaked}"
    # and the value itself never appears, under any key or nesting
    assert SECRET not in repr(data), f"{where}: the signing secret's VALUE rode the response"


# ── the transcript detail edge ───────────────────────────────────────────────────────────────────

def test_transcript_detail_omits_operational_keys_for_the_owner():
    store, client = _client()
    r = client.get(f"/transcripts/by-id/{_mid(store)}", headers={"x-user-id": str(OWNER)})
    assert r.status_code == 200
    data = r.json()["data"]
    _assert_clean(data, where="GET /transcripts/by-id (owner)")
    for k in CONTENT_KEYS:
        assert k in data, f"the projection erased content key {k}"


def test_transcript_detail_omits_operational_keys_for_a_share_recipient():
    """THE case: a different user, holding only a redeemed transcript link, reads the transcript.

    They are authorized for the meeting's content and nothing else — the owner's webhook signing
    config is not part of what was shared with them.
    """
    store, client = _client()
    _share_with(client, VIEWER)
    r = client.get(f"/transcripts/by-id/{_mid(store)}", headers={"x-user-id": str(VIEWER)})
    assert r.status_code == 200, "the share recipient must still be able to read the transcript"
    _assert_clean(r.json()["data"], where="GET /transcripts/by-id (share recipient)")


def test_share_recipient_still_gets_the_meetings_content():
    """The projection must not cost the recipient the thing that WAS shared."""
    store, client = _client()
    _share_with(client, VIEWER)
    data = client.get(f"/transcripts/by-id/{_mid(store)}",
                      headers={"x-user-id": str(VIEWER)}).json()["data"]
    for k in CONTENT_KEYS:
        assert k in data, f"share recipient lost content key {k}"


# ── the meeting detail + list edges ──────────────────────────────────────────────────────────────

def test_meeting_detail_omits_operational_keys_for_owner_and_share_recipient():
    store, client = _client()
    mid = _mid(store)
    _share_with(client, VIEWER)
    for uid, who in ((OWNER, "owner"), (VIEWER, "share recipient")):
        r = client.get(f"/meetings/{mid}", headers={"x-user-id": str(uid)})
        assert r.status_code == 200
        _assert_clean(r.json()["data"], where=f"GET /meetings/{{id}} ({who})")


def test_meetings_list_omits_operational_keys_for_a_share_recipient():
    """The list is where a recipient first meets a meeting they do not own — same rule applies."""
    store, client = _client()
    _share_with(client, VIEWER)
    rows = client.get("/meetings", headers={"x-user-id": str(VIEWER)}).json()["meetings"]
    assert rows, "the shared meeting must surface in the recipient's list"
    for row in rows:
        _assert_clean(row.get("data") or {}, where="GET /meetings (share recipient)")


def test_stranger_still_gets_nothing():
    """Unchanged: a user with neither ownership, share, nor workspace membership reads nothing."""
    store, client = _client()
    assert client.get(f"/transcripts/by-id/{_mid(store)}",
                      headers={"x-user-id": str(STRANGER)}).status_code == 404


# ── the projection functions themselves ──────────────────────────────────────────────────────────

def test_projection_is_pure_and_leaves_the_stored_row_intact():
    """It shapes the RESPONSE. The row keeps what the system needs to keep."""
    stored = dict(ROW_DATA)
    projected = project_response_data(stored)
    assert stored == ROW_DATA, "the projection mutated its input"
    assert "webhook_secret" not in projected and stored["webhook_secret"] == SECRET


def test_share_grants_and_viewer_roster_stay_off_the_response():
    """``share_grants`` carries each link's secret hash + allow-list; ``transcript_viewers`` is the
    roster of everyone who can read the meeting. Both are this read's authorization machinery."""
    store, client = _client()
    _share_with(client, VIEWER)
    data = client.get(f"/transcripts/by-id/{_mid(store)}",
                      headers={"x-user-id": str(VIEWER)}).json()["data"]
    assert "share_grants" not in data and "transcript_viewers" not in data
    # the stored row still has them — redeeming a second link must keep working
    assert client.post("/transcripts/share/accept",
                       json={"token": client.post(f"/meetings/{PLAT}/{NID}/share",
                                                  json={"mode": "open"},
                                                  headers={"x-user-id": str(OWNER)}).json()["token"]},
                       headers={"x-user-id": str(STRANGER)}).status_code == 200


def test_credential_shaped_keys_are_dropped_by_default():
    """A key a future producer stamps into ``data`` is omitted on NAME SHAPE, before anyone has
    thought to add it to the deny-set — so the failure mode is a missing field, not a leak."""
    blob = {"title": "t", "provider_api_key": "ak-1", "refresh_token": "rt-1",
            "db_password": "pw", "signing_key": "sk-1", "some_secret": "s"}
    for projected in (project_response_data(blob), project_list_data(blob)):
        assert projected == {"title": "t"}, projected
    # ...without catching names that merely CONTAIN a sensitive word
    keep = {"token_count": 12, "secret_santa_notes": "x", "tokens_used": 3}
    assert project_response_data(keep) == keep


def test_response_omissions_cover_the_delivery_paths_internal_keys():
    """The webhook delivery path already refuses to ship these in a payload
    (``webhooks.delivery._INTERNAL_DATA_KEYS``). An API response is no less outbound than a webhook,
    so the response edge must cover the same credential keys — this pins the two together."""
    from meeting_api.webhooks.delivery import _INTERNAL_DATA_KEYS

    credential_keys = {k for k in _INTERNAL_DATA_KEYS if "webhook" in k or is_sensitive_key(k)}
    missing = credential_keys - RESPONSE_OMIT_KEYS
    assert not missing, f"delivery strips these but the response edge does not: {sorted(missing)}"


# ── the delivery path is untouched ───────────────────────────────────────────────────────────────

def test_webhook_delivery_still_signs_after_a_read_has_been_served():
    """The regression this fix must not cause. The signing secret lives on the meeting row because
    the lifecycle callback reads it there at send time. The projection runs on the response edge, so
    the row — and therefore the signature — is unaffected. Read first, then deliver, then verify.
    """
    from meeting_api.webhooks.delivery import sign_payload, verify_signature

    store, client = _client()
    mid = _mid(store)
    # a read happens (this is what the projection touches)
    assert client.get(f"/transcripts/by-id/{mid}", headers={"x-user-id": str(OWNER)}).status_code == 200

    # the delivery path's source of truth: the stored row, not the response
    row_secret = store._meetings[mid]["data"]["webhook_secret"]
    assert row_secret == SECRET, "the projection must not have touched the stored signing secret"
    assert store._meetings[mid]["data"]["webhook_url"] == HOOK

    body, ts = b'{"event_type":"meeting.completed"}', "1750000000"
    headers = {"X-Webhook-Signature": sign_payload(body, row_secret, ts),
               "X-Webhook-Timestamp": ts}
    assert verify_signature(body, headers, SECRET), "signature must verify against the owner's secret"


def test_webhook_sink_delivers_with_the_row_secret_end_to_end():
    """The same property through the real sink: it signs with the secret handed to ``deliver``, which
    the lifecycle callback sources from ``meeting_row["data"]`` — a path the projection never sees."""
    from meeting_api.webhooks.delivery import WebhookSink, verify_signature

    captured: dict = {}

    async def transport(url, body, headers):
        captured.update(url=url, body=body, headers=headers)

        class _R:
            status_code = 200
        return _R()

    store, client = _client()
    mid = _mid(store)
    client.get(f"/meetings/{mid}", headers={"x-user-id": str(OWNER)})  # serve a read first

    data = store._meetings[mid]["data"]
    sink = WebhookSink(transport=transport, resolver=lambda host: ["93.184.216.34"])
    result = asyncio.run(sink.deliver(
        data["webhook_url"],
        {"event_type": "meeting.completed", "data": {"id": mid}},
        data.get("webhook_secret"),
        events_config={"meeting.completed": True},
    ))

    assert result.status == "delivered"
    assert captured["url"] == HOOK
    assert verify_signature(
        captured["body"] if isinstance(captured["body"], bytes) else captured["body"].encode(),
        captured["headers"], SECRET,
    ), "the receiver's verification must still pass with the owner's secret"

"""``meeting.data`` on the API response: two tiers, decided by WHO is reading.

The meeting row's ``data`` blob is shared. Besides the meeting's own content it carries state other
parts of the system stamped there — the per-user webhook config (``bot_spawn`` writes it so the
lifecycle callback can deliver), the transcript-share grants and viewer roster, the
authenticated-session path. The reads that ship ``data`` authorize more than the owner: owner **or**
transcript-share recipient **or** member of the bound workspace (the access union ``list_meetings``
documents). So the response edge projects — but not with one rule for everybody:

**Tier 1 · never shipped, to anyone** (``SENSITIVE_OMIT_KEYS`` + anything credential-SHAPED):
``webhook_secret``, ``share_grants``, ``auth_userdata_path``. Credential material and other people's
material. The owner is not excluded — they have dedicated endpoints for all three, and no meeting
response is the right carrier.

**Tier 2 · the owner's, and only the owner's** (``OWNER_ONLY_KEYS``): ``webhook_url``,
``webhook_events``, ``transcript_viewers``. Owner-private but not credentials. The v0.10 REST
contract promises the owner their own webhook config back on their own meeting row — the compat
suite's ``test_10_user_webhook_config_flow`` reads ``data.webhook_url`` out of ``GET /meetings``
after a spawn — so stripping it from the owner is a backward-compatibility break, not a hardening.
Stripped for everyone else: a transcript-share recipient was given a transcript, not the owner's
endpoint configuration, and must not be able to enumerate who ELSE the meeting was shared with.

Both load-bearing cases have a test on every edge (list · meeting detail · transcript detail · PATCH
echo): the owner keeps tier 2, a second user holding nothing but a redeemed share link keeps
neither, and nobody at all sees tier 1.

The projection is a RESPONSE-edge transform and must not disturb the stored row — the delivery path
reads the signing secret out of the meeting row at send time — so the last two tests sign a real
delivery after a read has been served.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.collector.projection import (
    OWNER_ONLY_KEYS,
    RESPONSE_OMIT_KEYS,
    SENSITIVE_OMIT_KEYS,
    is_sensitive_key,
    project_list_data,
    project_response_data,
)

OWNER, VIEWER, STRANGER, WS_MEMBER = 41, 42, 43, 44
PLAT, NID = "google_meet", "xyz-abcd-efg"
SECRET = "whsec-owner-signing-key"
HOOK = "https://hooks.example.com/owner-endpoint"
EVENTS = {"meeting.status_change": True}
USERDATA = "s3://vexa-bot-userdata/owner/session.tar"

# What a spawned meeting's row actually holds: real content next to both tiers of operational state.
ROW_DATA = {
    "title": "Quarterly review",
    "notes": "agenda in the doc",
    "constructed_meeting_url": f"https://meet.google.com/{NID}",
    "transcribe_enabled": True,
    # tier 2 — the owner's own configuration
    "webhook_url": HOOK,
    "webhook_events": EVENTS,
    # tier 1 — credential material, nobody's to read
    "webhook_secret": SECRET,
    "auth_userdata_path": USERDATA,
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


# ── the three assertions the whole file is made of ───────────────────────────────────────────────

def _assert_no_credentials(data: dict, *, where: str):
    """TIER 1. Nothing credential-shaped survived — for ANY viewer, the owner included."""
    for key in SENSITIVE_OMIT_KEYS:
        assert key not in data, f"{where}: {key} rode the response"
    leaked = [k for k in data if is_sensitive_key(k)]
    assert not leaked, f"{where}: credential-shaped keys rode the response: {leaked}"
    # and the value itself never appears, under any key or nesting
    assert SECRET not in repr(data), f"{where}: the signing secret's VALUE rode the response"
    assert USERDATA not in repr(data), f"{where}: the session userdata path rode the response"


def _assert_owner_private_absent(data: dict, *, where: str):
    """TIER 2, non-owner view. The owner's configuration and reader roster are not this reader's."""
    for key in OWNER_ONLY_KEYS:
        assert key not in data, f"{where}: owner-private {key} rode the response to a non-owner"
    assert HOOK not in repr(data), f"{where}: the owner's endpoint URL reached a non-owner"


def _assert_owner_private_present(data: dict, *, where: str):
    """TIER 2, owner view — the v0.10 contract. This is the assertion #1243 broke."""
    assert data.get("webhook_url") == HOOK, (
        f"{where}: the owner lost their own webhook_url — this is the v0.10 REST contract "
        f"(compat test_10_user_webhook_config_flow reads it back off the meeting row)"
    )
    assert data.get("webhook_events") == EVENTS, f"{where}: the owner lost their own webhook_events"


# ── TIER 2 · the owner keeps their own configuration, on every edge (the v0.10 regression) ───────

def test_owner_keeps_their_webhook_config_on_the_meetings_list():
    """THE regressed contract, on the exact edge the compat suite reads.

    ``test_10_user_webhook_config_flow`` configures a webhook, spawns a bot, then polls
    ``GET /meetings`` until the row carries ``data.webhook_url``. A single user, their own meeting.
    #1243 stripped it unconditionally and that poll timed out.
    """
    store, client = _client()
    rows = client.get("/meetings", headers={"x-user-id": str(OWNER)}).json()["meetings"]
    assert rows, "the owner's own meeting must be in their list"
    data = rows[0]["data"]
    _assert_owner_private_present(data, where="GET /meetings (owner)")
    _assert_no_credentials(data, where="GET /meetings (owner)")


def test_owner_keeps_their_webhook_config_on_the_meeting_detail():
    store, client = _client()
    data = client.get(f"/meetings/{_mid(store)}", headers={"x-user-id": str(OWNER)}).json()["data"]
    _assert_owner_private_present(data, where="GET /meetings/{id} (owner)")
    _assert_no_credentials(data, where="GET /meetings/{id} (owner)")


@pytest.mark.parametrize("path_for", [
    lambda store: f"/transcripts/by-id/{_mid(store)}",
    lambda store: f"/transcripts/{PLAT}/{NID}",
], ids=["by-id", "native"])
def test_owner_keeps_their_webhook_config_on_the_transcript_detail(path_for):
    store, client = _client()
    r = client.get(path_for(store), headers={"x-user-id": str(OWNER)})
    assert r.status_code == 200
    data = r.json()["data"]
    _assert_owner_private_present(data, where="GET /transcripts (owner)")
    _assert_no_credentials(data, where="GET /transcripts (owner)")


def test_owner_keeps_their_webhook_config_on_the_patch_echo():
    """PATCH is owner-scoped in the store (``WHERE id = … AND user_id = …``), so its echo is always
    the owner reading their own row back."""
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=OWNER, platform=PLAT, native_meeting_id=NID,
                             status="scheduled", data=dict(ROW_DATA))
    client = TestClient(create_app(store, redis=None))
    r = client.patch(f"/meetings/{mid}", json={"title": "renamed"},
                     headers={"x-user-id": str(OWNER)})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    _assert_owner_private_present(data, where="PATCH /meetings/{id} echo (owner)")
    _assert_no_credentials(data, where="PATCH /meetings/{id} echo (owner)")


def test_owner_sees_the_reader_roster_and_the_share_recipient_does_not():
    """``transcript_viewers`` is the meeting's reader roster. The owner may see who they shared
    with; a recipient enumerating it would learn who ELSE holds a link — other people's material."""
    store, client = _client()
    _share_with(client, VIEWER)
    mid = _mid(store)

    owner_data = client.get(f"/meetings/{mid}", headers={"x-user-id": str(OWNER)}).json()["data"]
    assert VIEWER in (owner_data.get("transcript_viewers") or []), (
        "the owner must still be able to see who their meeting is shared with"
    )

    viewer_data = client.get(f"/meetings/{mid}", headers={"x-user-id": str(VIEWER)}).json()["data"]
    assert "transcript_viewers" not in viewer_data, "a recipient enumerated the reader roster"


# ── TIER 2 · a NON-OWNER gets none of it, on every edge ──────────────────────────────────────────

def test_share_recipient_gets_no_owner_private_keys_from_the_list():
    """The list is where a recipient first meets a meeting they do not own."""
    store, client = _client()
    _share_with(client, VIEWER)
    rows = client.get("/meetings", headers={"x-user-id": str(VIEWER)}).json()["meetings"]
    assert rows, "the shared meeting must surface in the recipient's list"
    for row in rows:
        assert row["shared"] is True, "the row the recipient sees is not their own"
        _assert_owner_private_absent(row["data"], where="GET /meetings (share recipient)")
        _assert_no_credentials(row["data"], where="GET /meetings (share recipient)")


def test_share_recipient_gets_no_owner_private_keys_from_the_meeting_detail():
    store, client = _client()
    _share_with(client, VIEWER)
    data = client.get(f"/meetings/{_mid(store)}", headers={"x-user-id": str(VIEWER)}).json()["data"]
    _assert_owner_private_absent(data, where="GET /meetings/{id} (share recipient)")
    _assert_no_credentials(data, where="GET /meetings/{id} (share recipient)")


def test_share_recipient_gets_no_owner_private_keys_from_the_transcript_detail():
    """THE case: a different user, holding only a redeemed transcript link, reads the transcript.

    They are authorized for the meeting's content and nothing else — the owner's webhook endpoint
    configuration is not part of what was shared with them.
    """
    store, client = _client()
    _share_with(client, VIEWER)
    r = client.get(f"/transcripts/by-id/{_mid(store)}", headers={"x-user-id": str(VIEWER)})
    assert r.status_code == 200, "the share recipient must still be able to read the transcript"
    _assert_owner_private_absent(r.json()["data"], where="GET /transcripts/by-id (share recipient)")
    _assert_no_credentials(r.json()["data"], where="GET /transcripts/by-id (share recipient)")


def test_bound_workspace_member_is_not_the_owner_either():
    """The third branch of the access union. Membership authorizes the meeting, not the owner's
    configuration — ``shared`` is true for them too."""
    store, client = _client({**ROW_DATA, "workspace_id": "ws-42"})
    mid = _mid(store)
    hdrs = {"x-user-id": str(WS_MEMBER), "x-user-workspaces": "ws-42"}

    r = client.get(f"/transcripts/by-id/{mid}", headers=hdrs)
    assert r.status_code == 200, "a member of the bound workspace must still read the transcript"
    _assert_owner_private_absent(r.json()["data"], where="GET /transcripts/by-id (workspace member)")
    _assert_no_credentials(r.json()["data"], where="GET /transcripts/by-id (workspace member)")

    rows = client.get("/meetings", headers=hdrs).json()["meetings"]
    assert rows, "the bound meeting must surface in the member's list"
    for row in rows:
        _assert_owner_private_absent(row["data"], where="GET /meetings (workspace member)")


def test_share_recipient_still_gets_the_meetings_content():
    """The projection must not cost the recipient the thing that WAS shared."""
    store, client = _client()
    _share_with(client, VIEWER)
    data = client.get(f"/transcripts/by-id/{_mid(store)}",
                      headers={"x-user-id": str(VIEWER)}).json()["data"]
    for k in CONTENT_KEYS:
        assert k in data, f"share recipient lost content key {k}"


def test_stranger_still_gets_nothing():
    """Unchanged: a user with neither ownership, share, nor workspace membership reads nothing."""
    store, client = _client()
    assert client.get(f"/transcripts/by-id/{_mid(store)}",
                      headers={"x-user-id": str(STRANGER)}).status_code == 404


# ── TIER 1 · nobody, on any edge, ever ───────────────────────────────────────────────────────────

def test_credentials_never_ship_to_anyone_on_any_edge():
    """The property #1243 got right and this change must not weaken: the signing secret, the share
    grants and the session path stay off every response — for the OWNER as much as for a stranger.

    Runs after a share has been minted and redeemed, so ``share_grants`` and ``transcript_viewers``
    are genuinely populated on the row rather than absent by accident.
    """
    store, client = _client()
    mid = _mid(store)
    _share_with(client, VIEWER)
    assert "share_grants" in store._meetings[mid]["data"], "fixture did not populate share_grants"

    for uid, who in ((OWNER, "owner"), (VIEWER, "share recipient")):
        h = {"x-user-id": str(uid)}
        edges = {
            "GET /transcripts/by-id": client.get(f"/transcripts/by-id/{mid}", headers=h).json()["data"],
            "GET /meetings/{id}": client.get(f"/meetings/{mid}", headers=h).json()["data"],
            "GET /meetings": (client.get("/meetings", headers=h).json()["meetings"][0])["data"],
        }
        if uid == OWNER:
            edges["GET /transcripts/{platform}/{native}"] = client.get(
                f"/transcripts/{PLAT}/{NID}", headers=h).json()["data"]
        for edge, data in edges.items():
            _assert_no_credentials(data, where=f"{edge} ({who})")


def test_credential_shaped_keys_are_dropped_by_default_for_the_owner_too():
    """A key a future producer stamps into ``data`` is omitted on NAME SHAPE, before anyone has
    thought to add it to a set — so the failure mode is a missing field, not a leak. This is a
    TIER-1 rule: it applies to the owner as well, because nobody has decided what the key IS yet."""
    blob = {"title": "t", "provider_api_key": "ak-1", "refresh_token": "rt-1",
            "db_password": "pw", "signing_key": "sk-1", "some_secret": "s"}
    for owner in (True, False):
        for projected in (project_response_data(blob, viewer_is_owner=owner),
                          project_list_data(blob, viewer_is_owner=owner)):
            assert projected == {"title": "t"}, (owner, projected)
    # ...without catching names that merely CONTAIN a sensitive word
    keep = {"token_count": 12, "secret_santa_notes": "x", "tokens_used": 3}
    assert project_response_data(keep, viewer_is_owner=True) == keep
    assert project_response_data(keep) == keep


def test_response_omissions_cover_the_delivery_paths_internal_keys():
    """The webhook delivery path already refuses to ship these in a payload
    (``webhooks.delivery._INTERNAL_DATA_KEYS``). An API response to a party who is NOT the owner is
    no less outbound than a webhook to a third party, so the non-owner view must cover the same
    keys — this pins the two together. (The owner's own view is governed by the v0.10 contract
    instead, which is why the pin is against the union and not against tier 1.)"""
    from meeting_api.webhooks.delivery import _INTERNAL_DATA_KEYS

    credential_keys = {k for k in _INTERNAL_DATA_KEYS if "webhook" in k or is_sensitive_key(k)}
    missing = credential_keys - RESPONSE_OMIT_KEYS
    assert not missing, f"delivery strips these but the response edge does not: {sorted(missing)}"


# ── the projection functions themselves ──────────────────────────────────────────────────────────

def test_the_two_tiers_are_disjoint_and_compose_into_the_non_owner_view():
    """A key belongs to exactly one tier. Placing one in both would hide a decision nobody made."""
    assert not (SENSITIVE_OMIT_KEYS & OWNER_ONLY_KEYS), "a key is in both tiers"
    assert RESPONSE_OMIT_KEYS == SENSITIVE_OMIT_KEYS | OWNER_ONLY_KEYS
    # the credential tier must not have drifted into containing the v0.10 contract's fields
    assert {"webhook_url", "webhook_events"} <= OWNER_ONLY_KEYS
    assert {"webhook_secret", "share_grants", "auth_userdata_path"} <= SENSITIVE_OMIT_KEYS


def test_the_projections_default_to_the_strict_view():
    """A caller that has not threaded the access decision must get the NON-owner view. The failure
    direction of a forgotten keyword argument is a missing field, never a disclosure."""
    for projected in (project_response_data(ROW_DATA), project_list_data(ROW_DATA)):
        for key in RESPONSE_OMIT_KEYS:
            assert key not in projected, f"the default view shipped {key}"
        assert projected["title"] == "Quarterly review"


def test_projection_is_pure_and_leaves_the_stored_row_intact():
    """It shapes the RESPONSE. The row keeps what the system needs to keep — on both views."""
    stored = dict(ROW_DATA)
    for owner in (True, False):
        projected = project_response_data(stored, viewer_is_owner=owner)
        assert stored == ROW_DATA, "the projection mutated its input"
        assert "webhook_secret" not in projected
    assert stored["webhook_secret"] == SECRET


def test_share_grants_stay_off_the_response_without_breaking_redemption():
    """``share_grants`` carries each link's secret hash + allow-list — tier 1, off every response.
    The STORED row still has it, so redeeming a second link keeps working."""
    store, client = _client()
    _share_with(client, VIEWER)
    for uid in (OWNER, VIEWER):
        data = client.get(f"/transcripts/by-id/{_mid(store)}",
                          headers={"x-user-id": str(uid)}).json()["data"]
        assert "share_grants" not in data
    assert client.post("/transcripts/share/accept",
                       json={"token": client.post(f"/meetings/{PLAT}/{NID}/share",
                                                  json={"mode": "open"},
                                                  headers={"x-user-id": str(OWNER)}).json()["token"]},
                       headers={"x-user-id": str(STRANGER)}).status_code == 200


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

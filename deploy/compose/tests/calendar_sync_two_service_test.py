"""#991 — real admin-api save → meeting-api discovery → user sync witness.

This is deliberately a Compose test, not a route-level fake: one gateway-bound identity saves its
calendar in admin-api's Postgres-backed user record; meeting-api discovers that record over the
real internal edge and reaches the SSRF-pinned feed-fetch stage. A second identity with no saved
feed is the authoritative-absence control and must not inherit the first identity's config.
"""
from __future__ import annotations

import json
import uuid

from conftest import http, requires_docker

pytestmark = requires_docker


def _admin_headers(stack):
    return {"X-Admin-API-Key": stack.admin_token, "Content-Type": "application/json"}


def _create_user_and_token(stack) -> tuple[int, str]:
    email = f"calendar-991-{uuid.uuid4().hex[:8]}@vexa.ai"
    code, body = http(
        "POST",
        f"{stack.admin_api}/admin/users",
        headers=_admin_headers(stack),
        body=json.dumps({"email": email, "name": "calendar-991", "max_concurrent_bots": 1}).encode(),
    )
    assert code in (200, 201), f"create user: {code} {body}"
    user_id = body["id"]
    code, body = http(
        "POST",
        f"{stack.admin_api}/admin/users/{user_id}/tokens?scopes=bot",
        headers=_admin_headers(stack),
        body=b"",
    )
    assert code == 201, f"mint token: {code} {body}"
    return user_id, body["token"]


def test_saved_calendar_is_discovered_by_sync_for_the_same_identity(stack):
    connected_user, token = _create_user_and_token(stack)
    absent_user, absent_token = _create_user_and_token(stack)
    marker = "t303-private-feed-marker"
    # Loopback is accepted as a stored http(s) URL but refused by meeting-api's SSRF-pinned fetch.
    # That deterministic safe refusal proves discovery reached feed fetch without any outside call.
    feed_url = f"https://127.0.0.1/{marker}/calendar.ics"
    connected_auth = {"X-API-Key": token, "Content-Type": "application/json"}

    code, saved = http(
        "PUT",
        f"{stack.gateway}/user/calendar",
        headers=connected_auth,
        body=json.dumps({"ics_url": feed_url, "auto_join": False}).encode(),
    )
    assert code == 200, f"calendar save: {code} {saved}"
    assert saved["ics_url_set"] is True
    assert saved["auto_join"] is False
    assert marker not in json.dumps(saved)

    # The producer's internal response is authoritative and keyed to the saved user.
    code, discovered = http(
        "GET",
        f"{stack.admin_api}/internal/calendar-configs",
        headers={"X-Internal-Secret": stack.internal_secret},
    )
    assert code == 200, f"config discovery: {code} {discovered}"
    assert {
        "user_id": connected_user,
        "ics_url": feed_url,
        "auto_join": False,
    } in discovered["configs"]

    # Same identity: discovery succeeds and the request reaches feed fetch, so the result is a
    # precise 200 sync stamp—not the misleading 404 "no calendar feed connected" from #991.
    code, stamp = http(
        "POST",
        f"{stack.gateway}/user/calendar/sync",
        headers={"X-API-Key": token},
    )
    assert code == 200, f"saved-config sync: {code} {stamp}"
    assert stamp["last_error"] == "couldn't reach the URL (unreachable, timed out, or a blocked/internal address)"
    assert marker not in json.dumps(stamp)
    assert token not in json.dumps(stamp)

    # The sanitized stamp is durable across the POST→GET service edge.
    code, readback = http(
        "GET",
        f"{stack.gateway}/user/calendar/sync",
        headers={"X-API-Key": token},
    )
    assert code == 200
    assert readback == stamp

    # Different bound identity: the completed authoritative list proves this user absent. It must
    # neither select the connected user's feed nor turn absence into a retryable upstream fault.
    code, missing = http(
        "POST",
        f"{stack.gateway}/user/calendar/sync",
        headers={"X-API-Key": absent_token},
    )
    assert absent_user != connected_user
    assert code == 404
    assert missing == {"detail": "no calendar feed connected"}

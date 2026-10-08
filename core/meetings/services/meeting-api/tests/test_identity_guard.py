"""gateway-identity.v1 at meeting-api's door — who may name a person, one test per caller class.

meeting-api derives the owner, the bot limit (`X-User-Limits`), the workspace memberships and the
webhook from x-user-* headers. With the gateway's signing key configured (the production boot
requires it) a header is believed only when the gateway signed it, or the internal tier
(agent-api acting for a person) carries it. Bot and runtime callbacks name nobody and reach the
routes that authenticate them on their own terms.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app, identity_token

KEY = "meeting-test-signing-key"
INTERNAL = "meeting-test-internal-secret"


@pytest.fixture
def client():
    return TestClient(create_app(identity_secret=KEY, internal_secret=INTERNAL))


def _signed(sub: str, **claims) -> dict:
    return {identity_token.HEADER: identity_token.sign(KEY, {"sub": sub, **claims})}


def test_an_unsigned_identity_is_refused(client):
    r = client.get("/meetings", headers={"X-User-Id": "7"})
    assert r.status_code == 401
    assert "unsigned_identity" in r.json()["detail"]


def test_a_forged_signature_is_refused(client):
    forged = identity_token.sign("someone-elses-key", {"sub": "7"})
    assert client.get("/meetings", headers={identity_token.HEADER: forged}).status_code == 401


def test_an_unsigned_bot_limit_or_webhook_cannot_ride_along(client):
    """The cap and the webhook are identity's facts too: a plain header naming a bigger limit is
    refused like an unsigned user id, and beside a valid signature it is replaced by the claim."""
    assert client.post("/bots", headers={"X-User-Limits": "999"}, json={}).status_code == 401


# ── caller class: the gateway ──────────────────────────────────────────────────────────────────
def test_the_gateway_signed_identity_reaches_the_routes(client):
    r = client.get("/meetings", headers=_signed("7", limits=1))
    assert r.status_code == 200, r.text


def test_signed_claims_replace_whatever_plain_headers_came_with_them():
    seen = {}

    async def probe(scope, receive, send):
        seen.update({k.decode(): v.decode() for k, v in scope["headers"]})
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    import asyncio

    guard = identity_token.IdentityGuard(probe, secret=KEY)
    headers = [(k.encode(), v.encode()) for k, v in {
        **_signed("7", limits=1, webhook_url="https://hooks.example/x"),
        "x-user-limits": "999", "x-user-webhook-url": "https://attacker.example"}.items()]

    async def run():
        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(msg):
            pass
        await guard({"type": "http", "method": "POST", "path": "/bots", "headers": headers},
                    receive, send)
    asyncio.run(run())
    assert seen["x-user-id"] == "7"
    assert seen["x-user-limits"] == "1"
    assert seen["x-user-webhook-url"] == "https://hooks.example/x"
    assert identity_token.HEADER not in seen


# ── caller class: agent-api over the internal tier ──────────────────────────────────────────────
def test_the_internal_tier_may_name_the_person_it_acts_for(client):
    r = client.get("/meetings", headers={"X-User-Id": "7", "X-Internal-Secret": INTERNAL})
    assert r.status_code == 200, r.text


def test_a_wrong_internal_secret_names_nobody(client):
    r = client.get("/meetings", headers={"X-User-Id": "7", "X-Internal-Secret": "guess"})
    assert r.status_code == 401


# ── caller class: bots and the runtime (callbacks that name no person) ──────────────────────────
@pytest.mark.parametrize("path", ["/bots/internal/callback/lifecycle", "/runtime/callback"])
def test_callbacks_that_name_nobody_pass_the_door(client, path):
    r = client.post(path, json={})
    assert r.status_code != 401


def test_the_recordings_upload_keeps_its_own_bearer_check(client):
    r = client.post("/internal/recordings/upload", headers={"Authorization": "Bearer wrong"})
    assert "identity refused" not in r.text   # the route's own refusal, not the door's


def test_health_needs_nobody(client):
    assert client.get("/health").status_code == 200

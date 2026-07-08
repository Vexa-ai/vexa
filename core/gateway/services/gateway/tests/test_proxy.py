"""Focused unit tests for the REST proxy half of ``create_app`` (with injected fakes).

Proves, in isolation from the conformance contract layer, the load-bearing carve of
``main.forward_request``:
  * fail-closed auth (no key → 401; bad key → 401),
  * scope 403 (a token lacking the route scope is rejected),
  * verbatim body + status passthrough on success,
  * identity headers injected downstream; client-supplied identity headers stripped.
"""
import pytest
from fastapi.testclient import TestClient

from gateway import create_app
from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis

AUTH = {"x-api-key": VALID_KEY}


def _client(authorizer=None, downstream=None):
    downstream = downstream or FakeDownstream(status_code=200, body={"meetings": []})
    app = create_app(authorizer or FakeAuthorizer(), downstream, FakeRedis())
    return TestClient(app), downstream


def test_missing_api_key_is_401():
    client, _ = _client()
    r = client.get("/bots/status")
    assert r.status_code == 401
    assert r.json()["detail"] == "Missing API key"


def test_invalid_api_key_is_401():
    client, _ = _client()
    r = client.get("/bots/status", headers={"x-api-key": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid API key"


def test_insufficient_scope_is_403():
    """A tx-only token on a /bots route → 403 (ROUTE_SCOPES carve)."""
    client, _ = _client(authorizer=FakeAuthorizer(user={"user_id": 7, "scopes": ["tx"], "max_concurrent": 1}))
    r = client.get("/bots/status", headers=AUTH)
    assert r.status_code == 403
    assert "scope" in r.json()["detail"].lower()


def test_authed_request_passes_body_and_status_verbatim():
    """On success the downstream status + body are returned verbatim."""
    downstream = FakeDownstream(status_code=201, body={"id": 99, "platform": "google_meet"})
    client, _ = _client(downstream=downstream)
    r = client.post("/bots", headers=AUTH, json={"platform": "google_meet", "native_meeting_id": "abc"})
    assert r.status_code == 201
    assert r.json() == {"id": 99, "platform": "google_meet"}


def test_rate_limit_returns_429_past_the_per_user_cap():
    """WS-6: with a per-user limiter injected, requests up to the bucket pass (verbatim), the next is
    throttled with 429 + Retry-After — closing the unlimited-requests-on-a-valid-key DoS gap."""
    from gateway.ratelimit import PerUserRateLimiter

    now = {"t": 0.0}
    limiter = PerUserRateLimiter(capacity=2, refill_per_sec=0, clock=lambda: now["t"])
    app = create_app(FakeAuthorizer(), FakeDownstream(status_code=200, body={"meetings": []}),
                     FakeRedis(), rate_limiter=limiter)
    client = TestClient(app)

    assert client.get("/bots/status", headers=AUTH).status_code == 200
    assert client.get("/bots/status", headers=AUTH).status_code == 200
    r = client.get("/bots/status", headers=AUTH)
    assert r.status_code == 429
    assert r.json()["detail"] == "Rate limit exceeded"
    assert r.headers.get("retry-after") == "1"


def test_rate_limit_does_not_apply_when_unconfigured():
    """Default (no limiter) → no throttling: a burst of requests all pass (back-compat for harnesses)."""
    client, _ = _client()  # _client builds create_app WITHOUT a rate_limiter
    for _ in range(20):
        assert client.get("/bots/status", headers=AUTH).status_code == 200


@pytest.mark.xfail(
    reason="FINDING terminal-p20-complete-mediation: GET /agent/meeting/stream forwards WITHOUT a "
    "per-meeting ownership check (gateway app.py agent_meeting_stream → _forward_stream). Any "
    "authenticated user can stream any meeting's live transcript by passing its native id — the "
    "WS /ws path authorizes via authorize_subscribe, the SSE path does not. Fix is lane:contract "
    "(human-gated, P20/ADR-0012): authorize the requested meeting like /ws before forwarding. This "
    "executable spec flips RED (strict xfail) the moment the authz lands, forcing the marker's removal.",
    strict=True,
)
def test_meeting_stream_denies_a_meeting_the_user_does_not_own():
    """P20 complete mediation on the live-transcript SSE: a subscribe to a meeting the user does not
    own must be denied (403), not silently forwarded. auth_map is EMPTY → the user owns no meeting."""
    client, _ = _client(authorizer=FakeAuthorizer(auth_map={}))
    r = client.get(
        "/agent/meeting/stream",
        headers=AUTH,
        params={"meeting_id": "someone-elses-native", "platform": "google_meet",
                "session_uid": "someone-elses-native"},
    )
    assert r.status_code == 403


def test_identity_headers_injected_and_spoof_stripped():
    """The gateway injects x-user-id from the resolved token and STRIPS client-supplied
    identity headers (anti-spoofing, main.py:294-296)."""
    client, downstream = _client()
    r = client.get("/bots/status", headers={**AUTH, "x-user-id": "999", "x-user-scopes": "admin"})
    assert r.status_code == 200
    fwd = downstream.last["headers"]
    assert fwd["x-user-id"] == "7", "must reflect the resolved user, not the spoofed header"
    assert fwd["x-user-scopes"] == "bot,tx,browser"
    assert fwd["x-api-key"] == VALID_KEY


def test_meeting_intent_put_forwards_to_meeting_api():
    """The Meetings surface's Schedule/Cancel action PUTs the user-owned intent; the gateway must
    forward it verbatim to meeting-api's PUT /meetings/{platform}/{native}/intent. Regression: this
    route was missing, so the action 404'd at the gateway (and 405'd at the terminal proxy)."""
    client, downstream = _client()
    r = client.put("/meetings/google_meet/abc/intent", headers=AUTH, json={"intent": "scheduled"})
    assert r.status_code == 200
    assert downstream.last["method"] == "PUT"
    assert downstream.last["url"].endswith("/meetings/google_meet/abc/intent")
    assert "meeting-api" in downstream.last["url"]


def test_planned_meeting_routes_forward_to_meeting_api():
    """Planned meetings: POST /meetings (create), PATCH/DELETE /meetings/{id} (row-id-addressed
    edits) forward verbatim to meeting-api — the Meetings surface's 'Plan a meeting' flow."""
    client, downstream = _client()
    r = client.post("/meetings", headers=AUTH, json={"title": "Q3 kickoff"})
    assert r.status_code == 200  # FakeDownstream's status; the route itself declares 201
    assert downstream.last["method"] == "POST"
    assert downstream.last["url"].endswith("/meetings") and "meeting-api" in downstream.last["url"]

    client.patch("/meetings/42", headers=AUTH, json={"title": "renamed"})
    assert downstream.last["method"] == "PATCH"
    assert downstream.last["url"].endswith("/meetings/42")

    client.delete("/meetings/42", headers=AUTH)
    assert downstream.last["method"] == "DELETE"
    assert downstream.last["url"].endswith("/meetings/42")


def test_planned_meeting_routes_require_api_key():
    client, downstream = _client()
    assert client.post("/meetings", json={"title": "x"}).status_code == 401
    assert client.patch("/meetings/1", json={"title": "x"}).status_code == 401
    assert client.delete("/meetings/1").status_code == 401
    assert downstream.last is None


def test_user_calendar_routes_forward_to_admin_api():
    """Self-serve calendar-sync config: PUT/GET /user/calendar proxy to admin-api (which masks the
    secret ICS URL on read-back), same idiom as /user/webhook."""
    client, downstream = _client()
    r = client.put("/user/calendar", headers=AUTH, json={"ics_url": "https://cal.example/x.ics"})
    assert r.status_code == 200
    assert downstream.last["method"] == "PUT"
    assert downstream.last["url"] == "http://admin-api/user/calendar"
    assert downstream.last["headers"]["x-user-id"] == "7"

    client.get("/user/calendar", headers=AUTH)
    assert downstream.last["method"] == "GET"
    assert downstream.last["url"] == "http://admin-api/user/calendar"


def test_user_calendar_requires_api_key():
    client, downstream = _client()
    assert client.put("/user/calendar", json={"ics_url": "https://x"}).status_code == 401
    assert client.get("/user/calendar").status_code == 401
    assert downstream.last is None


def test_user_webhook_put_forwards_to_admin_api():
    """Self-serve webhook config: PUT /user/webhook proxies to admin-api (0.10.6 main.py:1080
    set_user_webhook_proxy) with the same auth + identity-injection idiom as every proxied route."""
    client, downstream = _client()
    r = client.put("/user/webhook", headers=AUTH,
                   json={"webhook_url": "https://hook.example/x", "webhook_secret": "shh"})
    assert r.status_code == 200
    assert downstream.last["method"] == "PUT"
    assert downstream.last["url"] == "http://admin-api/user/webhook"
    fwd = downstream.last["headers"]
    assert fwd["x-user-id"] == "7"
    assert fwd["x-api-key"] == VALID_KEY


def test_user_webhook_get_forwards_to_admin_api():
    """Read-back of the self-serve config: GET /user/webhook proxies to admin-api (which masks
    the secret) and returns the downstream body verbatim."""
    downstream = FakeDownstream(status_code=200, body={
        "webhook_url": "https://hook.example/x", "webhook_secret_set": True,
        "webhook_secret": "********", "webhook_events": None,
    })
    client, _ = _client(downstream=downstream)
    r = client.get("/user/webhook", headers=AUTH)
    assert r.status_code == 200
    assert downstream.last["method"] == "GET"
    assert downstream.last["url"] == "http://admin-api/user/webhook"
    assert r.json()["webhook_secret"] == "********"


def test_user_webhook_requires_api_key():
    """Fail-closed like every proxied route: no x-api-key → 401 before any downstream call."""
    client, downstream = _client()
    assert client.put("/user/webhook", json={"webhook_url": "https://x"}).status_code == 401
    assert client.get("/user/webhook").status_code == 401
    assert downstream.last is None


def test_downstream_target_url_matches_route_table():
    """v0.12 P2: the transcription-collector is folded INTO meeting-api (one modular monolith), so
    /transcripts + /meetings forward to the SAME meeting-api base as /bots — there is no longer a
    separate transcription-collector target."""
    client, downstream = _client()
    client.get("/transcripts/google_meet/abc", headers=AUTH)
    assert downstream.last["url"].endswith("/transcripts/google_meet/abc")
    assert "meeting-api" in downstream.last["url"]
    client.get("/meetings", headers=AUTH)
    assert "meeting-api" in downstream.last["url"]
    client.get("/bots/status", headers=AUTH)
    assert "meeting-api" in downstream.last["url"]

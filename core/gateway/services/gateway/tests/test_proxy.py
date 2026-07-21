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
from gateway.ports import AuthUnavailable
from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis

AUTH = {"x-api-key": VALID_KEY}


class UnavailableAuthorizer:
    """A ``ports.Authorizer`` whose validation hop is DOWN: ``resolve`` raises ``AuthUnavailable``
    (the #495 shape — admin-api unreachable/slow), so the gateway must answer 503, never 401."""

    async def resolve(self, api_key: str):
        raise AuthUnavailable("admin-api validate unreachable (test)")

    async def authorize_subscribe(self, api_key, meetings):
        return {"authorized": [], "errors": ["unavailable"]}


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


def test_auth_infra_failure_is_503(capsys):
    """#495 acceptance A2 + A3: when the validation hop is down, a VALID key must not be reported
    as invalid — the proxy answers 503 + Retry-After, never 401 'Invalid API key' — AND a typed,
    non-empty auth-infra log line is emitted (FM02: the old silent `except` erased that signal)."""
    client, downstream = _client(authorizer=UnavailableAuthorizer())
    r = client.get("/bots/status", headers=AUTH)
    assert r.status_code == 503
    assert r.json()["detail"] != "Invalid API key"
    assert r.headers.get("Retry-After") == "1"
    # Fail-closed: an unresolved caller is never forwarded downstream.
    assert downstream.last is None
    # A3 — the failure is named in a typed, non-empty log line (negative control: the old silent
    # `except` produced none).
    assert "auth_infra_unavailable" in capsys.readouterr().out


def test_auth_me_unavailable_is_503_not_401():
    """#495: /auth/me (the dashboard login/session check) maps auth-infra failure to 503 too."""
    app = create_app(UnavailableAuthorizer(), FakeDownstream(status_code=200, body={}), FakeRedis())
    r = TestClient(app).get("/auth/me", headers=AUTH)
    assert r.status_code == 503
    assert r.json()["detail"] != "Invalid API key"


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


def test_range_response_preserves_content_range_headers():
    """A 206 from a recording /raw byte stream must keep its Content-Range/Accept-Ranges on the way
    out. Without them the response is a malformed 206 and browsers abort <audio>/<video> playback
    (the 0.12 recording-playback "Preparing audio…" hang). Regression guard for the buffered proxy
    dropping end-to-end response headers."""
    downstream = FakeDownstream(
        status_code=206,
        content_type="audio/webm",
        extra_headers={
            "content-range": "bytes 500-999/722448",
            "accept-ranges": "bytes",
        },
    )
    client, _ = _client(downstream=downstream)
    r = client.get(
        "/recordings/369743266808/media/827161823280/raw?type=audio",
        headers={**AUTH, "range": "bytes=500-999"},
    )
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 500-999/722448"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"].startswith("audio/webm")


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


def test_native_meeting_mutate_forwards_to_meeting_api():
    """#579 C1: native-keyed PATCH/DELETE /meetings/{platform}/{native} forward verbatim to
    meeting-api (which resolves native → owned row). Negative control: on v0.12.2 there was no
    2-segment route → 404 at the gateway. The 1-segment row-id routes are unchanged (above)."""
    client, downstream = _client()
    r = client.patch("/meetings/google_meet/abc-defg-hij", headers=AUTH, json={"title": "renamed"})
    assert r.status_code == 200
    assert downstream.last["method"] == "PATCH"
    assert downstream.last["url"].endswith("/meetings/google_meet/abc-defg-hij")
    assert "meeting-api" in downstream.last["url"]

    client.delete("/meetings/google_meet/abc-defg-hij", headers=AUTH)
    assert downstream.last["method"] == "DELETE"
    assert downstream.last["url"].endswith("/meetings/google_meet/abc-defg-hij")


def test_native_meeting_mutate_passes_downstream_404_verbatim():
    """An unknown/unowned native id → meeting-api 404; the gateway returns it verbatim (not a
    gateway-minted 404 for a missing route)."""
    downstream = FakeDownstream(status_code=404, body={"detail": "Meeting not found"})
    client, _ = _client(downstream=downstream)
    r = client.patch("/meetings/google_meet/nope", headers=AUTH, json={"title": "x"})
    assert r.status_code == 404


def test_transcribe_forwards_to_meeting_api():
    """#525 C2: the sealed POST /meetings/{meeting_id}/transcribe forwards verbatim to meeting-api's
    transcribe module. Negative control: before this route the gateway 404'd a sealed path (the
    KNOWN_GAPS row this change removes)."""
    client, downstream = _client()
    r = client.post("/meetings/42/transcribe", headers=AUTH, json={"language": "en"})
    assert r.status_code == 200
    assert downstream.last["method"] == "POST"
    assert downstream.last["url"].endswith("/meetings/42/transcribe")
    assert "meeting-api" in downstream.last["url"]


def test_native_share_alias_forwards_to_meetings_share_mint():
    """#579 C3: the 0.10 POST /transcripts/{platform}/{native}/share aliases to the moved mint at
    POST /meetings/{platform}/{native}/share."""
    client, downstream = _client()
    r = client.post("/transcripts/google_meet/abc-defg-hij/share", headers=AUTH, json={})
    assert r.status_code == 200
    assert downstream.last["method"] == "POST"
    assert downstream.last["url"].endswith("/meetings/google_meet/abc-defg-hij/share")
    assert "meeting-api" in downstream.last["url"]


def test_native_chat_read_forwards_to_meeting_api():
    """#579 C3: GET /bots/{platform}/{native}/chat forwards to meeting-api's honest chat-read."""
    client, downstream = _client()
    r = client.get("/bots/google_meet/abc-defg-hij/chat", headers=AUTH)
    assert r.status_code == 200
    assert downstream.last["method"] == "GET"
    assert downstream.last["url"].endswith("/bots/google_meet/abc-defg-hij/chat")


def test_recording_download_alias_forwards_to_raw():
    """#579 C3: GET /recordings/{id}/media/{mid}/download aliases to the .../raw byte route."""
    client, downstream = _client()
    r = client.get("/recordings/5/media/9/download", headers=AUTH)
    assert r.status_code == 200
    assert downstream.last["method"] == "GET"
    assert downstream.last["url"].endswith("/recordings/5/media/9/raw")


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


def test_user_calendar_sync_routes_forward_to_meeting_api():
    """The sync FEEDBACK edges live in meeting-api (it runs the sync), unlike the config
    (identity): GET reads the last stamp, POST runs the user's sync right now."""
    client, downstream = _client()
    r = client.get("/user/calendar/sync", headers=AUTH)
    assert r.status_code == 200
    assert downstream.last["method"] == "GET"
    assert downstream.last["url"] == "http://meeting-api/user/calendar/sync"
    assert downstream.last["headers"]["x-user-id"] == "7"

    r = client.post("/user/calendar/sync", headers=AUTH)
    assert r.status_code == 200
    assert downstream.last["method"] == "POST"
    assert downstream.last["url"] == "http://meeting-api/user/calendar/sync"


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


def test_user_models_and_transcription_routes_forward_to_admin_api():
    """Self-serve model + transcription prefs (Settings → Models): PUT/GET proxy to admin-api
    (which masks api_key/token on read-back), same idiom as /user/webhook."""
    client, downstream = _client()
    for path in ("/user/models", "/user/transcription"):
        r = client.put(path, headers=AUTH, json={})
        assert r.status_code == 200
        assert downstream.last["method"] == "PUT"
        assert downstream.last["url"] == f"http://admin-api{path}"
        assert downstream.last["headers"]["x-user-id"] == "7"

        client.get(path, headers=AUTH)
        assert downstream.last["method"] == "GET"
        assert downstream.last["url"] == f"http://admin-api{path}"

        assert client.get(path).status_code == 401  # no key → the edge refuses

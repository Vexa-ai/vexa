"""ADVERSARIAL api.v1 agility + robustness probe for the meeting-api layer.

The public api.v1 surface (sealed: ``gateway/contracts/api.v1/api.schema.json``, "Vexa API
Gateway" 1.5.0) is served by the gateway and proxied verbatim to meeting-api. These tests drive
the SHIPPED meeting-api handlers (via ``meeting_api.create_app(...)`` over the in-memory fakes,
TestClient, fully offline) and hammer them with bad / hostile input to assert:

  * validation errors → 422 with a sensible shape (never 500),
  * auth fail-closed (missing/garbage ``x-user-id`` the gateway injects → 401),
  * not-found → 404 with a consistent ``{"detail": ...}`` envelope,
  * method-not-allowed → 405,
  * pagination (limit/offset: default / zero / negative / huge / non-numeric) is graceful,
  * idempotency of POST /bots (dup → 409) + DELETE /bots (stop, then 404) + GET after DELETE,
  * malformed / empty body → 422 (never 500),
  * extra/unexpected fields are accepted (additive — api.v1 MeetingCreate has no
    additionalProperties:false),
  * content-type handling,
  * the error envelope is CONSISTENT across endpoints,
  * successful bodies CONFORM to the sealed api.v1 component schemas.

BUGS / drift surfaced at this layer are left as clearly-named ``xfail(strict=True)`` tests so a
fix flips them green (and a regression that "fixes" them silently fails the suite).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher

from collector_contracts import assert_api_conforms

USER = 7
HEADERS = {"x-user-id": str(USER)}
SECRET = "test-admin-token"


# ── fixtures ─────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    """POST /bots mints a MeetingToken signed with ADMIN_TOKEN; without it the spawn raises and the
    request 500s. Every test that does not specifically probe the misconfig path wants it set."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)


def _seeded_store():
    """A transcript store with one owned, transcript-bearing meeting + one completed zoom meeting."""
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij",
        status="active", constructed_meeting_url="https://meet.google.com/abc-defg-hij",
        segments=[{
            "segment_id": "ch-0:1:a", "start": 1.0, "end": 2.5, "text": "This is Anna.",
            "language": "en", "speaker": "spk-Anna", "completed": True,
        }],
    )
    store.seed_meeting(
        user_id=USER, platform="zoom", native_meeting_id="99887766",
        status="completed", created_at="2026-06-20T10:00:00Z",
    )
    return store, mid


def _client(*, store=None, repo=None, runtime=None, publisher=None):
    return TestClient(create_app(
        transcript_store=store if store is not None else InMemoryTranscriptStore(),
        meeting_repo=repo if repo is not None else InMemoryMeetingRepo(),
        runtime=runtime if runtime is not None else FakeRuntimeClient(),
        command_publisher=publisher if publisher is not None else InMemoryCommandPublisher(),
    ))


def _assert_error_envelope(resp):
    """api.v1 error bodies are FastAPI/Starlette ``{"detail": ...}``. `detail` is a string for
    explicit HTTPExceptions and a list[ValidationError] for 422s — assert it is present + JSON."""
    body = resp.json()
    assert isinstance(body, dict), f"error body not an object: {body!r}"
    assert "detail" in body, f"error body has no 'detail' key: {body!r}"
    return body


# ══════════════════════════════════════════════════════════════════════════════════════════════════
#  AUTH — fail-closed on the gateway-injected identity (x-user-id)
# ══════════════════════════════════════════════════════════════════════════════════════════════════


def test_auth_missing_identity_is_401_everywhere():
    """No x-user-id → 401 on every authed client route (the gateway strips client identity + injects
    its own; absent == fail-closed)."""
    store, _ = _seeded_store()
    c = _client(store=store)
    cases = [
        ("GET", "/transcripts/google_meet/abc-defg-hij"),
        ("GET", "/meetings"),
        ("GET", "/bots"),
        ("GET", "/meetings/1"),
        ("GET", "/recordings"),
        ("POST", "/bots"),
        ("DELETE", "/bots/google_meet/abc-defg-hij"),
        ("POST", "/ws/authorize-subscribe"),
    ]
    for method, path in cases:
        r = c.request(method, path, json={} if method == "POST" else None)
        assert r.status_code == 401, f"{method} {path} → {r.status_code} (want 401)"
        _assert_error_envelope(r)


def test_auth_garbage_identity_is_401():
    """A non-integer x-user-id (the gateway only ever injects an int) → 401, never 500."""
    store, _ = _seeded_store()
    c = _client(store=store)
    # Non-ASCII header values are rejected by the HTTP client before transit, so they can never
    # reach the server — only ASCII garbage that Python's int() also rejects is a reachable case.
    for bad in ("abc", "7.5", "  ", "0x10", "NaN", "7,8", "7 8"):
        r = c.get("/meetings", headers={"x-user-id": bad})
        assert r.status_code == 401, f"x-user-id={bad!r} → {r.status_code} (want 401)"


def test_auth_identity_accepts_python_int_quirks():
    """ROBUSTNESS NOTE: ``_resolve_user_id`` uses bare ``int(x_user_id)``, which Python parses
    leniently — a leading '+', surrounding whitespace, or '_' digit-separators all parse. So
    x-user-id='+7' / ' 7 ' / '0_7' authenticate as user 7. Harmless (the gateway controls this
    header and only ever injects a clean int), but documented so the leniency is intentional, not a
    surprise. Tightening to a strict ``str.isdigit()`` check would close it."""
    store, _ = _seeded_store()
    c = _client(store=store)
    for quirky in ("+7", " 7 ", "0_7", "007"):
        r = c.get("/meetings", headers={"x-user-id": quirky})
        assert r.status_code == 200, f"x-user-id={quirky!r} → {r.status_code}"
        # all resolve to user 7 → see the seeded meetings
        assert len(r.json()["meetings"]) == 2


def test_auth_negative_and_zero_identity_do_not_500():
    """Edge user ids (0, negative, huge) parse as ints → 200 with an empty owned list (no 500)."""
    store, _ = _seeded_store()
    c = _client(store=store)
    for uid in ("0", "-1", "999999999999999999"):
        r = c.get("/meetings", headers={"x-user-id": uid})
        assert r.status_code == 200, f"x-user-id={uid} → {r.status_code}"
        assert r.json()["meetings"] == []


# ══════════════════════════════════════════════════════════════════════════════════════════════════
#  NOT-FOUND — 404 with a consistent {"detail": ...} envelope, owner-scoped (no leak)
# ══════════════════════════════════════════════════════════════════════════════════════════════════


def test_not_found_transcript_404_consistent_envelope():
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.get("/transcripts/google_meet/does-not-exist", headers=HEADERS)
    assert r.status_code == 404
    body = _assert_error_envelope(r)
    assert isinstance(body["detail"], str)


def test_not_found_meeting_by_id_404_consistent_envelope():
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.get("/meetings/123456", headers=HEADERS)
    assert r.status_code == 404
    body = _assert_error_envelope(r)
    assert body["detail"] == "Meeting not found"


def test_not_found_recording_master_404():
    c = _client()
    r = c.get("/recordings/424242/master", headers=HEADERS)
    assert r.status_code == 404
    _assert_error_envelope(r)


def test_not_found_is_owner_scoped_not_leaked():
    """A non-owner reading another user's meeting gets 404 (not 403) — existence is not leaked."""
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.get("/transcripts/google_meet/abc-defg-hij", headers={"x-user-id": "999"})
    assert r.status_code == 404
    r2 = c.get("/meetings/1", headers={"x-user-id": "999"})
    assert r2.status_code == 404


def test_404_envelope_matches_across_handlers():
    """CONSISTENCY: the two distinct 404 code paths (HTTPException raise vs JSONResponse return)
    both emit a top-level string ``detail`` — same envelope, so a client parses one way."""
    store, _ = _seeded_store()
    c = _client(store=store)
    a = c.get("/transcripts/google_meet/nope", headers=HEADERS).json()      # HTTPException path
    b = c.get("/meetings/999999", headers=HEADERS).json()                   # JSONResponse path
    assert set(a) == {"detail"} and isinstance(a["detail"], str)
    assert set(b) == {"detail"} and isinstance(b["detail"], str)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
#  METHOD-NOT-ALLOWED — 405 on a wrong verb against a real path
# ══════════════════════════════════════════════════════════════════════════════════════════════════


def test_method_not_allowed_405():
    store, _ = _seeded_store()
    c = _client(store=store)
    # /meetings is GET-only at this layer; /transcripts/... is GET-only.
    assert c.post("/meetings", headers=HEADERS, json={}).status_code == 405
    assert c.delete("/meetings", headers=HEADERS).status_code == 405
    assert c.put("/transcripts/google_meet/abc-defg-hij", headers=HEADERS, json={}).status_code == 405
    # /bots accepts GET + POST but not PUT/PATCH.
    assert c.patch("/bots", headers=HEADERS, json={}).status_code == 405


# ══════════════════════════════════════════════════════════════════════════════════════════════════
#  PAGINATION — limit/offset edge cases on the list endpoints are GRACEFUL (never 500)
# ══════════════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("path", ["/meetings", "/bots"])
def test_pagination_default_returns_all(path):
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.get(path, headers=HEADERS)
    assert r.status_code == 200
    assert len(r.json()["meetings"]) == 2


@pytest.mark.parametrize("path", ["/meetings", "/bots"])
@pytest.mark.parametrize("limit", [0, -1, 9999999, 101])
def test_pagination_out_of_range_limit_is_422_not_500(path, limit):
    """limit is constrained ge=1 le=100 → 0 / negative / >100 → 422 (validation), never 500."""
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.get(path, headers=HEADERS, params={"limit": limit})
    assert r.status_code == 422, f"{path}?limit={limit} → {r.status_code}"
    _assert_error_envelope(r)


@pytest.mark.parametrize("path", ["/meetings", "/bots"])
def test_pagination_negative_offset_is_422(path):
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.get(path, headers=HEADERS, params={"offset": -5})
    assert r.status_code == 422
    _assert_error_envelope(r)


@pytest.mark.parametrize("path", ["/meetings", "/bots"])
@pytest.mark.parametrize("param,value", [("limit", "abc"), ("offset", "xyz"), ("limit", "1.5")])
def test_pagination_non_numeric_is_422_not_500(path, param, value):
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.get(path, headers=HEADERS, params={param: value})
    assert r.status_code == 422, f"{path}?{param}={value} → {r.status_code}"
    _assert_error_envelope(r)


@pytest.mark.parametrize("path", ["/meetings", "/bots"])
def test_pagination_huge_offset_is_empty_not_500(path):
    """An offset past the end → empty page, 200 (the slice is graceful)."""
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.get(path, headers=HEADERS, params={"offset": 100000})
    assert r.status_code == 200
    assert r.json()["meetings"] == []


def test_pagination_limit_one_paginates():
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.get("/meetings", headers=HEADERS, params={"limit": 1})
    assert r.status_code == 200
    assert len(r.json()["meetings"]) == 1


# ══════════════════════════════════════════════════════════════════════════════════════════════════
#  POST /bots — body validation (missing / wrong-type / malformed / empty / extra fields)
# ══════════════════════════════════════════════════════════════════════════════════════════════════


def test_post_bots_missing_required_fields_is_422():
    c = _client()
    # No platform, no native_meeting_id, no meeting_url → 422.
    r = c.post("/bots", headers=HEADERS, json={})
    assert r.status_code == 422
    _assert_error_envelope(r)


def test_post_bots_platform_only_is_422():
    c = _client()
    r = c.post("/bots", headers=HEADERS, json={"platform": "google_meet"})
    assert r.status_code == 422


def test_post_bots_malformed_json_is_422_not_500():
    c = _client()
    r = c.post(
        "/bots", headers={**HEADERS, "content-type": "application/json"},
        content=b"{not valid json,,,",
    )
    assert r.status_code == 422, r.text
    _assert_error_envelope(r)


def test_post_bots_empty_body_is_422_not_500():
    c = _client()
    r = c.post("/bots", headers={**HEADERS, "content-type": "application/json"}, content=b"")
    assert r.status_code == 422, r.text


def test_post_bots_non_object_json_body_is_422():
    """A JSON array / scalar instead of an object → 422 (the handler asserts dict)."""
    c = _client()
    for payload in ("[1,2,3]", '"a string"', "42", "true", "null"):
        r = c.post("/bots", headers={**HEADERS, "content-type": "application/json"}, content=payload)
        assert r.status_code == 422, f"body={payload!r} → {r.status_code}"


def test_post_bots_extra_fields_are_accepted_additive():
    """api.v1 MeetingCreate has NO additionalProperties:false → unknown fields ride harmlessly.
    The meeting-api layer does not reject them (forward-compatible)."""
    c = _client()
    r = c.post("/bots", headers=HEADERS, json={
        "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
        "totally_unknown_field": "ignored", "another": {"nested": True},
    })
    assert r.status_code == 201, r.text


def test_post_bots_success_conforms_to_meeting_response():
    c = _client()
    r = c.post("/bots", headers=HEADERS,
               json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    assert r.status_code == 201, r.text
    assert_api_conforms("MeetingResponse", r.json())


def test_post_bots_meeting_url_only_is_accepted():
    """native_meeting_id OR meeting_url satisfies the required check."""
    c = _client()
    r = c.post("/bots", headers=HEADERS,
               json={"platform": "google_meet", "meeting_url": "https://meet.google.com/abc-defg-hij"})
    assert r.status_code == 201, r.text


# ── SCHEMA-DRIFT probes: meeting-api does NOT enforce the sealed MeetingCreate field constraints ──


# FIXED (A1): create_bot rejects an unsupported platform without a constructible meeting_url → 422
# (router.py), instead of a deep 500 in the invocation builder. Standing regression guard.
def test_post_bots_invalid_platform_should_be_422():
    c = _client()
    r = c.post("/bots", headers=HEADERS,
               json={"platform": "discord", "native_meeting_id": "x"})
    assert r.status_code == 422, (
        f"invalid platform accepted with {r.status_code}: {r.text}"
    )


# FIXED (A2): _resolve_recording_enabled parses booleans/strings + 422s other types — no silent
# bool() coercion that flipped "false"→True. Standing regression guard.
def test_post_bots_string_bool_should_not_silently_coerce():
    repo = InMemoryMeetingRepo()
    c = _client(repo=repo)
    r = c.post("/bots", headers=HEADERS, json={
        "platform": "google_meet", "native_meeting_id": "coerce-test",
        "recording_enabled": "false",
    })
    # Either reject the wrong type (422) OR honour the string's intent — but NOT flip "false"→True.
    if r.status_code == 201:
        # Inspect what was persisted: the meeting's data.recording_enabled.
        m = next(v for v in repo._meetings.values() if v["native_meeting_id"] == "coerce-test")
        assert m["data"].get("recording_enabled") is False, (
            "recording_enabled='false' silently coerced to True"
        )
    else:
        assert r.status_code == 422


# FIXED (CC3): transcribe_enabled used a bare bool() so the string "false" became True — the unfixed
# twin of A2. _resolve_transcribe_enabled now parses/validates it. Standing regression guard.
def test_post_bots_transcribe_enabled_string_false_is_false():
    repo = InMemoryMeetingRepo()
    c = _client(repo=repo)
    r = c.post("/bots", headers=HEADERS, json={
        "platform": "google_meet", "native_meeting_id": "transcribe-coerce",
        "transcribe_enabled": "false",
    })
    # Honour the string's intent OR 422 — but NEVER flip "false"→True (which silently enables transcription).
    if r.status_code == 201:
        m = next(v for v in repo._meetings.values() if v["native_meeting_id"] == "transcribe-coerce")
        assert m["data"].get("transcribe_enabled") is False, (
            "transcribe_enabled='false' silently coerced to True"
        )
    else:
        assert r.status_code == 422


# FIXED (CC4): a transcription bot needs STT. transcribe_enabled (default true) with STT UNCONFIGURED now
# fails loud (503) instead of silently launching a bot that joins+captures but can never transcribe (P18).
# (The autouse _stt_configured fixture provides STT for the default suite; here we clear it.)
def test_post_bots_transcribe_without_stt_fails_loud(monkeypatch):
    monkeypatch.delenv("TRANSCRIPTION_SERVICE_URL", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_SERVICE_TOKEN", raising=False)
    c = _client(repo=InMemoryMeetingRepo())
    r = c.post("/bots", headers=HEADERS, json={"platform": "google_meet", "native_meeting_id": "no-stt"})
    assert r.status_code == 503, f"transcribe (default) + no STT should 503, got {r.status_code}"


def test_post_bots_no_transcription_spawns_without_stt(monkeypatch):
    """A no-transcription bot (transcribe_enabled=false) must spawn even with STT unconfigured —
    recording-only is a legitimate deployment; the 503 fires ONLY when transcription is requested."""
    monkeypatch.delenv("TRANSCRIPTION_SERVICE_URL", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_SERVICE_TOKEN", raising=False)
    c = _client(repo=InMemoryMeetingRepo())
    r = c.post("/bots", headers=HEADERS, json={
        "platform": "google_meet", "native_meeting_id": "rec-only", "transcribe_enabled": False,
    })
    assert r.status_code == 201, f"recording-only spawn should 201 without STT, got {r.status_code}"


# ══════════════════════════════════════════════════════════════════════════════════════════════════
#  POST /bots — server misconfiguration robustness (no ADMIN_TOKEN → MeetingToken mint fails)
# ══════════════════════════════════════════════════════════════════════════════════════════════════


def test_post_bots_without_admin_token_500s_on_valid_request(monkeypatch):
    """ROBUSTNESS NOTE: with ADMIN_TOKEN unset the MeetingToken mint raises ValueError mid-flow →
    an UNHANDLED 500 on an otherwise-valid request. This is why the PRODUCTION boot now fails fast
    (A4): ``__main__._require_config`` refuses to build the app at all when ADMIN_TOKEN is unset, so a
    real deploy never reaches this state (see test_startup_requires_admin_token in
    test_robustness_seam). This test still pins the per-request blast radius for the create_app path
    (which is intentionally NOT gated, so the offline harness can stand the app up without secrets)."""
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    c = _client()
    with pytest.raises(Exception):
        # TestClient re-raises server exceptions by default → proves it is an unhandled 500, not a
        # mapped 4xx/5xx error body. (If the handler ever maps this to a clean 503, update this test.)
        c.post("/bots", headers=HEADERS,
               json={"platform": "google_meet", "native_meeting_id": "x"})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
#  IDEMPOTENCY — POST /bots twice, DELETE /bots twice, GET after DELETE
# ══════════════════════════════════════════════════════════════════════════════════════════════════


def test_post_bots_twice_same_meeting_is_409():
    """A second POST for the SAME (platform, native_id) while the first is active → 409 (dedup),
    not a duplicate spawn. NON-idempotent-by-design but DETERMINISTIC."""
    repo = InMemoryMeetingRepo()
    c = _client(repo=repo)
    body = {"platform": "google_meet", "native_meeting_id": "dup-1"}
    r1 = c.post("/bots", headers=HEADERS, json=body)
    assert r1.status_code == 201, r1.text
    r2 = c.post("/bots", headers=HEADERS, json=body)
    assert r2.status_code == 409, r2.text
    _assert_error_envelope(r2)
    # Exactly ONE meeting row created (no duplicate spawn on the retry).
    assert len([m for m in repo._meetings.values() if m["native_meeting_id"] == "dup-1"]) == 1


def test_delete_bots_then_delete_again_404():
    """DELETE marks the meeting `stopping`; a second DELETE finds no ACTIVE meeting → 404.
    Idempotency observation: the stop trigger is one-shot, the redelivery is a clean 404 (no 500,
    no double-publish)."""
    repo, pub = InMemoryMeetingRepo(), InMemoryCommandPublisher()
    c = _client(repo=repo, publisher=pub)
    # seed an active meeting via POST /bots
    assert c.post("/bots", headers=HEADERS,
                  json={"platform": "google_meet", "native_meeting_id": "stop-1"}).status_code == 201
    r1 = c.delete("/bots/google_meet/stop-1", headers=HEADERS)
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "stopping"
    n_published = len(pub.published)
    r2 = c.delete("/bots/google_meet/stop-1", headers=HEADERS)
    assert r2.status_code == 404, r2.text
    _assert_error_envelope(r2)
    # The second DELETE did NOT re-publish a leave command (no double-stop side-effect).
    assert len(pub.published) == n_published


def test_delete_unknown_bot_is_404():
    c = _client()
    r = c.delete("/bots/google_meet/never-existed", headers=HEADERS)
    assert r.status_code == 404
    _assert_error_envelope(r)


def test_get_meeting_after_post_reflects_it():
    """GET /meetings reflects a just-POSTed bot (read-after-write through the shared repo? — NO: the
    collector reads its OWN transcript_store, not the bot_spawn repo). This documents that GET
    /meetings and POST /bots are backed by SEPARATE stores in the fake wiring, so a POSTed bot is
    NOT visible via GET /meetings here. In prod both hit the same Postgres; the fakes are isolated."""
    repo = InMemoryMeetingRepo()
    store = InMemoryTranscriptStore()
    c = _client(store=store, repo=repo)
    assert c.post("/bots", headers=HEADERS,
                  json={"platform": "google_meet", "native_meeting_id": "rw-1"}).status_code == 201
    # GET /meetings is collector-backed (transcript_store), which the POST did not touch.
    r = c.get("/meetings", headers=HEADERS)
    assert r.status_code == 200
    # Documented seam: separate stores in the fake → not visible. (Prod: same DB → would be visible.)
    assert all(m["native_meeting_id"] != "rw-1" for m in r.json()["meetings"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
#  DELETE /bots — path-param platform handling (sealed schema types it as the Platform enum)
# ══════════════════════════════════════════════════════════════════════════════════════════════════


def test_delete_bots_invalid_platform_should_be_422():
    """A3 FIXED: DELETE /bots/{platform}/{native_meeting_id} validates `platform` against the
    sealed api.v1 Platform enum BEFORE find_active. An unsupported platform (e.g. 'discord') is a
    422 Validation Error — matching the contract — instead of leaking a 404 from a find_active miss."""
    c = _client()
    r = c.delete("/bots/discord/some-id", headers=HEADERS)
    assert r.status_code == 422, f"invalid platform → {r.status_code} (contract says 422)"
    _assert_error_envelope(r)


@pytest.mark.parametrize("platform", ["google_meet", "zoom", "teams", "browser_session"])
def test_delete_bots_valid_platform_nonexistent_is_404(platform):
    """Idempotent-delete preserved: a VALID platform with no active meeting still → 404
    (the 422 guard only rejects platforms outside the sealed enum, not unknown meetings)."""
    c = _client()
    r = c.delete(f"/bots/{platform}/never-existed", headers=HEADERS)
    assert r.status_code == 404, f"valid platform, no meeting → {r.status_code} (want 404)"


# ══════════════════════════════════════════════════════════════════════════════════════════════════
#  CONTENT-TYPE handling — POST endpoints tolerate / reject non-JSON gracefully
# ══════════════════════════════════════════════════════════════════════════════════════════════════


def test_post_bots_wrong_content_type_is_422_not_500():
    """A form-encoded body (wrong content-type) → the handler's request.json() fails → 422, never
    500."""
    c = _client()
    r = c.post(
        "/bots", headers={**HEADERS, "content-type": "application/x-www-form-urlencoded"},
        content=b"platform=google_meet&native_meeting_id=x",
    )
    assert r.status_code == 422, r.text
    _assert_error_envelope(r)


def test_post_bots_missing_content_type_with_json_bytes():
    """No content-type but a valid JSON byte body → request.json() still parses it → 201 (tolerant)."""
    c = _client()
    r = c.post("/bots", headers=HEADERS, content=b'{"platform":"google_meet","native_meeting_id":"ct-1"}')
    assert r.status_code in (201, 422), r.text  # tolerant parse OR clean reject — never 500
    assert r.status_code != 500


# ══════════════════════════════════════════════════════════════════════════════════════════════════
#  /ws/authorize-subscribe — internal authorizer hop, but still hardened against bad bodies
# ══════════════════════════════════════════════════════════════════════════════════════════════════


def test_ws_authorize_malformed_body_is_422_not_500():
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.post("/ws/authorize-subscribe", headers={**HEADERS, "content-type": "application/json"},
               content=b"{bad json")
    assert r.status_code == 422, r.text


def test_ws_authorize_missing_meetings_key_is_422():
    store, _ = _seeded_store()
    c = _client(store=store)
    for payload in ({}, {"meetings": []}, {"meetings": "notalist"}, {"meetings": None}):
        r = c.post("/ws/authorize-subscribe", headers=HEADERS, json=payload)
        assert r.status_code == 422, f"{payload} → {r.status_code}"


def test_ws_authorize_non_object_refs_become_errors_not_500():
    """Each ref element is validated per-item; a non-object element → an entry in `errors`, 200."""
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.post("/ws/authorize-subscribe", headers=HEADERS,
               json={"meetings": ["not-an-object", 42, None]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["authorized"] == []
    assert len(body["errors"]) == 3


def test_ws_authorize_overlong_native_id_is_error_not_500():
    """The handler bounds native_meeting_id length (>255) → a per-item error, not a crash."""
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.post("/ws/authorize-subscribe", headers=HEADERS,
               json={"meetings": [{"platform": "google_meet", "native_meeting_id": "x" * 300}]})
    assert r.status_code == 200
    assert len(r.json()["errors"]) == 1


# ══════════════════════════════════════════════════════════════════════════════════════════════════
#  PATH-PARAM type coercion — /meetings/{meeting_id} is typed int → non-numeric → 422
# ══════════════════════════════════════════════════════════════════════════════════════════════════


def test_meeting_by_id_non_numeric_path_is_422():
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.get("/meetings/not-a-number", headers=HEADERS)
    assert r.status_code == 422, r.text
    _assert_error_envelope(r)


def test_recording_master_non_numeric_path_is_422():
    c = _client()
    r = c.get("/recordings/not-a-number/master", headers=HEADERS)
    assert r.status_code == 422
    _assert_error_envelope(r)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
#  SUCCESS-SHAPE CONFORMANCE — happy-path bodies match the sealed api.v1 component schemas
# ══════════════════════════════════════════════════════════════════════════════════════════════════


def test_get_transcript_conforms_to_sealed_shape():
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.get("/transcripts/google_meet/abc-defg-hij", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert_api_conforms("TranscriptionResponse", r.json())


def test_get_meetings_conforms_to_sealed_shape():
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.get("/meetings", headers=HEADERS)
    assert r.status_code == 200
    assert_api_conforms("MeetingListResponse", r.json())


def test_get_meeting_by_id_conforms_to_meeting_response():
    """The single-meeting body the dashboard meeting-detail page consumes must conform to the sealed
    MeetingResponse."""
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.get("/meetings/1", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert_api_conforms("MeetingResponse", r.json())


def test_get_bots_list_meetings_conform_to_meeting_response():
    """GET /bots returns {meetings:[...], has_more}; each meeting must conform to MeetingResponse
    (the dashboard's primary list source)."""
    store, _ = _seeded_store()
    c = _client(store=store)
    r = c.get("/bots", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "has_more" in body
    for m in body["meetings"]:
        assert_api_conforms("MeetingResponse", m)


def test_get_recordings_list_shape():
    """GET /recordings returns {recordings:[...]} — the envelope key the dashboard reads."""
    c = _client()
    r = c.get("/recordings", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("recordings"), list)


# ── ENVELOPE CONSISTENCY across the list endpoints ──────────────────────────────────────────────


def test_list_envelopes_consistent_meetings_key():
    """CONSISTENCY: every meeting-list endpoint nests under the SAME `meetings` key (a client uses
    one accessor). GET /meetings → {meetings}; GET /bots → {meetings, has_more}."""
    store, _ = _seeded_store()
    c = _client(store=store)
    assert "meetings" in c.get("/meetings", headers=HEADERS).json()
    assert "meetings" in c.get("/bots", headers=HEADERS).json()
    assert "recordings" in c.get("/recordings", headers=HEADERS).json()

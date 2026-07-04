"""V010-compat · REST — a 0.10-style client's real HTTP flows against the running 0.12 stack.

Ground truth for "what a 0.10 client expects" is the Vexa-ai/vexa `release/0.10.7` tree:
`services/api-gateway/main.py` (route table + auth), `services/meeting-api/meeting_api/
{meetings,schemas}.py` (response envelopes), `services/admin-api/app/main.py` (admin surface).
Each test drives the 0.12 stack exactly as a 0.10 client would (X-API-Key via the gateway,
X-Admin-API-Key for admin) and asserts the 0.10 contract. Where 0.12 actually diverges the
test is a STRICT xfail with a `V010-BREAK:` reason — so the suite is green only while every
recorded break stays broken and every compatible surface stays compatible.

EXCLUDED (owner ruling): interactive-bots endpoints (chat/screen/avatar — API refactor
planned) and the documented not-wired routes (PUT /bots/.../config, POST /bots/.../speak,
transcript share links).

Bot flows run the MOCK bot (`bot_name="mock:<scenario>"` → BROWSER_IMAGE=mock-bot:dev): the
real meeting-api → runtime → container path, deterministic completion, no browser/STT.
"""
from __future__ import annotations

import json
import os
import time
import uuid

import pytest

# This module only loads under V010_COMPAT=1 (the parent conftest ignores compat/ otherwise), so
# the sentinel below never leaks into a default run. A faithful 0.10 client does NOT send
# `transcribe_enabled` (its default was ON) — 0.12's CC4 fail-loud guard would 503 such a spawn on
# an STT-less test stack. Point the STT env at an inert sentinel BEFORE the session `stack` fixture
# reads os.environ, so the plain 0.10 spawn body passes CC4; the MOCK bot fakes the pipeline and
# never dials STT.
os.environ.setdefault("TRANSCRIPTION_SERVICE_URL", "http://stt-compat-sentinel.invalid")
os.environ.setdefault("TRANSCRIPTION_SERVICE_TOKEN", "v010-compat-sentinel")

from conftest import http, post_json, requires_docker

pytestmark = requires_docker

# Shared across the ordered flow (one session stack, `-x`): the 0.10 client's identity.
S: dict = {}

RUNNING_STATUSES = {"requested", "joining", "awaiting_admission", "active", "stopping"}
TERMINAL = {"completed", "failed"}


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────

def _admin_headers(stack):
    return {"X-Admin-API-Key": stack.admin_token, "Content-Type": "application/json"}


def _poll(fn, *, timeout=90, poll=2.0):
    """Poll fn() until truthy or timeout; returns the last value (never sleep-and-hope)."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(poll)
    return last


def _gw_meeting(stack, native_id):
    """The client-visible meeting row via the gateway (the way a 0.10 client polls status)."""
    code, body = http("GET", f"{stack.gateway}/meetings", headers={"x-api-key": S["api_key"]})
    if code != 200 or not isinstance(body, dict):
        return None
    return next((m for m in body.get("meetings", [])
                 if m.get("native_meeting_id") == native_id), None)


def _wait_status(stack, native_id, statuses, *, timeout=120):
    return _poll(lambda: (lambda m: m if m and m.get("status") in statuses else None)(
        _gw_meeting(stack, native_id)), timeout=timeout)


# ── 1 · admin user + token lifecycle (the flows a 0.10 admin script runs) ─────────────────────────

def test_01_admin_user_token_lifecycle(stack):
    """0.10 admin flow: find-or-create user → mint scoped token → the token WORKS on the
    gateway → revoke (204) → the token is rejected (401). Asserts the semantic fields the
    0.10 flow consumes (user.id / token.token / token.id / scopes); full 0.10 response-model
    fidelity is test_02 (a separate, empirically-discovered divergence)."""
    email = f"v010-{uuid.uuid4().hex[:8]}@vexa.ai"
    # 0.10: POST /admin/users → 201 (created); repeat → 200 (found). Same semantics expected.
    code, user = http("POST", f"{stack.admin_api}/admin/users", headers=_admin_headers(stack),
                      body=json.dumps({"email": email, "name": "v010", "max_concurrent_bots": 5}).encode())
    assert code == 201, f"create user → {code} {user}"
    assert isinstance(user, dict) and isinstance(user.get("id"), int), f"user envelope: {user}"
    assert user.get("email") == email and user.get("max_concurrent_bots") == 5
    code2, again = http("POST", f"{stack.admin_api}/admin/users", headers=_admin_headers(stack),
                        body=json.dumps({"email": email}).encode())
    assert code2 == 200 and again.get("id") == user["id"], f"find-or-create → {code2} {again}"
    S["user_id"] = user["id"]
    S["user_created"] = user  # kept for the fidelity check (test_02)

    # 0.10 token mint: POST /admin/users/{id}/tokens?scopes=bot,tx → 201 TokenResponse.
    code, tok = http("POST", f"{stack.admin_api}/admin/users/{user['id']}/tokens?scopes=bot,tx",
                     headers=_admin_headers(stack), body=b"")
    assert code == 201, f"mint token → {code} {tok}"
    assert isinstance(tok.get("token"), str) and tok["token"], f"token envelope: {tok}"
    assert tok.get("user_id") == user["id"] and set(tok.get("scopes", [])) == {"bot", "tx"}
    assert isinstance(tok.get("id"), int)
    S["api_key"] = tok["token"]
    S["token_minted"] = tok  # kept for the fidelity check (test_02)

    # The minted key authenticates a 0.10 client route through the gateway.
    code, body = http("GET", f"{stack.gateway}/meetings", headers={"x-api-key": tok["token"]})
    assert code == 200 and "meetings" in body, f"minted key on gateway → {code} {body}"

    # Revoke a second token → 204; the revoked key is then rejected 401 at the gateway.
    code, tok2 = http("POST", f"{stack.admin_api}/admin/users/{user['id']}/tokens?scopes=tx",
                      headers=_admin_headers(stack), body=b"")
    assert code == 201
    code, _ = http("DELETE", f"{stack.admin_api}/admin/tokens/{tok2['id']}", headers=_admin_headers(stack))
    assert code == 204, f"revoke token → {code}"
    code, body = http("GET", f"{stack.gateway}/meetings", headers={"x-api-key": tok2["token"]})
    assert code == 401, f"revoked key should be 401, got {code} {body}"
    print(f"\n[compat/admin] user {user['id']} · mint→works · revoke→401 (0.10 admin flow intact)")


@pytest.mark.xfail(strict=True, reason=(
    "V010-BREAK: admin user/token response envelopes lost 0.10-required fields — POST /admin/users "
    "returns {id,email,name,max_concurrent_bots} (0.10 UserResponse required created_at, also carried "
    "image_url+data); POST /admin/users/{id}/tokens returns {id,token,user_id,scopes} (0.10 "
    "TokenResponse required created_at, also carried name/expires_at/last_used_at). "
    "NOTE: +1 vs the original run's recorded five."))
def test_02_admin_response_envelopes_v010_fields(stack):
    """0.10 response-model fidelity: `UserResponse` REQUIRED `created_at` (plus image_url/data);
    `TokenResponse` REQUIRED `created_at` (plus name/expires_at/last_used_at). A 0.10 SDK
    parsing these envelopes with the 0.10 pydantic models fails when they are absent.
    NOTE: this break is IN ADDITION to the five recorded in the original run — flagged as
    V010-BREAK(+1) in the PR inventory."""
    user, tok = S["user_created"], S["token_minted"]
    missing_user = [k for k in ("created_at",) if k not in user]
    missing_tok = [k for k in ("created_at",) if k not in tok]
    assert not missing_user and not missing_tok, (
        f"V010-BREAK: admin envelopes lost 0.10-required fields — "
        f"user missing {missing_user} (0.10 UserResponse also carried image_url+data), "
        f"token missing {missing_tok} (0.10 TokenResponse also carried name/expires_at/"
        f"last_used_at). got user={sorted(user)} token={sorted(tok)}"
    )


@pytest.mark.xfail(strict=True, reason=(
    "V010-BREAK: the admin API is no longer reachable via the gateway path a 0.10 client used — "
    "0.10's gateway forwarded /admin/{path} (X-Admin-API-Key) to admin-api (main.py:1133); the 0.12 "
    "gateway has no /admin forwarder → 404. Migration: call admin-api directly on its own port "
    "(test_01 proves that works)."))
def test_03_admin_via_gateway_v010_path(stack):
    """0.10 clients reached the admin API THROUGH the gateway: `main.py:1133` forwarded
    `/admin/{path}` (X-Admin-API-Key) to admin-api. The same call against the 0.12 gateway."""
    email = f"v010-gw-{uuid.uuid4().hex[:8]}@vexa.ai"
    code, body = http("POST", f"{stack.gateway}/admin/users", headers=_admin_headers(stack),
                      body=json.dumps({"email": email, "max_concurrent_bots": 1}).encode())
    assert code in (200, 201), (
        f"V010-BREAK: admin API no longer reachable via the gateway path a 0.10 client used "
        f"(POST {{gateway}}/admin/users → {code} {body}; 0.10 forwarded /admin/* to admin-api). "
        f"Migration: call admin-api directly (its own port), as test_01 proves works."
    )


# ── 2 · bot request (POST /bots, the 0.10 body — no transcribe_enabled field) ─────────────────────

def test_04_bot_request_v010_flow(stack):
    """POST /bots through the gateway with the plain 0.10 body {platform, native_meeting_id,
    bot_name} → 201 + the 0.10 `MeetingResponse` fields the client reads. `mock:immediate-stop`
    holds the meeting live (no self-end) so the status/stop tests below drive a running bot."""
    native_id = f"v010-{uuid.uuid4().hex[:8]}"
    code, body = post_json(
        f"{stack.gateway}/bots",
        {"platform": "google_meet", "native_meeting_id": native_id, "bot_name": "mock:immediate-stop"},
        headers={"x-api-key": S["api_key"]},
    )
    assert code == 201, f"POST /bots (0.10 body) → {code} {body}"
    # The 0.10 MeetingResponse keys a client dereferences (schemas.py:890).
    for key in ("id", "user_id", "platform", "native_meeting_id", "status", "created_at", "updated_at"):
        assert key in body, f"POST /bots response missing 0.10 MeetingResponse key {key!r}: {sorted(body)}"
    assert body["native_meeting_id"] == native_id and body["status"] in RUNNING_STATUSES
    S["native_id"] = native_id
    S["meeting_id"] = body["id"]

    # The bot actually runs: the meeting advances to a live status (the mock joins immediately).
    m = _wait_status(stack, native_id, {"active", "joining", "awaiting_admission"}, timeout=90)
    assert m, f"meeting {native_id} never went live via GET /meetings"
    print(f"\n[compat/bots] POST /bots (plain 0.10 body) → 201 · meeting {body['id']} live ({m['status']})")


@pytest.mark.xfail(strict=True, reason=(
    "V010-BREAK: the GET /bots/status response envelope changed — 0.10 returned BotStatusResponse "
    "{'running_bots': [BotStatus, ...]} (container_id/container_name/normalized_status entries); "
    "0.12 returns {'running': [meeting rows], 'count': N}."))
def test_05_bots_status_v010_envelope(stack):
    """0.10 `GET /bots/status` → `BotStatusResponse = {"running_bots": [...]}` (schemas.py:1238),
    each entry carrying container/platform/native_meeting_id fields."""
    code, body = http("GET", f"{stack.gateway}/bots/status", headers={"x-api-key": S["api_key"]})
    assert code == 200, f"GET /bots/status → {code} {body}"
    assert isinstance(body, dict) and "running_bots" in body, (
        f"V010-BREAK: bots/status response envelope changed — 0.10 clients read "
        f"body['running_bots'] (BotStatusResponse); 0.12 returns {sorted(body)} "
        f"(running+count). got: {body}"
    )


@pytest.mark.xfail(strict=True, reason=(
    "V010-BREAK: DELETE /bots/{platform}/{native_id} changed status code + message shape — 0.10 "
    "returned 202 Accepted + {'message': 'Stop request accepted and is being processed.'}; 0.12 "
    "returns 200 + {'status': 'stopping', 'meeting_id', 'native_meeting_id'}. The stop itself still "
    "works (test_07 proves the meeting reaches terminal)."))
def test_06_stop_bot_v010_contract(stack):
    """0.10 `DELETE /bots/{platform}/{native_id}` → HTTP 202 + `{"message": ...}`
    (meetings.py:1808 status_code=202, returns the message dict)."""
    code, body = http("DELETE", f"{stack.gateway}/bots/google_meet/{S['native_id']}",
                      headers={"x-api-key": S["api_key"]})
    # Whatever the envelope, the stop must have been ACCEPTED (2xx) — a 4xx/5xx would be a
    # hard functional regression, not a shape drift.
    assert 200 <= code < 300, f"DELETE /bots not accepted: {code} {body}"
    S["stop_accepted"] = True
    assert code == 202 and isinstance(body, dict) and "message" in body, (
        f"V010-BREAK: DELETE /bots returned {code} {body} — 0.10 returned 202 + "
        f"{{'message': 'Stop request accepted and is being processed.'}}; 0.12 returns "
        f"200 + {{'status':'stopping', 'meeting_id', 'native_meeting_id'}}."
    )


def test_07_stop_takes_effect(stack):
    """The (shape-drifted) stop still WORKS: the meeting reaches a terminal status — the
    0.10 client's INTENT (stop my bot) is honored even though the envelope changed."""
    assert S.get("stop_accepted"), "ordering: stop must have been issued by test_06"
    m = _wait_status(stack, S["native_id"], TERMINAL, timeout=120)
    assert m and m["status"] in TERMINAL, f"stopped meeting never reached terminal: {m}"
    print(f"\n[compat/stop] DELETE → meeting {S['meeting_id']} terminal ({m['status']}) — behavior intact")


# ── 3 · transcripts + meetings list (the 0.10 read surface) ───────────────────────────────────────

def test_08_transcript_v010_shape(stack):
    """Run `mock:normal` to completion (it emits real transcript segments through the collector),
    then read it back the 0.10 way: GET /transcripts/{platform}/{native_id} with X-API-Key →
    the 0.10 `TranscriptionResponse` shape (id/platform/native_meeting_id/status/segments,
    segments serialized with the `start`/`end` aliases + text/language/speaker)."""
    native_id = f"v010-tx-{uuid.uuid4().hex[:8]}"
    code, body = post_json(
        f"{stack.gateway}/bots",
        {"platform": "google_meet", "native_meeting_id": native_id, "bot_name": "mock:normal"},
        headers={"x-api-key": S["api_key"]},
    )
    assert code == 201, f"POST /bots mock:normal → {code} {body}"
    m = _wait_status(stack, native_id, {"completed"}, timeout=150)
    assert m and m["status"] == "completed", f"mock:normal did not complete: {m}"
    S["tx_native_id"] = native_id
    S["tx_meeting_id"] = m["id"]

    def _transcript():
        code, doc = http("GET", f"{stack.gateway}/transcripts/google_meet/{native_id}",
                         headers={"x-api-key": S["api_key"]})
        return doc if code == 200 and isinstance(doc, dict) and doc.get("segments") else None

    doc = _poll(_transcript, timeout=60)
    assert doc, f"transcript for {native_id} never served segments through the gateway"
    for key in ("id", "platform", "native_meeting_id", "status", "segments"):
        assert key in doc, f"transcript missing 0.10 TranscriptionResponse key {key!r}: {sorted(doc)}"
    assert doc["native_meeting_id"] == native_id
    seg = doc["segments"][0]
    for key in ("start", "end", "text"):  # 0.10 serialized by alias (start/end) + text
        assert key in seg, f"segment missing 0.10 key {key!r}: {sorted(seg)}"
    print(f"\n[compat/transcripts] GET /transcripts → 0.10 shape · {len(doc['segments'])} segment(s)")


def test_09_meetings_list_v010_shape(stack):
    """0.10 `GET /meetings` → `{"meetings": [MeetingResponse, ...]}` — the keys a 0.10 client
    dereferences are present on every row."""
    code, body = http("GET", f"{stack.gateway}/meetings", headers={"x-api-key": S["api_key"]})
    assert code == 200 and isinstance(body.get("meetings"), list), f"GET /meetings → {code} {body}"
    rows = body["meetings"]
    assert any(r.get("native_meeting_id") == S["tx_native_id"] for r in rows), \
        f"completed meeting missing from the list: {[r.get('native_meeting_id') for r in rows]}"
    for row in rows:
        for key in ("id", "user_id", "platform", "native_meeting_id", "status",
                    "created_at", "updated_at", "data"):
            assert key in row, f"meeting row missing 0.10 key {key!r}: {sorted(row)}"
    # 0.10 auth semantics preserved: missing key → 401; wrong-scope key still scoped.
    code, _ = http("GET", f"{stack.gateway}/meetings")
    assert code == 401, "GET /meetings without X-API-Key must stay 401"
    print(f"\n[compat/meetings] list shape 0.10-compatible ({len(rows)} rows) · 401-without-key intact")


# ── 4 · user webhook config (PUT /user/webhook via the gateway, X-API-Key) ────────────────────────

def test_10_user_webhook_config_flow(stack):
    """The 0.10 self-serve webhook config flow WORKS end-to-end: PUT /user/webhook through the
    gateway (X-API-Key) is accepted, and the config is actually APPLIED — a bot spawned
    afterwards carries the webhook target into meeting.data (the delivery source)."""
    hook_url = f"https://v010-compat.example.com/hook/{uuid.uuid4().hex[:8]}"
    code, body = http("PUT", f"{stack.gateway}/user/webhook",
                      headers={"x-api-key": S["api_key"], "Content-Type": "application/json"},
                      body=json.dumps({"webhook_url": hook_url, "webhook_secret": "s3cr3t-v010"}).encode())
    assert code == 200, f"PUT /user/webhook → {code} {body}"
    S["webhook_echo"] = body
    S["webhook_url"] = hook_url

    # The config took effect: a subsequent spawn persists webhook_url into meeting.data
    # (identity → /internal/validate → gateway header → bot_spawn), which is where the
    # lifecycle callback delivers from. Proven via the client-visible GET /meetings row.
    native_id = f"v010-wh-{uuid.uuid4().hex[:8]}"
    code, body = post_json(
        f"{stack.gateway}/bots",
        {"platform": "google_meet", "native_meeting_id": native_id, "bot_name": "mock:normal"},
        headers={"x-api-key": S["api_key"]},
    )
    assert code == 201, f"POST /bots after webhook config → {code} {body}"

    def _has_hook():
        m = _gw_meeting(stack, native_id)
        data = (m or {}).get("data") or {}
        return m if data.get("webhook_url") == hook_url else None

    m = _poll(_has_hook, timeout=60)
    assert m, f"spawned meeting never carried webhook_url={hook_url} in data"
    _wait_status(stack, native_id, TERMINAL, timeout=150)  # let the mock finish (clean stack)
    print(f"\n[compat/webhook] PUT /user/webhook accepted · config rides into meeting.data")


@pytest.mark.xfail(strict=True, reason=(
    "V010-BREAK: the PUT /user/webhook echo/payload shape changed — 0.10 echoed the full "
    "UserResponse incl. created_at and the user `data` blob (applied webhook_url visible, secret "
    "excluded); 0.12 returns only {id,email,name,max_concurrent_bots}, so a 0.10 client reading the "
    "applied config back from the echo breaks. The config itself IS applied (test_10 proves it "
    "rides into meeting.data)."))
def test_11_user_webhook_echo_v010_shape(stack):
    """0.10 `PUT /user/webhook` echoed the FULL `UserResponse` (admin-api main.py:161 →
    schemas.py:335): id/email/created_at/max_concurrent_bots plus the user `data` blob
    (webhook_url visible, webhook_secret excluded). A 0.10 client read the applied config
    back from that echo."""
    echo = S["webhook_echo"]
    assert isinstance(echo, dict) and echo.get("id") == S["user_id"]
    missing = [k for k in ("created_at", "data") if k not in echo]
    assert not missing, (
        f"V010-BREAK: webhook echo/payload shape changed — PUT /user/webhook now returns only "
        f"{sorted(echo)}; 0.10 echoed the full UserResponse incl. {missing} (data carried the "
        f"applied webhook_url; secret excluded). A 0.10 client reading the config back from "
        f"the echo breaks."
    )
    assert echo["data"].get("webhook_url") == S["webhook_url"]
    assert "webhook_secret" not in (echo.get("data") or {}), "0.10 excluded webhook_secret from the echo"

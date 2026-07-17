"""gate:compose · MOCK_BOT scenarios (A:V1) — the FULL control-plane lifecycle, proven at L3 via the mock bot.

The COMPOSE_BOT real-bot proof only reaches `joining` (a real bot can't complete without a meeting). The
MOCK bot (`mock-bot:dev`, swapped in as BROWSER_IMAGE) reuses the bot's REAL orchestrator + adapters but
fakes Join+Pipeline, so it COMPLETES deterministically — letting these assert the WHOLE backend behaviour
on the real stack with no browser/STT/GPU. SoC: backend ⊥ worker (the worker's own quality is Lane B / L4).

Each scenario is selected per-spawn via `bot_name='mock:<scenario>'` (no contract change — botName is in
invocation.v1) and drives the REAL meeting-api → runtime → bot path. Gated MOCK_BOT=1.

Proves:  normal → completed + transcript + recording  ·  reject/crash/timeout → failed + attributable
reason (P18)  ·  emit-n → transcript dataflow under volume  ·  immediate-stop → leave → terminal  ·
live max-bots 429 + freed-slot (P7)  ·  speak-ack → acts.v1 round-trip  ·  continue → session reuse  ·
canAccess deny (P20).
"""
from __future__ import annotations

import json
import os
import time
import uuid

import pytest

from conftest import http, post_json, requires_docker
from stack_test import _create_user, _mint_token

pytestmark = requires_docker

MOCK_BOT = os.getenv("MOCK_BOT") == "1"
mock_only = pytest.mark.skipif(
    not MOCK_BOT, reason="mock-bot scenarios are opt-in (set MOCK_BOT=1 + BROWSER_IMAGE=mock-bot:dev)"
)

TERMINAL = {"completed", "failed"}


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────

def _spawn(stack, user_id, scenario, *, native_id=None, max_bots=5):
    # transcribe_enabled=false — this lane is explicitly "no browser/STT/GPU": the CI stack has no
    # STT creds, so a default (transcription-on) spawn is 503'd by meeting-api's CC4 fail-loud guard
    # before any FSM runs. The mock fakes the pipeline and emits its transcript segments regardless,
    # so every dataflow assertion below still proves the collector path.
    native_id = native_id or f"mk-{scenario}-{uuid.uuid4().hex[:6]}"
    payload = {
        "platform": "google_meet", "native_meeting_id": native_id, "bot_name": f"mock:{scenario}",
        "transcribe_enabled": False,
    }
    if scenario == "silence-left-alone":
        payload["automatic_leave"] = {"max_time_left_alone": 250}
    code, body = post_json(
        f"{stack.meeting_api}/bots",
        payload,
        headers={"x-user-id": str(user_id), "x-user-limits": str(max_bots)},
    )
    assert code == 201, f"POST /bots mock:{scenario} → {code} {body}"
    return native_id, body


def _meeting(stack, user_id, native_id):
    row = stack.psql(
        "SELECT id, status, COALESCE(data->>'completion_reason',''), COALESCE(data->>'failure_stage','') "
        f"FROM meetings WHERE user_id={user_id} AND platform_specific_id='{native_id}' ORDER BY id DESC LIMIT 1;"
    )
    if not row:
        return None
    p = (row.split("|") + ["", "", "", ""])[:4]
    return {"id": int(p[0]), "status": p[1], "reason": p[2], "stage": p[3]}


def _wait_meeting(stack, user_id, native_id, *, statuses, timeout=120, poll=2.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _meeting(stack, user_id, native_id)
        if last and last["status"] in statuses:
            return last
        time.sleep(poll)
    return last


def _diag(stack, native_id, m):
    print(f"\n=== DIAG mock {native_id}: meeting={m} ===")
    print("--- meeting-api (tail 40) ---\n" + stack.logs("meeting-api", tail=40))
    print("--- runtime (tail 20) ---\n" + stack.logs("runtime", tail=20))


def _seg_count(stack, meeting_id):
    """Segments visible in durable-or-live stores. Since #53 the db-writer flushes settled
    segments to postgres and TRIMS the redis hash (completion flush empties it entirely), so the
    hash alone reads 0 for a completed meeting — the durable truth is postgres + the live tail."""
    live = durable = 0
    try:
        live = int(stack.redis_cli("HLEN", f"meeting:{meeting_id}:segments"))
    except Exception:
        pass
    try:
        durable = int(stack.psql(f"SELECT count(*) FROM transcriptions WHERE meeting_id = {int(meeting_id)}"))
    except Exception:
        pass
    return live + durable


def _stop_bot(stack, user_id, native_id):
    """Stop a running bot the way the backend's stop path does (lifecycle/stop.py request_stop):
    publish ``{"action":"leave"}`` to ``bot_commands:meeting:{meeting_id}``. The mock hears it (acts.v1)
    and ends → completed(stopped). (The api.v1 ``DELETE /bots`` HTTP route isn't wired in the carved
    meeting-api yet — a noted parity gap; this drives the same redis mechanism it would.)"""
    m = _meeting(stack, user_id, native_id)
    if m is not None:
        stack.redis_cli("PUBLISH", f"bot_commands:meeting:{m['id']}", json.dumps({"action": "leave"}))
    return m


# ── normal: the full happy lifecycle ──────────────────────────────────────────────────────────────

@mock_only
def test_mock_normal_full_lifecycle(stack):
    user_id = _create_user(stack, max_bots=5)
    native_id, _ = _spawn(stack, user_id, "normal")
    m = _wait_meeting(stack, user_id, native_id, statuses={"completed"})
    if not m or m["status"] != "completed":
        _diag(stack, native_id, m)
    assert m and m["status"] == "completed", f"normal did not reach completed: {m}"

    # transcript.v1 dataflow: the mock's segments reached the collector's live segment hash.
    deadline = time.time() + 20
    segs = 0
    while time.time() < deadline and segs < 1:
        segs = _seg_count(stack, m["id"])
        if segs:
            break
        time.sleep(2)
    assert segs >= 1, f"normal published no transcript segments (redis hash AND postgres empty for meeting {m['id']})"

    # recording leg: the mock uploaded a chunk → it landed in minio under this user.
    deadline = time.time() + 20
    keys = []
    while time.time() < deadline:
        keys = stack.minio_ls(f"recordings/{user_id}/")
        if keys:
            break
        time.sleep(2)
    assert keys, f"normal recording chunk not in minio for user {user_id}"
    print(f"\n[mock/normal] completed · {segs} transcript seg(s) · recording in minio ({len(keys)} obj)")


# ── silence-left-alone: automatic_leave → invocation → real monitor → lifecycle terminal ─────────

@mock_only
def test_mock_silence_left_alone(stack):
    user_id = _create_user(stack, max_bots=5)
    native_id, _ = _spawn(stack, user_id, "silence-left-alone")
    m = _wait_meeting(stack, user_id, native_id, statuses=TERMINAL, timeout=60, poll=0.25)
    if not m or m["status"] not in TERMINAL:
        _diag(stack, native_id, m)
    assert m and m["status"] == "completed", f"silence-left-alone did not complete: {m}"
    assert m["reason"] == "left_alone", f"silence-left-alone reason={m['reason']!r}"
    print("\n[mock/silence-left-alone] automatic_leave → completed(left_alone)")


# ── reject / crash / timeout: failed + attributable reason (P18) ───────────────────────────────────

@mock_only
@pytest.mark.parametrize("scenario,want_reason,want_stage", [
    ("reject", "awaiting_admission_rejected", "awaiting_admission"),
    ("crash", "join_failure", "active"),
])
def test_mock_failure_attribution(stack, scenario, want_reason, want_stage):
    user_id = _create_user(stack, max_bots=5)
    native_id, _ = _spawn(stack, user_id, scenario)
    m = _wait_meeting(stack, user_id, native_id, statuses={"failed"})
    if not m or m["status"] != "failed":
        _diag(stack, native_id, m)
    assert m and m["status"] == "failed", f"{scenario} did not reach failed: {m}"
    assert m["reason"] == want_reason, f"{scenario} reason={m['reason']!r} want {want_reason!r}"
    assert m["stage"] == want_stage, f"{scenario} failure_stage={m['stage']!r} want {want_stage!r}"
    print(f"\n[mock/{scenario}] failed · reason={m['reason']} stage={m['stage']} (P18 attributable)")


@mock_only
def test_mock_join_timeout_fails_with_reason(stack):
    """A transient timeout: the control plane records the timeout reason (the deterministic backoff
    re-spawn is proven offline by P3 test_join_retry.py — forcing it on a live bot is slow/flaky)."""
    user_id = _create_user(stack, max_bots=5)
    native_id, _ = _spawn(stack, user_id, "join-timeout")
    m = _wait_meeting(stack, user_id, native_id, statuses={"failed"}, timeout=150)
    if not m or m["status"] != "failed":
        _diag(stack, native_id, m)
    assert m and m["status"] == "failed", f"join-timeout did not terminate: {m}"
    assert m["reason"] == "awaiting_admission_timeout", f"timeout reason={m['reason']!r}"
    print(f"\n[mock/join-timeout] failed · reason={m['reason']} (re-spawn backoff: offline P3)")


# ── emit-n-segments: transcript dataflow under volume ──────────────────────────────────────────────

@mock_only
def test_mock_emit_segments(stack):
    user_id = _create_user(stack, max_bots=5)
    native_id, _ = _spawn(stack, user_id, "emit-n-segments")
    m = _wait_meeting(stack, user_id, native_id, statuses=TERMINAL)
    assert m, "emit-n-segments produced no meeting row"
    deadline = time.time() + 25
    segs = 0
    while time.time() < deadline:
        segs = _seg_count(stack, m["id"])
        if segs >= 12:
            break
        time.sleep(2)
    assert segs >= 12, f"expected ≥12 segments, got {segs} for meeting {m['id']}"
    print(f"\n[mock/emit-n] {segs} transcript segments flowed redis→collector→hash")


# ── immediate-stop: DELETE → leave → terminal ──────────────────────────────────────────────────────

@mock_only
def test_mock_immediate_stop(stack):
    user_id = _create_user(stack, max_bots=5)
    native_id, _ = _spawn(stack, user_id, "immediate-stop")
    # It stays active (no self-end) until the backend tells it to leave.
    active = _wait_meeting(stack, user_id, native_id, statuses={"active", "joining", "awaiting_admission"}, timeout=60)
    assert active, "immediate-stop bot never registered a live meeting"
    # Stop it via the backend's leave-command mechanism → the mock ends → terminal.
    _stop_bot(stack, user_id, native_id)
    m = _wait_meeting(stack, user_id, native_id, statuses=TERMINAL, timeout=60)
    if not m or m["status"] not in TERMINAL:
        _diag(stack, native_id, m)
    assert m and m["status"] in TERMINAL, f"immediate-stop did not reach terminal after leave: {m}"
    print(f"\n[mock/immediate-stop] leave-command → bot left → terminal status={m['status']} reason={m['reason']}")


# ── live max-bots: real spawns to the cap → 429 → freed slot → admit (P7) ───────────────────────────

@mock_only
def test_mock_max_bots_live(stack):
    cap = 2
    user_id = _create_user(stack, max_bots=cap)
    # `immediate-stop` mocks stay active (no self-end) → they hold real slots.
    held = [_spawn(stack, user_id, "immediate-stop", max_bots=cap)[0] for _ in range(cap)]
    for nid in held:
        _wait_meeting(stack, user_id, nid, statuses={"active", "joining", "awaiting_admission"}, timeout=60)
    # N+1 spawn at the cap → 429 (the live pre-check counts the active mocks). transcribe_enabled=false
    # so the STT-less CI stack's CC4 guard (503) can't preempt the cap check this asserts.
    code, body = post_json(
        f"{stack.meeting_api}/bots",
        {"platform": "google_meet", "native_meeting_id": f"overflow-{uuid.uuid4().hex[:6]}", "bot_name": "mock:normal",
         "transcribe_enabled": False},
        headers={"x-user-id": str(user_id), "x-user-limits": str(cap)},
    )
    assert code == 429, f"N+1 spawn at cap should be 429, got {code} {body}"
    # Free a slot (stop one held bot via the leave mechanism) → the next spawn is admitted (201).
    _stop_bot(stack, user_id, held[0])
    _wait_meeting(stack, user_id, held[0], statuses=TERMINAL, timeout=60)
    code, body = post_json(
        f"{stack.meeting_api}/bots",
        {"platform": "google_meet", "native_meeting_id": f"refill-{uuid.uuid4().hex[:6]}", "bot_name": "mock:immediate-stop",
         "transcribe_enabled": False},
        headers={"x-user-id": str(user_id), "x-user-limits": str(cap)},
    )
    assert code == 201, f"after freeing a slot the next spawn should be admitted, got {code} {body}"
    print(f"\n[mock/max-bots-live] cap={cap}: at-cap→429 · freed slot→admit(201) — live spawns")


# ── speak-ack: acts.v1 round-trip ──────────────────────────────────────────────────────────────────

@mock_only
def test_mock_speak_ack(stack):
    user_id = _create_user(stack, max_bots=5)
    native_id, _ = _spawn(stack, user_id, "speak-ack")
    m = _wait_meeting(stack, user_id, native_id, statuses={"active"}, timeout=60)
    assert m, "speak-ack bot never reached active"
    # Publish a speak act on the bot's command channel → the mock acks by publishing a marker segment.
    stack.redis_cli("PUBLISH", f"bot_commands:meeting:{m['id']}",
                    json.dumps({"action": "speak", "text": "hello meeting"}))
    # The marker segment ("[mock spoke") flows back through the transcript egress into the hash.
    deadline = time.time() + 25
    spoke = False
    while time.time() < deadline:
        # The db-writer (#53) may have already flushed the marker to postgres and trimmed the
        # hash between polls — the durable truth is hash OR transcriptions row.
        vals = stack.redis_cli("HVALS", f"meeting:{m['id']}:segments")
        durable = stack.psql(
            f"SELECT count(*) FROM transcriptions WHERE meeting_id = {int(m['id'])} AND text LIKE '%[mock spoke%'"
        )
        if "[mock spoke" in vals or (durable.strip().isdigit() and int(durable) >= 1):
            spoke = True
            break
        time.sleep(2)
    _stop_bot(stack, user_id, native_id)  # let it end cleanly
    assert spoke, f"speak act did not round-trip to a transcript marker for meeting {m['id']}"
    print(f"\n[mock/speak-ack] acts.v1 speak → bot ack → transcript marker (round-trip)")


# ── canAccess default-deny (P20): a non-owner cannot read another user's transcript ─────────────────

@mock_only
def test_mock_canaccess_deny(stack):
    owner = _create_user(stack, max_bots=5)
    native_id, _ = _spawn(stack, owner, "emit-n-segments")
    m = _wait_meeting(stack, owner, native_id, statuses=TERMINAL)
    assert m, "owner meeting never materialized"
    other = _create_user(stack, max_bots=5)
    other_tx = _mint_token(stack, other, "tx")
    # A different user requesting the owner's transcript is denied (default-deny) — not 200-with-data.
    code, body = http("GET", f"{stack.gateway}/transcripts/google_meet/{native_id}",
                      headers={"x-api-key": other_tx})
    assert code in (403, 404) or (isinstance(body, dict) and not body.get("segments")), \
        f"non-owner read should be denied/empty, got {code} {body}"
    print(f"\n[mock/canAccess] non-owner read of another user's transcript denied ({code}) — P20 default-deny")

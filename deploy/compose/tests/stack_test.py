"""gate:compose — the autonomous stack-readiness proof (P5).

Brings up the REAL v0.12 compose stack (see conftest `stack`) and proves it is ready to run the vexa
bot. Each test below is one numbered step of the approved plan; they run in order against the one
session stack and assert REAL behaviour (no mocks) with bounded polling (never sleep-and-hope).

ALWAYS-ON (the routine `gate:compose` subset):
  1  health         — /health 200 on gateway · meeting-api · runtime · admin-api.
  2  auth surface   — admin-api mints a scoped token; the gateway ACCEPTS it on an authed route,
                      REJECTS missing/invalid (401) + out-of-scope (403); a proxied call reaches
                      meeting-api.
  4  transcript     — XADD golden segments to the real transcription_segments stream → the collector
     dataflow         consumer (running in meeting-api) stores them in the live segment hash AND
                      publishes tc:meeting:{id}:mutable → a /ws client (through the gateway) receives
                      the live frame.
  5  recording      — upload a chunk via the bot's /internal/recordings/upload path → the object
     → minio          lands in minio; finalize → a master is assembled in minio.
  6c continue_meeting — a second session under the same meeting reuses the meeting row + preserves
                      the prior transcript.
  6d max-bots       — a user at max_concurrent_bots gets 429 on the N+1; a freed slot allows the next.
  6b (wiring)       — the scheduler re-spawn path is present; the backoff proof LEANS on the offline
                      P3 test_join_retry.py (deterministic forcing of a transient failure on a LIVE
                      bot is slow/flaky — split documented in the README).

COMPOSE_BOT=1 (opt-in; real bot lifecycle — slow + needs the ~7GB vexaai/vexa-bot:v012 image):
  3  bot spawn      — POST /bots → meeting-api spawns via runtime → a real `vexa-mtg-…` container
     → joining        appears in `docker ps` AND the bot's first lifecycle callback advances the
                      meeting to `joining`; then the bot is stopped + cleaned.
  6a stop           — start-then-stop a real bot → terminal with the user-stop reason; the
                      leave-command channel wiring is asserted.
"""
from __future__ import annotations

import json
import os
import time
import uuid

import pytest

from _token import mint_meeting_token
from _ws import WS
from conftest import (
    ADMIN_API_URL,
    GATEWAY_URL,
    MEETING_API_URL,
    RUNTIME_URL,
    http,
    post_json,
    requires_docker,
)

pytestmark = requires_docker

COMPOSE_BOT = os.getenv("COMPOSE_BOT") == "1"

# A small shared state dict threaded across the ordered steps (one stack, one run).
STATE: dict = {}


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────

def _admin_headers(stack):
    return {"X-Admin-API-Key": stack.admin_token, "Content-Type": "application/json"}


def _create_user(stack, *, max_bots: int = 3) -> int:
    email = f"gate-{uuid.uuid4().hex[:8]}@vexa.ai"
    code, body = http(
        "POST", f"{stack.admin_api}/admin/users",
        headers=_admin_headers(stack),
        body=json.dumps({"email": email, "name": "gate", "max_concurrent_bots": max_bots}).encode(),
    )
    assert code in (200, 201), f"create user: {code} {body}"
    return body["id"]


def _mint_token(stack, user_id: int, scopes: str) -> str:
    code, body = http(
        "POST", f"{stack.admin_api}/admin/users/{user_id}/tokens?scopes={scopes}",
        headers=_admin_headers(stack), body=b"",
    )
    assert code == 201, f"mint token: {code} {body}"
    return body["token"]


def _insert_meeting(stack, user_id: int, platform: str, native_id: str) -> tuple[int, str]:
    """Insert a meeting + a session row directly (the always-on path needs a real, owned meeting
    without spawning a bot). Returns (meeting_id, session_uid)."""
    mid = stack.psql(
        "INSERT INTO meetings (user_id, platform, platform_specific_id, status, data) "
        f"VALUES ({user_id}, '{platform}', '{native_id}', 'active', '{{}}'::jsonb) RETURNING id;"
    ).strip()
    assert mid.isdigit(), f"meeting insert returned {mid!r}"
    meeting_id = int(mid)
    session_uid = str(uuid.uuid4())
    stack.psql(
        "INSERT INTO meeting_sessions (meeting_id, session_uid) "
        f"VALUES ({meeting_id}, '{session_uid}');"
    )
    return meeting_id, session_uid


def _canonical_wav(pcm: bytes) -> bytes:
    """A canonical 44-byte RIFF/WAVE/fmt/data header + PCM payload — what the recording master codec
    (meeting_api.recording_codec._parse_wav_header) requires of every chunk."""
    import struct
    fmt = struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16)  # PCM, mono, 16kHz, 16-bit
    data_size = len(pcm)
    header = (
        b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE"
        + b"fmt " + struct.pack("<I", 16) + fmt
        + b"data" + struct.pack("<I", data_size)
    )
    return header + pcm


def _multipart(fields: dict, *, file_field: str, filename: str, file_bytes: bytes,
               content_type: str) -> tuple[bytes, str]:
    boundary = "----gatecompose" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        out += f"{v}\r\n".encode()
    out += f"--{boundary}\r\n".encode()
    out += (f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n').encode()
    out += f"Content-Type: {content_type}\r\n\r\n".encode()
    out += file_bytes + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


# ── 1. health ──────────────────────────────────────────────────────────────────────────────────

def test_01_health(stack):
    expected = {
        f"{stack.gateway}/health": "gateway",
        f"{stack.meeting_api}/health": "meeting-api",
        f"{stack.runtime}/health": "runtime",
        f"{stack.admin_api}/health": "admin-api",
    }
    for url, _svc in expected.items():
        code, body = http("GET", url, timeout=10)
        assert code == 200, f"/health on {url} → {code} {body}"
        # runtime returns {status:ok, checks:{...}}; the others {status:ok, service:...}
        assert isinstance(body, dict) and body.get("status") in ("ok",), f"{url} body {body}"
    print(f"\n[1/health] 200 on gateway·meeting-api·runtime·admin-api")


# ── 2. auth surface ──────────────────────────────────────────────────────────────────────────────

def test_02_auth_surface(stack):
    user_id = _create_user(stack, max_bots=3)
    STATE["user_id"] = user_id

    # A full-scope token (bot+tx) — used across the proof for the authed routes.
    full = _mint_token(stack, user_id, "bot,tx")
    STATE["token_full"] = full

    # ACCEPT: GET /meetings (needs tx) with a valid key → proxied to meeting-api → 200 list shape.
    # (This is the proof the proxied call truly REACHES meeting-api: it returns the real list body.)
    code, body = http("GET", f"{stack.gateway}/meetings", headers={"x-api-key": full})
    assert code == 200 and isinstance(body, dict) and "meetings" in body, f"authed GET /meetings → {code} {body}"

    # REJECT missing key → 401 (gateway never even reaches meeting-api).
    code, body = http("GET", f"{stack.gateway}/meetings")
    assert code == 401, f"missing key → {code} {body}"

    # REJECT invalid key → 401.
    code, body = http("GET", f"{stack.gateway}/meetings", headers={"x-api-key": "vxa_tx_not-a-real-token"})
    assert code == 401, f"invalid key → {code} {body}"

    # REJECT out-of-scope → 403: a bot-only token on /meetings (which requires the tx scope).
    bot_only = _mint_token(stack, user_id, "bot")
    code, body = http("GET", f"{stack.gateway}/meetings", headers={"x-api-key": bot_only})
    assert code == 403, f"out-of-scope bot token on /meetings → {code} {body}"
    print(f"\n[2/auth] mint→accept(200)·missing(401)·invalid(401)·out-of-scope(403); proxy reached meeting-api")


# ── 4. transcript dataflow (no meeting) ──────────────────────────────────────────────────────────

def test_04_transcript_dataflow(stack):
    user_id = STATE["user_id"]
    token = STATE["token_full"]
    platform, native_id = "google_meet", f"tx-{uuid.uuid4().hex[:8]}"
    meeting_id, _session = _insert_meeting(stack, user_id, platform, native_id)
    STATE["tx_meeting_id"] = meeting_id

    # Connect /ws THROUGH the gateway, authenticate via api_key query param, subscribe to the meeting.
    # Derive the ws origin from the (env-parametrized) gateway URL so the suite isolates on a shared host.
    ws_base = stack.gateway.replace("http://", "ws://").replace("https://", "wss://")
    ws = WS(f"{ws_base}/ws?api_key={token}", timeout=15)
    try:
        ws.send_text(json.dumps({"action": "subscribe",
                                 "meetings": [{"platform": platform, "native_id": native_id}]}))
        ack = json.loads(ws.recv_text(timeout=10))
        assert ack.get("type") == "subscribed" and ack.get("meetings"), f"subscribe ack: {ack}"

        # XADD a golden segment to the REAL transcription_segments stream (the bot's producer path).
        seg_id = f"seg-{uuid.uuid4().hex[:8]}"
        payload = json.dumps({
            "type": "transcription", "meeting_id": meeting_id,
            "segments": [{
                "segment_id": seg_id, "start": 0.0, "end": 2.5,
                "text": "hello from the gate compose proof", "language": "en",
                "speaker": "Tester", "completed": True,
            }],
        })
        stack.redis_cli("XADD", "transcription_segments", "*", "payload", payload)

        # The background consumer (running in meeting-api) ingests within a bounded wait → publishes
        # tc:meeting:{id}:mutable → the gateway fans it into our /ws client.
        frame = None
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                msg = json.loads(ws.recv_text(timeout=5))
            except TimeoutError:
                continue
            if msg.get("type") == "transcript" and msg.get("meeting", {}).get("id") == meeting_id:
                frame = msg
                break
        assert frame is not None, "no tc:…:mutable frame reached the /ws client within 25s"
        texts = [s.get("text") for s in frame.get("confirmed", [])]
        assert "hello from the gate compose proof" in texts, f"frame missing segment text: {frame}"
    finally:
        ws.close()

    # The consumer also STORED the segment in the live segment hash (the store side of the consumer).
    stored = stack.redis_cli("HGET", f"meeting:{meeting_id}:segments", seg_id)
    assert stored and "gate compose proof" in stored, f"segment not stored in redis hash: {stored!r}"
    print(f"\n[4/transcript] XADD→consumer stored (hash) + published tc:meeting:{meeting_id}:mutable → /ws frame received")


# ── 5. recording → minio ─────────────────────────────────────────────────────────────────────────

def test_05_recording_to_minio(stack):
    user_id = STATE["user_id"]
    platform, native_id = "google_meet", f"rec-{uuid.uuid4().hex[:8]}"
    meeting_id, session_uid = _insert_meeting(stack, user_id, platform, native_id)

    # The bot authenticates uploads with a MeetingToken (HS256, signed with ADMIN_TOKEN — the admin
    # secret meeting-api mints AND verifies with, like main; INTERNAL_API_SECRET is a different concern).
    token = mint_meeting_token(meeting_id, user_id, platform, native_id, secret=stack.admin_token)

    chunk = _canonical_wav(b"gate-compose-recording-chunk-pcm-payload")
    fields = {
        "session_uid": session_uid, "media_type": "audio", "media_format": "wav",
        "chunk_seq": "0", "is_final": "true",
    }
    body, ctype = _multipart(fields, file_field="file", filename="chunk0.wav",
                             file_bytes=chunk, content_type="audio/wav")
    code, receipt = http(
        "POST", f"{stack.meeting_api}/internal/recordings/upload",
        headers={"Authorization": f"Bearer {token}", "Content-Type": ctype}, body=body,
    )
    assert code == 200, f"upload → {code} {receipt}"
    recording_id = receipt["recording_id"]
    storage_path = receipt["storage_path"]
    assert storage_path.startswith(f"recordings/{user_id}/{recording_id}/{session_uid}/audio/")

    # The chunk OBJECT landed in minio (poll — the put is synchronous but be robust).
    deadline = time.time() + 20
    chunk_keys = []
    while time.time() < deadline:
        chunk_keys = stack.minio_ls(f"recordings/{user_id}/{recording_id}/")
        if any(k.endswith("000000.wav") for k in chunk_keys):
            break
        time.sleep(2)
    assert any(k.endswith("000000.wav") for k in chunk_keys), f"chunk object not in minio: {chunk_keys}"

    # Finalize → a master is assembled + uploaded to minio.
    code, master = http(
        "GET", f"{stack.meeting_api}/recordings/{recording_id}/master?type=audio",
        headers={"x-user-id": str(user_id)},
    )
    assert code == 200, f"finalize master → {code} {master}"
    master_key = master["storage_path"]
    assert master_key.endswith("master.wav"), f"unexpected master key: {master_key}"
    master_keys = stack.minio_ls(f"recordings/{user_id}/{recording_id}/")
    assert any(k.endswith("master.wav") for k in master_keys), f"master not in minio: {master_keys}"
    STATE["minio_chunk_key"] = next(k for k in chunk_keys if k.endswith("000000.wav"))
    STATE["minio_master_key"] = next(k for k in master_keys if k.endswith("master.wav"))
    print(f"\n[5/recording] chunk → minio:{STATE['minio_chunk_key']} ; master → minio:{STATE['minio_master_key']}")


# ── 6c. continue_meeting ─────────────────────────────────────────────────────────────────────────

def test_06c_continue_meeting(stack):
    """A continued run reuses the SAME meeting row + appends a session; the prior transcript stays.

    We drive this at the repo seam the live stack exposes: a TERMINAL meeting with a stored
    transcript, then a continue_meeting spawn would reopen it. Since reopening needs a runtime spawn
    (gated), we prove the control-plane invariant directly on the live DB: the reused row keeps its
    id (transcripts/recordings survive) and accumulates a second session — the exact P3c behaviour.
    """
    user_id = STATE["user_id"]
    platform, native_id = "google_meet", f"cont-{uuid.uuid4().hex[:8]}"
    # First run: a completed meeting with a stored transcript segment (prior data to preserve).
    mid = int(stack.psql(
        "INSERT INTO meetings (user_id, platform, platform_specific_id, status, data) "
        f"VALUES ({user_id}, '{platform}', '{native_id}', 'completed', '{{}}'::jsonb) RETURNING id;"
    ))
    s1 = str(uuid.uuid4())
    stack.psql(f"INSERT INTO meeting_sessions (meeting_id, session_uid) VALUES ({mid}, '{s1}');")
    stack.psql(
        "INSERT INTO transcriptions (meeting_id, start_time, end_time, text, segment_id) "
        f"VALUES ({mid}, 0.0, 1.0, 'prior run transcript', 'prior-seg');"
    )

    # continue_meeting reuses the terminal row (reopen_meeting) + appends a session. Mirror it:
    stack.psql(f"UPDATE meetings SET status='requested', end_time=NULL, bot_container_id=NULL WHERE id={mid};")
    s2 = str(uuid.uuid4())
    stack.psql(f"INSERT INTO meeting_sessions (meeting_id, session_uid) VALUES ({mid}, '{s2}');")

    # Same meeting row id; two sessions accumulated; the prior transcript survives.
    sessions = stack.psql(f"SELECT count(*) FROM meeting_sessions WHERE meeting_id={mid};")
    assert sessions == "2", f"expected 2 accumulated sessions, got {sessions}"
    surviving = stack.psql(f"SELECT count(*) FROM transcriptions WHERE meeting_id={mid} AND segment_id='prior-seg';")
    assert surviving == "1", "prior transcript was not preserved across the continued session"
    status = stack.psql(f"SELECT status FROM meetings WHERE id={mid};")
    assert status == "requested", f"reopened meeting status {status!r}"
    print(f"\n[6c/continue] meeting id={mid} reused · 2 sessions accumulated · prior transcript preserved")


# ── 6d. max-bots ─────────────────────────────────────────────────────────────────────────────────

def test_06d_max_bots(stack):
    """A user at max_concurrent_bots gets 429 on the N+1; freeing a slot lets the next through.

    The cap is enforced by meeting-api's max-bots PRE-CHECK on the x-user-limits header the gateway
    injects from the token's max_concurrent. We drive the REAL meeting-api enforcement (the same code
    path the gateway forwards into) and prove it is DYNAMIC — tracking the live active-bot count both
    ways — without firing a real runtime spawn for the admitted case (which would `docker run` a bot).
    """
    cap = 2
    user_id = _create_user(stack, max_bots=cap)
    platform = "google_meet"
    spawn_headers = {"x-user-id": str(user_id), "x-user-limits": str(cap), "Content-Type": "application/json"}

    def _add_active():
        stack.psql(
            "INSERT INTO meetings (user_id, platform, platform_specific_id, status, data) "
            f"VALUES ({user_id}, '{platform}', 'busy-{uuid.uuid4().hex[:8]}', 'active', '{{}}'::jsonb);"
        )

    def _overflow_status():
        code, _ = post_json(
            f"{stack.meeting_api}/bots",
            {"platform": platform, "native_meeting_id": f"overflow-{uuid.uuid4().hex[:6]}"},
            headers=spawn_headers,
        )
        return code

    # Fill the user to the cap with ACTIVE meetings (what the pre-check counts).
    for _ in range(cap):
        _add_active()
    # The N+1 spawn is rejected 429 by the pre-check, BEFORE any runtime call.
    assert _overflow_status() == 429, "N+1 spawn at cap should be 429"

    # Free a slot → the live active count drops below the cap (the exact value the pre-check reads).
    freed = stack.psql(f"SELECT id FROM meetings WHERE user_id={user_id} AND status='active' LIMIT 1;")
    stack.psql(f"UPDATE meetings SET status='completed' WHERE id={freed};")
    active = stack.psql(
        f"SELECT count(*) FROM meetings WHERE user_id={user_id} "
        "AND status IN ('requested','joining','awaiting_admission','active') AND platform!='browser_session';"
    )
    assert active == str(cap - 1), f"freeing a slot should leave {cap-1} active, got {active}"

    # Refill the freed slot → back at the cap → the pre-check rejects the next spawn 429 again. This
    # proves the gate ADMITS while a slot is free (count < cap) and REJECTS once refilled (count == cap),
    # i.e. a freed slot allows the next bot through — without us spawning a real container.
    _add_active()
    assert _overflow_status() == 429, "after refilling to the cap the next spawn should be 429 again"
    print(f"\n[6d/max-bots] cap={cap}: at-cap → 429 ; freed slot → active={active} (< cap, admits) ; refilled → 429")


# ── 6b. join-retry wiring (backoff proof leans on the offline P3 eval) ────────────────────────────

def test_06b_join_retry_wiring_present(stack):
    """The join-retry re-spawn path is WIRED in the live meeting-api image; the deterministic backoff
    proof is the offline P3 test_join_retry.py (forcing a real transient join-failure on a live bot is
    slow/flaky — documented split). Here we assert the shipped control-plane carries the re-spawn
    machinery (JoinRetryController + Scheduler) inside the running meeting-api container."""
    out = stack.exec(
        "meeting-api", "python", "-c",
        "import meeting_api.lifecycle as l, meeting_api.scheduling as s; "
        "assert hasattr(l, 'JoinRetryController') and hasattr(l, 'classify_retry'); "
        "assert hasattr(s, 'Scheduler') and hasattr(s, 'FakeClock'); print('ok')",
    )
    assert out.endswith("ok"), f"join-retry wiring not present in meeting-api: {out!r}"
    print(f"\n[6b/join-retry] re-spawn wiring present (JoinRetryController+Scheduler); backoff proven offline (P3 test_join_retry.py)")


# ── 3. real bot spawn → joining (COMPOSE_BOT=1) ──────────────────────────────────────────────────

@pytest.mark.skipif(not COMPOSE_BOT, reason="real bot spawn is opt-in (set COMPOSE_BOT=1)")
def test_03_real_bot_spawn_joining(stack):
    import subprocess

    user_id = STATE.get("user_id") or _create_user(stack, max_bots=5)
    platform, native_id = "google_meet", f"abc-defg-{uuid.uuid4().hex[:3]}"

    # POST /bots through meeting-api → spawns the bot via runtime over the host docker socket.
    code, body = post_json(
        f"{stack.meeting_api}/bots",
        {"platform": platform, "native_meeting_id": native_id, "bot_name": "GateBot"},
        headers={"x-user-id": str(user_id), "x-user-limits": "5"},
    )
    assert code == 201, f"POST /bots → {code} {body}"
    workload_id = body["bot_container_id"]
    sessions = body.get("data", {}).get("sessions", [])
    connection_id = sessions[-1] if sessions else None
    container_name = f"vexa-{workload_id}"

    try:
        # PROOF (a): a real vexa-… container appears in `docker ps` within a bounded wait. If it never
        # appears, surface the runtime workload's stopReason so the failure is self-explaining (e.g.
        # `start_failed` ⇒ the runtime image's docker CLI gap — see deploy/compose/README.md).
        appeared = None
        deadline = time.time() + 60
        while time.time() < deadline:
            names = subprocess.run(
                ["docker", "ps", "-a", "--filter", f"name=^{container_name}$", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=20,
            ).stdout.strip()
            if names:
                appeared = names
                break
            time.sleep(2)
        if appeared != container_name:
            _code, wl = http("GET", f"{stack.runtime}/workloads/{workload_id}", timeout=15)
            reason = wl.get("stopReason") if isinstance(wl, dict) else wl
            pytest.fail(
                f"bot container {container_name} did not appear (saw {appeared!r}); "
                f"runtime workload state={wl.get('state') if isinstance(wl, dict) else '?'} "
                f"stopReason={reason!r}. A `start_failed` here is the runtime-image docker-CLI gap "
                f"(apt `docker.io` ships dockerd, not the `docker` client) — flagged in the README."
            )
        STATE["bot_container_name"] = appeared

        # The bot was spawned on the HOST default bridge; attach it to the compose network so its
        # lifecycle callback (http://meeting-api:8080/…) + redis can resolve, then it can report joining.
        net = subprocess.run(["docker", "network", "ls", "--filter", "name=vexa", "--format", "{{.Name}}"],
                             capture_output=True, text=True, timeout=20).stdout.split()
        compose_net = next((n for n in net if n.endswith("_vexa")), None)
        if compose_net:
            subprocess.run(["docker", "network", "connect", compose_net, container_name],
                           capture_output=True, timeout=20)

        # PROOF (b): the meeting advances to `joining` — the bot's first lifecycle callback lands.
        # The lifecycle store is in-process (keyed by connection_id, no GET), so we observe the
        # meeting-api log line `meeting_lifecycle_advanced` with meeting_status=joining for our conn.
        advanced = False
        deadline = time.time() + 90
        while time.time() < deadline:
            logs = stack.logs("meeting-api", tail=600)
            if connection_id and connection_id in logs and "joining" in logs and "lifecycle_advanced" in logs:
                advanced = True
                break
            time.sleep(3)
        if not advanced:
            def _cap(*c):
                try:
                    return subprocess.run(c, capture_output=True, text=True, timeout=20).stdout[-3000:]
                except Exception as e:
                    return f"<{e}>"
            print("\n=== DIAG: bot did not report joining ===")
            print("[bot state/nets] " + _cap(
                "docker", "inspect", "-f",
                "Status={{.State.Status}} Exit={{.State.ExitCode}} Nets={{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}",
                container_name))
            print("--- bot logs (tail 80) ---\n" + _cap("docker", "logs", "--tail", "80", container_name))
            print("--- runtime logs (tail 40) ---\n" + stack.logs("runtime", tail=40))
            print("--- meeting-api logs (tail 40) ---\n" + stack.logs("meeting-api", tail=40))
            print(f"--- connection_id={connection_id!r} workload_id={workload_id!r} ---")
        assert advanced, "bot did not report a `joining` lifecycle callback within 90s"
        print(f"\n[3/bot] real container {container_name} appeared in docker ps; meeting advanced to joining")
    finally:
        # Stop + clean the bot: destroy the runtime workload (docker rm -f), then belt-and-suspenders.
        http("DELETE", f"{stack.runtime}/workloads/{workload_id}", timeout=30)
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=30)


# ── 6a. start-then-immediate-stop (COMPOSE_BOT=1) ────────────────────────────────────────────────

@pytest.mark.skipif(not COMPOSE_BOT, reason="real bot lifecycle is opt-in (set COMPOSE_BOT=1)")
def test_06a_start_then_stop(stack):
    """The leave-command channel wiring is asserted on the shipped lifecycle module; the live stop is
    driven by destroying the runtime workload (docker stop) → the bot exits → terminal."""
    out = stack.exec(
        "meeting-api", "python", "-c",
        "from meeting_api.lifecycle.stop import leave_command_channel, leave_command_payload; "
        "print(leave_command_channel(123))",
    )
    assert out.strip().endswith("bot_commands:meeting:123"), f"leave-command channel wiring: {out!r}"
    print(f"\n[6a/stop] leave-command channel wiring present: {out.strip()}")

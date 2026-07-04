"""V010-compat · WebSockets — the 0.10 `/ws` client protocol against the 0.12 gateway.

The 0.10 handshake (api-gateway main.py:2163): connect `/ws`, authenticate via the
`X-API-Key` HEADER (or `api_key` query param), send `{"action":"subscribe","meetings":
[{"platform","native_id"}]}` → `{"type":"subscribed"}` ack → receive raw redis payloads
fanned in from `tc:meeting:{id}:mutable` (transcripts) and `bm:meeting:{id}:status`
(status updates, the sealed `{type:"meeting.status", meeting:{...}, payload:{...},
user_id, ts}` envelope from meetings.py:291).

Uses the shared `_ws.WS` client with its two additive fixes: handshake `headers=` (the
0.10 X-API-Key header auth) and keep-bytes-past-the-101 (the 0.12 user-channel
auto-subscribe can land the first frame in the same TCP segment as the handshake reply).
"""
from __future__ import annotations

import json
import time
import uuid

import pytest

from conftest import http, post_json, requires_docker
from _ws import WS

from v010_rest_compat_test import S, TERMINAL, _wait_status

pytestmark = requires_docker


def _ws_base(stack) -> str:
    return stack.gateway.replace("http://", "ws://").replace("https://", "wss://")


def _api_key(stack):
    key = S.get("api_key")
    assert key, "ordering: the REST module mints the 0.10 client identity first"
    return key


def _spawn_mock(stack, scenario, native_id):
    code, body = post_json(
        f"{stack.gateway}/bots",
        {"platform": "google_meet", "native_meeting_id": native_id, "bot_name": f"mock:{scenario}"},
        headers={"x-api-key": S["api_key"]},
    )
    assert code == 201, f"POST /bots mock:{scenario} → {code} {body}"
    return body


def _recv_json(ws, *, timeout: float):
    return json.loads(ws.recv_text(timeout=timeout))


def _is_sealed_status(msg: dict) -> bool:
    """The 0.10 sealed status envelope: meeting OBJECT + payload envelope (meetings.py:291)."""
    return (msg.get("type") == "meeting.status"
            and isinstance(msg.get("meeting"), dict)
            and isinstance(msg.get("payload"), dict)
            and "status" in msg["payload"])


def _is_flat_status(msg: dict) -> bool:
    """The 0.12 user-channel FLAT frame: meeting_id/native/status/when, no payload envelope."""
    return (msg.get("type") == "meeting.status"
            and "payload" not in msg
            and ("meeting_id" in msg or "status" in msg))


# ── 1 · transcript stream over the 0.10 X-API-Key HEADER handshake ────────────────────────────────

def test_20_ws_transcript_stream_xapikey_header(stack):
    """Header-authed /ws + subscribe on a LIVE meeting the client owns (a running mock bot),
    then a real segment through the collector (XADD to `transcription_segments`, the bot's
    producer path) → the live transcript frame reaches the 0.10 client."""
    api_key = _api_key(stack)
    platform, native_id = "google_meet", f"v010-txws-{uuid.uuid4().hex[:8]}"
    _spawn_mock(stack, "immediate-stop", native_id)
    live = _wait_status(stack, native_id, {"active", "joining", "awaiting_admission"}, timeout=90)
    assert live, f"mock never went live for {native_id}"
    meeting_id = live["id"]

    with WS(f"{_ws_base(stack)}/ws", timeout=15, headers={"X-API-Key": api_key}) as ws:
        ws.send_text(json.dumps({"action": "subscribe",
                                 "meetings": [{"platform": platform, "native_id": native_id}]}))
        # 0.12 auto-subscribes the user channel → tolerate unsolicited frames while waiting
        # for the ack (their EXISTENCE is asserted as a break in test_22, not here).
        ack = None
        deadline = time.time() + 15
        while time.time() < deadline:
            msg = _recv_json(ws, timeout=10)
            if msg.get("type") == "subscribed":
                ack = msg
                break
        assert ack and ack.get("meetings"), f"no `subscribed` ack for {native_id}: {ack}"

        seg_id = f"v010-seg-{uuid.uuid4().hex[:8]}"
        stack.redis_cli("XADD", "transcription_segments", "*", "payload", json.dumps({
            "type": "transcription", "meeting_id": meeting_id,
            "segments": [{"segment_id": seg_id, "start": 90.0, "end": 92.0,
                          "text": "v010 compat live segment", "language": "en",
                          "speaker": "Compat", "completed": True}],
        }))

        frame = None
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                msg = _recv_json(ws, timeout=5)
            except TimeoutError:
                continue
            if msg.get("type") == "transcript" and msg.get("meeting", {}).get("id") == meeting_id:
                frame = msg
                break
        assert frame, "no live transcript frame reached the header-authed 0.10 /ws client"
        texts = [s.get("text") for s in frame.get("confirmed", []) + frame.get("segments", [])]
        assert "v010 compat live segment" in texts, f"segment text missing from frame: {frame}"

    # Clean up the held slot (the mock stays live until told to leave).
    http("DELETE", f"{stack.gateway}/bots/google_meet/{native_id}", headers={"x-api-key": api_key})
    _wait_status(stack, native_id, TERMINAL, timeout=90)
    print(f"\n[compat/ws] X-API-Key header handshake + subscribe + live transcript frame — 0.10 protocol intact")


# ── 2 · status updates on the subscribed meeting (the sealed 0.10 envelope) ───────────────────────

def test_21_ws_status_updates_sealed_envelope(stack):
    """Subscribe to a live meeting, then stop it → the 0.10 client receives the status update
    as the SEALED envelope on the per-meeting channel (`bm:meeting:{id}:status`):
    `{type:"meeting.status", meeting:{id,platform,native_id}, payload:{status}, user_id, ts}`."""
    api_key = _api_key(stack)
    native_id = f"v010-ws-{uuid.uuid4().hex[:8]}"
    _spawn_mock(stack, "immediate-stop", native_id)
    m = _wait_status(stack, native_id, {"active", "joining", "awaiting_admission"}, timeout=90)
    assert m, f"mock never went live for {native_id}"

    with WS(f"{_ws_base(stack)}/ws?api_key={api_key}", timeout=15) as ws:  # query-param auth leg
        ws.send_text(json.dumps({"action": "subscribe",
                                 "meetings": [{"platform": "google_meet", "native_id": native_id}]}))
        deadline = time.time() + 15
        ack = None
        while time.time() < deadline:
            msg = _recv_json(ws, timeout=10)
            if msg.get("type") == "subscribed":
                ack = msg
                break
        assert ack and ack.get("meetings"), f"subscribe ack missing: {ack}"

        # Drive a real transition: user-stop → the bot leaves → terminal status published.
        code, _ = http("DELETE", f"{stack.gateway}/bots/google_meet/{native_id}",
                       headers={"x-api-key": api_key})
        assert 200 <= code < 300

        sealed = None
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                msg = _recv_json(ws, timeout=5)
            except TimeoutError:
                continue
            if _is_sealed_status(msg) and msg["meeting"].get("id") == m["id"]:
                sealed = msg
                break
        assert sealed, "no sealed meeting.status envelope reached the subscribed 0.10 client"
        assert sealed["meeting"].get("platform") == "google_meet"
        assert sealed["meeting"].get("native_id") == native_id
        assert "user_id" in sealed and "ts" in sealed, f"envelope fields missing: {sealed}"
    _wait_status(stack, native_id, TERMINAL, timeout=90)
    print(f"\n[compat/ws] sealed meeting.status envelope on the subscribed channel — 0.10 shape intact "
          f"(status={sealed['payload'].get('status')})")


# ── 3 · no unsolicited frames (the 0.10 quiet-socket contract) ────────────────────────────────────

@pytest.mark.xfail(strict=True, reason=(
    "V010-BREAK: the /ws user-channel auto-subscribe (u:{user_id}:meetings, Track G) emits flat "
    "`meeting.status` frames — {type, meeting_id, native, status, when}, WITHOUT the sealed payload "
    "envelope — unsolicited, for ALL the user's meetings. 0.10 sockets were silent until an explicit "
    "subscribe and only ever received the sealed {meeting:{...}, payload:{...}} envelope."))
def test_22_ws_no_unsolicited_frames(stack):
    """0.10: a connected-but-not-subscribed /ws socket receives NOTHING — every frame follows
    an explicit subscribe. Spawn a bot WITHOUT subscribing and assert the socket stays silent."""
    api_key = _api_key(stack)
    native_id = f"v010-quiet-{uuid.uuid4().hex[:8]}"

    with WS(f"{_ws_base(stack)}/ws", timeout=15, headers={"X-API-Key": api_key}) as ws:
        _spawn_mock(stack, "immediate-stop", native_id)
        m = _wait_status(stack, native_id, {"active", "joining", "awaiting_admission"}, timeout=90)
        assert m, f"mock never went live for {native_id}"
        # 0.12 tears the booting workload down on DELETE + the bot leaves → more transitions.
        http("DELETE", f"{stack.gateway}/bots/google_meet/{native_id}",
             headers={"x-api-key": api_key})

        unsolicited = []
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                unsolicited.append(_recv_json(ws, timeout=5))
            except (TimeoutError, ConnectionError):
                continue
            if unsolicited:
                break  # one unsolicited frame is already the divergence — no need to sit out the window
        _wait_status(stack, native_id, TERMINAL, timeout=90)
        assert not unsolicited, (
            f"V010-BREAK: the /ws user-channel auto-subscribe emits flat `meeting.status` frames "
            f"WITHOUT the sealed payload envelope, unsolicited, for ALL the user's meetings — a "
            f"0.10 client that never subscribed received {len(unsolicited)} frame(s), e.g. "
            f"{unsolicited[0]} (flat={_is_flat_status(unsolicited[0])}). 0.10 sockets were silent "
            f"until an explicit subscribe."
        )

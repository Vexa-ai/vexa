"""O-API-1 (ws.v1) — behavioral conformance of the sealed ``/ws`` live multiplex.

Replays the real ``/ws`` protocol against the unit harness (FakeWebSocket + FakeRedis +
FakeAuthorizer), OFFLINE — no real WebSocket, no real redis, no meetings. Every emitted /
forwarded frame is validated BY PATH against its sealed ``ws.v1 #/$defs/<Shape>``:

  * subscribe        → a ``Subscribed`` ack frame conforms,
  * forwarded redis  → ``Transcript``/``TranscriptionSegment`` / ``MeetingStatus`` / ``ChatMessage`` data frames
                       (forwarded raw from tc/bm/va channels) conform,
  * malformed input  → an ``Error`` frame (invalid_json / unknown_action / missing_api_key)
                       conforms.
"""
from __future__ import annotations

import json

import pytest

from gateway_conformance.contracts import assert_ws_conforms, ws_def_validator
from gateway_conformance.ws_harness import (
    FakeAuthorizer,
    FakeRedis,
    FakeWebSocket,
    WSMultiplexHarness,
)

# (platform, native_meeting_id) → backing meeting_id + user_id, as the authorizer would map.
AUTH_MAP = {("google_meet", "g5-ws-gate-room"): {"meeting_id": 42, "user_id": 7}}
API_KEY = "vxa_test_conformance_key"


SUBSCRIBE = {"action": "subscribe",
             "meetings": [{"platform": "google_meet", "native_id": "g5-ws-gate-room"}]}


def _harness(inbound, api_key=API_KEY, close_when_drained=True):
    ws = FakeWebSocket(inbound=[json.dumps(m) if isinstance(m, dict) else m for m in inbound],
                       api_key=api_key, close_when_drained=close_when_drained)
    return ws, WSMultiplexHarness(ws, FakeRedis(), FakeAuthorizer(AUTH_MAP))


async def _subscribe_then_deliver(channel: str, payload: dict):
    """subscribe → keep the loop live → publish a redis payload on `channel` → forward →
    disconnect. Returns the captured frames. Mirrors the live fan-in path.

    `h.deliver` blocks on an explicit subscription-ready signal before publishing and on a
    frame-forwarded signal after, so there is no sleep(0) guesswork for the fan_in task to
    register — the delivered payload can never be dropped before the subscriber exists."""
    import asyncio

    ws, h = _harness([SUBSCRIBE], close_when_drained=False)
    task = asyncio.ensure_future(h.run())
    await h.deliver(channel, payload)
    ws.disconnect()
    await task
    return ws.sent


async def test_subscribe_acks_with_subscribed_frame():
    """subscribe → a `subscribed` ack frame conforming to ws.v1 #/$defs/Subscribed."""
    ws, h = _harness([
        {"action": "subscribe",
         "meetings": [{"platform": "google_meet", "native_id": "g5-ws-gate-room"}]},
    ])
    await h.run()
    acks = [f for f in ws.sent if f.get("type") == "subscribed"]
    assert acks, f"no subscribed ack; frames={ws.sent}"
    assert_ws_conforms("Subscribed", acks[0])
    assert acks[0]["meetings"] == [{"platform": "google_meet", "native_id": "g5-ws-gate-room"}]


async def test_forwarded_transcription_segment_conforms():
    """A payload PUBLISHed to tc:meeting:{id}:mutable is forwarded raw → conforms to
    ws.v1 #/$defs/TranscriptionSegment."""
    frames = await _subscribe_then_deliver("tc:meeting:42:mutable", {
        "type": "transcription_segment",
        "speaker": "Alice",
        "text": "Hello from the conformance harness",
        "start_time": "2026-06-21T10:00:00Z",
        "end_time": 12.5,
        "language": "en",
        "completed": True,
        "segment_id": "ch-0:1:abc",
    })
    seg = [f for f in frames if f.get("type") == "transcription_segment"]
    assert seg, f"no segment frame forwarded; frames={frames}"
    assert_ws_conforms("TranscriptionSegment", seg[0])


async def test_forwarded_meeting_status_conforms():
    """A payload on bm:meeting:{id}:status → conforms to ws.v1 #/$defs/MeetingStatus (0.10.6 shape)."""
    frames = await _subscribe_then_deliver(
        "bm:meeting:42:status",
        {"type": "meeting.status",
         "meeting": {"id": 42, "platform": "google_meet", "native_id": "abc-defg-hij"},
         "payload": {"status": "active"}, "user_id": 7, "ts": "2026-03-27T10:00:00Z"})
    ms = [f for f in frames if f.get("type") == "meeting.status"]
    assert ms, f"no meeting.status frame; frames={frames}"
    assert_ws_conforms("MeetingStatus", ms[0])


async def test_forwarded_chat_message_conforms():
    """A payload on va:meeting:{id}:chat → conforms to ws.v1 #/$defs/ChatMessage."""
    frames = await _subscribe_then_deliver(
        "va:meeting:42:chat", {"type": "chat_message", "sender": "Bot", "text": "Summary ready"})
    cm = [f for f in frames if f.get("type") == "chat_message"]
    assert cm, f"no chat_message frame; frames={frames}"
    assert_ws_conforms("ChatMessage", cm[0])


async def test_unsubscribe_acks_and_stops_fanin():
    """unsubscribe → an `unsubscribed` ack conforming to ws.v1 #/$defs/Unsubscribed AND the
    fan-in STOPS: a payload published after the unsubscribe is NOT forwarded. The sealed
    Unsubscribed frame is proven as RUNTIME BEHAVIOR, not just a static golden. Mirrors
    main.websocket_multiplex unsubscribe (main.py:2286-2325)."""
    import asyncio

    ws, h = _harness([SUBSCRIBE], close_when_drained=False)
    task = asyncio.ensure_future(h.run())

    # while subscribed, a delivered frame is forwarded (deliver handshakes on the
    # subscription-ready + frame-forwarded signals — no sleep(0) race).
    await h.deliver("tc:meeting:42:mutable", {
        "type": "transcription_segment", "speaker": "Alice", "text": "before unsubscribe",
        "start_time": "2026-06-21T10:00:00Z", "end_time": 1.0, "language": "en",
        "completed": True, "segment_id": "ch-0:1:a"})
    assert [f for f in ws.sent if f.get("type") == "transcription_segment"], \
        f"frame not forwarded while subscribed; {ws.sent}"

    # unsubscribe (inject mid-stream, after the first delivery) and wait for the ack frame
    # deterministically — the control loop sends `unsubscribed` only after cancelling fan_in.
    sent_before_unsub = len(ws.sent)
    ws._inbound.put_nowait(json.dumps({
        "action": "unsubscribe",
        "meetings": [{"platform": "google_meet", "native_id": "g5-ws-gate-room"}]}))
    await ws.wait_for_sent(sent_before_unsub + 1)
    acks = [f for f in ws.sent if f.get("type") == "unsubscribed"]
    assert acks, f"no unsubscribed ack; frames={ws.sent}"
    assert_ws_conforms("Unsubscribed", acks[0])
    assert acks[0]["meetings"] == [{"platform": "google_meet", "native_id": "g5-ws-gate-room"}]

    # fan-in STOPPED: with the subscription cancelled there is no subscriber, so a payload
    # published now must NOT be forwarded (expect_forward=False — just publish + drop).
    await h.deliver("tc:meeting:42:mutable", {
        "type": "transcription_segment", "speaker": "Alice", "text": "after unsubscribe",
        "start_time": "2026-06-21T10:00:01Z", "end_time": 2.0, "language": "en",
        "completed": True, "segment_id": "ch-0:1:b"}, expect_forward=False)
    assert not [f for f in ws.sent if f.get("type") == "transcription_segment"
                and f.get("text") == "after unsubscribe"], \
        f"frame forwarded AFTER unsubscribe — fan-in did not stop; {ws.sent}"

    ws.disconnect()
    await task


async def test_unsubscribe_unknown_meeting_yields_error():
    """unsubscribe for a meeting that was never subscribed → invalid_unsubscribe_payload Error
    (errors and no unsubscribed) conforming to ws.v1 #/$defs/Error (main.py:2318-2320)."""
    ws, h = _harness([{"action": "unsubscribe",
                       "meetings": [{"platform": "zoom", "native_id": "never-subscribed"}]}])
    await h.run()
    errs = [f for f in ws.sent if f.get("type") == "error"]
    assert errs and errs[0]["error"] == "invalid_unsubscribe_payload", f"frames={ws.sent}"
    assert_ws_conforms("Error", errs[0])


async def test_invalid_json_yields_error_frame():
    """Malformed input (not JSON) → an Error frame conforming to ws.v1 #/$defs/Error."""
    ws, h = _harness(["this-is-not-json{"])
    await h.run()
    errs = [f for f in ws.sent if f.get("type") == "error"]
    assert errs, f"no error frame; frames={ws.sent}"
    assert errs[0]["error"] == "invalid_json"
    assert_ws_conforms("Error", errs[0])


async def test_unknown_action_yields_error_frame():
    """An unknown action → Error frame conforming to ws.v1 #/$defs/Error."""
    ws, h = _harness([{"action": "frobnicate"}])
    await h.run()
    errs = [f for f in ws.sent if f.get("type") == "error"]
    assert errs and errs[0]["error"] == "unknown_action"
    assert_ws_conforms("Error", errs[0])


async def test_invalid_subscribe_payload_yields_error_with_details():
    """subscribe with non-list meetings → Error frame (with `details`) conforms."""
    ws, h = _harness([{"action": "subscribe", "meetings": "not-a-list"}])
    await h.run()
    errs = [f for f in ws.sent if f.get("type") == "error"]
    assert errs and errs[0]["error"] == "invalid_subscribe_payload"
    assert_ws_conforms("Error", errs[0])


async def test_missing_api_key_yields_error_and_closes():
    """AUTH-NEGATIVE: no x-api-key (header or ?api_key=) → missing_api_key Error frame +
    close 4401. Mirrors main.websocket_multiplex:2171-2176."""
    ws = FakeWebSocket(inbound=[], api_key=None)
    h = WSMultiplexHarness(ws, FakeRedis(), FakeAuthorizer(AUTH_MAP))
    await h.run()
    assert ws.sent and ws.sent[0]["error"] == "missing_api_key"
    assert_ws_conforms("Error", ws.sent[0])
    assert ws.close_code == 4401


def test_ws_goldens_conform_by_path():
    """Belt-and-braces: the on-disk ws.v1 goldens conform to their sealed $defs (BY PATH)."""
    from gateway_conformance.contracts import CONTRACTS_DIR

    golden_dir = CONTRACTS_DIR / "ws.v1" / "golden"
    cases = {
        "SubscribeRequest": "SubscribeRequest.subscribe.json",
        "UnsubscribeRequest": "UnsubscribeRequest.unsubscribe.json",
        "Subscribed": "Subscribed.ack.json",
        "TranscriptionSegment": "TranscriptionSegment.live.json",
        "Transcript": "Transcript.bundle.json",
        "MeetingStatus": "MeetingStatus.active.json",
        "ChatMessage": "ChatMessage.summary.json",
        "Error": "Error.missing-key.json",
    }
    for shape, fname in cases.items():
        data = json.loads((golden_dir / fname).read_text())
        assert_ws_conforms(shape, data)


def test_subscribe_request_schema_is_loadable():
    """The harness can load every ws.v1 $def it asserts against (by path)."""
    for shape in ("Subscribed", "TranscriptionSegment", "Transcript", "MeetingStatus", "ChatMessage", "Error"):
        assert ws_def_validator(shape) is not None

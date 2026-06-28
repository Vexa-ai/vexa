"""L3 SEAM — intent → publish → forward, end-to-end over IN-MEMORY FAKES (no docker, no DB, no live redis).

Flow proven (the user-scoped meeting.status WS feature):
  1. Drive the REAL meeting-api intent endpoint (PUT /meetings/{platform}/{native}/intent) over its
     in-memory store, with a SHARED FakeRedis bus injected as its publisher.
  2. meeting-api publishes the flat meeting.status frame to `u:{user_id}:meetings` on that bus.
  3. The REAL gateway `_run_multiplex` (same bus injected) auto-subscribes `u:{user_id}:meetings` and its
     verbatim `fan_in` forwards the raw payload to the socket.
  4. A faked socket receives the frame UNCHANGED, and it conforms to the ws.v1 golden.

The producer and consumer are the SHIPPED handlers; only their injected ports are fakes. This pins the
seam between the two services without standing up either one for real.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import pytest
from starlette.websockets import WebSocketDisconnect

# REAL producer + its in-memory store
from meeting_api.collector import create_app as create_meeting_app
from meeting_api.collector.fakes import InMemoryTranscriptStore
from fastapi.testclient import TestClient

# REAL consumer forward path + the gateway's own injected fakes (reused verbatim)
from gateway.app import _run_multiplex
from gateway_fakes import FakeAuthorizer, FakeRedis  # re-export of gateway/services/gateway/tests/conftest.py

USER = 7
PLAT, NID = "google_meet", "abc-defg-hij"
AT = "2026-06-25T18:00:00Z"
API_KEY = "vxa_test_unit_key"


# --- ws.v1 golden (the SHARED contract both sides pin to) -------------------------------

def _ws_golden() -> dict:
    rel = Path("gateway") / "contracts" / "ws.v1" / "golden" / "MeetingStatus.scheduled.json"
    for parent in Path(__file__).resolve().parents:
        if (parent / rel).is_file():
            return json.loads((parent / rel).read_text())
    raise FileNotFoundError("ws.v1 golden MeetingStatus.scheduled.json not found")


_WS_FLAT_KEYS = {"type", "meeting_id", "native", "status", "when"}


# --- a minimal fake socket for the gateway forward path (mirrors test_multiplex._WS) -----

class _FakeWS:
    def __init__(self, api_key: Optional[str] = API_KEY):
        self.sent: list = []
        self.headers = {"x-api-key": api_key} if api_key else {}
        self.query_params: dict[str, str] = {}
        self.close_code: Optional[int] = None
        self._gone = asyncio.Event()

    async def accept(self) -> None:
        pass

    async def send_text(self, data: str) -> None:
        self.sent.append(data)  # keep RAW so we can prove the payload is forwarded UNCHANGED

    async def receive_text(self) -> str:
        await self._gone.wait()
        raise WebSocketDisconnect(code=1000)

    async def close(self, code: int = 1000) -> None:
        self.close_code = code

    def disconnect(self) -> None:
        self._gone.set()


async def test_intent_publish_forward_seam_golden_conforming():
    golden = _ws_golden()

    # ONE shared in-memory bus links the producer's publish to the consumer's fan_in.
    # Wrap publish() to record the RAW payload so we can prove the forward is byte-for-byte UNCHANGED.
    bus = FakeRedis()
    published: list[tuple[str, str]] = []
    _orig_publish = bus.publish

    async def _recording_publish(channel: str, data: str):
        published.append((channel, data))
        return await _orig_publish(channel, data)

    bus.publish = _recording_publish  # type: ignore[method-assign]
    auth = FakeAuthorizer(user={"user_id": USER, "scopes": ["bot"], "max_concurrent": 3,
                                "email": "u@example.com"},
                          valid_key=API_KEY)

    # ── consumer up FIRST: gateway auto-subscribes u:{USER}:meetings on the shared bus ──
    ws = _FakeWS()
    gw_task = asyncio.ensure_future(_run_multiplex(ws, auth, bus))
    for _ in range(10):
        await asyncio.sleep(0)  # let connect() resolve + fan_in subscribe

    # ── producer: drive the REAL intent endpoint; it publishes onto the SAME bus ──
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NID, status="idle")
    client = TestClient(create_meeting_app(store, bus))
    r = client.put(f"/meetings/{PLAT}/{NID}/intent",
                   json={"intent": "scheduled", "at": AT},
                   headers={"x-user-id": str(USER)})
    assert r.status_code == 200, r.text

    # ── let the forward path deliver ──
    for _ in range(10):
        await asyncio.sleep(0)

    # the producer published exactly one frame to the user channel
    user_pubs = [raw for ch, raw in published if ch == f"u:{USER}:meetings"]
    assert len(user_pubs) == 1, published

    # the socket received exactly one meeting.status frame, forwarded VERBATIM (byte-for-byte) ──
    raw_sent = [d for d in ws.sent if isinstance(d, str) and '"meeting.status"' in d]
    assert len(raw_sent) == 1, ws.sent
    assert raw_sent[0] == user_pubs[0], "gateway must forward the producer's payload UNCHANGED"
    forwarded = json.loads(raw_sent[0])

    # golden-conforming: same type, exact flat key set, matching per-field types
    assert forwarded["type"] == golden["type"] == "meeting.status"
    assert set(forwarded.keys()) == _WS_FLAT_KEYS
    golden_flat = {k: golden[k] for k in _WS_FLAT_KEYS}
    for k in _WS_FLAT_KEYS:
        assert type(forwarded[k]) is type(golden_flat[k]), f"field {k!r} type drifted from ws.v1 golden"

    # the concrete intent change surfaced end-to-end
    assert forwarded["status"] == "scheduled"
    assert forwarded["native"] == NID
    assert forwarded["when"] == AT
    assert forwarded["meeting_id"] == mid

    ws.disconnect()
    await gw_task

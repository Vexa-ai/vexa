"""Fidelity pins for the lite E2E rig's two instruments (deploy/lite/tests/e2e — WP-M9 C).

The rig's whole value is that everything DOWNSTREAM of its two synthetic files is production
machinery; that only holds while the instruments themselves stay on-contract. These tests load
the instruments BY PATH (they are deploy scripts, not a package) and drive them against the SAME
shipped validators/clients the engine uses in the rig:

  * the scripted bot's lifecycle events validate against sealed ``lifecycle.v1`` — an
    off-contract event would be 422'd by the receiver and the rig would time out mysteriously;
  * the scripted bot's segment envelope round-trips through the REAL collector ``ingest``;
  * the stub's chat completion satisfies the REAL ``openai_chat_llm`` client the summarizer uses;
  * the stub's callback ACK satisfies the REAL outbox drain's acknowledgement predicate — a bare
    200 would be treated as non-delivery and wedge every capture short of settlement.
"""
from __future__ import annotations

import importlib.util
import json
import re
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import fakeredis.aioredis
import pytest

from meeting_api.collector import ingest
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore
from meeting_api.collector.summarizer import openai_chat_llm
from meeting_api.lifecycle.receiver import conforms
from meeting_api.zaki_control.callbacks import ControlCallbackDispatcher
from meeting_api.zaki_control.fakes import InMemoryControlStore
from meeting_api.zaki_control.ports import CallbackEvent, Capture, Subject


def _rig_file(name: str) -> Path:
    """Locate a rig file by path (deploy/lite/tests/e2e is not a package)."""
    rel = Path("deploy") / "lite" / "tests" / "e2e" / name
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"e2e rig file not found by path: {rel}")


def _load(name: str):
    candidate = _rig_file(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"zaki_e2e_{name}", candidate)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rig_summary_token() -> str:
    """The token run_e2e.sh's managed .env block hands the summarizer — pinned, not a lookalike.

    openai_chat_llm sends Authorization unconditionally; an empty token yields the header
    value b'Bearer ' which httpx refuses (LocalProtocolError) on every tick, so the rig's
    [summary] stage is red while a pin using its own token value stays green. Reading the
    value out of the script keeps the pin and the live wiring on one axis.
    """
    text = _rig_file("run_e2e.sh").read_text(encoding="utf-8")
    match = re.search(r"^SUMMARY_SERVICE_TOKEN=(\S+)$", text, re.MULTILINE)
    assert match, (
        "run_e2e.sh no longer writes a non-empty SUMMARY_SERVICE_TOKEN into its managed "
        ".env block — the summarizer client cannot authorize with an empty token"
    )
    return match.group(1)


scripted_bot = _load("scripted_bot")
stub_llm = _load("stub_llm")


@pytest.fixture
def stub_server():
    """The REAL stub handler on an ephemeral loopback port, exactly as the rig runs it."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), stub_llm.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join(timeout=5)
    stub_llm._EVENTS.clear()


def test_scripted_bot_lifecycle_events_conform_to_the_sealed_contract():
    # The exact body scripted_bot._post_lifecycle serializes, for every status it can emit.
    for status in ("joining", "awaiting_admission", "active", "completed"):
        conforms(
            {
                "connection_id": "e2e-conn-1",
                "status": status,
                "timestamp": scripted_bot._now_iso(),
            },
            "LifecycleEvent",
        )


async def test_scripted_bot_segment_envelope_drives_the_real_collector_ingest():
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=7, platform="google_meet", native_meeting_id="zaki-e2e-native")
    client = fakeredis.aioredis.FakeRedis()
    bus = FakeRedisBus(client)
    cfg = {"meeting_id": 1, "nativeMeetingId": "zaki-e2e-native", "platform": "google_meet"}

    envelope = scripted_bot._segment_envelope(cfg, 0)
    persisted = await ingest(store, bus, {"payload": json.dumps(envelope)})

    assert persisted == 1
    doc = await store.get_transcript(7, "google_meet", "zaki-e2e-native")
    assert doc["segments"][0]["speaker"] == "Alice Example"
    assert doc["segments"][0]["text"].startswith("Synthetic segment 0")
    # The read plane's turn projection needs aware absolute times — pin the format here.
    assert datetime.fromisoformat(doc["segments"][0]["absolute_start_time"]).tzinfo is not None
    await client.aclose()


async def test_stub_completion_satisfies_the_real_summarizer_client(stub_server):
    llm = openai_chat_llm(stub_server, _rig_summary_token(), "zaki-e2e-stub", timeout_s=10.0)
    text = await llm([{"role": "user", "content": "Transcript:\n\nAlice: hi\n\nWrite the minutes."}])
    assert "## TL;DR" in text


async def test_stub_callback_ack_satisfies_the_real_outbox_drain(stub_server):
    store = InMemoryControlStore()
    subject = Subject("e2e-tenant", "42")
    capture = Capture(
        capture_id="capture-e2e", subject=subject, operation_id="op-e2e",
        reservation_id="resv-e2e", platform="google_meet",
        native_meeting_id="zaki-e2e-native", meeting_id="1", state="requested",
    )
    await store.create_capture(capture)
    event = CallbackEvent(
        event_id="status-capture-e2e-joining",
        body={
            "event_id": "status-capture-e2e-joining",
            "event_type": "minutes.capture.status",
            "api_version": "zaki-control.v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "data": {"capture_id": "capture-e2e", "state": "joining"},
        },
        subject=subject, capture_id="capture-e2e", terminal=False,
    )
    await store.record_capture_transition(
        capture=capture, state="joining", failure_code=None, events=(event,)
    )
    dispatcher = ControlCallbackDispatcher(
        store,
        callback_url=f"{stub_server}/api/minutes/callback/v1",
        hmac_key="e2e-callback-hmac-key-0123456789abcdef00",
    )

    delivered = await dispatcher.drain_once()

    assert delivered == 1, "the stub's CallbackAck must satisfy the drain's acknowledgement predicate"
    assert not await store.pending_callbacks(limit=10)
    recorded = stub_llm._EVENTS
    assert [e.get("event_id") for e in recorded] == ["status-capture-e2e-joining"]

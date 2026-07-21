"""The LIVE half of STT-fault reporting (#552): a refusing backend must say WHY while the meeting
is still running, not only on the bot's terminal event (#836).

Before this, a meeting whose STT backend refused every window sat at ``status=active`` with an
empty transcript and no user-visible reason for its whole duration — the #807 shape, observed live
on 2026-07-20 as a 402 "Insufficient balance" storm that produced zero segments in silence.

The collector forwards the bot's ``stt_fault`` envelope onto the row-keyed transcript stream it
single-writes (P23), which is the same pipe the segments ride and the one the terminal SSE reads.
"""
import json

import pytest

from meeting_api.collector.ingest import ingest


class FakeRedis:
    """Records xadds; the collector's RedisBus stores the dict under the ``payload`` field."""

    def __init__(self):
        self.adds = []

    async def xadd(self, stream, payload):
        self.adds.append((stream, payload))
        return "1-1"

    async def publish(self, channel, data):  # pragma: no cover - unused here
        return None


class ExplodingRedis(FakeRedis):
    async def xadd(self, stream, payload):
        raise RuntimeError("redis is down")


def _fault(**over):
    body = {
        "type": "stt_fault", "meeting_id": 42, "native_meeting_id": "abc-defg-hij",
        "kind": "payment_required", "status": 402,
        "detail": "Insufficient balance. Available: 0.00 minutes", "count": 1,
    }
    body.update(over)
    return json.dumps(body)


@pytest.mark.asyncio
async def test_stt_fault_reaches_the_live_transcript_stream():
    """A1 — the fault is forwarded onto tc:meeting:{row}, carrying the backend's OWN words."""
    r = FakeRedis()
    n = await ingest(None, r, {"payload": _fault()})

    assert n == 0, "a fault persists no segments"
    assert len(r.adds) == 1, f"expected exactly one forward, got {r.adds}"
    stream, payload = r.adds[0]
    assert "42" in stream, f"must key on the numeric ROW id (cross-tenant safe), got {stream}"
    assert payload["type"] == "stt_fault"
    assert payload["kind"] == "payment_required"
    assert payload["status"] == "402"
    assert "Insufficient balance" in payload["detail"], "the backend's own words, not a paraphrase"


@pytest.mark.asyncio
async def test_detail_is_bounded():
    """A2 — an adversarial backend cannot push an unbounded string onto the live stream."""
    r = FakeRedis()
    await ingest(None, r, {"payload": _fault(detail="x" * 5000)})
    assert len(r.adds[0][1]["detail"]) == 300


@pytest.mark.asyncio
async def test_no_numeric_row_id_is_skipped_not_crashed():
    """A3 — an older bot that sent only a native id has no row to key on; skip, never raise."""
    r = FakeRedis()
    n = await ingest(None, r, {"payload": _fault(meeting_id=None)})
    assert n == 0
    assert r.adds == [], "no row id → nothing forwarded"


@pytest.mark.asyncio
async def test_a_failing_forward_never_aborts_the_batch():
    """A4 — the live leg is best-effort: redis being down must not break ingestion."""
    n = await ingest(None, ExplodingRedis(), {"payload": _fault()})
    assert n == 0, "a redis failure on the live leg is swallowed, not raised"

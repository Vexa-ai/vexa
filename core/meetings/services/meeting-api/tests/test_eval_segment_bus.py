"""The eval tools that write ``transcription_segments`` sign their entries the way a bot does.

``core/meetings/eval/src/segment_bus.py`` publishes fixture segments for the counting tools: it mints
a MeetingToken for the meeting it publishes to and signs each entry with the collector's own
``signed_entry``, so the collector admits them. Offline: fakeredis stands in for redis, and the real
collector (``consume_segments``) drains between the tool's polls. ``counting_matrix.run_one`` runs end
to end, with its ``docker exec`` replaced by running the same script in this process.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import fakeredis
import pytest

from meeting_api.collector import consume_segments
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore
from meeting_api.collector.ingest import _admitted

from _segment_auth import admin_token  # noqa: F401

EVAL = Path(__file__).resolve().parents[3] / "eval" / "src"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"eval_{name}", EVAL / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server(monkeypatch):
    """One fake redis server; every ``redis.from_url`` the tools open lands on it."""
    import redis

    srv = fakeredis.FakeServer()
    monkeypatch.setattr(redis, "from_url", lambda *a, **k: fakeredis.FakeRedis(server=srv, decode_responses=True))
    return srv


def _collect(srv) -> int:
    """One collector drain over the shared server, as meeting-api runs it."""
    async def run():
        client = fakeredis.aioredis.FakeRedis(server=srv, decode_responses=True)
        try:
            return await consume_segments(InMemoryTranscriptStore(), FakeRedisBus(client), count=100)
        finally:
            await client.aclose()
    return asyncio.run(run())


def _segment(i: int) -> dict:
    return {"segment_id": f"s{i}", "speaker": "A", "text": f"number {i}", "start": float(i),
            "end": float(i) + 0.5, "completed": True}


def test_published_entries_are_admitted_for_their_meeting_only(server, admin_token):
    bus = _load("segment_bus")
    lines = [json.dumps({"type": "transcription", "meeting_id": 900002, "native_meeting_id": "mtx-a",
                         "segments": [_segment(i)]}) for i in range(1, 4)]
    assert bus.publish(lines, 900002, "mtx-a") == 3
    raw = fakeredis.FakeRedis(server=server, decode_responses=True).xrange(bus.STREAM)
    assert len(raw) == 3 and all(_admitted(fields) for _id, fields in raw)
    assert _collect(server) == 3
    assert [s["text"] for s in bus.read_feed(900002, settle_s=0)] == ["number 1", "number 2", "number 3"]
    # a line naming another meeting, signed for 900002, is not admitted
    bus.publish([json.dumps({"type": "transcription", "meeting_id": 900003, "segments": [_segment(9)]})],
                900002, "mtx-a")
    assert _collect(server) == 0
    assert bus.read_feed(900003, settle_s=0, tries=1) == []


def test_counting_matrix_runs_end_to_end(server, admin_token, monkeypatch, tmp_path):
    """The matrix's own script, run where ``docker exec`` would run it; the collector drains whenever
    the script waits."""
    matrix = _load("counting_matrix")
    bus = sys.modules["segment_bus"]
    fx = tmp_path / "count-silence-1to3"
    fx.mkdir()
    (fx / "3-segments.jsonl").write_text("\n".join(json.dumps(_segment(i)) for i in range(1, 4)) + "\n")
    (fx / "truth.jsonl").write_text(json.dumps({"numbers": [1, 2, 3]}) + "\n")

    monkeypatch.setattr(bus.time, "sleep", lambda _s: _collect(server))

    def fake_run(argv, *, input, text, capture_output):
        assert argv[:3] == ["docker", "exec", "-i"] and argv[-2] == "-c"
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.StringIO(input))
        with contextlib.redirect_stdout(out):
            exec(compile(argv[-1], "<docker exec>", "exec"), {"__name__": "__main__"})
        return subprocess.CompletedProcess(argv, 0, out.getvalue(), "")

    monkeypatch.setattr(matrix.subprocess, "run", fake_run)
    result = matrix.run_one(fx)
    assert result["native_segments"] == 3
    assert result["downstream_lossless"] is True and result["speakers_present"] is True

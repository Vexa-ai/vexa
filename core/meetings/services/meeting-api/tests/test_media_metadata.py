import subprocess
import shutil
from unittest import SkipTest
from meeting_api.recordings.media_metadata import seekable_webm


def require_media_tools():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SkipTest("Requires ffmpeg + ffprobe; also run inside the shipped meeting-api image")


def test_streaming_webm_gets_finite_duration(tmp_path):
    require_media_tools()
    # Pipe output has no final duration header, like browser MediaRecorder chunks.
    raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                          "sine=frequency=440:duration=2", "-c:a", "libopus",
                          "-f", "webm", "pipe:1"], check=True, capture_output=True).stdout
    result, duration = seekable_webm(raw)
    assert 1.9 < duration < 2.2
    assert len(result) > 0
    assert result != raw


def test_existing_master_is_repaired_once_without_chunks():
    require_media_tools()
    import asyncio
    from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage
    from meeting_api.recordings.service import finalize_master

    async def run():
        raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                              "sine=frequency=440:duration=2", "-c:a", "libopus",
                              "-f", "webm", "pipe:1"], check=True, capture_output=True).stdout
        repo, storage = InMemoryRecordingRepo(), InMemoryStorage()
        repo.seed(meeting_id=1, user_id=7, session_uid="fixture")
        key = "recordings/7/100/fixture/audio/master.webm"
        storage.blobs[key] = raw
        repo._meetings[1]["recordings"] = [{"id": 100, "media_files": [{"id": 11,
            "type": "audio", "format": "webm", "storage_path": key}]}]
        await finalize_master(repo, storage, meeting_id=1, recording_id=100)
        media = (await repo.get_recordings(1))[0]["media_files"][0]
        assert media["seekable_version"] == 1
        assert 1.9 < media["duration_seconds"] < 2.2
        assert storage.blobs[key] != raw
        repaired = storage.blobs[key]
        await finalize_master(repo, storage, meeting_id=1, recording_id=100)
        assert storage.blobs[key] == repaired
    asyncio.run(run())


# ── a failure is a typed, logged fault, never a bare RuntimeError ───────────────────────────────

import json as _json

import pytest


def _faults(out: str) -> list[dict]:
    lines = [_json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return [e for e in lines if e.get("event") == "recording_metadata_fault"]


def _scripted(monkeypatch, *outcomes):
    """subprocess.run answers each call with the next outcome: an exception to raise, or stdout."""
    import meeting_api.recordings.media_metadata as mm

    calls = list(outcomes)

    def run(argv, **kw):
        outcome = calls.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return subprocess.CompletedProcess(argv, 0, stdout=outcome, stderr=b"secret ffmpeg chatter")

    monkeypatch.setattr(mm.subprocess, "run", run)


@pytest.mark.parametrize("outcomes, source, kind", [
    ((FileNotFoundError("ffmpeg"),), "ffmpeg", "unavailable"),
    ((subprocess.TimeoutExpired("ffmpeg", 120),), "ffmpeg", "timeout"),
    ((subprocess.CalledProcessError(1, "ffmpeg", stderr=b"secret ffmpeg chatter"),), "ffmpeg", "failed"),
    ((b"", subprocess.CalledProcessError(1, "ffprobe")), "ffprobe", "failed"),
    ((b"", b"not json"), "ffprobe", "invalid_duration"),
    ((b"", b'{"format": {}}'), "ffprobe", "invalid_duration"),
    ((b"", b'{"format": {"duration": "0"}}'), "ffprobe", "invalid_duration"),
    ((b"", b'{"format": {"duration": "nan"}}'), "ffprobe", "invalid_duration"),
])
def test_a_failure_is_a_typed_fault_and_is_logged(monkeypatch, capsys, outcomes, source, kind):
    from meeting_api.recordings.media_metadata import MediaMetadataFault

    _scripted(monkeypatch, *outcomes)
    with pytest.raises(MediaMetadataFault) as ei:
        seekable_webm(b"\x1a\x45\xdf\xa3 recording bytes")
    assert (ei.value.source, ei.value.kind) == (source, kind)
    assert isinstance(ei.value, RuntimeError)  # callers that caught the old RuntimeError still do
    out = capsys.readouterr().out
    faults = _faults(out)
    assert faults and faults[-1]["fields"] == {"source": source, "kind": kind}
    assert "secret ffmpeg chatter" not in out and "recording bytes" not in out
    assert "secret ffmpeg chatter" not in str(ei.value)


def test_the_remux_path_returns_the_new_bytes_and_duration_without_a_fault(monkeypatch, capsys):
    """The same path with the tools answering: no fault, no fault log (runs without ffmpeg)."""
    import meeting_api.recordings.media_metadata as mm

    def run(argv, **kw):
        if argv[0] == "ffmpeg":
            from pathlib import Path

            Path(argv[-1]).write_bytes(b"seekable master")
            return subprocess.CompletedProcess(argv, 0)
        return subprocess.CompletedProcess(argv, 0, stdout=b'{"format": {"duration": "2.0"}}')

    monkeypatch.setattr(mm.subprocess, "run", run)
    assert seekable_webm(b"raw") == (b"seekable master", 2.0)
    assert not _faults(capsys.readouterr().out)

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

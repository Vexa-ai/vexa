"""recordings — ffmpeg-for-all live HLS (fMP4/CMAF) storage + the on-demand combined download.

Drives the SHIPPED ``upload_hls_file`` / ``finalize_hls_download`` / ``build_router`` HLS routes over
the in-memory fakes, OFFLINE (no MinIO, no ffmpeg): a bot's HLS files store verbatim under the
recording's ``hls/`` prefix, the playlist registers an ``hls`` media-file + ``playback_url.hls``, the
serve route returns the stored bytes, and the combined download is gated by ENABLE_COMBINED_RECORDING.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api.recordings import build_router
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage
from meeting_api.recordings.service import (
    _hls_download_key,
    finalize_hls_download,
    upload_hls_file,
)

SECRET = "test-admin-token"
USER = 7
MEETING_ID = 1
SESSION_UID = "conn-abc"


def _seeded():
    repo = InMemoryRecordingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    return repo, InMemoryStorage()


def _client_for(repo, storage):
    app = FastAPI()
    app.include_router(build_router(repo, storage, token_secret=SECRET))
    return TestClient(app)


async def _record_hls(repo, storage, *, final: bool = True):
    """Upload a minimal HLS bundle (init + one segment + playlist). Returns (recording_id, playlist_key)."""
    await upload_hls_file(repo, storage, token_meeting_id=None, session_uid=SESSION_UID,
                          relpath="init.mp4", data=b"INIT")
    await upload_hls_file(repo, storage, token_meeting_id=None, session_uid=SESSION_UID,
                          relpath="chunk-00000.m4s", data=b"CHUNK0")
    receipt = await upload_hls_file(repo, storage, token_meeting_id=None, session_uid=SESSION_UID,
                                    relpath="playlist.m3u8", data=b"#EXTM3U", is_final=final)
    recs = await repo.get_recordings(MEETING_ID)
    rec = next(r for r in recs if r["id"] == receipt["recording_id"])
    hmf = next(m for m in rec["media_files"] if m["type"] == "hls")
    return receipt["recording_id"], hmf["storage_path"]


# ── storage + registration ───────────────────────────────────────────────────────────────────────

async def test_upload_hls_registers_media_file_and_completes():
    repo, storage = _seeded()
    rec_id, playlist_key = await _record_hls(repo, storage, final=True)
    prefix = playlist_key.rsplit("/", 1)[0]
    assert await storage.exists(f"{prefix}/init.mp4")
    assert await storage.exists(f"{prefix}/chunk-00000.m4s")
    assert await storage.get(playlist_key) == b"#EXTM3U"
    recs = await repo.get_recordings(MEETING_ID)
    rec = next(r for r in recs if r["id"] == rec_id)
    hmf = next(m for m in rec["media_files"] if m["type"] == "hls")
    assert hmf["format"] == "m3u8" and hmf["is_final"] is True
    assert rec["playback_url"]["hls"] == f"/recordings/{rec_id}/hls/playlist.m3u8"
    assert rec["status"] == "completed"


async def test_upload_hls_anchors_first_chunk_at_to_started_at():
    # The bot sends the recorder's true t=0 (ffmpeg start) — first_chunk_at must anchor to it, NOT the
    # server receive-time of the first playlist (a whole segment late → transcript-sync drift).
    repo, storage = _seeded()
    started = "2026-08-11T03:39:38.952934Z"
    await upload_hls_file(repo, storage, token_meeting_id=None, session_uid=SESSION_UID,
                          relpath="playlist.m3u8", data=b"#EXTM3U", is_final=True, started_at=started)
    recs = await repo.get_recordings(MEETING_ID)
    hmf = next(m for m in recs[0]["media_files"] if m["type"] == "hls")
    assert hmf["first_chunk_at"] == started


async def test_upload_hls_stamps_has_video():
    # The bot tells the server whether the recording captured video — the player renders a <video> vs an
    # audio-only control off this flag.
    repo, storage = _seeded()
    await upload_hls_file(repo, storage, token_meeting_id=None, session_uid=SESSION_UID,
                          relpath="playlist.m3u8", data=b"#EXTM3U", is_final=True, has_video=True)
    recs = await repo.get_recordings(MEETING_ID)
    hmf = next(m for m in recs[0]["media_files"] if m["type"] == "hls")
    assert hmf["has_video"] is True

    repo2, storage2 = _seeded()
    await upload_hls_file(repo2, storage2, token_meeting_id=None, session_uid=SESSION_UID,
                          relpath="playlist.m3u8", data=b"#EXTM3U", is_final=True, has_video=False)
    recs2 = await repo2.get_recordings(MEETING_ID)
    hmf2 = next(m for m in recs2[0]["media_files"] if m["type"] == "hls")
    assert hmf2["has_video"] is False


async def test_upload_hls_pending_when_session_unknown():
    repo, storage = _seeded()
    receipt = await upload_hls_file(repo, storage, token_meeting_id=None, session_uid="nope",
                                    relpath="playlist.m3u8", data=b"#EXTM3U", is_final=True)
    assert receipt == {"status": "pending"}


# ── serve route ───────────────────────────────────────────────────────────────────────────────────

async def test_hls_serve_route_returns_stored_bytes():
    repo, storage = _seeded()
    rec_id, _ = await _record_hls(repo, storage)
    client = _client_for(repo, storage)
    r = client.get(f"/recordings/{rec_id}/hls/playlist.m3u8", headers={"x-user-id": str(USER)})
    assert r.status_code == 200
    assert r.content == b"#EXTM3U"
    assert r.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    seg = client.get(f"/recordings/{rec_id}/hls/chunk-00000.m4s", headers={"x-user-id": str(USER)})
    assert seg.status_code == 200 and seg.content == b"CHUNK0"


async def test_hls_serve_route_rejects_traversal():
    repo, storage = _seeded()
    rec_id, _ = await _record_hls(repo, storage)
    client = _client_for(repo, storage)
    r = client.get(f"/recordings/{rec_id}/hls/../secret", headers={"x-user-id": str(USER)})
    assert r.status_code in (400, 404)


# ── combined download (ENABLE_COMBINED_RECORDING) ──────────────────────────────────────────────────

async def test_finalize_hls_download_stamps_combined_with_injected_muxer():
    repo, storage = _seeded()
    rec_id, playlist_key = await _record_hls(repo, storage)
    key = await finalize_hls_download(
        repo, storage, meeting_id=MEETING_ID, recording_id=rec_id, muxer=lambda files: b"MP4BYTES"
    )
    assert key == _hls_download_key(playlist_key)
    assert await storage.get(key) == b"MP4BYTES"
    recs = await repo.get_recordings(MEETING_ID)
    rec = next(r for r in recs if r["id"] == rec_id)
    cmf = next(m for m in rec["media_files"] if m["type"] == "combined")
    assert cmf["format"] == "mp4" and cmf["storage_path"] == key
    assert rec["playback_url"]["combined"] == f"/recordings/{rec_id}/master?type=combined"


async def test_combined_download_disabled_by_default_404s(monkeypatch):
    monkeypatch.delenv("ENABLE_COMBINED_RECORDING", raising=False)
    repo, storage = _seeded()
    rec_id, _ = await _record_hls(repo, storage)
    client = _client_for(repo, storage)
    r = client.get(f"/recordings/{rec_id}/master?type=combined", headers={"x-user-id": str(USER)})
    assert r.status_code == 404
    assert r.json()["status"] == "disabled"


async def test_combined_download_click_to_build_then_serves(monkeypatch):
    monkeypatch.setenv("ENABLE_COMBINED_RECORDING", "true")
    repo, storage = _seeded()
    rec_id, playlist_key = await _record_hls(repo, storage)
    client = _client_for(repo, storage)
    # A plain GET only REPORTS status — enabled but not built → "available" (the player shows a button),
    # and must NOT have kicked off a build.
    r1 = client.get(f"/recordings/{rec_id}/master?type=combined", headers={"x-user-id": str(USER)})
    assert r1.status_code == 200 and r1.json()["status"] == "available"
    # The download click sends ?build=1 → "building".
    r2 = client.get(f"/recordings/{rec_id}/master?type=combined&build=1", headers={"x-user-id": str(USER)})
    assert r2.status_code == 202 and r2.json()["status"] == "building"
    # Simulate the remux landing.
    await storage.upload(_hls_download_key(playlist_key), b"MP4BYTES", content_type="video/mp4")
    r3 = client.get(f"/recordings/{rec_id}/master?type=combined", headers={"x-user-id": str(USER)})
    assert r3.status_code == 200
    body = r3.json()
    assert body["media_file_id"] is not None
    assert body["raw_url"] and "type=combined" in body["raw_url"]

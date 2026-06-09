"""Frame extractor — captures visual snapshots from meeting video recordings.

Downloads the master.webm from MinIO, extracts frames at a configurable
rate via ffmpeg, downscales each to 320x180 WebP via Pillow, uploads
thumbnails back to MinIO, and batch-inserts RecordingFrame rows.

Public entry point: extract_frames_if_enabled(meeting_id)
  - Gated by SNAPSHOTS_ENABLED env var (opt-in, default false)
  - Idempotent (triple check: DB rows + first frame file + early return)
  - Isolated failure (try/except in run_all_tasks Task 4)
  - OOM-safe streaming via download_to_file to NamedTemporaryFile
"""

import asyncio
import glob
import io
import logging
import os
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from .database import async_session_local

# Per-meeting lock: prevents two concurrent extraction calls for the same meeting
# from both running ffmpeg (idempotency is guaranteed by DB ON CONFLICT DO NOTHING,
# but the lock avoids wasted CPU from duplicate ffmpeg runs).
_extraction_locks: Dict[int, asyncio.Lock] = {}
_locks_mutex = asyncio.Lock()


async def _get_meeting_lock(meeting_id: int) -> asyncio.Lock:
    async with _locks_mutex:
        if meeting_id not in _extraction_locks:
            _extraction_locks[meeting_id] = asyncio.Lock()
        return _extraction_locks[meeting_id]
from .models import Meeting, MediaFile, Recording, RecordingFrame
from .storage import StorageClient, create_storage_client

logger = logging.getLogger("meeting_api.frame_extractor")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DUAL_HEURISTIC_THRESHOLD_S = 300  # 5 minutes (D-34)
_FPS_BASELINE = "1/30"              # one frame every 30s (D-34)
_FPS_SHORT_MEETING = "1/5"          # one frame every 5s for short meetings (D-34)
_THUMBNAIL_SIZE = (320, 180)        # D-33
_WEBP_QUALITY = 75                  # D-33
_FFMPEG_TIMEOUT_MULTIPLIER = 2
_FFMPEG_TIMEOUT_BUFFER_S = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _probe_duration(video_path: str) -> float:
    """Probe video duration via ffprobe. Returns seconds."""
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1",
         video_path],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe exited {result.returncode}: {result.stderr[:500]}"
        )
    return float(result.stdout.strip())


def _find_video_master_jsonb(meeting: Meeting) -> Optional[Tuple[int, Optional[str], str, int]]:
    """Search meeting.data JSONB for a finalized video master.

    Returns (recording_id, session_uid, video_master_storage_path, user_id)
    or None if no suitable video master found.
    """
    rec_list = list((meeting.data or {}).get("recordings") or [])
    for rec in rec_list:
        if not isinstance(rec, dict):
            continue
        if rec.get("status") == "failed":
            continue
        media_files = list(rec.get("media_files") or [])
        for mf in media_files:
            if not isinstance(mf, dict):
                continue
            if (mf.get("type") == "video"
                    and mf.get("is_final") is True
                    and mf.get("finalized_by") == "recording_finalizer.master"):
                recording_id = rec.get("id")
                if recording_id is None:
                    continue
                session_uid = rec.get("session_uid")
                storage_path = mf.get("storage_path")
                if not storage_path:
                    continue
                return (recording_id, session_uid, storage_path, meeting.user_id)
    return None


async def _find_video_master_sql(
    meeting_id: int, db: AsyncSession,
) -> Optional[Tuple[int, Optional[str], str, int]]:
    """Fallback SQL-path lookup for a video master recording.

    Queries Recording + MediaFile tables for a video MediaFile whose
    storage_path contains 'master.webm'.
    """
    stmt = select(Recording).where(
        Recording.meeting_id == meeting_id,
        Recording.status != "failed",
    )
    result = await db.execute(stmt)
    for rec in result.scalars().all():
        mf_stmt = select(MediaFile).where(
            MediaFile.recording_id == rec.id,
            MediaFile.type == "video",
        )
        mf_result = await db.execute(mf_stmt)
        for mf in mf_result.scalars().all():
            if mf.storage_path and "master.webm" in mf.storage_path:
                return (rec.id, rec.session_uid, mf.storage_path, rec.user_id)
    return None


def _extract_frames_sync(
    video_master_path: str,
    user_id: int,
    recording_id: int,
    session_uid: Optional[str],
    storage: StorageClient,
) -> List[Tuple[int, str]]:
    """Sync core: download, extract, downscale, upload. No DB access.

    Returns list of (timestamp_s, storage_path) tuples.
    """
    frame_data: List[Tuple[int, str]] = []
    tmp_master_path: Optional[str] = None

    try:
        # Step 1 — Download master to NamedTemporaryFile (OOM-safe)
        tmp_master = tempfile.NamedTemporaryFile(
            suffix=".webm", delete=False,
        )
        tmp_master_path = tmp_master.name
        storage.download_to_file(video_master_path, tmp_master)
        tmp_master.close()

        # Step 2 — Probe duration + pick fps heuristic
        duration_seconds = _probe_duration(tmp_master_path)
        fps = _FPS_SHORT_MEETING if duration_seconds < _DUAL_HEURISTIC_THRESHOLD_S else _FPS_BASELINE
        interval_seconds = 5 if fps == _FPS_SHORT_MEETING else 30
        logger.info(
            "Frame extraction: recording_id=%s duration=%.1fs fps=%s interval=%ds",
            recording_id, duration_seconds, fps, interval_seconds,
        )

        # Step 3 — Extract JPEG intermediates via ffmpeg
        with tempfile.TemporaryDirectory() as tmp_dir:
            timeout_s = int(duration_seconds * _FFMPEG_TIMEOUT_MULTIPLIER) + _FFMPEG_TIMEOUT_BUFFER_S
            result = subprocess.run(
                ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-i", tmp_master_path,
                 "-vf", f"fps={fps}",
                 "-an", "-y", "-f", "image2",
                 os.path.join(tmp_dir, "frame_%06d.jpg")],
                capture_output=True, timeout=timeout_s,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg exited {result.returncode}: {result.stderr.decode()[:500]}"
                )

            jpeg_files = sorted(glob.glob(os.path.join(tmp_dir, "frame_*.jpg")))

            # Step 4 — Downscale + upload each frame
            for jpeg_file in jpeg_files:
                seq = int(os.path.basename(jpeg_file).replace("frame_", "").replace(".jpg", ""))
                timestamp_s = round(seq * interval_seconds)

                img = Image.open(jpeg_file)
                img.thumbnail(_THUMBNAIL_SIZE, Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="WebP", quality=_WEBP_QUALITY)
                webp_bytes = buf.getvalue()

                if session_uid:
                    storage_path = (
                        f"recordings/{user_id}/{recording_id}/{session_uid}/frames/{seq:06d}.webp"
                    )
                else:
                    storage_path = (
                        f"recordings/{user_id}/{recording_id}/frames/{seq:06d}.webp"
                    )

                storage.upload_file(storage_path, webp_bytes, content_type="image/webp")
                frame_data.append((timestamp_s, storage_path))

    finally:
        if tmp_master_path and os.path.exists(tmp_master_path):
            os.unlink(tmp_master_path)

    return frame_data


# ---------------------------------------------------------------------------
# Public async entry point
# ---------------------------------------------------------------------------

async def extract_frames_if_enabled(meeting_id: int) -> int:
    """Extract frames from a meeting's video recording.

    Returns the number of frames written. Returns 0 if snapshots are
    disabled, no video exists, frames already exist, or on failure.
    """
    # Step 1 — Feature flag gate (D-25)
    if os.getenv("SNAPSHOTS_ENABLED", "false").lower() != "true":
        logger.debug("Snapshots disabled, skipping frame extraction for meeting %s", meeting_id)
        return 0

    # Per-meeting idempotency lock — skip if already extracting
    _lock = await _get_meeting_lock(meeting_id)
    if _lock.locked():
        logger.info("Extraction already in progress for meeting %s — skipping duplicate call", meeting_id)
        return 0
    async with _lock:
        return await _do_extract_frames(meeting_id)


async def _do_extract_frames(meeting_id: int) -> int:
    """Inner implementation — called only when the per-meeting lock is held."""
    storage = create_storage_client()

    # Step 3 — First DB session: pre-checks + dual-path video lookup
    async with async_session_local() as db:
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None:
            logger.error("Meeting %s not found for frame extraction", meeting_id)
            return 0

        # Dual-path video master lookup
        video_info = _find_video_master_jsonb(meeting)
        if video_info is None:
            video_info = await _find_video_master_sql(meeting_id, db)

        if video_info is None:
            logger.info(
                "No finalized video recording found for meeting %s, skipping frame extraction",
                meeting_id,
            )
            return 0

        recording_id, session_uid, video_master_path, user_id = video_info

        # Idempotency check (D-36)
        existing_count = await db.execute(
            select(func.count()).select_from(RecordingFrame).where(
                RecordingFrame.meeting_id == meeting_id,
                RecordingFrame.recording_id == recording_id,
            )
        )
        count = existing_count.scalar() or 0
        if count > 0:
            first_frame_path = (
                f"recordings/{user_id}/{recording_id}/{session_uid}/frames/000001.webp"
                if session_uid
                else f"recordings/{user_id}/{recording_id}/frames/000001.webp"
            )
            if storage.file_exists(first_frame_path):
                logger.info("Frames already extracted for meeting %s, skipping", meeting_id)
                return 0

        # Set frames_status to 'processing' in meeting.data JSONB
        meeting_data = dict(meeting.data or {})
        rec_list = list(meeting_data.get("recordings") or [])
        rec_idx = None
        for i, rec in enumerate(rec_list):
            if isinstance(rec, dict) and rec.get("id") == recording_id:
                rec_idx = i
                break
        if rec_idx is not None:
            rec_list[rec_idx] = dict(rec_list[rec_idx])
            rec_list[rec_idx]["frames_status"] = "processing"
            meeting_data["recordings"] = rec_list
            meeting.data = meeting_data
            flag_modified(meeting, "data")
            await db.commit()

        # Also update Recording SQL row if it exists
        rec_sql = await db.execute(
            select(Recording).where(Recording.id == recording_id)
        )
        rec_row = rec_sql.scalars().first()
        if rec_row is not None:
            rec_row.frames_status = "processing"
            await db.commit()

    # Step 4 — Sync extraction in thread pool (no DB session held)
    try:
        frame_data = await asyncio.to_thread(
            _extract_frames_sync,
            video_master_path, user_id, recording_id, session_uid, storage,
        )
    except Exception as e:
        # D-37: On failure, set frames_status='failed'
        logger.error("Frame extraction failed for meeting %s: %s", meeting_id, e, exc_info=True)
        async with async_session_local() as db:
            meeting = await db.get(Meeting, meeting_id)
            if meeting is not None:
                meeting_data = dict(meeting.data or {})
                rec_list = list(meeting_data.get("recordings") or [])
                if rec_idx is not None and rec_idx < len(rec_list):
                    rec_list[rec_idx] = dict(rec_list[rec_idx])
                    rec_list[rec_idx]["frames_status"] = "failed"
                    rec_list[rec_idx]["frames_failure_reason"] = str(e)[:200]
                    meeting_data["recordings"] = rec_list
                    meeting.data = meeting_data
                    flag_modified(meeting, "data")
                    await db.commit()
            # Also update Recording SQL row
            rec_row_result = await db.execute(
                select(Recording).where(Recording.id == recording_id)
            )
            rec_row = rec_row_result.scalars().first()
            if rec_row is not None:
                rec_row.frames_status = "failed"
                meta = dict(rec_row.extra_metadata or {})
                meta["frames_failure_reason"] = str(e)[:200]
                rec_row.extra_metadata = meta
                flag_modified(rec_row, "extra_metadata")
                await db.commit()
        return 0

    # Step 5 — Second DB session: batch insert + status update
    async with async_session_local() as db:
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None:
            logger.error("Meeting %s disappeared before batch insert", meeting_id)
            return 0

        # Batch insert RecordingFrame rows (ON CONFLICT DO NOTHING)
        if frame_data:
            rows = [
                {
                    "meeting_id": meeting_id,
                    "recording_id": recording_id,
                    "session_uid": session_uid,
                    "timestamp_s": ts,
                    "storage_path": sp,
                }
                for ts, sp in frame_data
            ]
            stmt = pg_insert(RecordingFrame).values(rows).on_conflict_do_nothing(
                constraint="uq_recording_frame_identity",
            )
            await db.execute(stmt)
            await db.commit()

        # Set frames_status to 'complete'
        meeting_data = dict(meeting.data or {})
        rec_list = list(meeting_data.get("recordings") or [])
        if rec_idx is not None and rec_idx < len(rec_list):
            rec_list[rec_idx] = dict(rec_list[rec_idx])
            rec_list[rec_idx]["frames_status"] = "complete"
            meeting_data["recordings"] = rec_list
            meeting.data = meeting_data
            flag_modified(meeting, "data")
            await db.commit()

        # Also update Recording SQL row if it exists
        rec_sql = await db.execute(
            select(Recording).where(Recording.id == recording_id)
        )
        rec_row = rec_sql.scalars().first()
        if rec_row is not None:
            rec_row.frames_status = "complete"
            await db.commit()

        return len(frame_data)


# ---------------------------------------------------------------------------
# REPL trigger (WORK-12)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m meeting_api.frame_extractor <meeting_id>")
        sys.exit(1)
    meeting_id = int(sys.argv[1])
    count = asyncio.run(extract_frames_if_enabled(meeting_id))
    print(f"Extracted {count} frames for meeting {meeting_id}")
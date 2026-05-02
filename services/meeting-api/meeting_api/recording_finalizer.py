"""Server-side master-recording finalizer.

Builds a single playable master file (master.webm or master.wav) from the
per-chunk objects already in MinIO/S3 for a given meeting.

Why this exists (v0.10.6 chunk-leak release):
- Pre-fix: the bot constructed the master client-side at graceful-leave by
  concatenating its in-memory chunk buffer. Pack M's chunk-buffer cap
  shrank that buffer for memory-leak reasons, leaving graceful-leave
  master assembly with only the most-recent N chunks. Result: downloaded
  recordings were ~270KB unplayable fragments instead of full meetings.
- Pre-fix also: master construction only fired on graceful exit. Crash
  mid-meeting → no master at all.
- Now: meeting-api builds the master server-side from the durable chunks
  in MinIO. The bot's job is reduced to "land every chunk in MinIO";
  master assembly is decoupled from process lifetime.

Integration (Pack U.7, in callbacks.py):
- Called from `bot_exit_callback` synchronously BEFORE
  `update_meeting_status`, so by the time `meeting.status` flips to a
  terminal state, the corresponding `media_files.storage_path` already
  points at the master.

No-fallback contract (project owner directive, v0.10.6):
- If listing returns 0 chunks → log warning + return. Do NOT fabricate
  an empty master file. The audit trail in `meeting.data` is sufficient.
- If concat fails → raise. Caller (bot_exit_callback) will return
  non-2xx; runtime-api's idle_loop will retry.
- No try/except that swallows.

Idempotency:
- If `<prefix>/master.<format>` already exists, skip. The caller can
  invoke this safely on retry without producing duplicate work or
  re-uploading large blobs.
"""

import asyncio
import io
import logging
import struct
from typing import List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from .models import MediaFile, Recording
from .storage import StorageClient, create_storage_client

logger = logging.getLogger("meeting_api.recording_finalizer")


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

# WebM / EBML container magic: 0x1A 0x45 0xDF 0xA3
_WEBM_MAGIC = b"\x1A\x45\xDF\xA3"
# WAV / RIFF container magic: "RIFF" then size (4) then "WAVE"
_WAV_MAGIC = b"RIFF"
_WAV_FORMAT = b"WAVE"

# Standard PCM WAV header is exactly 44 bytes:
# RIFF<sz4>WAVE fmt <16><fmt-chunk-16-bytes> data<datasz4>
_WAV_HEADER_BYTES = 44


def _detect_format(first_chunk_head: bytes, declared_format: str) -> str:
    """Return the actual container format ('webm' | 'wav') or raise.

    Cross-checks magic bytes against the file extension claimed by
    media_file.format. Mismatch → raise: corrupt chunk or wrong format
    label. We prefer to fail loudly than silently produce a master with
    a wrong extension.
    """
    head = first_chunk_head[:12]
    if head.startswith(_WEBM_MAGIC):
        actual = "webm"
    elif head.startswith(_WAV_MAGIC) and head[8:12] == _WAV_FORMAT:
        actual = "wav"
    else:
        raise ValueError(
            f"Unrecognized chunk format: declared={declared_format!r} "
            f"head={head!r} (expected EBML 1A45DFA3 or RIFF...WAVE)"
        )
    if declared_format and declared_format.lower() != actual:
        raise ValueError(
            f"Chunk format mismatch: file extension claims {declared_format!r} "
            f"but bytes look like {actual!r}"
        )
    return actual


# ---------------------------------------------------------------------------
# WAV concat (RIFF-aware)
# ---------------------------------------------------------------------------

def _parse_wav_header(buf: bytes) -> Tuple[bytes, int]:
    """Return (fmt_chunk_bytes, data_payload_length).

    `fmt_chunk_bytes` is the 16-byte fmt-chunk body (PCM format, channels,
    sample-rate, byte-rate, block-align, bits-per-sample) — copied
    verbatim into the master so the master inherits the source PCM
    format.

    `data_payload_length` is the length of the data section as declared
    by the RIFF header. We DON'T use the declared length to slice the
    payload (some captures truncate or pad); instead the caller uses
    `len(buf) - 44` for actual payload bytes. The declared length is
    returned for sanity logging only.
    """
    if len(buf) < _WAV_HEADER_BYTES:
        raise ValueError(f"WAV chunk shorter than 44-byte header: {len(buf)} bytes")
    if buf[:4] != _WAV_MAGIC or buf[8:12] != _WAV_FORMAT:
        raise ValueError(f"WAV chunk missing RIFF/WAVE signature: head={buf[:12]!r}")
    if buf[12:16] != b"fmt ":
        raise ValueError(f"WAV chunk missing fmt chunk: head[12:16]={buf[12:16]!r}")
    if buf[36:40] != b"data":
        # PulseAudioCapture._wrapWav layout has data at offset 36; if a
        # different writer inserts a LIST/INFO chunk between fmt and data
        # we'd need to skip it. Fail loudly here — the bot only produces
        # the canonical layout.
        raise ValueError(
            f"WAV chunk has non-canonical layout: data tag expected at "
            f"offset 36, found {buf[36:40]!r}"
        )
    fmt_chunk_bytes = buf[20:36]  # the 16-byte fmt body
    declared_data_size = struct.unpack("<I", buf[40:44])[0]
    return fmt_chunk_bytes, declared_data_size


def _build_wav_master(chunks: List[bytes]) -> bytes:
    """RIFF-aware merge: strip per-chunk 44-byte headers, sum data
    payloads, prepend a single corrected master header.

    Master layout (matches PulseAudioCapture._wrapWav, audio-pipeline.ts:443):
        RIFF<36+total_data><WAVE>fmt <16><fmt-chunk><data><total_data><payload>

    The fmt chunk is copied verbatim from the FIRST chunk, so the
    master inherits the original PCM format (16kHz / mono / s16le for
    PulseAudio captures). All subsequent chunks must declare the same
    fmt — mismatch → raise.
    """
    if not chunks:
        raise ValueError("_build_wav_master requires at least one chunk")

    fmt_chunk, first_declared = _parse_wav_header(chunks[0])
    payloads: List[bytes] = []
    for i, c in enumerate(chunks):
        c_fmt, c_declared = _parse_wav_header(c)
        if c_fmt != fmt_chunk:
            raise ValueError(
                f"WAV fmt chunk mismatch at chunk index {i}: "
                f"first={fmt_chunk!r} this={c_fmt!r}"
            )
        payload = c[_WAV_HEADER_BYTES:]
        # Sanity log — declared vs actual. PulseAudio writer always
        # writes consistent values, but useful for catching truncation.
        if c_declared != len(payload):
            logger.warning(
                "WAV chunk %d declared data size %d but body is %d bytes — "
                "using actual body length",
                i, c_declared, len(payload),
            )
        payloads.append(payload)

    total_data = sum(len(p) for p in payloads)
    out = io.BytesIO()
    out.write(_WAV_MAGIC)                          # 0..3   "RIFF"
    out.write(struct.pack("<I", 36 + total_data))  # 4..7   RIFF size = header(36) + data
    out.write(_WAV_FORMAT)                         # 8..11  "WAVE"
    out.write(b"fmt ")                             # 12..15 "fmt "
    out.write(struct.pack("<I", 16))               # 16..19 fmt chunk size = 16
    out.write(fmt_chunk)                           # 20..35 16-byte fmt body
    out.write(b"data")                             # 36..39 "data"
    out.write(struct.pack("<I", total_data))       # 40..43 data chunk size
    for p in payloads:
        out.write(p)
    return out.getvalue()


# ---------------------------------------------------------------------------
# WebM concat (byte-concat — chunks from a single MediaRecorder stream form
# a valid WebM container when concatenated in seq order).
# ---------------------------------------------------------------------------

def _build_webm_master(chunks: List[bytes]) -> bytes:
    """Byte-concat WebM chunks.

    The MediaRecorder pipeline in the bot emits a self-describing chunk 0
    (EBML header + Segment header + first Cluster) followed by
    Cluster-only chunks (1..N). Concatenating them in seq order yields
    a valid WebM container — Cluster elements stack inside the Segment.

    No transcoding, no muxing — just byte-level concat. This is the same
    technique the bot used client-side; the only difference is server-side
    runs against the durable MinIO objects, not an in-memory buffer that
    Pack M's cap could shrink.
    """
    if not chunks:
        raise ValueError("_build_webm_master requires at least one chunk")
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# Storage path helpers
# ---------------------------------------------------------------------------

def _chunk_prefix(storage_path: str) -> str:
    """Return the directory portion of a chunk storage_path.

    storage_path convention (recordings.py:220):
        recordings/<user>/<rec>/<session>/<media_type>/<seq:06d>.<ext>

    Storage paths use forward slashes always — never os.path.join.
    """
    if "/" not in storage_path:
        raise ValueError(f"Invalid storage_path (no separator): {storage_path!r}")
    return storage_path.rsplit("/", 1)[0]


def _master_path(prefix: str, fmt: str) -> str:
    return f"{prefix}/master.{fmt}"


def _is_master_key(key: str) -> bool:
    """Filter master.* out of a chunk listing — we don't want the master
    to recursively concat itself if list_objects returns it."""
    tail = key.rsplit("/", 1)[-1]
    return tail.startswith("master.")


# ---------------------------------------------------------------------------
# Sync core (runs in a thread-pool from the async wrapper)
# ---------------------------------------------------------------------------

def _finalize_one_media_file_sync(
    storage: StorageClient,
    media_file_id: int,
    storage_path: str,
    declared_format: str,
) -> Optional[str]:
    """Build and upload the master for one MediaFile. Returns the new
    storage_path (the master path) or None if no chunks were found.

    Pure-sync (boto3 is sync) — wrapped in asyncio.to_thread by the
    async caller to avoid blocking the event loop.
    """
    prefix = _chunk_prefix(storage_path)
    fmt = (declared_format or "").lower()
    if fmt not in {"webm", "wav"}:
        raise ValueError(
            f"Unsupported format for master finalization: {declared_format!r} "
            f"(expected webm or wav)"
        )

    master_key = _master_path(prefix, fmt)

    # Idempotency — if the master is already there, skip the work.
    if storage.file_exists(master_key):
        logger.info(
            "[FINALIZER] master already exists, skipping: media_file_id=%s key=%s",
            media_file_id, master_key,
        )
        return master_key

    # List chunks under the prefix. Filter out any pre-existing master.*
    # objects (defensive: there shouldn't be one given the file_exists
    # check above, but a partial run could leave an unrelated master.*
    # of a different format around).
    all_keys = storage.list_objects(prefix + "/")
    chunk_keys = [k for k in all_keys if not _is_master_key(k)]

    if not chunk_keys:
        # No-fallback contract: do NOT fabricate an empty master.
        logger.warning(
            "[FINALIZER] no chunks under prefix — skipping master build: "
            "media_file_id=%s prefix=%s",
            media_file_id, prefix,
        )
        return None

    logger.info(
        "[FINALIZER] building master: media_file_id=%s format=%s chunks=%d prefix=%s",
        media_file_id, fmt, len(chunk_keys), prefix,
    )

    # Download all chunks in seq order. list_objects returns sorted
    # ascending; chunk seq is zero-padded 6 digits → lexicographic sort
    # equals numeric sort.
    chunks: List[bytes] = []
    for k in chunk_keys:
        chunks.append(storage.download_file(k))

    # Format detect + cross-check against declared extension. Uses the
    # FIRST chunk's bytes — webm chunk 0 has the EBML header, wav chunks
    # all have the RIFF header.
    actual_fmt = _detect_format(chunks[0][:12], fmt)

    if actual_fmt == "webm":
        master_bytes = _build_webm_master(chunks)
        content_type = "video/webm"
    else:  # wav
        master_bytes = _build_wav_master(chunks)
        content_type = "audio/wav"

    storage.upload_file(master_key, master_bytes, content_type=content_type)

    logger.info(
        "[FINALIZER] master uploaded: media_file_id=%s key=%s size=%d chunks=%d",
        media_file_id, master_key, len(master_bytes), len(chunk_keys),
    )
    return master_key


# ---------------------------------------------------------------------------
# Public async entrypoint
# ---------------------------------------------------------------------------

async def finalize_recording_master(meeting_id: int, db: AsyncSession) -> None:
    """Build master.{webm|wav} from chunks in MinIO. Idempotent.

    Called from bot_exit_callback synchronously BEFORE update_meeting_status,
    so by the time meeting.status flips to terminal, media_file.storage_path
    points at the master.

    Operates on the SQL Recording + MediaFile tables (DB metadata mode).
    In meeting_data mode (default for now) Recording rows do not exist;
    this function will simply find no rows and return — the integrating
    callback handles the meeting_data case via a separate path.
    """
    # Pull all in-flight Recording rows for this meeting + their MediaFiles.
    # We filter out only `failed` recordings — `in_progress`, `uploading`,
    # and `completed` are all valid finalization candidates: the bot can
    # exit before, during, or just-after the final-chunk upload that
    # promotes status to `completed`. Idempotency is provided by the
    # master-file-exists check inside the inner sync core, so re-running
    # on an already-finalized recording is cheap (one HEAD request per
    # media file).
    stmt = (
        select(Recording)
        .where(
            Recording.meeting_id == meeting_id,
            Recording.status != "failed",
        )
    )
    result = await db.execute(stmt)
    recordings = result.scalars().all()

    if not recordings:
        logger.info(
            "[FINALIZER] no Recording rows for meeting_id=%s — skipping "
            "(meeting_data mode or recording never started)",
            meeting_id,
        )
        return

    storage = create_storage_client()

    for rec in recordings:
        # Pull MediaFiles for this recording. In production the per-chunk
        # upload path keeps ONE MediaFile row per (recording, media_type)
        # and rewrites its storage_path to the latest chunk. So we expect
        # 1-2 rows per recording (audio, optionally video).
        mf_stmt = (
            select(MediaFile)
            .where(
                MediaFile.recording_id == rec.id,
                MediaFile.type.in_(("audio", "video")),
            )
        )
        mf_result = await db.execute(mf_stmt)
        media_files = mf_result.scalars().all()

        if not media_files:
            logger.info(
                "[FINALIZER] recording_id=%s has no audio/video MediaFile rows "
                "— skipping",
                rec.id,
            )
            continue

        for mf in media_files:
            if not mf.storage_path or not mf.format:
                logger.warning(
                    "[FINALIZER] media_file_id=%s missing storage_path or format "
                    "— skipping (storage_path=%r format=%r)",
                    mf.id, mf.storage_path, mf.format,
                )
                continue

            # Run the sync I/O in a thread-pool so we don't block the
            # event loop. boto3 is sync; download_file/upload_file/
            # list_objects all do blocking network I/O.
            master_key = await asyncio.to_thread(
                _finalize_one_media_file_sync,
                storage,
                mf.id,
                mf.storage_path,
                mf.format,
            )

            if master_key is None:
                # Storage list returned 0 chunks. No-fallback contract:
                # leave the existing storage_path alone and move on.
                continue

            if mf.storage_path == master_key:
                # Already pointing at the master (idempotent re-run).
                continue

            mf.storage_path = master_key
            logger.info(
                "[FINALIZER] media_file_id=%s storage_path updated to master: %s",
                mf.id, master_key,
            )

    # Single commit at the end — all media_file updates land atomically.
    await db.commit()

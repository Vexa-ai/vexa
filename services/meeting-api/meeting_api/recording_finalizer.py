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
import os
import struct
from typing import List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from .models import Meeting
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
    """Byte-concat WebM chunks (legacy in-memory path).

    Kept as a fallback for tiny meetings + tests that pre-load chunks.
    Production path uses _build_webm_master_streaming() instead — see
    its docstring for memory bounds and rationale.
    """
    if not chunks:
        raise ValueError("_build_webm_master requires at least one chunk")
    return b"".join(chunks)


def _build_webm_master_streaming_file(
    storage: "StorageClient",  # type: ignore[name-defined]
    chunk_keys: List[str],
) -> str:
    """Streamed byte-concat of WebM chunks to a local temp file.

    Returns the path of the assembled master. Caller is responsible
    for cleanup.

    Why BYTE-CONCAT (and not ffmpeg `-f concat`):
      The bot's MediaRecorder pipeline emits a self-describing chunk 0
      (EBML header + Segment header + first Cluster) followed by
      Cluster-only chunks (1..N). The chunks are NOT standalone WebM
      containers. ffmpeg's concat demuxer expects a list of standalone
      files with compatible stream layout — it silently drops Cluster-
      only inputs. Pack U.5's byte-concat is correct: stacking Cluster
      elements inside the Segment yields a valid container.

    Bounded memory:
      - download_file_to_path streams chunk to disk via boto3 multipart.
      - Local file copy uses a 1 MB buffer; bounded regardless of chunk
        size.
      - No bytes-in-memory round-trip at any point.

    Peak RAM through this path ≈ a few MB constant, regardless of
    meeting length.
    """
    import shutil
    import tempfile

    if not chunk_keys:
        raise ValueError("_build_webm_master_streaming_file requires at least one chunk")

    with tempfile.NamedTemporaryFile(
        prefix="webm-master-", suffix=".webm", delete=False
    ) as out_fh:
        out_path = out_fh.name

    chunk_dir = tempfile.mkdtemp(prefix="webm-chunks-")
    try:
        with open(out_path, "wb") as out:
            for idx, key in enumerate(chunk_keys):
                chunk_path = os.path.join(chunk_dir, f"{idx:06d}.webm")
                storage.download_file_to_path(key, chunk_path)
                with open(chunk_path, "rb") as src:
                    shutil.copyfileobj(src, out, length=1024 * 1024)
                os.remove(chunk_path)
        return out_path
    except Exception:
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise
    finally:
        try:
            os.rmdir(chunk_dir)
        except OSError:
            # If chunks weren't all cleaned up (mid-iteration crash),
            # remove the dir tree non-destructively.
            shutil.rmtree(chunk_dir, ignore_errors=True)


def _build_webm_master_streaming(
    storage: "StorageClient",  # type: ignore[name-defined]
    chunk_keys: List[str],
) -> bytes:
    """Bytes-in/bytes-out wrapper for backward compat with the unit
    test exec-loader. Production path uses _build_webm_master_streaming_file
    + storage.upload_file_path so no bytes-in-memory round-trip happens.
    """
    out_path = _build_webm_master_streaming_file(storage, chunk_keys)
    try:
        with open(out_path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass


def _inject_webm_duration_file(src_path: str) -> str:
    """File-to-file variant of duration injection (#302). Returns the
    path of the duration-injected file (caller must clean up).

    On failure — ffmpeg missing, non-zero exit, empty output, timeout —
    returns the SOURCE path unchanged so the byte-concat output is
    still uploaded. Emits a structured WARN per failure mode. This is
    the explicitly approved fallback for #302; the failure is
    observable, the recording still plays without the duration tag.

    Bounded memory: ffmpeg streams the input file; only the Python
    subprocess wait costs RAM (negligible).
    """
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as dst_fh:
        dst_path = dst_fh.name

    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y",
                "-loglevel", "error",
                "-fflags", "+genpts",
                "-i", src_path,
                "-c", "copy",
                dst_path,
            ],
            capture_output=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            logger.warning(
                "[FINALIZER] webm.duration_inject.failed rc=%s stderr=%s "
                "— falling back to byte-concat output (#302)",
                proc.returncode, proc.stderr[:300].decode("utf-8", errors="replace"),
            )
            try:
                os.remove(dst_path)
            except OSError:
                pass
            return src_path
        if os.path.getsize(dst_path) == 0:
            logger.warning("[FINALIZER] webm.duration_inject.empty_output — falling back (#302)")
            try:
                os.remove(dst_path)
            except OSError:
                pass
            return src_path
        return dst_path
    except FileNotFoundError:
        logger.warning("[FINALIZER] webm.duration_inject.ffmpeg_missing — falling back (#302)")
        try:
            os.remove(dst_path)
        except OSError:
            pass
        return src_path
    except subprocess.TimeoutExpired:
        logger.warning("[FINALIZER] webm.duration_inject.timeout (>120s) — falling back (#302)")
        try:
            os.remove(dst_path)
        except OSError:
            pass
        return src_path


def _inject_webm_duration(webm_bytes: bytes) -> bytes:
    """Bytes-in/bytes-out wrapper for backward compat with tests that
    pre-load chunks. Production path uses _inject_webm_duration_file."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as src_fh:
        src_path = src_fh.name
        src_fh.write(webm_bytes)
    try:
        out_path = _inject_webm_duration_file(src_path)
        with open(out_path, "rb") as fh:
            data = fh.read()
        if out_path != src_path:
            try:
                os.remove(out_path)
            except OSError:
                pass
        return data
    finally:
        try:
            os.remove(src_path)
        except OSError:
            pass


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


def _media_content_type(media_type: str, media_format: str) -> str:
    fmt = str(media_format or "").lower()
    typ = str(media_type or "").lower()
    if fmt == "webm":
        return "audio/webm" if typ == "audio" else "video/webm"
    if fmt == "wav":
        return "audio/wav"
    return "application/octet-stream"


# ---------------------------------------------------------------------------
# Sync core (runs in a thread-pool from the async wrapper)
# ---------------------------------------------------------------------------

def _finalize_one_media_file_sync(
    storage: StorageClient,
    media_file_id: int,
    storage_path: str,
    declared_format: str,
    media_type: str,
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
    all_keys = storage.list_objects_bounded(prefix + "/")
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

    # Format-detect using ONLY the first chunk's header bytes — bounded
    # memory, no need to download the whole list.
    first_chunk_bytes = storage.download_file(chunk_keys[0])
    actual_fmt = _detect_format(first_chunk_bytes[:12], fmt)
    # Hint to GC; we don't need the full first chunk past the format
    # cross-check (webm path streams everything via ffmpeg, wav path
    # re-downloads via _build_wav_master).
    del first_chunk_bytes

    if actual_fmt == "webm":
        # Bounded-memory path: chunks downloaded to disk, concatenated
        # via shutil (1 MB buffered I/O), duration-injected via ffmpeg
        # file-to-file, uploaded via boto3 multipart. No bytes-in-memory
        # round-trip — peak RAM is a few MB constant regardless of
        # meeting length.
        concat_path = _build_webm_master_streaming_file(storage, chunk_keys)
        try:
            final_path = _inject_webm_duration_file(concat_path)
            try:
                storage.upload_file_path(
                    master_key,
                    final_path,
                    content_type=_media_content_type(media_type, "webm"),
                )
            finally:
                if final_path != concat_path:
                    try:
                        os.remove(final_path)
                    except OSError:
                        pass
        finally:
            try:
                os.remove(concat_path)
            except OSError:
                pass
        master_size = None  # logged via storage.upload_file_path itself
    else:  # wav
        # WAV path: in-memory concat. WAV streams aren't long-meeting
        # (typically post-meeting transcribe use only). Convert to
        # streaming if memory profiling ever surfaces an issue.
        chunks: List[bytes] = []
        for k in chunk_keys:
            chunks.append(storage.download_file(k))
        master_bytes = _build_wav_master(chunks)
        storage.upload_file(master_key, master_bytes, content_type="audio/wav")
        master_size = len(master_bytes)

    if master_size is not None:
        logger.info(
            "[FINALIZER] master uploaded: media_file_id=%s key=%s size=%d chunks=%d",
            media_file_id, master_key, master_size, len(chunk_keys),
        )
    else:
        # webm path — size already logged by storage.upload_file_path.
        logger.info(
            "[FINALIZER] master uploaded: media_file_id=%s key=%s chunks=%d (size streamed)",
            media_file_id, master_key, len(chunk_keys),
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

    v0.10.6.1 — JSONB-only. Recordings live in
    meeting.data->'recordings' (array) → recording.media_files (array) →
    media_file fields including storage_path. We mutate the JSONB
    structure in place and flag_modified() to force SQLAlchemy to detect
    the change.
    """
    storage = create_storage_client()
    finalized_any = False

    meeting_q = await db.execute(
        select(Meeting)
        .where(Meeting.id == meeting_id)
        .execution_options(populate_existing=True)
    )
    meeting = meeting_q.scalars().first()

    if meeting is None:
        logger.info(
            "[FINALIZER] meeting_id=%s — no Meeting row; nothing to finalize",
            meeting_id,
        )
        return

    meeting_data = dict(meeting.data or {})
    rec_list = list(meeting_data.get("recordings") or [])

    if not rec_list:
        # Race recovery: bot exit callback can fire before the chunk-write
        # handler has populated meeting.data.recordings. Instead of bailing
        # (and waiting up to ~120s for the sweep to recover), attempt the
        # same JSONB recovery inline so playback_url is available on the
        # post-meeting page immediately.
        from .sweeps import recover_recordings_jsonb_from_storage
        recovered = await recover_recordings_jsonb_from_storage(meeting, db)
        if recovered:
            # Re-read meeting after recovery seeded recordings.
            meeting_q = await db.execute(
                select(Meeting)
                .where(Meeting.id == meeting_id)
                .execution_options(populate_existing=True)
            )
            meeting = meeting_q.scalars().first()
            meeting_data = dict(meeting.data or {})
            rec_list = list(meeting_data.get("recordings") or [])
        if not rec_list:
            logger.info(
                "[FINALIZER] meeting_id=%s — no recordings in meeting.data even after recovery; nothing to finalize",
                meeting_id,
            )
            return
        logger.info(
            "[FINALIZER] meeting_id=%s — recovered %d recording entr(ies) inline before finalize",
            meeting_id, len(rec_list),
        )

    for rec_idx, rec_payload in enumerate(rec_list):
        if not isinstance(rec_payload, dict):
            continue
        if rec_payload.get("status") == "failed":
            continue
        media_files = list(rec_payload.get("media_files") or [])
        if not media_files:
            continue

        for mf_idx, mf in enumerate(media_files):
            if not isinstance(mf, dict):
                continue
            mf_type = mf.get("type")
            mf_format = (mf.get("format") or "").lower()
            mf_path = mf.get("storage_path") or ""
            mf_id = mf.get("id")

            if mf_type not in ("audio", "video"):
                continue
            if not mf_path or not mf_format:
                logger.warning(
                    "[FINALIZER] [DATA] meeting_id=%s rec_idx=%s mf_idx=%s missing path/format — skipping",
                    meeting_id, rec_idx, mf_idx,
                )
                continue
            if mf_format not in ("webm", "wav"):
                logger.warning(
                    "[FINALIZER] [DATA] meeting_id=%s mf_id=%s unsupported format=%r — skipping",
                    meeting_id, mf_id, mf_format,
                )
                continue

            try:
                master_key = await asyncio.to_thread(
                    _finalize_one_media_file_sync,
                    storage,
                    mf_id or f"meeting_data:{meeting_id}/{rec_idx}/{mf_idx}",
                    mf_path,
                    mf_format,
                    mf_type,
                )
            except Exception as fin_err:
                logger.error(
                    "[FINALIZER] [DATA] meeting_id=%s mf_id=%s failed: %s",
                    meeting_id, mf_id, str(fin_err)[:200],
                )
                raise

            if master_key is None:
                # No-fallback: leave storage_path alone if list returned 0 chunks.
                continue
            if mf.get("storage_path") == master_key:
                # Idempotent re-run.
                continue

            mf["storage_path"] = master_key
            mf["finalized_at"] = mf.get("finalized_at") or _now_iso()
            mf["finalized_by"] = "recording_finalizer.master"
            # Pack U.7 — set is_final=True so the chunk_write handler's defensive
            # check (recordings.py: refuse overwrite when is_final or storage_path
            # ends at /master.*) keeps a late-arriving chunk POST from stomping
            # the master path back to the chunk path. Without this, real-meeting
            # tests on helm reproduce the race: chunk N+1 lands after Pack U.5
            # commits, chunk_write overwrites mf.storage_path → dashboard sees
            # chunk-path, post_meeting_reconciler then sets finalized_by back.
            mf["is_final"] = True
            media_files[mf_idx] = mf
            finalized_any = True
            logger.info(
                "[FINALIZER] [DATA] meeting_id=%s mf_id=%s storage_path → master: %s",
                meeting_id, mf_id, master_key,
            )

        rec_payload["media_files"] = media_files

        # v0.10.6.1 — canonical playback_url field (ADR-2). Producer
        # writes this once master assembly succeeds; dashboard reads it
        # directly instead of running pickMasterMediaFile() over the
        # media_files array. Null sub-field means "no master for this
        # type yet" — dashboard renders explicit "finalizing" UI state.
        # The URL is stable (a route, not a presigned URL); the backend
        # endpoint at /recordings/<id>/master resolves to a fresh
        # presigned URL on each fetch.
        recording_id = rec_payload.get("id")
        if recording_id is not None:
            has_audio_master = any(
                mf.get("type") == "audio" and mf.get("finalized_by") == "recording_finalizer.master"
                for mf in media_files
            )
            has_video_master = any(
                mf.get("type") == "video" and mf.get("finalized_by") == "recording_finalizer.master"
                for mf in media_files
            )
            if has_audio_master or has_video_master:
                rec_payload["playback_url"] = {
                    "audio": f"/recordings/{recording_id}/master?type=audio" if has_audio_master else None,
                    "video": f"/recordings/{recording_id}/master?type=video" if has_video_master else None,
                }
                # Mark JSONB dirty even if we only re-wrote playback_url with no
                # new finalizations — idempotent fix-up writes are valuable
                # (e.g. backfill after the original commit succeeded but
                # playback_url got dropped).
                finalized_any = True

        rec_list[rec_idx] = rec_payload

    if finalized_any:
        meeting_data["recordings"] = rec_list
        meeting.data = meeting_data
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(meeting, "data")
        await db.commit()
        logger.info(
            "[FINALIZER] meeting_id=%s — committed master storage_path update(s) to meeting.data",
            meeting_id,
        )


def _now_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat()

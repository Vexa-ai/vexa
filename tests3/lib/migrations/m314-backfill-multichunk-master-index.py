#!/usr/bin/env python3
"""
═════════════════════════════════════════════════════════════════════════
  ONE-SHOT MIGRATION — BACKFILL playback_url ON HISTORICAL MULTICHUNK
  RECORDINGS (m314)
═════════════════════════════════════════════════════════════════════════

  WHAT THIS DOES:
    v0.10.6.1 introduced `playback_url` as the canonical pointer on
    each JSONB recording in `meetings.data['recordings']`:

        playback_url: {audio?: str, video?: str}

    The dashboard reads `recording.playback_url.{audio,video}` and stops
    picking the master from `media_files[]` client-side. Recordings
    finalized BEFORE this release have a master `media_file` row
    (`finalized_by == "recording_finalizer.master"`) but no
    `playback_url`. The UI renders them as "finalizing" forever.

    There are ~73 such historical multichunk recordings in prod
    (recordings whose media_files include >=2 chunk entries plus a
    master entry). This script walks every meeting row, finds those
    recordings, and:

      1. Ensures the master media_file entry has
         `finalized_by: "recording_finalizer.master"` (idempotent: skip
         if already set).
      2. Writes `playback_url` onto the recording:
           - {audio: "/recordings/<id>/master?type=audio"} if an audio
             master exists.
           - {video: "/recordings/<id>/master?type=video"} if a video
             master exists.
         The endpoint is registered in
         services/meeting-api/meeting_api/recordings.py.

  BLAST RADIUS:
    Bounded to recordings where playback_url is null/missing AND a
    master exists in media_files. Each is a single JSONB update on
    one meetings row. If misidentified, the affected recording's
    playback_url is wrong; the UI shows "finalizing" until the script
    is re-run with the correct selector. No data is destroyed: the
    raw media_files entries are not touched (other than setting
    `finalized_by` on the entry that already IS the master).

  ROLLBACK: `m314-restore-multichunk-master-index.py` sets
  `playback_url` to null on every recording this script touched. The
  `finalized_by` field is intentionally left in place — it's a property
  of the actual master file, not a backfill artefact.

  USAGE:
      # Scan + log what would change, no writes:
      python3 tests3/lib/migrations/m314-backfill-multichunk-master-index.py --dry-run

      # Commit:
      python3 tests3/lib/migrations/m314-backfill-multichunk-master-index.py

  IDEMPOTENCY:
    - A recording that already has a non-null `playback_url` is
      skipped entirely.
    - A master media_file that already has `finalized_by` set is
      skipped (no field write).
    - Safe to re-run after a partial run.

  ENV: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_SSL_MODE
  (same shape as meeting-api). Uses the meeting-api engine directly.
═════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "meeting-api"))

from sqlalchemy import text  # type: ignore[import]
from meeting_api.database import engine  # type: ignore[import]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [m314] %(levelname)s %(message)s",
)
logger = logging.getLogger("m314")

MASTER_MARK = "recording_finalizer.master"


def _is_master(mf: dict) -> bool:
    return isinstance(mf, dict) and mf.get("finalized_by") == MASTER_MARK


def _looks_like_unmarked_master(mf: dict) -> bool:
    """Best-effort: a media_file that IS the master but lacks the marker.

    Heuristic: in the historical multichunk shape the master is the
    single entry per (type) without a chunk_seq AND without a
    chunk-style storage path. We're deliberately conservative: only
    treat an entry as a master if there's exactly one entry per type
    that lacks chunk_seq AND there are >=2 OTHER entries for the same
    type with chunk_seq set. The caller resolves ties by leaving the
    marker off and logging "ambiguous".
    """
    return (
        isinstance(mf, dict)
        and mf.get("type") in ("audio", "video")
        and mf.get("chunk_seq") in (None,)
        and not mf.get("is_chunk", False)
    )


def _classify_recording(rec: dict) -> tuple[bool, dict | None, dict | None, str]:
    """Return (has_multichunk_master, audio_master, video_master, reason).

    has_multichunk_master:
      True iff media_files contains >=1 chunk-style entry AND at least
      one master entry (existing-marker OR unique unmarked candidate).
    audio_master / video_master:
      The master entry per type if uniquely identifiable, else None.
    reason:
      Human-readable selector outcome for logging.
    """
    mfs = rec.get("media_files") or []
    if not isinstance(mfs, list) or len(mfs) < 1:
        return False, None, None, "skip: no media_files"

    chunk_entries = [
        mf for mf in mfs
        if isinstance(mf, dict) and (mf.get("chunk_seq") is not None or mf.get("is_chunk"))
    ]

    # v0.10.6.1 develop-code 2026-05-12: original script was multichunk-only
    # (required >=2 media_files and >=1 chunk entry). Backfill scope widened
    # to also cover single-master-only recordings completed before the
    # v0.10.6.1 finalizer wrote `playback_url`. A recording carrying the
    # `recording_finalizer.master` marker is in-scope regardless of
    # multichunk-ness; the unmarked-fallback path below remains multichunk-only
    # (it relies on >=2 same-type chunks to disambiguate).

    # First preference: explicitly marked masters.
    audio_marked = [mf for mf in mfs if _is_master(mf) and mf.get("type") == "audio"]
    video_marked = [mf for mf in mfs if _is_master(mf) and mf.get("type") == "video"]

    audio_master = audio_marked[0] if len(audio_marked) == 1 else None
    video_master = video_marked[0] if len(video_marked) == 1 else None

    # Fallback: unmarked-but-unique candidate (one non-chunk entry per
    # type sitting alongside multiple chunk entries of the same type).
    if audio_master is None:
        cands = [
            mf for mf in mfs
            if _looks_like_unmarked_master(mf) and mf.get("type") == "audio"
            and not _is_master(mf)
        ]
        audio_chunks = [mf for mf in chunk_entries if mf.get("type") == "audio"]
        if len(cands) == 1 and len(audio_chunks) >= 2:
            audio_master = cands[0]

    if video_master is None:
        cands = [
            mf for mf in mfs
            if _looks_like_unmarked_master(mf) and mf.get("type") == "video"
            and not _is_master(mf)
        ]
        video_chunks = [mf for mf in chunk_entries if mf.get("type") == "video"]
        if len(cands) == 1 and len(video_chunks) >= 2:
            video_master = cands[0]

    has = audio_master is not None or video_master is not None
    if not has:
        return False, None, None, "skip: multichunk but no master identifiable"

    return True, audio_master, video_master, "ok"


async def run(dry_run: bool) -> int:
    scanned = 0
    updated = 0
    skipped_already = 0
    skipped_no_master = 0
    errors = 0

    async with engine.begin() as conn:
        # Pull only meetings whose data has a recordings array.
        result = await conn.execute(
            text(
                "SELECT id, data FROM meetings "
                "WHERE jsonb_typeof(data->'recordings') = 'array'"
            )
        )
        rows = result.mappings().all()
        logger.info("scanning %d meeting rows with recordings arrays", len(rows))

        for row in rows:
            meeting_id = row["id"]
            data = row["data"]
            if not isinstance(data, dict):
                continue
            recordings = data.get("recordings")
            if not isinstance(recordings, list):
                continue

            new_data = copy.deepcopy(data)
            new_recs = new_data["recordings"]
            row_changed = False

            for idx, rec in enumerate(new_recs):
                if not isinstance(rec, dict):
                    continue
                scanned += 1
                rec_id = rec.get("id")

                # Skip recordings already backfilled.
                if rec.get("playback_url"):
                    skipped_already += 1
                    logger.info(
                        "skip meeting=%s rec=%s already has playback_url",
                        meeting_id, rec_id,
                    )
                    continue

                has_mm, audio_master, video_master, reason = _classify_recording(rec)
                if not has_mm:
                    skipped_no_master += 1
                    logger.info(
                        "skip meeting=%s rec=%s reason=%s",
                        meeting_id, rec_id, reason,
                    )
                    continue

                # Ensure finalized_by marker on identified masters.
                marker_writes: list[str] = []
                for mf in (rec.get("media_files") or []):
                    if mf is audio_master and not _is_master(mf):
                        mf["finalized_by"] = MASTER_MARK
                        marker_writes.append("audio")
                    elif mf is video_master and not _is_master(mf):
                        mf["finalized_by"] = MASTER_MARK
                        marker_writes.append("video")

                # Build playback_url. We need the recording's own id —
                # if it's missing we cannot construct the URL; log and
                # skip rather than guess.
                if rec_id is None:
                    errors += 1
                    logger.error(
                        "skip meeting=%s rec=<missing id> cannot build playback_url",
                        meeting_id,
                    )
                    continue

                playback_url: dict = {}
                if audio_master is not None:
                    playback_url["audio"] = f"/recordings/{rec_id}/master?type=audio"
                if video_master is not None:
                    playback_url["video"] = f"/recordings/{rec_id}/master?type=video"
                rec["playback_url"] = playback_url

                updated += 1
                row_changed = True
                logger.info(
                    "update meeting=%s rec=%s playback_url=%s marker_writes=%s",
                    meeting_id, rec_id, playback_url,
                    marker_writes or "[]",
                )

            if row_changed and not dry_run:
                await conn.execute(
                    text("UPDATE meetings SET data = :data WHERE id = :id"),
                    {"data": _json_dumps(new_data), "id": meeting_id},
                )

        verb = "would update" if dry_run else "updated"
        logger.info(
            "summary: scanned=%d %s=%d skipped_already=%d skipped_no_master=%d errors=%d",
            scanned, verb, updated, skipped_already, skipped_no_master, errors,
        )

    return 0 if errors == 0 else 3


def _json_dumps(obj: dict) -> str:
    import json
    return json.dumps(obj, default=str)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan + log but do not write.",
    )
    args = parser.parse_args()
    return asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
═════════════════════════════════════════════════════════════════════════
  ROLLBACK COMPANION — UNDO playback_url BACKFILL (m314-restore)
═════════════════════════════════════════════════════════════════════════

  WHAT THIS DOES:
    Sets `playback_url` to null on every JSONB recording where it is
    currently a non-null value matching the canonical backfill shape
    produced by m314-backfill-multichunk-master-index.py — i.e.
    `playback_url.{audio,video}` strings that match
    `/recordings/<id>/master?type={audio,video}`.

    The `finalized_by` field on master media_files is intentionally
    LEFT ALONE — it's a property of the actual master file (set by
    recording_finalizer.master), not a backfill artefact. Removing it
    here would corrupt recordings that legitimately had a master
    assembled by the finalizer.

  BLAST RADIUS:
    Bounded to recordings whose playback_url matches the m314 canonical
    shape. New recordings finalized AFTER m314 ran will also have
    playback_url set the same way; this rollback will clear those too.
    That is acceptable: the dashboard's "finalizing" state handler
    (DASHBOARD_RENDERS_FINALIZING_STATE_ON_NULL_PLAYBACK_URL) re-renders
    those recordings as finalizing until the next master-assembly run
    re-populates the field. No data loss.

  USAGE:
      # See what would be cleared:
      python3 tests3/lib/migrations/m314-restore-multichunk-master-index.py --dry-run

      # Commit:
      python3 tests3/lib/migrations/m314-restore-multichunk-master-index.py

  IDEMPOTENCY:
    - A recording without `playback_url` is skipped.
    - Safe to re-run.

  ENV: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_SSL_MODE
═════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "meeting-api"))

from sqlalchemy import text  # type: ignore[import]
from meeting_api.database import engine  # type: ignore[import]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [m314-restore] %(levelname)s %(message)s",
)
logger = logging.getLogger("m314-restore")


def _is_canonical_backfill(playback_url: object, rec_id: object) -> bool:
    """Match only playback_url values that look like m314's output."""
    if not isinstance(playback_url, dict) or rec_id is None:
        return False
    expected_audio = f"/recordings/{rec_id}/master?type=audio"
    expected_video = f"/recordings/{rec_id}/master?type=video"
    audio = playback_url.get("audio")
    video = playback_url.get("video")
    # Each key, if present, must match the canonical shape. At least
    # one must be present.
    if audio is not None and audio != expected_audio:
        return False
    if video is not None and video != expected_video:
        return False
    return audio is not None or video is not None


async def run(dry_run: bool) -> int:
    scanned = 0
    cleared = 0
    skipped_empty = 0
    skipped_non_canonical = 0

    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT id, data FROM meetings "
                "WHERE jsonb_typeof(data->'recordings') = 'array'"
            )
        )
        rows = result.mappings().all()
        logger.info("scanning %d meeting rows", len(rows))

        for row in rows:
            meeting_id = row["id"]
            data = row["data"]
            if not isinstance(data, dict):
                continue
            recordings = data.get("recordings")
            if not isinstance(recordings, list):
                continue

            new_data = copy.deepcopy(data)
            row_changed = False

            for rec in new_data["recordings"]:
                if not isinstance(rec, dict):
                    continue
                scanned += 1
                pu = rec.get("playback_url")
                if pu is None:
                    skipped_empty += 1
                    continue
                if not _is_canonical_backfill(pu, rec.get("id")):
                    skipped_non_canonical += 1
                    logger.info(
                        "skip meeting=%s rec=%s playback_url is non-canonical: %s",
                        meeting_id, rec.get("id"), pu,
                    )
                    continue
                rec["playback_url"] = None
                cleared += 1
                row_changed = True
                logger.info(
                    "clear meeting=%s rec=%s (was: %s)",
                    meeting_id, rec.get("id"), pu,
                )

            if row_changed and not dry_run:
                await conn.execute(
                    text("UPDATE meetings SET data = :data WHERE id = :id"),
                    {"data": json.dumps(new_data, default=str), "id": meeting_id},
                )

        verb = "would clear" if dry_run else "cleared"
        logger.info(
            "summary: scanned=%d %s=%d skipped_empty=%d skipped_non_canonical=%d",
            scanned, verb, cleared, skipped_empty, skipped_non_canonical,
        )

    return 0


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

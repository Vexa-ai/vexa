#!/usr/bin/env python3
"""
═════════════════════════════════════════════════════════════════════════
  ONE-SHOT MIGRATION — DROP DEAD RELATIONAL RECORDINGS TABLES
═════════════════════════════════════════════════════════════════════════

  WHAT THIS DOES:
    Drops the `media_files` and `recordings` tables. As of v0.10.6.1
    all recording metadata lives in `meetings.data['recordings']`
    (JSONB); the relational tables hold 1 stray row each in prod and
    have had zero idx_scans since pg_stat began. The
    `RECORDING_METADATA_MODE` toggle and the `Recording` / `MediaFile`
    ORM models are removed in the same release; nothing in code reads
    these tables anymore.

  RETIRE-AFTER: Once every prod / self-hosted Vexa deployment has run
  this script (or been deployed fresh on v0.10.6.1+, where the tables
  are never created), this file can be deleted. Recommended retirement
  trigger: post-v0.10.7 ship, after surveying self-hoster Discord +
  prod telemetry confirms no remaining `recordings` or `media_files`
  tables in any active deployment.

  BLAST RADIUS:
    - Affects ANY deployment that still has the recordings/media_files
      tables present (i.e. any DB created before v0.10.6.1).
    - Worst-case data loss: the at-most-2 stray rows that have been
      sitting unused since Feb 2026 are deleted. These are archived
      to a JSON file BEFORE the DROP runs (see ARCHIVE_PATH below)
      so the data is recoverable manually if some operator
      retroactively decides they wanted it.
    - Rollback: `m331-restore-relational-recordings.py` re-creates the
      empty tables matching the pre-drop prod schema. The archived
      rows are NOT re-inserted by the rollback (manual step if needed).
    - Code-side rollback: revert v0.10.6.1's deletions of the
      `Recording` / `MediaFile` ORM models and the
      `recording_metadata_mode` toggle.

  USAGE:
      # See what would be dropped without committing:
      python3 tests3/lib/migrations/m331-drop-relational-recordings.py --dry-run

      # Commit the drop (archives stray rows first, then DROPs):
      python3 tests3/lib/migrations/m331-drop-relational-recordings.py

  IDEMPOTENCY:
    - If either table is already absent, this script logs that fact
      and skips the corresponding DROP. Safe to re-run.

  ARCHIVE:
    - Stray rows are written to ARCHIVE_PATH below before DROP. If
      ARCHIVE_PATH already exists, the script refuses to overwrite —
      operator must move or delete the existing file first. (Avoids
      accidental clobber on a manual re-run.)

  SAFETY:
    - Single transaction: archive read + DROP both succeed or both
      roll back. Drops `media_files` FIRST (it FKs to recordings),
      then `recordings`.

  ENV: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_SSL_MODE
  (same as meeting-api). Uses the meeting-api engine directly.
═════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Allow importing meeting_api from this repo when run via
# `kubectl exec` against a meeting-api pod (the package is on PYTHONPATH)
# OR from a dev shell at the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "meeting-api"))

from sqlalchemy import text  # type: ignore[import]
from meeting_api.database import engine  # type: ignore[import]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [m331] %(levelname)s %(message)s",
)
logger = logging.getLogger("m331")

# Default archive location lives in the release dir so it travels with
# the release notes. Operator may override via --archive-path.
DEFAULT_ARCHIVE_PATH = (
    Path(__file__).resolve().parents[2]
    / "releases"
    / "260508-v0.10.6.1"
    / "archive"
    / "2026-05-12-relational-recordings-archive.json"
)

TABLE_DUMP_QUERIES = {
    "recordings": text("SELECT * FROM recordings"),
    "media_files": text("SELECT * FROM media_files"),
}


async def _table_exists(conn, table_name: str) -> bool:
    result = await conn.execute(
        text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables "
            "  WHERE table_schema = 'public' AND table_name = :t"
            ")"
        ),
        {"t": table_name},
    )
    return bool(result.scalar())


async def _dump_table(conn, table_name: str) -> list[dict]:
    """Read every row from `table_name` and return as list-of-dicts."""
    query = TABLE_DUMP_QUERIES[table_name]
    result = await conn.execute(query)
    rows = result.mappings().all()
    out: list[dict] = []
    for row in rows:
        record: dict = {}
        for key, value in dict(row).items():
            # JSON-friendly coercion for datetimes etc.
            if isinstance(value, datetime):
                record[key] = value.isoformat()
            else:
                record[key] = value
        out.append(record)
    return out


async def run(dry_run: bool, archive_path: Path) -> int:
    async with engine.begin() as conn:
        recordings_present = await _table_exists(conn, "recordings")
        media_files_present = await _table_exists(conn, "media_files")

        if not recordings_present and not media_files_present:
            logger.info(
                "both tables already absent — nothing to do (idempotent skip)"
            )
            return 0

        logger.info(
            "table presence: recordings=%s media_files=%s",
            recordings_present, media_files_present,
        )

        # Archive stray rows BEFORE dropping. We read both tables
        # (whichever exist) into a single JSON document so the archive
        # captures the relational shape as-was.
        archive: dict = {
            "schema_version": "v0.10.6.1-pre-drop",
            "captured_at": datetime.utcnow().isoformat() + "Z",
            "tables": {},
        }
        if recordings_present:
            recs = await _dump_table(conn, "recordings")
            archive["tables"]["recordings"] = recs
            for row in recs:
                logger.info("archive recordings row id=%s", row.get("id"))
        if media_files_present:
            mfs = await _dump_table(conn, "media_files")
            archive["tables"]["media_files"] = mfs
            for row in mfs:
                logger.info("archive media_files row id=%s", row.get("id"))

        total_rows = sum(len(v) for v in archive["tables"].values())
        logger.info("archive contains %d row(s) total", total_rows)

        if dry_run:
            logger.info(
                "[DRY-RUN] would write archive to %s and DROP TABLE "
                "media_files; DROP TABLE recordings. Not committing.",
                archive_path,
            )
            return 0

        # Write archive to disk before any DROP runs. Refuse to clobber.
        if archive_path.exists():
            logger.error(
                "archive path already exists: %s — refusing to overwrite. "
                "Move or delete the existing file and re-run.",
                archive_path,
            )
            return 2
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(json.dumps(archive, indent=2, default=str))
        logger.info("wrote archive to %s", archive_path)

        # Drop in FK-safe order: media_files first (it FKs to recordings),
        # then recordings. CASCADE on recordings drops any FK *constraints*
        # owned by other tables that reference it — notably
        # transcription_jobs.recording_id_fkey on legacy schemas. CASCADE
        # does NOT drop the dependent tables themselves, only the FK
        # constraint pointing at us; observed on LOCAL=1 compose stack
        # 2026-05-12 where transcription_jobs (0 rows in prod, dead-but-
        # present table) had an inbound FK. Adding CASCADE keeps the
        # archive contract intact: the rows in recordings/media_files are
        # archived to JSON before any DROP runs; no surprise data loss
        # via cascade-of-tables.
        if media_files_present:
            await conn.execute(text("DROP TABLE media_files CASCADE"))
            logger.info("dropped table media_files")
        if recordings_present:
            await conn.execute(text("DROP TABLE recordings CASCADE"))
            logger.info("dropped table recordings")

        logger.info(
            "summary: archived=%d rows; dropped=media_files=%s recordings=%s",
            total_rows, media_files_present, recordings_present,
        )
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be archived/dropped without committing.",
    )
    parser.add_argument(
        "--archive-path",
        type=Path,
        default=DEFAULT_ARCHIVE_PATH,
        help=f"Override archive output path (default: {DEFAULT_ARCHIVE_PATH}).",
    )
    args = parser.parse_args()
    rc = asyncio.run(run(dry_run=args.dry_run, archive_path=args.archive_path))
    sys.exit(rc)


if __name__ == "__main__":
    main()

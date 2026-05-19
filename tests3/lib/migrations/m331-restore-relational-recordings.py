#!/usr/bin/env python3
"""
═════════════════════════════════════════════════════════════════════════
  ROLLBACK COMPANION — RESTORE EMPTY RELATIONAL RECORDINGS TABLES
═════════════════════════════════════════════════════════════════════════

  WHAT THIS DOES:
    Re-creates the `recordings` and `media_files` tables matching the
    schema that existed in prod immediately BEFORE
    m331-drop-relational-recordings.py ran. Tables are created empty;
    no row-level rollback. Operators who need to restore the (at most
    2) stray rows can do so manually from the archive JSON written by
    the forward migration.

  IMPORTANT — prod schema vs. code model:
    The pre-v0.10.6.1 ORM model had grown columns that prod NEVER
    received (no migration existed for them): chunk_seq, is_final,
    finalized_by, chunk_count on recordings; and various extras on
    media_files. This rollback re-creates the ACTUAL prod schema, NOT
    the historical code model. If a deployment somehow got further
    along, the operator can add columns by hand — but the canonical
    pre-drop shape is what's here.

  BLAST RADIUS:
    - Safe everywhere: only creates tables if absent. Never drops.
    - Re-running after a partial restore is a no-op.
    - Adopting this rollback does NOT undo the v0.10.6.1 code
      deletions (Recording / MediaFile models, RECORDING_METADATA_MODE
      toggle). Operators wanting full rollback must also git-revert
      the code changes.

  USAGE:
      # See what would be created without committing:
      python3 tests3/lib/migrations/m331-restore-relational-recordings.py --dry-run

      # Commit:
      python3 tests3/lib/migrations/m331-restore-relational-recordings.py

  IDEMPOTENCY:
    - Uses CREATE TABLE IF NOT EXISTS. Safe to re-run.

  ENV: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_SSL_MODE
═════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "meeting-api"))

from sqlalchemy import text  # type: ignore[import]
from meeting_api.database import engine  # type: ignore[import]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [m331-restore] %(levelname)s %(message)s",
)
logger = logging.getLogger("m331-restore")


CREATE_RECORDINGS_SQL = """
CREATE TABLE IF NOT EXISTS recordings (
    id SERIAL PRIMARY KEY,
    meeting_id INTEGER REFERENCES meetings(id),
    user_id INTEGER NOT NULL,
    session_uid VARCHAR,
    source VARCHAR(50) NOT NULL DEFAULT 'bot',
    status VARCHAR(50) NOT NULL DEFAULT 'in_progress',
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
)
"""

CREATE_RECORDINGS_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS ix_recordings_meeting_id ON recordings(meeting_id)",
    "CREATE INDEX IF NOT EXISTS ix_recordings_user_id ON recordings(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_recordings_session_uid ON recordings(session_uid)",
    "CREATE INDEX IF NOT EXISTS ix_recordings_status ON recordings(status)",
    "CREATE INDEX IF NOT EXISTS ix_recordings_created_at ON recordings(created_at)",
    "CREATE INDEX IF NOT EXISTS ix_recording_meeting_session ON recordings(meeting_id, session_uid)",
    "CREATE INDEX IF NOT EXISTS ix_recording_user_created ON recordings(user_id, created_at)",
]

CREATE_MEDIA_FILES_SQL = """
CREATE TABLE IF NOT EXISTS media_files (
    id SERIAL PRIMARY KEY,
    recording_id INTEGER NOT NULL REFERENCES recordings(id),
    type VARCHAR(50) NOT NULL,
    format VARCHAR(20) NOT NULL,
    storage_path VARCHAR(1024) NOT NULL,
    storage_backend VARCHAR(50) NOT NULL DEFAULT 'minio',
    file_size_bytes INTEGER,
    duration_seconds DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT NOW()
)
"""

CREATE_MEDIA_FILES_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS ix_media_files_recording_id ON media_files(recording_id)",
]


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


async def run(dry_run: bool) -> int:
    async with engine.begin() as conn:
        recordings_present = await _table_exists(conn, "recordings")
        media_files_present = await _table_exists(conn, "media_files")

        logger.info(
            "current table presence: recordings=%s media_files=%s",
            recordings_present, media_files_present,
        )

        if recordings_present and media_files_present:
            logger.info("both tables already present — nothing to do (idempotent skip)")
            return 0

        if dry_run:
            logger.info(
                "[DRY-RUN] would CREATE TABLE IF NOT EXISTS recordings, media_files + indexes. Not committing."
            )
            return 0

        if not recordings_present:
            await conn.execute(text(CREATE_RECORDINGS_SQL))
            logger.info("created table recordings")
            for stmt in CREATE_RECORDINGS_INDEXES_SQL:
                await conn.execute(text(stmt))
            logger.info("created %d index(es) on recordings", len(CREATE_RECORDINGS_INDEXES_SQL))

        if not media_files_present:
            await conn.execute(text(CREATE_MEDIA_FILES_SQL))
            logger.info("created table media_files")
            for stmt in CREATE_MEDIA_FILES_INDEXES_SQL:
                await conn.execute(text(stmt))
            logger.info("created %d index(es) on media_files", len(CREATE_MEDIA_FILES_INDEXES_SQL))

        logger.info("rollback complete: tables restored empty")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be created without committing.",
    )
    args = parser.parse_args()
    rc = asyncio.run(run(dry_run=args.dry_run))
    sys.exit(rc)


if __name__ == "__main__":
    main()

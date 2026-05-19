#!/usr/bin/env python3
"""
═════════════════════════════════════════════════════════════════════════
  ONE-SHOT MIGRATION — RETIRE AFTER UNIVERSAL APPLICATION
═════════════════════════════════════════════════════════════════════════

  RETIRE-AFTER: When every known prod / self-hosted Vexa deployment has
  either (a) been deployed fresh with the v0.10.6.1+ model declaration
  (constraint present via create_all) or (b) had this script applied
  once. After retirement, delete this file. Recommended retirement
  trigger: post-v0.10.7 ship, after surveying self-hoster Discord +
  prod telemetry confirms no remaining duplicate (recording_id, type)
  rows in any active deployment.

  WHAT THIS DOES:
    - Dedup duplicate (recording_id, type) rows in media_files.
      Per #314, ~legacy rows accumulated under pre-Pack-E.1.a code
      (before 2026-04-21) when chunk_write would APPEND a new entry
      per chunk instead of mutating in place. The application-level
      filter has been correct for 19+ days; legacy rows persist.
    - Optionally adds the UniqueConstraint(recording_id, type) on
      existing DBs (--add-constraint).
    - Default: DRY-RUN. --apply commits the dedup. --add-constraint
      additionally runs ALTER TABLE.

  WHY THIS IS NOT IN MEETING-API'S BOOT PATH:
    - Multi-replica race: N pods would all attempt ALTER TABLE.
    - Destructive dedup must be operator-reviewed, not automatic.
    - Forever-living migration code in the service package is debt.

  USAGE (operator runs once per affected DB, from any host with DB env):

      # See the dedup plan without committing:
      python3 tests3/lib/migrations/m328-dedup-media-files.py

      # Commit the dedup only (data fix; no schema change):
      python3 tests3/lib/migrations/m328-dedup-media-files.py --apply

      # Belt-and-suspenders: dedup AND add the unique constraint
      # (matches what create_all gives fresh DBs):
      python3 tests3/lib/migrations/m328-dedup-media-files.py --apply --add-constraint

      # Read-only: report whether the constraint is present:
      python3 tests3/lib/migrations/m328-dedup-media-files.py --check

  DEDUP STRATEGY (per (recording_id, type) group, keep ONE row):
      1. storage_path ends in /master.{webm,wav}   ← winner
      2. else highest id (most recently inserted)  ← winner

  SAFETY:
    - Holds a Postgres advisory lock for the duration: concurrent
      runners serialize at the DB.
    - Single transaction: dedup + (optional) ALTER TABLE either both
      succeed or both roll back.

  ENV: DATABASE_URL (or the meeting-api DB envs DB_HOST/DB_PORT/...).
═════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
MEETING_API_PKG = os.path.join(ROOT, "services", "meeting-api")
if MEETING_API_PKG not in sys.path:
    sys.path.insert(0, MEETING_API_PKG)

from sqlalchemy import text  # type: ignore[import]
from meeting_api.database import engine  # type: ignore[import]

# Arbitrary 64-bit lock id — distinct from any other migration's lock.
ADVISORY_LOCK_ID = 7328000314


async def _check_constraint(conn) -> bool:
    res = await conn.execute(text("""
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_media_files_recording_type'
    """))
    return res.first() is not None


async def run(apply: bool, add_constraint: bool, check_only: bool) -> int:
    async with engine.begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_ID})

        already = await _check_constraint(conn)
        if check_only:
            print("OK: uq_media_files_recording_type is present." if already
                  else "MISSING: uq_media_files_recording_type is NOT present.")
            return 0 if already else 1

        # Identify duplicates.
        ranked_sql = text("""
            WITH ranked AS (
                SELECT
                    id, recording_id, type, storage_path,
                    ROW_NUMBER() OVER (
                        PARTITION BY recording_id, type
                        ORDER BY
                            CASE
                                WHEN storage_path LIKE '%/master.webm' THEN 0
                                WHEN storage_path LIKE '%/master.wav'  THEN 0
                                ELSE 1
                            END,
                            id DESC
                    ) AS rn
                FROM media_files
            )
            SELECT id, recording_id, type, storage_path, rn FROM ranked
            ORDER BY recording_id, type, rn
        """)
        rows = (await conn.execute(ranked_sql)).all()
        winners: list[tuple] = []
        losers: list[tuple] = []
        for row in rows:
            (winners if row.rn == 1 else losers).append(
                (row.recording_id, row.type, row.id, row.storage_path)
            )

        unique_groups = len({(rid, t) for (rid, t, _, _) in losers})
        print(f"\nmedia_files dedup plan — {len(losers)} loser row(s) across "
              f"{unique_groups} (recording_id,type) group(s)\n")

        from collections import defaultdict
        by_group: dict = defaultdict(list)
        for rid, t, mf_id, sp in losers:
            by_group[(rid, t)].append((mf_id, sp))
        for (rid, t), losers_in_group in sorted(by_group.items()):
            winner = next(((wid, wsp) for (wrid, wt, wid, wsp) in winners
                           if wrid == rid and wt == t), None)
            print(f"  recording_id={rid} type={t}")
            if winner:
                print(f"    KEEP   id={winner[0]} storage_path={winner[1]}")
            for mf_id, sp in losers_in_group:
                print(f"    DROP   id={mf_id} storage_path={sp}")

        if not apply:
            print("\nDRY-RUN — re-run with --apply to dedup. "
                  "Add --add-constraint to also ALTER TABLE.")
            return 0

        # APPLY path.
        if losers:
            del_res = await conn.execute(text("""
                WITH ranked AS (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY recording_id, type
                            ORDER BY
                                CASE
                                    WHEN storage_path LIKE '%/master.webm' THEN 0
                                    WHEN storage_path LIKE '%/master.wav'  THEN 0
                                    ELSE 1
                                END,
                                id DESC
                        ) AS rn
                    FROM media_files
                )
                DELETE FROM media_files
                WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
            """))
            print(f"\ndedup: deleted {del_res.rowcount or 0} row(s)")
        else:
            print("\ndedup: 0 rows to delete (already clean)")

        if add_constraint:
            if already:
                print("constraint: already present — skipping")
            else:
                await conn.execute(text("""
                    ALTER TABLE media_files
                    ADD CONSTRAINT uq_media_files_recording_type
                    UNIQUE (recording_id, type)
                """))
                print("constraint: ADDED uq_media_files_recording_type")
        else:
            if not already:
                print("constraint: NOT added (skip --add-constraint to keep "
                      "convention-only enforcement on this DB)")

        print("\nMIGRATION COMPLETE.")
        return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="commit changes (default: dry-run)")
    ap.add_argument("--add-constraint", action="store_true",
                    help="also run ALTER TABLE to add uq_media_files_recording_type "
                         "(belt-and-suspenders; matches what fresh DBs get via create_all)")
    ap.add_argument("--check", action="store_true",
                    help="exit 0 if constraint present, 1 if missing; no writes")
    args = ap.parse_args()
    rc = asyncio.run(run(apply=args.apply, add_constraint=args.add_constraint,
                         check_only=args.check))
    sys.exit(rc)


if __name__ == "__main__":
    main()

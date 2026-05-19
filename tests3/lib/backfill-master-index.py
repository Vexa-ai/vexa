#!/usr/bin/env python3
"""#314 backfill — collapse duplicate same-type media_files entries and
hoist the master entry to index 0 for the ~73 prod multi-chunk meetings.

The dashboard reader picks the master entry regardless of position
(commit-of-this-cycle), so this backfill is COSMETIC — it normalizes the
JSONB layout so external consumers (analytics, /raw exports, OpenAPI
clients) that still rely on `media_files[0]` for the canonical asset
behave correctly.

Usage:

    # dry-run (default) — prints which recordings would be touched
    python3 tests3/lib/backfill-master-index.py

    # apply changes (commits to the DB)
    python3 tests3/lib/backfill-master-index.py --apply

    # restrict to specific meeting ids
    python3 tests3/lib/backfill-master-index.py --meeting-ids 12345,12346

Env: DATABASE_URL (or the meeting-api DB envs DB_HOST/DB_PORT/...).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

# Hoist the meeting-api package onto the path so we reuse its DB plumbing
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
MEETING_API_PKG = os.path.join(ROOT, "services", "meeting-api")
if MEETING_API_PKG not in sys.path:
    sys.path.insert(0, MEETING_API_PKG)

from sqlalchemy import select
from sqlalchemy.orm import attributes

from meeting_api.database import async_session_local  # type: ignore[import]
from meeting_api.models import Meeting  # type: ignore[import]


def is_master_path(p: Any) -> bool:
    if not isinstance(p, str):
        return False
    return p.endswith("/master.webm") or p.endswith("/master.wav")


def normalize_media_files(media_files: list[dict]) -> tuple[list[dict], bool]:
    """Return (new_media_files, changed).

    For each (type) group: if a master entry exists, drop other entries
    of the same type and keep only the master at the same relative
    position the first entry of that type was at. If no master exists,
    leave the group untouched.
    """
    if not media_files:
        return media_files, False
    by_type: dict[str, list[int]] = {}
    for i, mf in enumerate(media_files):
        if not isinstance(mf, dict):
            continue
        t = mf.get("type")
        if not t:
            continue
        by_type.setdefault(t, []).append(i)

    drop_indexes: set[int] = set()
    promote: dict[int, dict] = {}  # earliest_idx → master_entry

    for t, indexes in by_type.items():
        if len(indexes) <= 1:
            continue
        master_idx = next(
            (i for i in indexes if is_master_path((media_files[i] or {}).get("storage_path"))),
            None,
        )
        if master_idx is None:
            # No master entry — leave alone.
            continue
        earliest = indexes[0]
        if earliest != master_idx:
            promote[earliest] = media_files[master_idx]
            drop_indexes.add(master_idx)
        # Drop every non-master same-type entry except the slot we'll
        # populate with the master at `earliest`.
        for i in indexes:
            if i == earliest:
                continue
            drop_indexes.add(i)

    if not drop_indexes and not promote:
        return media_files, False

    new_list: list[dict] = []
    for i, mf in enumerate(media_files):
        if i in drop_indexes:
            continue
        if i in promote:
            new_list.append(promote[i])
            continue
        new_list.append(mf)
    return new_list, True


async def run(apply: bool, meeting_ids: list[int] | None) -> None:
    touched = 0
    skipped = 0
    async with async_session_local() as db:
        stmt = select(Meeting)
        if meeting_ids:
            stmt = stmt.where(Meeting.id.in_(meeting_ids))
        rows = (await db.execute(stmt)).scalars().all()
        for meeting in rows:
            data = meeting.data
            if not isinstance(data, dict):
                continue
            recordings = data.get("recordings")
            if not isinstance(recordings, list):
                continue
            new_recordings: list[dict] = []
            changed_any = False
            for rec in recordings:
                if not isinstance(rec, dict):
                    new_recordings.append(rec)
                    continue
                mfs = rec.get("media_files") or []
                new_mfs, changed = normalize_media_files(mfs)
                if changed:
                    changed_any = True
                    rec = dict(rec)
                    rec["media_files"] = new_mfs
                new_recordings.append(rec)
            if changed_any:
                touched += 1
                action = "WOULD APPLY" if not apply else "APPLY"
                print(f"  [{action}] meeting_id={meeting.id} (recordings={len(recordings)})")
                if apply:
                    new_data = dict(data)
                    new_data["recordings"] = new_recordings
                    meeting.data = new_data
                    attributes.flag_modified(meeting, "data")
            else:
                skipped += 1

        if apply and touched > 0:
            await db.commit()

    print(f"\nbackfill summary: touched={touched} skipped={skipped} apply={apply}")
    if not apply and touched > 0:
        print("re-run with --apply to commit.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="commit changes (default: dry-run)")
    ap.add_argument(
        "--meeting-ids",
        type=lambda s: [int(x) for x in s.split(",") if x.strip()],
        default=None,
        help="comma-separated meeting ids (default: scan all)",
    )
    args = ap.parse_args()
    asyncio.run(run(apply=args.apply, meeting_ids=args.meeting_ids))


if __name__ == "__main__":
    main()

"""One-off backfill: write ``data.outcome_class`` / ``data.outcome_reason`` on terminal meetings
written before meeting-api stamped them at the terminal transition.

Uses the SAME classifier the write path uses (``meeting_api.lifecycle.outcome``), so a backfilled row
is indistinguishable from one graded live.

    # dry run (the default): read everything, write nothing, print class + reason counts
    DATABASE_URL=postgresql+asyncpg://… uv run python scripts/backfill_outcome_class.py

    # write, only where data.outcome_class is ABSENT (never overwrites a live-graded row)
    DATABASE_URL=… uv run python scripts/backfill_outcome_class.py --apply

Reads in keyset batches over ``meetings.id`` and selects only the ``data`` keys the classifier reads
(never ``bot_logs`` or recordings). Segment evidence is ``data.segments_captured`` when present (the
count persisted at the terminal write), else the live ``transcriptions`` count. Writes are a raw
``jsonb ||`` merge guarded by ``NOT (data ? 'outcome_class')``, so they neither clobber a concurrent
writer's keys nor bump ``updated_at`` (which the reconcile sweep reads as liveness). Non-terminal rows
are counted (``pending`` / stale ``never_terminated``) but never written.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys
from collections import Counter
from typing import Any, Iterable, Mapping, Optional

from meeting_api.lifecycle.outcome import TERMINAL, Outcome, outcome_for_meeting

#: The data keys the classifier reads (see outcome.row_from_db + outcome_for_meeting). The heavy
#: `speaker_events` list is never selected; only whether it is non-empty (`has_speaker_events`).
DATA_KEYS = (
    "completion_reason", "failure_stage", "reason", "failure_reason", "join_evidence",
    "last_error", "error_details", "constructed_meeting_url", "meeting_url", "transcribe_enabled",
    "stt_fault", "segments_captured", "artifact_deletion", "redacted", "status_transition",
    "participants", "stop_requested", "bot_exit_classification", "outcome_class",
)

_SELECT = """
SELECT m.id, m.status, m.platform, m.start_time, m.end_time, m.created_at,
       jsonb_strip_nulls(jsonb_build_object({keys})) AS data,
       COALESCE(jsonb_typeof(m.data->'speaker_events') = 'array'
                AND jsonb_array_length(m.data->'speaker_events') > 0, false) AS has_speaker_events,
       CASE WHEN m.data ? 'segments_captured' THEN NULL
            ELSE (SELECT count(*) FROM transcriptions t WHERE t.meeting_id = m.id) END AS live_segments
  FROM meetings m
 WHERE m.id > :after
 ORDER BY m.id
 LIMIT :limit
""".format(keys=", ".join(f"'{k}', m.data->'{k}'" for k in DATA_KEYS))

_UPDATE = """
UPDATE meetings
   SET data = data || jsonb_build_object('outcome_class', CAST(:cls AS text),
                                         'outcome_reason', CAST(:reason AS text))
 WHERE id = :id AND NOT (data ? 'outcome_class')
"""


def plan_row(row: Mapping[str, Any], now: Optional[dt.datetime] = None) -> tuple[Outcome, bool]:
    """(outcome, should_write) for one selected row. Pure — the unit the tests pin."""
    data = dict(row.get("data") or {})
    if row.get("has_speaker_events"):
        data["speaker_events"] = [True]  # presence is all row_from_db reads
    o = outcome_for_meeting(
        status=row.get("status"), data=data, platform=row.get("platform"),
        start_time=row.get("start_time"), end_time=row.get("end_time"),
        created_at=row.get("created_at"), segments=row.get("live_segments"), now=now,
    )
    terminal = str(row.get("status") or "").lower() in TERMINAL
    return o, terminal and "outcome_class" not in data


def summarize_plan(plans: Iterable[tuple[Outcome, bool]]) -> dict:
    by_class: Counter = Counter()
    by_reason: Counter = Counter()
    writes = 0
    for o, w in plans:
        by_class[o.cls] += 1
        by_reason[(o.cls, o.reason)] += 1
        writes += int(w)
    return {"by_class": by_class, "by_reason": by_reason, "writes": writes}


def _database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    return "postgresql+asyncpg://{u}:{p}@{h}:{port}/{n}".format(
        u=os.getenv("DB_USER", "postgres"), p=os.getenv("DB_PASSWORD", "postgres"),
        h=os.getenv("DB_HOST", "postgres"), port=os.getenv("DB_PORT", "5432"),
        n=os.getenv("DB_NAME", "vexa"),
    )


async def run(*, apply: bool, batch: int, limit: Optional[int]) -> dict:
    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.asyncio import create_async_engine

    select_stmt = text(_SELECT).columns(data=JSONB)  # typed, so asyncpg decodes the JSONB

    engine = create_async_engine(_database_url(), pool_size=1, max_overflow=0)
    plans: list[tuple[Outcome, bool]] = []
    written = 0
    after = 0
    now = dt.datetime.now(dt.timezone.utc)
    try:
        while True:
            async with engine.connect() as conn:
                rows = (await conn.execute(select_stmt, {"after": after, "limit": batch})).mappings().all()
            if not rows:
                break
            batch_plans = [(r["id"], *plan_row(r, now=now)) for r in rows]
            plans.extend((o, w) for _, o, w in batch_plans)
            if apply:
                todo = [{"id": i, "cls": o.cls, "reason": o.reason} for i, o, w in batch_plans if w]
                if todo:
                    async with engine.begin() as conn:
                        for p in todo:
                            written += (await conn.execute(text(_UPDATE), p)).rowcount or 0
            after = rows[-1]["id"]
            print(f"… scanned through id {after} ({len(plans)} rows)", file=sys.stderr)
            if limit and len(plans) >= limit:
                break
    finally:
        await engine.dispose()
    out = summarize_plan(plans)
    out["written"] = written
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="read only (default)")
    mode.add_argument("--apply", action="store_true", help="write outcome where it is absent")
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--limit", type=int, default=None, help="stop after about this many rows")
    a = ap.parse_args(argv)

    res = asyncio.run(run(apply=a.apply, batch=a.batch, limit=a.limit))
    total = sum(v for k, v in res["by_class"].items() if k != "pending")
    print(f"{'APPLY' if a.apply else 'DRY RUN'}: {total} terminal-or-stale rows graded"
          f" · would write {res['writes']} · wrote {res['written']}")
    for cls, n in res["by_class"].most_common():
        print(f"  {cls:18} {n}")
    print("by reason:")
    for (cls, reason), n in res["by_reason"].most_common():
        print(f"  {cls:18} {reason:42} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

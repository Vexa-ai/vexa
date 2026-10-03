"""The transcript read cursor (`?since=`) — one definition of the incremental-fetch semantics, shared
by the production store and the in-memory fake so the two can never drift.

A follower polls a live transcript every few seconds. Without a cursor it re-downloads the whole
transcript every time, so following ONE two-hour meeting costs ~172MB to learn ~250KB of new text —
quadratic, because the payload and the poll count both grow with meeting length.

**The cursor is a CHANGE watermark, not an append pointer.** A segment is not append-only: a live
draft (`completed: false`) is rewritten in place as its confirmation, a speaker can be repainted
retroactively across already-served segments, and a superseded draft can be retracted outright. An
"after this segment id" cursor would hand a follower the draft and never the confirmation — silently
stale, which is worse than no cursor at all. `segment_id` is also unsortable by construction (three
producer-local mint formats, none of them ordered). Filtering on a change-stamp re-delivers a
revision exactly the way it delivers a new segment.

**Why the stamp is safe.** It is monotone across the two homes a segment lives in:

  * LIVE (redis hash `meeting:{id}:segments`) — `updated_at`, stamped by ingest on EVERY write.
  * DURABLE (postgres `transcriptions`) — `created_at`, which the db-writer's upsert sets to
    `utcnow()` on insert AND on conflict-update, i.e. the last-flush time.

A segment only moves live→durable once it has been immutable for `IMMUTABILITY_THRESHOLD` (30s), so
its durable stamp is always LATER than its final live stamp. The stamp never moves backwards, so a
watermark can never step over a segment.

**Every ambiguity resolves toward sending more.** Re-delivery is free — segments are idempotent by
`segment_id`, and every client already upserts on it — while a skip is data loss the follower cannot
detect. So: an unstamped segment is always returned, an unparsable stamp is always returned, the
watermark is taken before the read and lagged, and a cursor whose retraction window has expired is
refused in favour of a full re-read.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

# The watermark lags read-start by this much, so a segment written concurrently with the read is
# re-sent on the next poll rather than skipped. It also absorbs clock skew between the ingest process
# and this one — separate containers, possibly separate nodes, and both stamps are process-local
# wall-clock. At the measured 6 segments/minute a 2s lag re-sends 0.2 segments per poll.
SAFETY_LAG_SEC = float(os.environ.get("TRANSCRIPT_CURSOR_LAG_SEC", "2"))

# Retraction tombstones share the live segment hash's TTL, so the cursor's deletion window matches the
# window in which live segments exist at all.
RETRACTION_TOMBSTONE_TTL_SEC = int(os.environ.get("REDIS_SEGMENT_TTL", "3600"))


def retractions_hash_key(meeting_id) -> str:
    """The retraction tombstones for one meeting — ``{segment_id: retracted_at}``."""
    return f"meeting:{meeting_id}:retracted"


def iso(dt: datetime) -> str:
    """UTC ISO-8601 (``…Z``) — the stamp format the cursor emits and compares."""
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")


def now_iso() -> str:
    """``iso`` for this instant."""
    return iso(datetime.now(timezone.utc))


def watermark(now: Optional[datetime] = None) -> datetime:
    """The `next_since` a response advertises: read-start minus the safety lag."""
    return (now or datetime.now(timezone.utc)) - timedelta(seconds=SAFETY_LAG_SEC)


def is_stale(since: Optional[datetime]) -> bool:
    """Whether a cursor predates the retraction-tombstone window — meaning a retraction inside it may
    already have expired unseen, so the incremental answer could not be shown to be complete. Such a
    cursor is IGNORED and a full transcript served (`resynced: true`): a cursor we can no longer
    honour completely degrades to a full read, never to a quietly wrong one."""
    if since is None:
        return False
    return since < datetime.now(timezone.utc) - timedelta(seconds=RETRACTION_TOMBSTONE_TTL_SEC)


def parse_stamp(raw) -> Optional[datetime]:
    """A stored ``updated_at`` → aware UTC datetime, or None when absent/unparsable."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def changed_since(seg: dict, since: Optional[datetime]) -> bool:
    """Whether a segment is at-or-after the cursor. FAIL OPEN: a segment carrying no parsable
    `updated_at` (a legacy producer) is always returned — an unstamped segment must never be the one
    the follower silently loses."""
    if since is None:
        return True
    stamp = parse_stamp(seg.get("updated_at"))
    return True if stamp is None else stamp >= since

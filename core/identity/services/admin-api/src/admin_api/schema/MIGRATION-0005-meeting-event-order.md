# MIGRATION-0005 — `meeting_event_time()` + the event-order list indexes (#1222)

**Status:** function added to `ensure_schema` (`_sync_functions`, runs before any index DDL) and
the two indexes added to the SSOT model (`schema/models.py`) + the meeting-api mirror
(`meeting-api/.../sessions/models.py`). On an **existing** prod/staging DB the function + indexes
are an **out-of-band ops step that MUST precede the deploy** — the new `list_meetings` ORDER BY
calls `meeting_event_time()` directly, so a meeting-api serving the new code against a DB without
the function fails every list request. Fresh/empty DBs (tests, new envs) converge cleanly via
`ensure_schema` and need no manual step.

## What this is

The meetings list (`GET /meetings`, `GET /bots`) orders by **meeting event time** with
**non-terminal rows pinned first**, instead of `created_at DESC`. A calendar-managed row is
created at *import* time — possibly days before the meeting — so `created_at` buried a meeting
that was live right now under every row created since the import (witnessed in production
2026-08-18, row 26298: imported Aug 16 22:40, scheduled Aug 18 09:00, sat at list position 19
while the founder was in the meeting).

```sql
CREATE OR REPLACE FUNCTION meeting_event_time(
    data jsonb, start_time timestamp, created_at timestamp
) RETURNS timestamp
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
BEGIN
    RETURN COALESCE(
        ((data ->> 'scheduled_at')::timestamptz AT TIME ZONE 'UTC'),
        start_time,
        created_at
    );
EXCEPTION WHEN OTHERS THEN
    RETURN COALESCE(start_time, created_at);
END
$fn$;
```

Why a function: the list's union branches are top-N **index walks** (#800), so the sort key needs
an expression index, and index expressions must be IMMUTABLE — a bare text→timestamptz cast is
only STABLE. Declaring the cast IMMUTABLE is sound here because `scheduled_at` is written as
ISO-8601 (calendar sync and the meetings API validate it) and anything unparsable falls through
the exception guard instead of failing row writes via index maintenance.

```sql
CREATE INDEX ix_meeting_user_event_order ON meetings (
    user_id,
    (status IN ('active', 'awaiting_admission', 'joining', 'requested', 'scheduled', 'stopping')),
    meeting_event_time(data, start_time, created_at),
    id
);
CREATE INDEX ix_meeting_workspace_event_order ON meetings (
    (data ->> 'workspace_id'),
    (status IN ('active', 'awaiting_admission', 'joining', 'requested', 'scheduled', 'stopping')),
    meeting_event_time(data, start_time, created_at),
    id
);
```

The expressions are **verbatim** the ORDER BY in `collector/adapters.py::list_meetings` — the
planner only substitutes an expression index when the query expression matches it structurally.
`ix_meeting_user_created_at` / `ix_meeting_workspace_created_at` stay: internal enumeration
(get-by-id filter, `/bots/status`, calendar sync) still orders by `created_at DESC`.

## Production rollout (run in this ORDER, before deploying)

Run as standalone psql statements (no `BEGIN` — `CONCURRENTLY` refuses transaction blocks).

### 1. Create the function (instant, no lock concerns)

Run the `CREATE OR REPLACE FUNCTION` above verbatim.

### 2. Sanity-check the data (read-only)

The exception guard makes malformed `scheduled_at` harmless, but know what's there:

```sql
SELECT count(*) FROM meetings
WHERE data ? 'scheduled_at'
  AND meeting_event_time(data, start_time, created_at) = COALESCE(start_time, created_at);
```

Rows counted here have a `scheduled_at` the cast rejected (fell through to the guard). Expect 0.

### 3. Build both indexes without locking writes

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_meeting_user_event_order ON meetings (
    user_id,
    (status IN ('active', 'awaiting_admission', 'joining', 'requested', 'scheduled', 'stopping')),
    meeting_event_time(data, start_time, created_at),
    id
);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_meeting_workspace_event_order ON meetings (
    (data ->> 'workspace_id'),
    (status IN ('active', 'awaiting_admission', 'joining', 'requested', 'scheduled', 'stopping')),
    meeting_event_time(data, start_time, created_at),
    id
);
```

A failed `CONCURRENTLY` build leaves an INVALID index — check and drop-retry:

```sql
SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;
```

### 4. Verify the plan, then deploy

```sql
EXPLAIN SELECT id FROM meetings WHERE user_id = <heavy_user>
ORDER BY (status IN ('active', 'awaiting_admission', 'joining', 'requested', 'scheduled', 'stopping')) DESC,
         meeting_event_time(data, start_time, created_at) DESC,
         id DESC
LIMIT 51;
```

Must show `Index Scan Backward using ix_meeting_user_event_order` with no Sort node. With the
function + indexes committed, the admin-api boot's `ensure_schema` finds them (function via
`CREATE OR REPLACE`, indexes by name) and no-ops.

## Deploy-order hazard

`meeting_event_time()` is called by the new list query itself, not only by the indexes. Deploying
the new meeting-api against a DB where step 1 never ran → every `GET /meetings` / `GET /bots`
errors (`function meeting_event_time(jsonb, ...) does not exist`). In compose/lite the admin-api
(which runs `ensure_schema` on boot) and meeting-api ship together and healthcheck-order covers
it; on prod, run steps 1–3 first.

## Rollback

Revert the code deploy (the old query never references the function), then optionally:

```sql
DROP INDEX CONCURRENTLY IF EXISTS ix_meeting_user_event_order;
DROP INDEX CONCURRENTLY IF EXISTS ix_meeting_workspace_event_order;
DROP FUNCTION IF EXISTS meeting_event_time(jsonb, timestamp, timestamp);
```

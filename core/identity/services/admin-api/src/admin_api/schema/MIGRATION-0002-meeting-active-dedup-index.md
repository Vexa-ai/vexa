# MIGRATION-0002 — `uq_meeting_active_user_platform_native` (ROB1/ROB2 spawn-dedup backstop)

**Status:** index added to the SSOT model (`schema/models.py`) + O-STACK eval
(`tests/test_stack_postgres.py::test_meeting_active_unique_partial_index`). The DB-level build on
an **existing** prod/staging DB is an **out-of-band, human-run ops step that MUST precede the
deploy** — see "Production rollout" below. Fresh / empty DBs (tests, new envs) build it cleanly via
`ensure_schema` and need no manual step.

**Cross-domain:** the meeting domain owns the spawn primitive (`meeting-api` →
`bot_spawn/adapters.py::create_meeting_guarded`) and the per-service model mirror
(`meeting-api/.../sessions/models.py`). The **identity** domain owns the schema SSOT + convergence
(`admin-api/.../schema/`). This index lives in BOTH; **the identity/admin-api owner must sign off on
the dedup policy + the build before the SSOT change deploys.**

## What the index is

```sql
CREATE UNIQUE INDEX uq_meeting_active_user_platform_native
ON meetings (user_id, platform, platform_specific_id)
WHERE status NOT IN ('completed', 'failed');
```

At most ONE active (non-terminal) meeting per `(user_id, platform, platform_specific_id)`. It is the
DB-level backstop for the atomic spawn dedup: `create_meeting_guarded` holds a
`pg_advisory_xact_lock(user_id)` (serializes same-process spawns) and relies on this index to catch
the **cross-process / cross-instance** race the advisory lock cannot cover — the resulting
`IntegrityError` is mapped to `DuplicateMeeting` (HTTP 409).

## Why this can't just ride `ensure_schema` on a live DB

`_sync_indexes` (`schema/sync.py`) is additive and matches existing indexes **by name**, so it
*will* emit this index on a DB that lacks it. But against a populated `meetings` table there are
three hazards, all of which the out-of-band build avoids:

1. **A dirty table makes the build fail.** If `meetings` already holds ≥2 active rows for the same
   `(user, platform, native)`, `CREATE UNIQUE INDEX` raises `UniqueViolation`.
2. **The failure used to be swallowed silently.** Pre-this-change `_sync_indexes` logged the failure
   at `debug` and moved on → the backstop would silently not exist, dedup degrading to advisory-lock-
   only. (This change hardens it: per-index `SAVEPOINT` so a failure no longer poisons the whole
   convergence txn, and a **WARNING** for any failed *unique* index. It is still not a substitute for
   building the index cleanly — a WARNING + missing backstop is not an acceptable prod state.)
3. **No `CONCURRENTLY` via convergence.** `ensure_schema` runs inside one transaction;
   `CREATE INDEX CONCURRENTLY` cannot run in a transaction block. A plain `CREATE INDEX` takes an
   `ACCESS EXCLUSIVE`-ish lock that blocks writes to `meetings` for the build duration — not
   acceptable on the hot spawn table in prod.

## Production rollout (run in this ORDER, before deploying the SSOT change)

Run against prod **as standalone statements** (psql, not wrapped in a `BEGIN`).

### 1. Pre-flight — are there active duplicates? (read-only)

`platform_specific_id IS NOT NULL` because a standard unique index treats NULLs as DISTINCT — rows
with a NULL native id never collide and must not be touched.

```sql
SELECT user_id, platform, platform_specific_id,
       count(*) AS active_dups,
       array_agg(id ORDER BY created_at DESC, id DESC) AS meeting_ids
FROM meetings
WHERE status NOT IN ('completed', 'failed')
  AND platform_specific_id IS NOT NULL
GROUP BY user_id, platform, platform_specific_id
HAVING count(*) > 1
ORDER BY active_dups DESC;
```

If this returns **zero rows**, skip step 2 and go straight to step 3.

### 2. One-time dedup — only if step 1 returned rows  ⚠ NEEDS OWNER SIGN-OFF

**Recommended policy (confirm before running):** keep the **most-recently-created** active row per
key; retire the older active duplicates to a terminal status (`failed`, which the partial predicate
excludes) and stamp `data.dedup` for auditability. This matches dedup's "an active meeting already
exists" semantics and is reversible via the stamp. The losing rows are stale spawns that the new
constraint would have rejected anyway.

> Open question for the owner: is `failed` the right terminal status for a retired duplicate, or
> should it be `completed` / a new `superseded` value? And is "newest active wins" correct, or should
> the row with a live `bot_container_id` win regardless of age? Decide before running.

```sql
WITH ranked AS (
  SELECT id,
         row_number() OVER (
           PARTITION BY user_id, platform, platform_specific_id
           ORDER BY created_at DESC, id DESC
         ) AS rn
  FROM meetings
  WHERE status NOT IN ('completed', 'failed')
    AND platform_specific_id IS NOT NULL
)
UPDATE meetings m
SET status = 'failed',
    data   = jsonb_set(coalesce(m.data, '{}'::jsonb), '{dedup}',
                       '{"reason":"rob1_rob2_active_dedup","migration":"0002"}'::jsonb)
FROM ranked r
WHERE m.id = r.id AND r.rn > 1;
```

Re-run step 1; it must now return zero rows.

### 3. Build the index without locking writes

```sql
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_meeting_active_user_platform_native
ON meetings (user_id, platform, platform_specific_id)
WHERE status NOT IN ('completed', 'failed');
```

`CONCURRENTLY` cannot run inside a transaction block. If it fails partway it leaves an **INVALID**
index — drop and retry:

```sql
SELECT indexrelid::regclass FROM pg_index
WHERE NOT indisvalid AND indexrelid::regclass::text = 'uq_meeting_active_user_platform_native';
-- if present:
DROP INDEX CONCURRENTLY uq_meeting_active_user_platform_native;
```

### 4. Deploy the SSOT change

With the index already present and committed, the admin-api boot's `ensure_schema` finds
`uq_meeting_active_user_platform_native` in the existing-index set (matched by name) and **no-ops** —
the swallow/poison hazard never triggers because there is nothing left to build.

## Rollback

`DROP INDEX CONCURRENTLY IF EXISTS uq_meeting_active_user_platform_native;` then revert the SSOT
change. The dedup in step 2 is not auto-reversed, but every retired row carries `data.dedup` so the
affected rows are queryable (`WHERE data ? 'dedup'`).

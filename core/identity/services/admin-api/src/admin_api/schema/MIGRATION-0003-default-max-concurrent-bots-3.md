# MIGRATION-0003 — raise default `max_concurrent_bots` 1 → 3 (all users, incl. existing)

**Status:** SSOT model updated (`schema/models.py` — `server_default="3", default=3`) plus the two
default fallbacks that duplicated the old `1`:

- `admin-api/.../app/main.py` — `UserCreate.max_concurrent_bots: int = 3` (admin-created users default to 3)
- `gateway/.../adapters.py` + `gateway/.../app.py` — the `user_data.get("max_concurrent", 3)` fallback used
  only when `/internal/validate` omits the field (the authoritative value still comes from the user row).

The DB step below is an **out-of-band, human-run ops step** for **existing** prod/staging DBs. Fresh /
empty DBs (tests, new envs) pick up `server_default="3"` from `ensure_schema`'s `create_all` and need no
manual step.

## Why code-only is NOT enough on a live DB

`ensure_schema` (`schema/sync.py`) is **additive-only**:

- `_sync_columns` runs `ALTER TABLE … ADD COLUMN` **only for columns that don't yet exist**
  (`if col.name in existing_cols: continue`). On a DB where `users.max_concurrent_bots` already exists it
  is a no-op — it does **not** `ALTER … SET DEFAULT` and it never rewrites row values.
- So on an existing DB, without this migration: the column DEFAULT stays `1`, and every existing row keeps
  its current value. The model/app change alone would only affect *new users created through the admin API*
  (which now passes `3` explicitly).

Hence two explicit statements: fix the column DEFAULT (for any direct insert that relies on it) and backfill
existing rows.

## Backfill policy — RAISE-ONLY (never lower)

Confirmed policy: bump everyone on the old default up to 3, but **preserve any user already granted more
than 3** (power/enterprise accounts). The `WHERE max_concurrent_bots < 3` predicate is what makes this
non-destructive and idempotent.

## Production rollout

Safe to run **before or right after** the deploy — the app already passes an explicit value on user
create, so ordering is not load-bearing. Run against prod as standalone statements (psql). The `users`
table is small and single-column; a plain `UPDATE` is fine (no `CONCURRENTLY` needed).

### 1. Pre-flight — who is below the new default? (read-only)

```sql
SELECT max_concurrent_bots AS current_limit, count(*) AS users
FROM users
GROUP BY max_concurrent_bots
ORDER BY current_limit;
```

Rows with `current_limit < 3` are the ones step 3 will raise; rows `>= 3` are left untouched.

### 2. Column default 1 → 3

```sql
ALTER TABLE users ALTER COLUMN max_concurrent_bots SET DEFAULT 3;
```

### 3. Backfill existing rows (raise-only, idempotent)

```sql
UPDATE users
SET max_concurrent_bots = 3
WHERE max_concurrent_bots < 3;
```

Re-run step 1; there must be no rows with `current_limit < 3`.

### 4. Deploy the SSOT change

`ensure_schema` finds the `max_concurrent_bots` column already present and no-ops on it. New envs build
the `DEFAULT 3` directly from the model.

## Rollback

Revert the code, then restore the old column default:

```sql
ALTER TABLE users ALTER COLUMN max_concurrent_bots SET DEFAULT 1;
```

The backfill is **not** auto-reversed — prior per-user values were not recorded (only users below 3 were
raised, so a data rollback cannot distinguish an intentional `1` or `2` from a raised one). If a hard
revert of the raised rows is required, it must be reconstructed from a DB backup taken before step 3.

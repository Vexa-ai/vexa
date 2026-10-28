# MIGRATION-0008: the org as a first-class object (`orgs`, `org_members`, `users.org_id`)

**Status:** in the SSOT model (`schema/models.py`); `ensure_schema` applies it in-band, on a live
database, with no out-of-band step. One org per install: nothing anybody sees changes.

## What it adds

| Object | Shape | How it lands on an existing database |
|---|---|---|
| `orgs` | `id` (PK, text), `name`, `kind` (`'org'` or `'personal'`), `policy_source` (`'_global'`), `crm_org_id`, `billing_account`, `data`, `created_at` | `CREATE TABLE` (new, empty) |
| `org_members` | PK `(org_id → orgs ON DELETE CASCADE, user_id → users ON DELETE CASCADE)`, `role` (`'member'`), `created_at`; index on `user_id` | `CREATE TABLE` (new, empty) |
| `users.org_id` | `VARCHAR(64) NOT NULL DEFAULT 'default'`, no FK, no index | `ALTER TABLE users ADD COLUMN … DEFAULT 'default'` |
| the default org | row `('default', 'Default', '_global', $CRM_ORG_ID or NULL)` | `INSERT … ON CONFLICT (id) DO NOTHING` |

The CRM binding (`orgs.crm_org_id`) is stamped from admin-api's `CRM_ORG_ID` (the value crm-api
runs as) only while it is NULL, so an operator's later choice is never overwritten.

## How it works

- An account's home org is `users.org_id`. It is a member there **without** an `org_members` row;
  its role is derived (`admin` for the instance admin, else `member`; `app/orgs.py`). So the
  migration writes **no row per user**. A row in `org_members` is a role override, or (later) a
  membership in a second org.
- `GET /internal/users/{id}/org` and `GET /internal/orgs/{id}` (internal tier, read-only) answer an
  account's org and an org's record. crm-api asks the first only when `personal_crm` is on.
- `POST /internal/users/{id}/personal-org` (internal tier, explicit, idempotent) makes an account an
  individual one: its home org becomes its own org of one, `p-<id>`, kind `personal`, which it
  administers. That org is the boundary of the person's personal CRM. Nothing calls it yet.
- agent-api resolves every policy read through the org (`control_plane/orgs.py`): the default org's
  `policy_source` is `_global`, so `system_mounts.global_root` answers exactly what it did before.
  A workspace's org is the `org` on its record, else the default org; no workspace file is rewritten.

## Why it is safe on a live database

- **No table rewrite, no per-row UPDATE.** `users.org_id` arrives with a constant default, which
  Postgres 11 and later keep in the catalog: existing rows read `'default'` without being touched.
  `test_orgs_migration_pg.py` pins this: the `users` relfilenode and every row's `xmin` are unchanged.
- **Locks are brief and bounded.** `ADD COLUMN` takes `ACCESS EXCLUSIVE` on `users` for a catalog
  update only; creating `org_members` takes `SHARE ROW EXCLUSIVE` on `users` (for the FK trigger),
  which blocks writes to `users` for that instant but not reads. Every lock is asked for with the
  convergence's `lock_timeout` (500 ms) and retried with backoff (`sync.py`), so a reader never
  queues behind a migration waiting for its lock (pinned by the same test file).
- **Nothing is built in-band on a big table.** No index and no FK on `users.org_id`: both would scan
  or lock `users`. They are out-of-band steps for when a second org exists (below).
- **Old code keeps working.** It never names `org_id` (its inserts get the default) and never reads
  `orgs` or `org_members`.

Requires Postgres 11 or later (every supported deployment runs 16).

## Later, out of band (when a second org exists, not now)

Run as standalone statements (psql, not inside a `BEGIN`):

```sql
CREATE INDEX CONCURRENTLY ix_users_org_id ON users (org_id);
ALTER TABLE users ADD CONSTRAINT fk_users_org FOREIGN KEY (org_id) REFERENCES orgs (id) NOT VALID;
ALTER TABLE users VALIDATE CONSTRAINT fk_users_org;   -- SHARE UPDATE EXCLUSIVE: reads and writes continue
```

Only after that may `models.py` carry the index and the FK.

## Rollback

Safe at any time, in this order, and nothing else depends on these objects:

```sql
DROP TABLE IF EXISTS org_members;
DROP TABLE IF EXISTS orgs;
ALTER TABLE users DROP COLUMN IF EXISTS org_id;
```

`DROP COLUMN` is a catalog change (no rewrite). Deploy the previous image **first**: this release's
admin-api would re-create all three on its next start. With one org per install nothing is lost but
an explicit `org_members` role override, and none exist yet. `test_orgs_migration_pg.py` runs these
statements and checks that the accounts survive and that a later roll-forward re-applies.

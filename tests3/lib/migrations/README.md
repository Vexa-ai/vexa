# Migrations — the hand-rolled convention

Vexa does NOT use alembic or any other migration framework. Schema is managed by two cooperating mechanisms:

1. **SQLAlchemy `Base.metadata.create_all()`** — additive-only schema sync at meeting-api boot. Picks up new tables and new indexes; never alters existing columns, never drops anything.
2. **Hand-rolled `m<NNN>-<slug>.py` scripts in this directory** — every destructive change (DROP, ALTER, data migration, backfill) is a deliberate Python script written by hand and run explicitly via the helm `job-migrations.yaml` Job or via `kubectl exec` against a meeting-api pod.

That's the whole system. This README documents the convention so the next contributor doesn't reverse-engineer it from `database.py`.

---

## When `create_all` is enough

Use cases:

- Adding a new ORM model (new `class X(Base):` in `services/meeting-api/meeting_api/models.py` or peer service models).
- Adding a new column to an existing model **on fresh databases** — but see the caveat below.

Caveat: `create_all` is **additive**. It runs `CREATE TABLE IF NOT EXISTS` per model. If the table already exists, it leaves the existing columns alone. A new column added to an ORM model **will not appear in an existing database** without a hand-rolled migration. This is the schema-drift class — see issue #328 for an example (the `MediaFile` model's `UniqueConstraint` did not land on pre-existing DBs).

If you're adding a column that any prod-deployed code needs to read or write, you must also write a hand-rolled migration.

## When a hand-rolled `m<NNN>-<slug>.py` script is required

ALL of these:

- `DROP TABLE`, `DROP COLUMN`, `DROP INDEX`, `DROP CONSTRAINT`
- `ALTER TABLE` (add column, change type, change nullability, add/drop constraint)
- Data migrations (e.g. backfilling JSONB fields, rewriting denormalised columns)
- Cross-table merges or splits
- Anything that needs to run on a specific DB revision rather than at every boot

Each script is one-shot, idempotent (re-running on already-migrated data is a no-op), and explicitly invoked. Never wired into `init_db()`.

## Naming convention

`m<issue-or-pack-id>-<slug>.py`

Examples:

- `m314-backfill-multichunk-master-index.py` — backfill for issue #314 (multichunk recordings).
- `m328-dedup-media-files.py` — addresses issue #328 (duplicate `(recording_id, type)` rows).
- `m331-drop-relational-recordings.py` — drops the dead `recordings` + `media_files` tables (v0.10.6.1).

The `<issue-or-pack-id>` should be a GH issue number when there is one, or a release-pack slug when the migration is part of a broader pack with no single issue.

Forward and rollback scripts come as pairs: `m<NNN>-<forward-slug>.py` and `m<NNN>-restore-<slug>.py` (or `m<NNN>-rollback-<slug>.py`). The rollback should:

- Reverse the forward script's data changes where reasonable.
- Recreate dropped tables/columns as empty stubs (data IS lost on a drop; rollback restores schema only).
- Be safe to run even if the forward script never ran (idempotent in both directions).

## Required content of a forward script

Every `m<NNN>-*.py` must contain, in this order:

1. **Module docstring** — what this script does in one paragraph, the issue/pack id it addresses, and any non-obvious assumptions.
2. **Blast-radius declaration** — a top-of-file comment block stating: who is affected if this runs wrong, what the worst-case data loss looks like, how to roll back.
3. **A `--dry-run` flag** — when set, the script reports what it would change without writing. Operators run `--dry-run` first, eyeball the output, then run without it.
4. **Idempotency check** — at the top of each per-row operation, verify whether the row has already been migrated; skip if so. Re-running the script after a partial run must be safe.
5. **Forward action** — the actual `ALTER TABLE` / `UPDATE` / etc.
6. **Logging** — print one line per affected row (or per N rows for large migrations) so the operator sees progress.
7. **Summary line at end** — total rows touched, skipped (already-migrated), errored.

Connect to the DB via the same env vars (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_SSL_MODE`) that meeting-api uses. Do not invent a new auth path.

## How the Helm Job invokes them

`deploy/helm/charts/vexa/templates/job-migrations.yaml` defines a Kubernetes Job that runs at chart-upgrade time. Per release, the operator edits the Job's command list to include the new migration script(s) for that cycle. Once the Job completes, the migration has run on the LKE cluster.

For compose deployments: operator runs the script via `docker compose exec meeting-api python3 /app/tests3/lib/migrations/m<NNN>-*.py` (path relative to the meeting-api image).

For lite: same shape; `docker exec` into the vexa-lite container.

## Tracking what's run

Right now: there is no `alembic_version`-style table. The fact that a migration has run is observable from its effects (the column exists, the data is backfilled). That's brittle.

**v0.10.7 will add a drift-detection prove** (`schema-drift-detection` pack) that compares prod's `information_schema.columns` against the code model at validate time, flagging any column the model declares but the DB lacks. That's the substitute for an `alembic_version` table — instead of tracking "what scripts ran," we verify "the DB matches the code." The same prove catches the case where a script *didn't* run and should have.

## Why no alembic

Recurring temptation: adopt alembic, get auto-generated migrations, frictionless schema evolution. Rejected explicitly in v0.10.6.1 ADR-3:

- Frictionless migrations encourage drift via "just add a column." The friction at every destructive change is the *feature*, not the cost.
- Auto-generated DDL is often subtly wrong (missing `CASCADE`, wrong type widths, no online-DDL options).
- Principle 4 ("no DB migrations unless explicitly decided") is diluted by tooling that makes migrations frictionless.

The trade-off cost: there's no formal record of "what schema version is this DB at." We accept that cost; v0.10.7's drift-detection prove substitutes by checking that the *current* schema matches the *current* model, which is the question that actually matters.

## Adding a new migration — checklist

1. Decide if `create_all` covers it. If yes, no migration script needed.
2. Pick the `m<NNN>-<slug>.py` filename. Drop it in this directory.
3. Write the script with all seven required sections above.
4. Write the companion `m<NNN>-restore-<slug>.py` rollback script.
5. Test in LOCAL=1 compose first. Verify `--dry-run` lists the right rows. Verify idempotency by running it twice.
6. Add the migration to the helm chart's Job command list (`deploy/helm/charts/vexa/templates/job-migrations.yaml`).
7. Reference the migration in the corresponding `scope.yaml` issue block's `migration_decision:` field.
8. Document the rollback path in `scope.md`'s blast-radius statement for that issue.

## Existing scripts

| Script | Purpose | Issue |
|---|---|---|
| `m328-dedup-media-files.py` | Dedups duplicate `(recording_id, type)` rows in `media_files`, then adds the `UniqueConstraint` declared by the model | #328 |
| `m331-drop-relational-recordings.py` | Drops dead `recordings` + `media_files` tables (data archived to JSON first) | v0.10.6.1 cleanup |
| `m331-restore-relational-recordings.py` | Restores empty `recordings` + `media_files` tables; rollback for the above | v0.10.6.1 cleanup |
| `m314-backfill-multichunk-master-index.py` | Backfills `finalized_by` + `playback_url` for ~73 historical multichunk recordings | #314 |
| `m314-restore-multichunk-master-index.py` | Rollback for the above | #314 |

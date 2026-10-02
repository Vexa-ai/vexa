# migrate — the 0.10 → 0.12 database migration tool

`python -m admin_api.migrate check` / `python -m admin_api.migrate run --fix`.

`../schema/sync.py` (`ensure_schema()`) converges the database to the models on every boot, inside
one transaction, additively. Two steps of the 0.10 → 0.12 upgrade fall outside that on a populated
database, and this package is those two:

1. **dedup** — retire duplicate ACTIVE meeting rows per `(user_id, platform,
   platform_specific_id)`. Convergence never rewrites an existing row's values.
2. **the index** — build `uq_meeting_active_user_platform_native` with `CREATE UNIQUE INDEX
   CONCURRENTLY`. Postgres refuses `CONCURRENTLY` inside a transaction block.

Nothing else. `max_concurrent_bots` (MIGRATION-0003) is reported, never written — a product knob,
not a migration step. Token scopes (MIGRATION-0004) are applied by convergence itself.

| File | Owns |
|---|---|
| `sql.py` | every statement the tool can issue, one named constant each. `Q_` read-only, `W_` writes. |
| `core.py` | state read, the pure `decide()` verdict, the keep/retire plan, the two executions, the `--json` document. |
| `receipt.py` | the plain-text receipt renderer. |
| `__main__.py` | the CLI: verbs, flags, connection, exit codes (`0` GO · `10` ACTION_REQUIRED · `20` STOP). |

`check` and a `run` without `--fix` execute inside a `SET TRANSACTION READ ONLY` transaction, so a
dry run cannot write even by mistake.

Evals: `../../../tests/test_migrate_unit.py` (no database) and `../../../tests/test_stack_migrate.py`
(testcontainers Postgres). Operator documentation, with every statement verbatim:
`docs/docs/upgrade-migrate-tool.mdx`.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._

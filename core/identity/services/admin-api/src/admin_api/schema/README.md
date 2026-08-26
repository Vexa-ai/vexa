# schema — the v0.12 backing-stack SQLAlchemy source-of-truth

`models.py` defines the identity + meeting tables (User, APIToken, Meeting, Transcription,
MeetingSession). `sync.py` is `ensure_schema()` — idempotent, additive, never-drops convergence
(the parent's no-alembic discipline). The dead `recordings`/`media_files` tables are dropped —
see `MIGRATION-0001-drop-recordings.md`.

**Upgrade provenance:** the 0.10 → 0.12 convergence has been executed against a live production
database (Vexa Cloud's own, over the full live DB, July 2026), plus the out-of-band steps in
`MIGRATION-0002` (verified in production 2026-08-17) and `MIGRATION-0005` (2026-08-18). The
per-file "out-of-band, human-run ops step" language describes **who runs the statement**, not an
untried path. The operator-facing runbook is `docs/docs/changelog.mdx` §
*Upgrade runbook: 0.10 to 0.12.x*.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._

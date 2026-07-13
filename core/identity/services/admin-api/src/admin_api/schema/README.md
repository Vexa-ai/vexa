# schema — the v0.12 backing-stack SQLAlchemy source-of-truth

`models.py` defines the identity + meeting tables (User, APIToken, Meeting, Transcription,
MeetingSession). `sync.py` is `ensure_schema()` — idempotent, additive, never-drops convergence
(the parent's no-alembic discipline). The dead `recordings`/`media_files` tables are dropped —
see `MIGRATION-0001-drop-recordings.md`.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._

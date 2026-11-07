# schema — the v0.12 backing-stack SQLAlchemy source-of-truth

`models.py` defines the identity + meeting tables (User, APIToken, Meeting, Transcription,
MeetingSession). `sync.py` is `ensure_schema()` — idempotent, additive, never-drops convergence
(the parent's no-alembic discipline). The dead `recordings`/`media_files` tables are dropped —
see `MIGRATION-0001-drop-recordings.md`. The org as a first-class object (`orgs`, `org_members`,
`users.org_id`, one default org per install) is `MIGRATION-0008-orgs.md`. The workspace principal (`principals`, PRD 0.13.4 D2) is
`MIGRATION-0009-principals.md`. The workspace registry (`workspaces`, `memberships`, `invites`: who may enter a workspace, out of git) is `MIGRATION-0009-workspace-registry.md`. The credential class (`api_tokens.kind`) is
`MIGRATION-0010-token-kind.md`; every sign-in credential ending with its cookie (a data pass, run on
each boot) is `MIGRATION-0011-sign-in-credential-expiry.md`. Removing stored Zoom host tokens (`zak`) from `meetings.data` and masking the passcodes stored
diagnostics quoted, an out-of-band data pass run by an operator, is `MIGRATION-0012-scrub-zoom-host-tokens.md`.

**Upgrade path:** the 0.10 → 0.12 convergence is validated against a full production migration
(July 2026), together with the out-of-band steps in `MIGRATION-0002` and `MIGRATION-0005`. The
operator-facing runbook is `docs/docs/changelog.mdx` § *Upgrade runbook: 0.10 to 0.12.x*.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._

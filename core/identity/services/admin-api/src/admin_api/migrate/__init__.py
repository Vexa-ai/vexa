"""0.10 → 0.12 database migration tool.

`ensure_schema()` converges the database to the SQLAlchemy models on every admin-api boot, inside
one transaction, additively. Two steps of the 0.10 → 0.12 upgrade fall outside what that can do on
a live database, and this tool is those two steps:

  1. retiring duplicate ACTIVE meeting rows, so the unique dedup index can be built at all
     (a transaction-bound, never-rewrites convergence cannot change existing row values);
  2. building `uq_meeting_active_user_platform_native` with `CREATE UNIQUE INDEX CONCURRENTLY`,
     which Postgres refuses inside a transaction block.

CLI: ``python -m admin_api.migrate check`` / ``python -m admin_api.migrate run --fix``.
Documented at ``docs/docs/upgrade-migrate-tool.mdx``.
"""
from .core import (
    ACTION_REQUIRED,
    GO,
    STOP,
    State,
    Verdict,
    decide,
    dedup_plan,
    read_state,
    state_to_json,
)

__all__ = [
    "ACTION_REQUIRED", "GO", "STOP", "State", "Verdict",
    "decide", "dedup_plan", "read_state", "state_to_json",
]

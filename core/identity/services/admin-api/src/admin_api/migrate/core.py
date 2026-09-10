"""0.10 → 0.12 migration tool — state inspection, verdict, plan, execution.

Two verbs, both defined here and driven by `admin_api.migrate.__main__`:

  check   strictly read-only. Opens ONE `READ ONLY` transaction, reads state, returns a report.
  run     executes the two steps `ensure_schema()` structurally cannot do on a live database —
          the active-meeting dedup, and the CONCURRENTLY build of the dedup index. Nothing else.

Why those two and only those two: `ensure_schema()` (`admin_api.schema.sync`) converges the DB to
the SQLAlchemy models inside ONE transaction, additively — it adds missing tables, columns and
indexes and never rewrites an existing value. So it cannot delete or retire a row (the dedup), and
it cannot issue `CREATE INDEX CONCURRENTLY` (Postgres forbids that inside a transaction block).
Everything else in the 0.10 → 0.12 delta is either additive (auto) or a product decision this tool
deliberately leaves to a human — see `MAX_CONCURRENT_BOTS` below.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import inspect, text

from . import sql as S

TOOL = "admin_api.migrate"
TOOL_VERSION = "1"

GO = "GO"
ACTION_REQUIRED = "ACTION_REQUIRED"
STOP = "STOP"

#: The columns the dedup index must cover, in order. An index that already carries the name but a
#: different shape is a human's decision to resolve — the tool will not drop it.
EXPECTED_INDEX_COLUMNS = "(user_id, platform, platform_specific_id)"

#: Rows that must exist in `meetings` for a dedup to be meaningful.
REQUIRED_TABLES = ("meetings",)


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #
@dataclass
class IndexState:
    present: bool = False
    valid: bool = False
    unique: bool = False
    ready: bool = False
    indexdef: str | None = None

    @property
    def shape_ok(self) -> bool:
        """Structural match, not string equality: Postgres re-renders `pg_get_indexdef` with its
        own casts, so the test is that the index is UNIQUE, covers exactly the three key columns
        in order, and is PARTIAL (carries a WHERE predicate)."""
        if not self.present or not self.indexdef:
            return False
        return (
            self.unique
            and EXPECTED_INDEX_COLUMNS in self.indexdef.replace("USING btree ", "")
            and " WHERE " in self.indexdef
        )


@dataclass
class DuplicateGroup:
    user_id: int
    platform: str
    platform_specific_id: str
    active_dups: int
    meeting_ids: list[int]


@dataclass
class State:
    """Everything `check` read, before any interpretation."""
    dsn_display: str = ""
    server_version: str = ""
    tables: list[str] = field(default_factory=list)
    missing_tables: list[str] = field(default_factory=list)
    missing_columns: dict[str, list[str]] = field(default_factory=dict)
    missing_indexes: dict[str, list[str]] = field(default_factory=dict)
    legacy_tables_present: list[str] = field(default_factory=list)
    duplicate_groups: list[DuplicateGroup] = field(default_factory=list)
    duplicate_rows: int = 0
    index: IndexState = field(default_factory=IndexState)
    max_concurrent_bots: list[dict[str, int]] = field(default_factory=list)
    max_concurrent_bots_default: str | None = None
    empty_scope_tokens: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class Verdict:
    verdict: str
    reasons: list[str]
    actions: list[str]
    blockers: list[str]


# --------------------------------------------------------------------------- #
# check — read-only
# --------------------------------------------------------------------------- #
def read_state(conn, base, dsn_display: str = "") -> State:
    """Read every fact `check` reports. Issues only `Q_` statements from `sql.py`.

    The caller opens the transaction and sets it READ ONLY; this function never commits.
    """
    st = State(dsn_display=dsn_display)
    st.server_version = str(conn.execute(text("SHOW server_version")).scalar() or "")

    insp = inspect(conn)
    present = set(insp.get_table_names())
    st.tables = sorted(present)
    st.legacy_tables_present = sorted({"recordings", "media_files"} & present)

    # Convergence delta vs the SQLAlchemy SSOT — what `ensure_schema()` will apply on next boot.
    for table in base.metadata.sorted_tables:
        if table.name not in present:
            st.missing_tables.append(table.name)
            continue
        existing_cols = {c["name"] for c in insp.get_columns(table.name)}
        missing = [c.name for c in table.columns if c.name not in existing_cols]
        if missing:
            st.missing_columns[table.name] = missing
        existing_idx = {i["name"] for i in insp.get_indexes(table.name) if i["name"]}
        missing_idx = [i.name for i in table.indexes if i.name and i.name not in existing_idx]
        if missing_idx:
            st.missing_indexes[table.name] = missing_idx

    if "meetings" in present:
        for row in conn.execute(text(S.Q_ACTIVE_DUPLICATE_GROUPS)).mappings():
            st.duplicate_groups.append(DuplicateGroup(
                user_id=row["user_id"],
                platform=row["platform"],
                platform_specific_id=row["platform_specific_id"],
                active_dups=int(row["active_dups"]),
                meeting_ids=list(row["meeting_ids"]),
            ))
        # Rows to retire = every row in a duplicate group beyond the one keeper.
        st.duplicate_rows = sum(g.active_dups - 1 for g in st.duplicate_groups)

        idx = conn.execute(text(S.Q_INDEX_STATE), {"index_name": S.INDEX_NAME}).mappings().first()
        if idx:
            st.index = IndexState(
                present=True,
                valid=bool(idx["indisvalid"]),
                unique=bool(idx["indisunique"]),
                ready=bool(idx["indisready"]),
                indexdef=idx["indexdef"],
            )
    else:
        st.notes.append("`meetings` table absent — nothing to dedup; ensure_schema() builds the "
                        "table and its indexes cleanly on an empty database.")

    if "users" in present:
        st.max_concurrent_bots = [
            {"limit": int(r["current_limit"]), "users": int(r["users"])}
            for r in conn.execute(text(S.Q_MAX_CONCURRENT_BOTS)).mappings()
        ]
        st.max_concurrent_bots_default = conn.execute(
            text(S.Q_MAX_CONCURRENT_BOTS_DEFAULT)).scalar()

    if "api_tokens" in present:
        cols = {c["name"] for c in insp.get_columns("api_tokens")}
        if "scopes" in cols:
            st.empty_scope_tokens = int(
                conn.execute(text(S.Q_EMPTY_SCOPE_TOKENS)).scalar() or 0)
        else:
            st.notes.append("`api_tokens.scopes` absent (0.10-era). ensure_schema() adds the column "
                            "and backfills every pre-existing token to the full scope set "
                            "(MIGRATION-0004) on the next admin-api boot.")

    return st


def decide(st: State) -> Verdict:
    """Pure. State in, verdict out — no I/O, so it is directly testable.

    GO               nothing this tool must do before 0.12 admin-api boots.
    ACTION_REQUIRED  `migrate run --fix` closes the gap.
    STOP             a human must act; `run` refuses.
    """
    reasons: list[str] = []
    actions: list[str] = []
    blockers: list[str] = []

    if "meetings" not in st.tables:
        reasons.append("No `meetings` table — this database has not run a Vexa schema yet. "
                       "ensure_schema() builds everything, including the dedup index.")
        return Verdict(GO, reasons, actions, blockers)

    if st.index.present and not st.index.shape_ok and st.index.valid:
        blockers.append(
            f"An index named `{S.INDEX_NAME}` already exists with a different shape "
            f"({st.index.indexdef}). Expected a UNIQUE PARTIAL index on "
            f"{EXPECTED_INDEX_COLUMNS}. The tool will not drop an index it did not build — "
            "inspect it, then drop it by hand and re-run.")

    if st.index.present and not st.index.valid:
        reasons.append(f"`{S.INDEX_NAME}` exists but is INVALID — the corpse of a failed "
                       "CONCURRENTLY build. It enforces nothing.")
        actions.append("drop the invalid index CONCURRENTLY, then rebuild it")

    if st.duplicate_rows:
        reasons.append(
            f"{st.duplicate_rows} active meeting row(s) across {len(st.duplicate_groups)} "
            "duplicate key(s). A UNIQUE index cannot be built over them, and on 0.12.23+ a "
            "failed UNIQUE index aborts admin-api startup.")
        actions.append(f"retire {st.duplicate_rows} losing row(s) to a terminal status")

    if not blockers:
        if not st.index.present:
            reasons.append(f"`{S.INDEX_NAME}` is absent — the cross-process spawn-dedup backstop "
                           "does not exist on this database.")
        if not st.index.present or not st.index.valid:
            actions.append("build the dedup index with CREATE UNIQUE INDEX CONCURRENTLY")

    if blockers:
        return Verdict(STOP, reasons, actions, blockers)
    if actions:
        return Verdict(ACTION_REQUIRED, reasons, actions, blockers)

    reasons.append(f"`{S.INDEX_NAME}` is present, UNIQUE, PARTIAL and VALID; no active duplicate "
                   "meetings. The remaining 0.10 → 0.12 schema delta is additive and applies "
                   "itself on the next admin-api boot.")
    return Verdict(GO, reasons, actions, blockers)


# --------------------------------------------------------------------------- #
# run — the plan, then the execution
# --------------------------------------------------------------------------- #
@dataclass
class PlanRow:
    meeting_id: int
    user_id: int
    platform: str
    platform_specific_id: str
    status: str
    bot_container_id: str | None
    created_at: str
    rank: int

    @property
    def action(self) -> str:
        return "KEEP" if self.rank == 1 else "RETIRE"


def dedup_plan(conn, keep_strategy: str = "newest", keep_ids: list[int] | None = None) -> list[PlanRow]:
    """Row-by-row keep/retire plan. Read-only — this is what `run` prints without `--fix`."""
    if keep_strategy not in S.ORDER_BY:
        raise ValueError(f"unknown keep strategy: {keep_strategy}")
    stmt = S.Q_DEDUP_PLAN.format(order=S.ORDER_BY[keep_strategy])
    rows = conn.execute(text(stmt), {"keep_ids": list(keep_ids or [])}).mappings()
    return [
        PlanRow(
            meeting_id=r["id"],
            user_id=r["user_id"],
            platform=r["platform"],
            platform_specific_id=r["platform_specific_id"],
            status=r["status"],
            bot_container_id=r["bot_container_id"],
            created_at=str(r["created_at"]),
            rank=int(r["rn"]),
        )
        for r in rows
    ]


def dedup_sql_for_docs(keep_strategy: str = "newest") -> str:
    """The exact statement `dedup_plan` issues, for the receipt and the docs page."""
    return S.Q_DEDUP_PLAN.format(order=S.ORDER_BY[keep_strategy]).strip()


@dataclass
class Executed:
    statement: str
    params: dict[str, Any] | None
    rowcount: int
    seconds: float
    detail: list[str] = field(default_factory=list)


def retire_duplicates(conn, losers: list[PlanRow], retire_status: str, keep_strategy: str) -> Executed:
    """Execute `W_RETIRE_DUPLICATES` over an id set computed and printed beforehand.

    One statement, one transaction, ids bound explicitly. The stamp makes every touched row
    findable afterwards: `SELECT * FROM meetings WHERE data ? 'dedup'`.
    """
    if retire_status not in S.TERMINAL_STATUSES:
        raise ValueError(
            f"retire status {retire_status!r} is not one of {S.TERMINAL_STATUSES} — the dedup "
            "index's partial predicate excludes only those, so any other value leaves the row "
            "inside the index and the build still fails.")
    stamp = json.dumps({
        "reason": "rob1_rob2_active_dedup",
        "migration": "0002",
        "tool": TOOL,
        "keep_strategy": keep_strategy,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    ids = [r.meeting_id for r in losers]
    t0 = time.monotonic()
    result = conn.execute(text(S.W_RETIRE_DUPLICATES), {
        "retire_status": retire_status, "stamp": stamp, "loser_ids": ids,
    })
    touched = [f"meeting {r['id']} → status={r['status']}" for r in result.mappings()]
    return Executed(
        statement=S.W_RETIRE_DUPLICATES.strip(),
        params={"retire_status": retire_status, "stamp": stamp, "loser_ids": ids},
        rowcount=len(touched),
        seconds=round(time.monotonic() - t0, 3),
        detail=touched,
    )


def build_index(autocommit_conn, drop_invalid_first: bool) -> list[Executed]:
    """`CREATE UNIQUE INDEX CONCURRENTLY`, outside any transaction, then verify.

    CONCURRENTLY is why this cannot ride `ensure_schema()`: Postgres refuses it inside a
    transaction block, and `ensure_schema()` is one transaction. Plain `CREATE INDEX` would take
    a lock that blocks every write to `meetings` for the duration of the build — not acceptable
    on the hot spawn table.
    """
    out: list[Executed] = []
    if drop_invalid_first:
        t0 = time.monotonic()
        autocommit_conn.execute(text(S.W_DROP_INDEX_CONCURRENTLY))
        out.append(Executed(S.W_DROP_INDEX_CONCURRENTLY, None, 0,
                            round(time.monotonic() - t0, 3),
                            ["dropped INVALID index left by a failed CONCURRENTLY build"]))
    t0 = time.monotonic()
    autocommit_conn.execute(text(S.W_CREATE_INDEX_CONCURRENTLY))
    seconds = round(time.monotonic() - t0, 3)

    verify = autocommit_conn.execute(
        text(S.Q_INDEX_STATE), {"index_name": S.INDEX_NAME}).mappings().first()
    if not verify or not verify["indisvalid"] or not verify["indisunique"]:
        raise RuntimeError(
            f"{S.INDEX_NAME} did not verify after the build "
            f"(present={bool(verify)}, valid={verify and verify['indisvalid']}). "
            f"An INVALID index enforces nothing: drop it with "
            f"`{S.W_DROP_INDEX_CONCURRENTLY}` and re-run.")
    out.append(Executed(S.W_CREATE_INDEX_CONCURRENTLY, None, 0, seconds,
                        ["verified: indisvalid=t indisunique=t", str(verify["indexdef"])]))
    return out


# --------------------------------------------------------------------------- #
# report rendering
# --------------------------------------------------------------------------- #
def state_to_json(st: State, v: Verdict, plan: list[PlanRow] | None = None) -> dict[str, Any]:
    """The `--json` document. Stable keys — this is the machine-readable contract."""
    doc: dict[str, Any] = {
        "tool": TOOL,
        "tool_version": TOOL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": {"target": st.dsn_display, "server_version": st.server_version},
        "verdict": v.verdict,
        "reasons": v.reasons,
        "actions": v.actions,
        "blockers": v.blockers,
        "schema_delta": {
            "missing_tables": st.missing_tables,
            "missing_columns": st.missing_columns,
            "missing_indexes": st.missing_indexes,
            "legacy_tables_present": st.legacy_tables_present,
            "applied_by": "ensure_schema() on admin-api boot (additive, never drops)",
        },
        "duplicate_active_meetings": {
            "groups": len(st.duplicate_groups),
            "rows_to_retire": st.duplicate_rows,
            "detail": [
                {
                    "user_id": g.user_id,
                    "platform": g.platform,
                    "platform_specific_id": g.platform_specific_id,
                    "active_rows": g.active_dups,
                    "meeting_ids": g.meeting_ids,
                }
                for g in st.duplicate_groups
            ],
        },
        "dedup_index": {
            "name": S.INDEX_NAME,
            "present": st.index.present,
            "valid": st.index.valid,
            "unique": st.index.unique,
            "shape_ok": st.index.shape_ok,
            "definition": st.index.indexdef,
        },
        "max_concurrent_bots": {
            "note": "report only — this tool never writes this column (product knob, not a "
                    "migration step). See MIGRATION-0003 for the one-line SQL.",
            "column_default": st.max_concurrent_bots_default,
            "histogram": st.max_concurrent_bots,
            "accounts_at_1": next(
                (h["users"] for h in st.max_concurrent_bots if h["limit"] == 1), 0),
        },
        "token_scopes": {
            "empty_scope_tokens": st.empty_scope_tokens,
            "note": "MIGRATION-0004 — backfilled automatically by ensure_schema() on boot.",
        },
        "notes": st.notes,
    }
    if plan is not None:
        doc["dedup_plan"] = [
            {
                "meeting_id": r.meeting_id,
                "action": r.action,
                "rank": r.rank,
                "user_id": r.user_id,
                "platform": r.platform,
                "platform_specific_id": r.platform_specific_id,
                "status": r.status,
                "bot_container_id": r.bot_container_id,
                "created_at": r.created_at,
            }
            for r in plan
        ]
    return doc

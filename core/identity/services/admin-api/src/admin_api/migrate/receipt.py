"""The receipt — a plain-text record of everything a `migrate` invocation read and executed.

One file, one invocation. It is the artefact an operator keeps: it names the database, the mode,
every statement issued verbatim with its bound parameters, every row touched, and the verdict
before and after. A dry run produces a receipt too, marked DRY-RUN, listing the statements that
were NOT executed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .core import Executed, PlanRow, State, Verdict

RULE = "=" * 78
THIN = "-" * 78


def _block(title: str) -> list[str]:
    return ["", RULE, title, RULE]


def render(
    *,
    verb: str,
    mode: str,
    st: State,
    verdict: Verdict,
    plan: list[PlanRow] | None = None,
    executed: list[Executed] | None = None,
    skipped: list[str] | None = None,
    final_verdict: Verdict | None = None,
    tool: str,
    tool_version: str,
) -> str:
    L: list[str] = []
    L.append(RULE)
    L.append(f"{tool} v{tool_version} — receipt")
    L.append(RULE)
    L.append(f"verb           : {verb}")
    L.append(f"mode           : {mode}")
    L.append(f"generated (UTC): {datetime.now(timezone.utc).isoformat()}")
    L.append(f"database       : {st.dsn_display}")
    L.append(f"server_version : {st.server_version}")

    L += _block("STATE READ")
    L.append(f"tables                     : {', '.join(st.tables) or '(none)'}")
    L.append(f"legacy tables present      : {', '.join(st.legacy_tables_present) or '(none)'}")
    L.append(f"missing tables (additive)  : {', '.join(st.missing_tables) or '(none)'}")
    L.append(f"missing columns (additive) : {st.missing_columns or '(none)'}")
    L.append(f"missing indexes (additive) : {st.missing_indexes or '(none)'}")
    L.append(f"dedup index                : present={st.index.present} valid={st.index.valid} "
             f"unique={st.index.unique} shape_ok={st.index.shape_ok}")
    if st.index.indexdef:
        L.append(f"  definition               : {st.index.indexdef}")
    L.append(f"duplicate active keys      : {len(st.duplicate_groups)}")
    L.append(f"rows that would be retired : {st.duplicate_rows}")
    for g in st.duplicate_groups:
        L.append(f"  user={g.user_id} platform={g.platform} native={g.platform_specific_id} "
                 f"active_rows={g.active_dups} ids={g.meeting_ids}")
    L.append(f"max_concurrent_bots default: {st.max_concurrent_bots_default}")
    L.append(f"max_concurrent_bots        : {st.max_concurrent_bots or '(no users table)'}")
    L.append("  (report only — this tool never writes that column)")
    L.append(f"api tokens with no scopes  : {st.empty_scope_tokens} "
             "(MIGRATION-0004, applied by ensure_schema() on boot)")
    for n in st.notes:
        L.append(f"note: {n}")

    L += _block("VERDICT (before)")
    L.append(verdict.verdict)
    for r in verdict.reasons:
        L.append(f"  reason : {r}")
    for a in verdict.actions:
        L.append(f"  action : {a}")
    for b in verdict.blockers:
        L.append(f"  BLOCKER: {b}")

    if plan is not None:
        L += _block("DEDUP PLAN (row by row)")
        if not plan:
            L.append("(no duplicate active meetings)")
        for r in plan:
            L.append(f"  {r.action:<6} meeting_id={r.meeting_id} rank={r.rank} "
                     f"user={r.user_id} platform={r.platform} native={r.platform_specific_id} "
                     f"status={r.status} bot_container_id={r.bot_container_id} "
                     f"created_at={r.created_at}")

    if skipped:
        L += _block("NOT EXECUTED (dry run)")
        for s in skipped:
            L.append(THIN)
            L.append(s)

    if executed is not None:
        L += _block("EXECUTED")
        if not executed:
            L.append("(nothing — already converged)")
        for e in executed:
            L.append(THIN)
            L.append(e.statement)
            if e.params:
                L.append(f"  params    : {e.params}")
            L.append(f"  rowcount  : {e.rowcount}")
            L.append(f"  duration  : {e.seconds}s")
            for d in e.detail:
                L.append(f"  {d}")

    if final_verdict is not None:
        L += _block("VERDICT (after)")
        L.append(final_verdict.verdict)
        for r in final_verdict.reasons:
            L.append(f"  reason : {r}")
        for a in final_verdict.actions:
            L.append(f"  action : {a}")
        for b in final_verdict.blockers:
            L.append(f"  BLOCKER: {b}")

    L.append("")
    L.append(RULE)
    L.append("end of receipt")
    L.append(RULE)
    return "\n".join(L)

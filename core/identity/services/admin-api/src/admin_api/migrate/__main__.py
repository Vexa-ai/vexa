"""``python -m admin_api.migrate`` — the 0.10 → 0.12 database migration tool.

    python -m admin_api.migrate check [--json] [--dsn URL] [--receipt PATH]
    python -m admin_api.migrate run  [--fix] [--keep-strategy newest|live-bot]
                                     [--keep-meeting-id ID ...] [--retire-status failed|completed]
                                     [--json] [--dsn URL] [--receipt PATH]

`check` is the default verb and is strictly read-only: it opens one `READ ONLY` transaction, so
the database itself refuses any write it could attempt. `run` without `--fix` is also read-only —
it prints the statements it WOULD execute. Only `run --fix` writes.

Connection: `--dsn`, else `DATABASE_URL`, else the `DB_*` environment the admin-api itself reads
(`DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/`DB_PASSWORD`). An async driver in the URL is rewritten
to the sync one — this tool needs `CREATE INDEX CONCURRENTLY` on a real autocommit connection.

Exit codes:
    0   GO — nothing left for this tool to do
    10  ACTION_REQUIRED — `run --fix` closes the gap (also the exit of a dry run with work pending)
    20  STOP — a human must act first; `run` refuses to proceed
    1   execution failure
    2   usage error
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from sqlalchemy import create_engine, text

from . import core, receipt
from . import sql as S

EXIT_GO = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_ACTION_REQUIRED = 10
EXIT_STOP = 20

_VERDICT_EXIT = {core.GO: EXIT_GO, core.ACTION_REQUIRED: EXIT_ACTION_REQUIRED, core.STOP: EXIT_STOP}


def database_url(explicit: str | None = None) -> str:
    """Sync (psycopg) Postgres URL. Mirrors `admin_api.__main__._database_url`'s env contract."""
    url = explicit or os.getenv("DATABASE_URL")
    if not url:
        host = os.getenv("DB_HOST", "postgres")
        port = os.getenv("DB_PORT", "5432")
        name = os.getenv("DB_NAME", "vexa")
        user = os.getenv("DB_USER", "postgres")
        password = os.getenv("DB_PASSWORD", "postgres")
        url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"
    # CREATE INDEX CONCURRENTLY needs a real autocommit connection — force the sync driver.
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg://")


def display_dsn(url: str) -> str:
    """The URL with any password removed — receipts and JSON are shareable artefacts."""
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.split("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


def _read_only_state(engine, dsn_display: str):
    """One READ ONLY transaction: state + verdict + dedup plan. Always rolled back."""
    from ..schema.models import Base

    with engine.connect() as conn:
        with conn.begin() as txn:
            conn.execute(text(S.Q_READ_ONLY_TXN))
            st = core.read_state(conn, Base, dsn_display=dsn_display)
            v = core.decide(st)
            plan = core.dedup_plan(conn) if st.duplicate_rows else []
            txn.rollback()
    return st, v, plan


def _emit(args, doc: dict, text_report: str) -> None:
    if args.json:
        print(json.dumps(doc, indent=2, default=str))
    else:
        print(text_report)
    if args.receipt:
        with open(args.receipt, "w", encoding="utf-8") as fh:
            fh.write(text_report + "\n")
        if not args.json:
            print(f"\nreceipt written to {args.receipt}")


def cmd_check(args) -> int:
    url = database_url(args.dsn)
    shown = display_dsn(url)
    engine = create_engine(url)
    try:
        st, v, _plan = _read_only_state(engine, shown)
    finally:
        engine.dispose()
    report = receipt.render(
        verb="check", mode="READ-ONLY", st=st, verdict=v,
        tool=core.TOOL, tool_version=core.TOOL_VERSION,
    )
    _emit(args, core.state_to_json(st, v), report)
    return _VERDICT_EXIT[v.verdict]


def cmd_run(args) -> int:
    if args.retire_status not in S.TERMINAL_STATUSES:
        print(f"error: --retire-status must be one of {S.TERMINAL_STATUSES}", file=sys.stderr)
        return EXIT_USAGE

    url = database_url(args.dsn)
    shown = display_dsn(url)
    engine = create_engine(url)
    try:
        from ..schema.models import Base

        # ── phase 0: read-only state, verdict, plan ──────────────────────────────────────────
        with engine.connect() as conn:
            with conn.begin() as txn:
                conn.execute(text(S.Q_READ_ONLY_TXN))
                st = core.read_state(conn, Base, dsn_display=shown)
                v = core.decide(st)
                plan = core.dedup_plan(
                    conn, keep_strategy=args.keep_strategy, keep_ids=args.keep_meeting_id,
                ) if st.duplicate_rows else []
                txn.rollback()

        if v.verdict == core.STOP:
            report = receipt.render(
                verb="run", mode="REFUSED (check says STOP)", st=st, verdict=v, plan=plan,
                tool=core.TOOL, tool_version=core.TOOL_VERSION,
            )
            _emit(args, core.state_to_json(st, v, plan), report)
            return EXIT_STOP

        losers = [r for r in plan if r.rank > 1]
        drop_invalid = st.index.present and not st.index.valid
        needs_index = not st.index.present or not st.index.valid

        # ── dry run: print the statements, execute nothing ───────────────────────────────────
        if not args.fix:
            skipped: list[str] = []
            if losers:
                skipped.append(
                    f"-- dedup plan query (keep-strategy={args.keep_strategy}, "
                    f"keep-meeting-id={args.keep_meeting_id or []}):\n"
                    + core.dedup_sql_for_docs(args.keep_strategy))
                skipped.append(
                    S.W_RETIRE_DUPLICATES.strip()
                    + f"\n-- :retire_status = {args.retire_status!r}"
                    + f"\n-- :loser_ids = {[r.meeting_id for r in losers]}")
            if drop_invalid:
                skipped.append(S.W_DROP_INDEX_CONCURRENTLY)
            if needs_index:
                skipped.append(S.W_CREATE_INDEX_CONCURRENTLY)
            report = receipt.render(
                verb="run", mode="DRY-RUN (no --fix; nothing was executed)", st=st, verdict=v,
                plan=plan, skipped=skipped,
                tool=core.TOOL, tool_version=core.TOOL_VERSION,
            )
            _emit(args, core.state_to_json(st, v, plan), report)
            return _VERDICT_EXIT[v.verdict]

        # ── phase 1: dedup ───────────────────────────────────────────────────────────────────
        executed: list[core.Executed] = []
        if losers:
            with engine.begin() as conn:
                executed.append(core.retire_duplicates(
                    conn, losers, retire_status=args.retire_status,
                    keep_strategy=args.keep_strategy,
                ))
            with engine.connect() as conn:
                remaining = conn.execute(text(S.Q_ACTIVE_DUPLICATE_GROUPS)).mappings().all()
            if remaining:
                print("error: duplicate active meetings remain after dedup — refusing to build "
                      f"the index. Groups left: {len(remaining)}", file=sys.stderr)
                return EXIT_FAILURE

        # ── phase 2: the index, outside any transaction ──────────────────────────────────────
        if needs_index:
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                executed.extend(core.build_index(conn, drop_invalid_first=drop_invalid))

        # ── phase 3: re-read and re-decide ───────────────────────────────────────────────────
        st_after, v_after, _ = _read_only_state(engine, shown)
        report = receipt.render(
            verb="run", mode="EXECUTED (--fix)", st=st, verdict=v, plan=plan,
            executed=executed, final_verdict=v_after,
            tool=core.TOOL, tool_version=core.TOOL_VERSION,
        )
        _emit(args, core.state_to_json(st_after, v_after, plan), report)
        return _VERDICT_EXIT[v_after.verdict]
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    # SUPPRESS so the shared flags may appear before OR after the verb: without it the subparser
    # re-applies its own defaults over a value already parsed at the top level.
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--dsn", help="Postgres URL. Default: DATABASE_URL, else the DB_* environment.")
    common.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    common.add_argument("--receipt", metavar="PATH", help="write the plain-text receipt to PATH")

    p = argparse.ArgumentParser(
        prog="python -m admin_api.migrate",
        parents=[common],
        description="0.10 → 0.12 database migration tool: the two steps ensure_schema() cannot do.",
    )
    sub = p.add_subparsers(dest="verb")

    sub.add_parser("check", parents=[common],
                   help="read-only: report the delta and a GO / ACTION_REQUIRED / STOP verdict")

    run = sub.add_parser("run", parents=[common],
                         help="execute the dedup and the CONCURRENTLY index build")
    run.add_argument("--fix", action="store_true",
                     help="actually execute. Without it, run prints what it would do and writes nothing.")
    run.add_argument("--keep-strategy", choices=sorted(S.ORDER_BY), default="newest",
                     help="which row survives in a duplicate group (default: newest)")
    run.add_argument("--keep-meeting-id", type=int, action="append", default=[], metavar="ID",
                     help="force this meeting id to survive in its group; repeatable. Overrides --keep-strategy.")
    run.add_argument("--retire-status", choices=sorted(S.TERMINAL_STATUSES), default="failed",
                     help="terminal status written to the losing rows (default: failed)")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    # `check` is the default verb: bare invocation, or only global flags, means check.
    known = {"check", "run"}
    if not any(a in known for a in argv):
        argv = ["check", *argv]
    args = parser.parse_args(argv)
    # argparse puts subparser flags on the same namespace; give `check` the defaults it lacks.
    for name, default in (("fix", False), ("keep_strategy", "newest"),
                          ("keep_meeting_id", []), ("retire_status", "failed"),
                          ("dsn", None), ("json", False), ("receipt", None)):
        if not hasattr(args, name):
            setattr(args, name, default)
    try:
        return cmd_run(args) if args.verb == "run" else cmd_check(args)
    except Exception as exc:  # noqa: BLE001 — an operator tool reports, it does not traceback
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())

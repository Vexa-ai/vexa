"""Unit evals for the 0.10 → 0.12 migrate tool — no database, no docker.

Covers the parts that are pure: the verdict function, the CLI's argument contract, the refusal
paths, the two dedup orderings, and the `--json` document shape. The Postgres-backed behaviour
(duplicate detection over real rows, the CONCURRENTLY build, idempotency, dry-run-writes-nothing)
lives in `test_stack_migrate.py`.
"""
import json

import pytest

from admin_api.migrate import core
from admin_api.migrate import sql as S
from admin_api.migrate.__main__ import (
    EXIT_USAGE, build_parser, database_url, display_dsn, main,
)


# --------------------------------------------------------------------------- #
# decide() — the verdict is a pure function of state
# --------------------------------------------------------------------------- #
def _state(**kw) -> core.State:
    st = core.State(tables=["users", "api_tokens", "meetings"])
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def _valid_index() -> core.IndexState:
    return core.IndexState(
        present=True, valid=True, unique=True, ready=True,
        indexdef="CREATE UNIQUE INDEX uq_meeting_active_user_platform_native ON public.meetings "
                 "USING btree (user_id, platform, platform_specific_id) "
                 "WHERE ((status)::text <> ALL (ARRAY['completed', 'failed']))",
    )


def test_go_when_index_valid_and_no_duplicates():
    v = core.decide(_state(index=_valid_index()))
    assert v.verdict == core.GO
    assert v.actions == []


def test_action_required_when_index_absent():
    v = core.decide(_state())
    assert v.verdict == core.ACTION_REQUIRED
    assert any("CONCURRENTLY" in a for a in v.actions)


def test_action_required_when_duplicates_present():
    v = core.decide(_state(duplicate_rows=3, duplicate_groups=[
        core.DuplicateGroup(1, "google_meet", "abc-def-ghi", 4, [4, 3, 2, 1])]))
    assert v.verdict == core.ACTION_REQUIRED
    assert any("retire 3" in a for a in v.actions)


def test_invalid_index_is_fixable_not_a_stop():
    idx = _valid_index()
    idx.valid = False
    v = core.decide(_state(index=idx))
    assert v.verdict == core.ACTION_REQUIRED
    assert any("drop the invalid index" in a for a in v.actions)


def test_wrong_shaped_index_is_a_stop():
    """A same-named index of a different shape is a human's call — the tool never drops it."""
    idx = core.IndexState(
        present=True, valid=True, unique=False, ready=True,
        indexdef="CREATE INDEX uq_meeting_active_user_platform_native ON public.meetings "
                 "USING btree (user_id)")
    v = core.decide(_state(index=idx))
    assert v.verdict == core.STOP
    assert v.blockers and "different shape" in v.blockers[0]


def test_empty_database_is_go():
    v = core.decide(core.State(tables=[]))
    assert v.verdict == core.GO


def test_max_concurrent_bots_never_produces_an_action():
    """Product knob, not a migration step: reported, never acted on."""
    st = _state(index=_valid_index(),
                max_concurrent_bots=[{"limit": 1, "users": 42}],
                max_concurrent_bots_default="1")
    v = core.decide(st)
    assert v.verdict == core.GO
    assert not any("max_concurrent_bots" in a for a in v.actions + v.reasons)
    doc = core.state_to_json(st, v)
    assert doc["max_concurrent_bots"]["accounts_at_1"] == 42


# --------------------------------------------------------------------------- #
# the --json document
# --------------------------------------------------------------------------- #
REQUIRED_JSON_KEYS = {
    "tool", "tool_version", "generated_at", "database", "verdict", "reasons", "actions",
    "blockers", "schema_delta", "duplicate_active_meetings", "dedup_index",
    "max_concurrent_bots", "token_scopes", "notes",
}


def test_json_document_shape():
    st = _state(index=_valid_index(), dsn_display="postgresql+psycopg://vexa:***@db:5432/vexa",
                server_version="16.4")
    doc = core.state_to_json(st, core.decide(st))
    assert REQUIRED_JSON_KEYS <= set(doc)
    assert doc["verdict"] in {core.GO, core.ACTION_REQUIRED, core.STOP}
    assert doc["dedup_index"]["name"] == S.INDEX_NAME
    assert set(doc["duplicate_active_meetings"]) == {"groups", "rows_to_retire", "detail"}
    json.dumps(doc, default=str)   # serialisable as emitted


def test_json_carries_the_dedup_plan_when_one_exists():
    st = _state(duplicate_rows=1)
    plan = [core.PlanRow(9, 1, "google_meet", "abc", "active", None, "2026-08-01", 1),
            core.PlanRow(8, 1, "google_meet", "abc", "active", None, "2026-07-01", 2)]
    doc = core.state_to_json(st, core.decide(st), plan)
    assert [r["action"] for r in doc["dedup_plan"]] == ["KEEP", "RETIRE"]


# --------------------------------------------------------------------------- #
# refusal paths
# --------------------------------------------------------------------------- #
def test_retire_status_outside_the_partial_predicate_is_refused():
    with pytest.raises(ValueError, match="not one of"):
        core.retire_duplicates(None, [], retire_status="superseded", keep_strategy="newest")


def test_unknown_keep_strategy_is_refused():
    with pytest.raises(ValueError, match="unknown keep strategy"):
        core.dedup_plan(None, keep_strategy="oldest")


def test_cli_rejects_a_non_terminal_retire_status(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--retire-status", "superseded"])


def test_cli_run_with_bad_status_returns_usage_exit(monkeypatch):
    args = build_parser().parse_args(["run"])
    args.retire_status = "superseded"
    from admin_api.migrate.__main__ import cmd_run
    assert cmd_run(args) == EXIT_USAGE


# --------------------------------------------------------------------------- #
# CLI contract
# --------------------------------------------------------------------------- #
def test_check_is_the_default_verb(monkeypatch):
    seen = {}

    def fake_check(args):
        seen["verb"] = args.verb
        seen["json"] = args.json
        return 0

    monkeypatch.setattr("admin_api.migrate.__main__.cmd_check", fake_check)
    assert main([]) == 0
    assert seen["verb"] == "check"       # no verb given → check
    assert main(["--json"]) == 0
    assert seen["json"] is True


def test_run_defaults_to_dry_run():
    args = build_parser().parse_args(["run"])
    assert args.fix is False
    assert args.keep_strategy == "newest"
    assert args.retire_status == "failed"


def test_shared_flags_work_on_both_sides_of_the_verb():
    from admin_api.migrate.__main__ import main as _m  # noqa: F401  (import shape check)
    for argv in (["run", "--json"], ["--json", "run"]):
        args = build_parser().parse_args(argv)
        assert getattr(args, "json", False) is True


def test_keep_meeting_id_is_repeatable():
    args = build_parser().parse_args(["run", "--keep-meeting-id", "7", "--keep-meeting-id", "9"])
    assert args.keep_meeting_id == [7, 9]


def test_main_reports_errors_without_a_traceback(monkeypatch, capsys):
    def boom(_args):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("admin_api.migrate.__main__.cmd_check", boom)
    assert main(["check"]) == 1
    assert "connection refused" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# connection handling
# --------------------------------------------------------------------------- #
def test_async_driver_is_rewritten_to_sync():
    assert database_url("postgresql+asyncpg://u:p@h:5432/db").startswith("postgresql+psycopg://")


def test_database_url_falls_back_to_the_admin_api_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_HOST", "pg.internal")
    monkeypatch.setenv("DB_NAME", "vexa_prod")
    url = database_url(None)
    assert "pg.internal" in url and url.endswith("/vexa_prod")


def test_display_dsn_hides_the_password():
    shown = display_dsn("postgresql+psycopg://vexa:hunter2@db:5432/vexa")
    assert "hunter2" not in shown and "vexa:***@db:5432/vexa" in shown


# --------------------------------------------------------------------------- #
# the SQL surface itself
# --------------------------------------------------------------------------- #
def test_index_build_is_concurrently_and_partial():
    stmt = S.W_CREATE_INDEX_CONCURRENTLY
    assert "CONCURRENTLY" in stmt and "IF NOT EXISTS" in stmt
    assert "WHERE status NOT IN ('completed', 'failed')" in stmt


def test_dedup_orderings_are_deterministic():
    """Both strategies end in `id DESC`, so no group can rank two rows equally."""
    for order in S.ORDER_BY.values():
        assert order.strip().endswith("id DESC")
        assert order.startswith("(id = ANY(:keep_ids)) DESC")


def test_live_bot_strategy_prefers_a_running_container():
    assert "(bot_container_id IS NOT NULL) DESC" in S.ORDER_BY["live-bot"]


def test_docs_sql_matches_what_the_tool_executes():
    """The docs page quotes `dedup_sql_for_docs`; it must be the statement actually issued."""
    assert core.dedup_sql_for_docs("newest") == \
        S.Q_DEDUP_PLAN.format(order=S.ORDER_BY["newest"]).strip()

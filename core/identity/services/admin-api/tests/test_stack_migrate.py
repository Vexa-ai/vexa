"""Postgres-backed evals for the 0.10 → 0.12 migrate tool (testcontainers).

What the unit suite cannot prove without a real server:
  1. duplicate detection over real rows, including the NULL-native-id rows that must NOT be touched
  2. `run` without `--fix` writes nothing — asserted against a full before/after row snapshot
  3. `run --fix` retires exactly the losing rows, keep-newest, and stamps `data.dedup`
  4. the CONCURRENTLY build lands a VALID UNIQUE PARTIAL index and the constraint then bites
  5. re-running is a no-op (idempotent) and the verdict is GO
  6. `run` refuses when `check` says STOP
  7. `--keep-meeting-id` and `--keep-strategy live-bot` override the default rule
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from admin_api.migrate import core
from admin_api.migrate import sql as S
from admin_api.migrate.__main__ import main
from admin_api.schema.models import Base, Meeting, User
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker

pytestmark = requires_docker

T0 = datetime(2026, 8, 1, 12, 0, 0)


@pytest.fixture()
def engine(pg_url):
    """A database shaped like a populated 0.10 install: schema converged, dedup index REMOVED."""
    eng = create_engine(pg_url)
    Base.metadata.drop_all(eng)
    ensure_schema_sync(eng, Base)
    with eng.connect() as conn:
        conn.execute(text(f"DROP INDEX IF EXISTS {S.INDEX_NAME}"))
        conn.commit()
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


def _seed(eng, rows):
    """rows: (user_id, platform, native_id, status, minutes_offset, bot_container_id)."""
    with Session(eng) as s:
        s.add(User(id=1, email="one@example.com", max_concurrent_bots=1))
        s.add(User(id=2, email="two@example.com", max_concurrent_bots=1))
        s.flush()
        for uid, plat, native, status, off, bot in rows:
            s.add(Meeting(user_id=uid, platform=plat, platform_specific_id=native,
                          status=status, bot_container_id=bot,
                          created_at=T0 + timedelta(minutes=off)))
        s.commit()


def _snapshot(eng):
    with eng.connect() as conn:
        return conn.execute(text(
            "SELECT id, user_id, platform, platform_specific_id, status, data "
            "FROM meetings ORDER BY id")).mappings().all()


def _state(eng):
    with eng.connect() as conn:
        with conn.begin() as txn:
            conn.execute(text(S.Q_READ_ONLY_TXN))
            st = core.read_state(conn, Base)
            plan = core.dedup_plan(conn) if st.duplicate_rows else []
            txn.rollback()
    return st, core.decide(st), plan


DUPES = [
    # user 1, one native id, three active rows → 2 to retire, newest (offset 20) survives
    (1, "google_meet", "aaa-bbb-ccc", "active", 0, None),
    (1, "google_meet", "aaa-bbb-ccc", "requested", 10, None),
    (1, "google_meet", "aaa-bbb-ccc", "active", 20, "container-newest"),
    # same key but TERMINAL — outside the partial predicate, must be left alone
    (1, "google_meet", "aaa-bbb-ccc", "completed", 30, None),
    # user 2, two active rows on a different platform → 1 to retire
    (2, "zoom", "999888777", "requested", 0, None),
    (2, "zoom", "999888777", "active", 5, None),
    # NULL native id — a unique index treats NULLs as DISTINCT, so these never collide
    (2, "teams", None, "active", 0, None),
    (2, "teams", None, "active", 1, None),
    # a clean single active row
    (1, "zoom", "111222333", "active", 0, None),
]


# --------------------------------------------------------------------------- #
# 1. detection
# --------------------------------------------------------------------------- #
def test_detects_duplicate_active_meetings_and_leaves_null_and_terminal_rows_out(engine):
    _seed(engine, DUPES)
    st, verdict, plan = _state(engine)

    keys = {(g.user_id, g.platform, g.platform_specific_id) for g in st.duplicate_groups}
    assert keys == {(1, "google_meet", "aaa-bbb-ccc"), (2, "zoom", "999888777")}
    assert st.duplicate_rows == 3                     # (3-1) + (2-1)
    assert verdict.verdict == core.ACTION_REQUIRED

    # The terminal row and both NULL-native rows are absent from the plan entirely.
    planned_ids = {r.meeting_id for r in plan}
    with engine.connect() as conn:
        terminal_id = conn.execute(text(
            "SELECT id FROM meetings WHERE status = 'completed'")).scalar()
        null_ids = {r[0] for r in conn.execute(text(
            "SELECT id FROM meetings WHERE platform_specific_id IS NULL")).all()}
    assert terminal_id not in planned_ids
    assert not (null_ids & planned_ids)


def test_keep_newest_is_the_rule(engine):
    _seed(engine, DUPES)
    _st, _v, plan = _state(engine)
    keepers = {r.meeting_id: r for r in plan if r.action == "KEEP"}
    assert len(keepers) == 2
    for r in keepers.values():
        group = [p for p in plan if (p.user_id, p.platform, p.platform_specific_id)
                 == (r.user_id, r.platform, r.platform_specific_id)]
        assert r.created_at == max(p.created_at for p in group)


def test_check_reports_accounts_at_the_old_default(engine):
    _seed(engine, DUPES)
    st, v, _ = _state(engine)
    doc = core.state_to_json(st, v)
    assert doc["max_concurrent_bots"]["accounts_at_1"] == 2
    # ...and never proposes writing it.
    assert not any("max_concurrent_bots" in a for a in doc["actions"] + doc["reasons"])


# --------------------------------------------------------------------------- #
# 2. dry run writes nothing
# --------------------------------------------------------------------------- #
def test_dry_run_produces_zero_writes(engine, pg_url, capsys):
    _seed(engine, DUPES)
    before = _snapshot(engine)
    index_before = _state(engine)[0].index.present

    rc = main(["run", "--dsn", pg_url])

    after = _snapshot(engine)
    assert after == before, "run without --fix must not write a single row"
    assert _state(engine)[0].index.present == index_before
    assert rc == 10                                   # ACTION_REQUIRED
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "NOT EXECUTED" in out
    assert "CREATE UNIQUE INDEX CONCURRENTLY" in out


def test_check_is_read_only_even_with_work_pending(engine, pg_url):
    _seed(engine, DUPES)
    before = _snapshot(engine)
    assert main(["check", "--dsn", pg_url]) == 10
    assert _snapshot(engine) == before


# --------------------------------------------------------------------------- #
# 3 + 4. the fix
# --------------------------------------------------------------------------- #
def test_fix_retires_losers_stamps_them_and_builds_a_valid_index(engine, pg_url, tmp_path):
    _seed(engine, DUPES)
    _st, _v, plan = _state(engine)
    losers = {r.meeting_id for r in plan if r.action == "RETIRE"}
    keepers = {r.meeting_id for r in plan if r.action == "KEEP"}

    receipt_path = tmp_path / "receipt.txt"
    rc = main(["run", "--fix", "--dsn", pg_url, "--receipt", str(receipt_path)])
    assert rc == 0

    with engine.connect() as conn:
        retired = {r[0] for r in conn.execute(text(
            "SELECT id FROM meetings WHERE data ? 'dedup'")).all()}
        statuses = dict(conn.execute(text("SELECT id, status FROM meetings")).all())
        idx = conn.execute(text(S.Q_INDEX_STATE), {"index_name": S.INDEX_NAME}).mappings().first()

    assert retired == losers
    assert all(statuses[i] == "failed" for i in losers)
    assert all(statuses[i] != "failed" for i in keepers)
    assert idx and idx["indisvalid"] and idx["indisunique"]

    receipt = receipt_path.read_text()
    assert "EXECUTED" in receipt
    assert S.W_CREATE_INDEX_CONCURRENTLY.splitlines()[0] in receipt
    for i in losers:
        assert f"meeting_id={i}" in receipt


def test_the_index_then_actually_blocks_a_duplicate_spawn(engine, pg_url):
    _seed(engine, DUPES)
    assert main(["run", "--fix", "--dsn", pg_url]) == 0
    from sqlalchemy.exc import IntegrityError
    with Session(engine) as s:
        s.add(Meeting(user_id=1, platform="zoom", platform_specific_id="111222333",
                      status="requested"))
        with pytest.raises(IntegrityError):
            s.commit()


# --------------------------------------------------------------------------- #
# 5. idempotency
# --------------------------------------------------------------------------- #
def test_second_run_is_a_no_op_and_the_verdict_is_go(engine, pg_url, capsys):
    _seed(engine, DUPES)
    assert main(["run", "--fix", "--dsn", pg_url]) == 0
    after_first = _snapshot(engine)
    capsys.readouterr()

    assert main(["run", "--fix", "--dsn", pg_url]) == 0
    assert _snapshot(engine) == after_first
    assert "(nothing — already converged)" in capsys.readouterr().out

    assert main(["check", "--dsn", pg_url]) == 0     # GO


def test_run_on_an_already_clean_database_writes_nothing(engine, pg_url):
    _seed(engine, [(1, "zoom", "111222333", "active", 0, None)])
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(S.W_CREATE_INDEX_CONCURRENTLY))
    before = _snapshot(engine)
    assert main(["run", "--fix", "--dsn", pg_url]) == 0
    assert _snapshot(engine) == before


def test_an_invalid_index_is_dropped_and_rebuilt(engine, pg_url):
    """The corpse a failed CONCURRENTLY build leaves behind enforces nothing — the tool replaces it."""
    _seed(engine, DUPES)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        # Force an INVALID index the way Postgres does: a CONCURRENTLY build over dirty rows.
        with pytest.raises(Exception):
            conn.execute(text(S.W_CREATE_INDEX_CONCURRENTLY))
    st, verdict, _ = _state(engine)
    assert st.index.present and not st.index.valid
    assert verdict.verdict == core.ACTION_REQUIRED

    assert main(["run", "--fix", "--dsn", pg_url]) == 0
    st_after, verdict_after, _ = _state(engine)
    assert st_after.index.valid and st_after.index.unique
    assert verdict_after.verdict == core.GO


# --------------------------------------------------------------------------- #
# 6. refusal
# --------------------------------------------------------------------------- #
def test_run_refuses_when_an_index_of_the_wrong_shape_holds_the_name(engine, pg_url, capsys):
    _seed(engine, DUPES)
    with engine.connect() as conn:
        conn.execute(text(f"CREATE INDEX {S.INDEX_NAME} ON meetings (user_id)"))
        conn.commit()
    before = _snapshot(engine)

    assert main(["run", "--fix", "--dsn", pg_url]) == 20
    assert _snapshot(engine) == before
    out = capsys.readouterr().out
    assert "REFUSED" in out and "STOP" in out


# --------------------------------------------------------------------------- #
# 7. overrides
# --------------------------------------------------------------------------- #
def test_keep_meeting_id_overrides_keep_newest(engine, pg_url):
    _seed(engine, DUPES)
    _st, _v, plan = _state(engine)
    oldest = min((r for r in plan if r.platform == "zoom"), key=lambda r: r.created_at)

    assert main(["run", "--fix", "--dsn", pg_url,
                 "--keep-meeting-id", str(oldest.meeting_id)]) == 0
    with engine.connect() as conn:
        status = conn.execute(text("SELECT status FROM meetings WHERE id = :i"),
                              {"i": oldest.meeting_id}).scalar()
    assert status != "failed", "the explicitly kept row must survive"


def test_live_bot_strategy_keeps_the_row_with_a_container(engine, pg_url):
    _seed(engine, [
        (1, "google_meet", "aaa-bbb-ccc", "active", 0, "container-old"),
        (1, "google_meet", "aaa-bbb-ccc", "active", 30, None),
    ])
    assert main(["run", "--fix", "--dsn", pg_url, "--keep-strategy", "live-bot"]) == 0
    with engine.connect() as conn:
        survivor = conn.execute(text(
            "SELECT bot_container_id FROM meetings "
            "WHERE status NOT IN ('completed','failed')")).scalar()
    assert survivor == "container-old"


def test_retire_status_completed_is_accepted(engine, pg_url):
    _seed(engine, DUPES)
    assert main(["run", "--fix", "--dsn", pg_url, "--retire-status", "completed"]) == 0
    with engine.connect() as conn:
        statuses = {r[0] for r in conn.execute(text(
            "SELECT status FROM meetings WHERE data ? 'dedup'")).all()}
    assert statuses == {"completed"}


# --------------------------------------------------------------------------- #
# --json over a real database
# --------------------------------------------------------------------------- #
def test_json_output_parses_and_carries_the_verdict(engine, pg_url, capsys):
    import json as _json
    _seed(engine, DUPES)
    rc = main(["check", "--json", "--dsn", pg_url])
    doc = _json.loads(capsys.readouterr().out)
    assert rc == 10
    assert doc["verdict"] == core.ACTION_REQUIRED
    assert doc["duplicate_active_meetings"]["rows_to_retire"] == 3
    assert doc["dedup_index"]["present"] is False
    assert "***" in doc["database"]["target"] or "@" not in doc["database"]["target"]

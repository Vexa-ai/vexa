"""ADR-0052 s2c — planned rows reach Flows durably, on a real Postgres.

Every write that creates, moves, cancels, deletes or toggles a planned row puts a
``meeting.planned.changed`` fact into ``meeting_planned_outbox`` IN THE SAME TRANSACTION
(``planned_outbox``'s flush hook), and ``deliver`` hands the facts to Flows at least once, in
revision order, surviving a Flows outage. Also on Postgres: the start's planned-row claim
re-checks the row under its lock, and the booking cap counts in the creation transaction.

Opt-in: set ``MEETING_API_TEST_DATABASE_URL`` (``postgresql+asyncpg://…``) to a Postgres the role
can create databases on. Each test runs in a fresh database that is dropped afterwards.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

URL = os.getenv("MEETING_API_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set MEETING_API_TEST_DATABASE_URL to run")

USER = 7
PLAT = "google_meet"

_EVENT_TIME_FN = """
CREATE OR REPLACE FUNCTION meeting_event_time(data jsonb, start_time timestamp, created_at timestamp)
RETURNS timestamp LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
BEGIN
    RETURN COALESCE(((data ->> 'scheduled_at')::timestamptz AT TIME ZONE 'UTC'), start_time, created_at);
EXCEPTION WHEN OTHERS THEN
    RETURN COALESCE(start_time, created_at);
END
$fn$
"""


@pytest.fixture
async def sf():
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from meeting_api.sessions.models import Base

    name = f"planned_outbox_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as c:
        await c.execute(text(f"CREATE DATABASE {name}"))
    eng = create_async_engine(make_url(URL).set(database=name))
    async with eng.begin() as c:
        await c.execute(text(_EVENT_TIME_FN))
        await c.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(eng, expire_on_commit=False)
    finally:
        await eng.dispose()
        async with admin.connect() as c:
            await c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        await admin.dispose()


def _at(minutes: int) -> datetime:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).replace(microsecond=0)


def _z(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


async def _facts(sf) -> list[tuple[int, dict, bool]]:
    from sqlalchemy import select

    from meeting_api.sessions.models import MeetingPlannedOutbox as O

    async with sf() as db:
        return [(r.id, r.event, r.delivered)
                for r in (await db.execute(select(O).order_by(O.id))).scalars().all()]


async def _book(sf, native="abc-defg-hij", minutes=60, at=None, **kw):
    from meeting_api.collector.adapters import SqlAlchemyTranscriptStore

    store = SqlAlchemyTranscriptStore(sf)
    return await store.create_planned_meeting(
        USER, platform=PLAT, native_meeting_id=native,
        scheduled_at=(at or _at(minutes)).isoformat(), **kw)


async def _write(sf, meeting_id, **changes):
    """An ORM write of the row, as every route and sweep makes one."""
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    from meeting_api.sessions.models import Meeting

    async with sf() as db:
        m = (await db.execute(select(Meeting).where(Meeting.id == meeting_id))).scalars().one()
        if "status" in changes:
            m.status = changes["status"]
        if "data" in changes:
            m.data = {**m.data, **changes["data"]}
            flag_modified(m, "data")
        if changes.get("delete"):
            await db.delete(m)
        await db.commit()


# ── capture: the fact rides the row's own transaction ───────────────────────────────────────────

async def test_a_booking_writes_its_fact_with_the_computed_times(sf):
    start = _at(60)
    row = await _book(sf, at=start)
    ((rev, fact, delivered),) = await _facts(sf)
    assert not delivered
    assert fact == {"meeting_id": row["id"], "uid": "7", "status": "scheduled", "auto_join": True,
                    "pod_at": _z(start - timedelta(seconds=600)),
                    "join_at": _z(start - timedelta(seconds=120)),
                    "deadline": _z(start + timedelta(seconds=600))}
    assert "meeting_url" not in str(fact) and "passcode" not in str(fact)


async def test_a_plan_without_a_time_owes_no_fact(sf):
    from meeting_api.collector.adapters import SqlAlchemyTranscriptStore

    await SqlAlchemyTranscriptStore(sf).create_planned_meeting(
        USER, platform=PLAT, native_meeting_id="abc-defg-hij")
    assert await _facts(sf) == []


async def test_move_toggle_cancel_and_delete_each_write_a_newer_fact(sf):
    row = await _book(sf, minutes=60)
    mid = row["id"]
    later = _at(90)
    await _write(sf, mid, data={"scheduled_at": later.isoformat()})            # moved
    await _write(sf, mid, data={"auto_join": False})                           # toggled off
    await _write(sf, mid, data={"auto_join": True})                            # toggled on
    await _write(sf, mid, status="completed")                                  # cancelled
    facts = await _facts(sf)
    revs = [r for r, _, _ in facts]
    assert revs == sorted(revs) and len(set(revs)) == 5
    moved, off, on, cancelled = (f for _, f, _ in facts[1:])
    assert moved["join_at"] == _z(later - timedelta(seconds=120)) and moved["auto_join"]
    assert off["auto_join"] is False and on["auto_join"] is True
    assert cancelled["status"] == "completed" and cancelled["auto_join"] is False


async def test_a_deleted_planned_row_writes_a_deleted_fact(sf):
    row = await _book(sf)
    await _write(sf, row["id"], delete=True)
    assert (await _facts(sf))[-1][1]["status"] == "deleted"


async def test_a_stamp_that_does_not_change_the_fact_writes_nothing(sf):
    from meeting_api.bot_spawn.adapters import SqlAlchemyMeetingRepo

    row = await _book(sf)
    await SqlAlchemyMeetingRepo(sf).merge_meeting_data(
        row["id"], {"auto_join_last_attempt": _at(0).isoformat(), "auto_join_error": "x"})
    assert len(await _facts(sf)) == 1


async def test_a_rolled_back_write_leaves_no_fact(sf):
    from sqlalchemy import select

    from meeting_api.sessions.models import Meeting

    row = await _book(sf)
    async with sf() as db:
        m = (await db.execute(select(Meeting).where(Meeting.id == row["id"]))).scalars().one()
        m.status = "completed"
        await db.flush()                      # the fact is written in this transaction…
        await db.rollback()                   # …and goes with it
    assert len(await _facts(sf)) == 1


# ── the start claims THIS row, re-checked under the lock ────────────────────────────────────────

async def test_the_claim_takes_the_named_row_and_writes_its_fact(sf):
    from meeting_api.bot_spawn.adapters import SqlAlchemyMeetingRepo

    row = await _book(sf)
    claimed = await SqlAlchemyMeetingRepo(sf).create_meeting_guarded(
        user_id=USER, platform=PLAT, native_meeting_id="abc-defg-hij", data={"join_at": "x"},
        max_concurrent=3, planned_meeting_id=row["id"])
    assert claimed["id"] == row["id"] and claimed["status"] == "requested"
    last = (await _facts(sf))[-1][1]
    assert last["status"] == "requested" and last["auto_join"] is False


@pytest.mark.parametrize("change", [{"status": "completed"}, {"data": {"auto_join": False}}])
async def test_a_cancel_that_committed_first_wins_the_claim(sf, change):
    from meeting_api.bot_spawn.adapters import SqlAlchemyMeetingRepo
    from meeting_api.bot_spawn.ports import NotScheduled

    row = await _book(sf)
    await _write(sf, row["id"], **change)
    with pytest.raises(NotScheduled):
        await SqlAlchemyMeetingRepo(sf).create_meeting_guarded(
            user_id=USER, platform=PLAT, native_meeting_id="abc-defg-hij", data={},
            max_concurrent=3, planned_meeting_id=row["id"])


async def test_the_ceiling_counts_needs_help_on_postgres(sf):
    from meeting_api.bot_spawn.adapters import SqlAlchemyMeetingRepo

    row = await _book(sf, native="zzz-zzzz-zzz")
    await _write(sf, row["id"], status="needs_help")
    assert await SqlAlchemyMeetingRepo(sf).count_active_bots(user_id=USER) == 1


# ── delivery: at least once, in revision order, through an outage ───────────────────────────────

async def test_delivery_survives_a_flows_outage_and_keeps_revision_order(sf):
    from meeting_api.planned_outbox import deliver

    row = await _book(sf, minutes=60)
    await _write(sf, row["id"], data={"scheduled_at": _at(75).isoformat()})
    await _book(sf, native="xyz-wxyz-xyz", minutes=120)

    async def down(_body):
        raise ConnectionError("flows is down")

    assert (await deliver(sf, post=down))["delivered"] == 0
    assert all(not d for _, _, d in await _facts(sf))

    seen: list[dict] = []

    async def refuses_second(body):
        seen.append(body)
        return 202 if len(seen) == 1 else 503

    out = await deliver(sf, post=refuses_second)
    assert out["delivered"] == 1 and out["status"] == 503 and out["pending"]
    assert [d for _, _, d in await _facts(sf)] == [True, False, False], "stops at the first refusal"

    sent: list[dict] = []

    async def up(body):
        sent.append(body)
        return 202

    assert (await deliver(sf, post=up))["delivered"] == 2
    assert all(d for _, _, d in await _facts(sf))
    revs = [b["refs"]["revision"] for b in seen[:1] + sent]
    assert revs == sorted(revs)
    assert {b["event_type"] for b in sent} == {"meeting.planned.changed"}
    first = seen[0]
    assert first["source_event_id"] == f"planned-{row['id']}-r{first['refs']['revision']}"
    moved = sent[0]
    assert moved["refs"]["meeting_id"] == row["id"]
    assert moved["refs"]["revision"] > first["refs"]["revision"], "a later fact, a larger revision"
    assert (await deliver(sf, post=up))["delivered"] == 0, "nothing is delivered twice"


async def test_without_a_flows_address_rows_stay_pending(sf, monkeypatch):
    from meeting_api.planned_outbox import deliver

    monkeypatch.delenv("VEXA_FLOWS_API_URL", raising=False)
    await _book(sf)
    out = await deliver(sf)
    assert out["delivered"] == 0 and out["pending"]
    assert [d for _, _, d in await _facts(sf)] == [False]


# ── the booking cap counts inside the creation transaction ──────────────────────────────────────

async def test_the_booking_cap_on_postgres(sf, monkeypatch):
    monkeypatch.setenv("BOOKED_MAX_PER_ACCOUNT", "2")
    assert "error" not in await _book(sf, native="aaa-aaaa-aaa", minutes=30)
    assert "error" not in await _book(sf, native="bbb-bbbb-bbb", minutes=40)
    assert await _book(sf, native="ccc-cccc-ccc", minutes=50) == {"error": "booking_cap", "cap": 2}
    stamped = await _book(sf, native="ddd-dddd-ddd", minutes=50, over_cap="stamp")
    assert stamped["data"]["auto_join"] is False and stamped["data"]["auto_join_error"]
    assert (await _facts(sf))[-1][1]["auto_join"] is False, "no alarm for a capped row"
    assert await _book(sf, native="eee-eeee-eee", minutes=50,
                       **{}) == {"error": "booking_cap", "cap": 2}


async def test_the_start_route_end_to_end_on_postgres(sf, monkeypatch):
    """Book → Flows calls the start at pod_at → the bot's pod starts for that row with its join time
    → the row's fact says it is no longer armed. A second call answers not_scheduled."""
    from meeting_api.bot_spawn.adapters import SqlAlchemyMeetingRepo
    from meeting_api.bot_spawn.fakes import FakeRuntimeClient
    from meeting_api.bot_spawn.planned_start import start_planned_meeting

    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
    start = _at(10)
    row = await _book(sf, at=start)
    repo, runtime = SqlAlchemyMeetingRepo(sf), FakeRuntimeClient()

    async def ctx(_uid):
        return {"max_concurrent": 2}

    kw = dict(user_id=USER, repo=repo, runtime=runtime, fetch_bot_context=ctx,
              transcribe_gate=lambda: None, token_secret="s", redis_url="redis://r")
    out = await start_planned_meeting(row["id"], **kw)
    assert out.kind == "spawned", out
    stored = await repo.get_meeting(row["id"])
    assert stored["status"] == "requested"
    assert stored["data"]["join_at"] == _z(start - timedelta(seconds=120))
    assert (await _facts(sf))[-1][1]["auto_join"] is False
    again = await start_planned_meeting(row["id"], **kw)
    assert (again.code, again.http) == ("not_scheduled", 409) and len(runtime.specs) == 1

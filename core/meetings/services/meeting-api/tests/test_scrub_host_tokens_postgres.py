"""MIGRATION-0012 on a REAL Postgres: the scrub removes stored Zoom host tokens (``zak``) everywhere
and masks passcodes in the bot's diagnostics only, in ``meetings.data`` alone; a second run does
nothing.

Seeded rows:
  A  the host token in the join link, the calendar snapshot, ``reason`` and ``status_transition``,
     beside the attendee passcode;
  B  a join link with only its passcode (auto-join needs it: never touched);
  C  "zak=1" and "pwd=1" outside any URL, under a non-diagnostic key (never touched);
  D  a Teams passcode quoted in ``last_error`` and ``join_evidence.detail``, no host token;
  E  diagnostics already masked (``?pwd=***``): not selected;
  F  diagnostics with ``?P=`` in a stored JavaScript error and ``?pwd=`` on no link: not selected,
     because both rewrites act on http(s) URLs only (a selection wider than the rewrite would never
     reach zero).
The dry run counts both kinds and writes nothing; the apply strips the token, masks the
diagnostics' passcodes, keeps the join links' passcode, the numbers' stored spelling, ``updated_at``
and every other row byte for byte; the second apply changes nothing.

Opt-in: set MEETING_API_TEST_DATABASE_URL to an EMPTY database (postgresql+asyncpg://…). The test
creates the tables it needs and drops them afterwards.
"""
from __future__ import annotations

import json
import os

import pytest

URL = os.getenv("MEETING_API_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set MEETING_API_TEST_DATABASE_URL to an empty Postgres database")

TOKEN = "eyJ0eXAiOiJKV1QiLCJob3N0In0.c2VjcmV0LWhvc3QtdG9rZW4.sig"
PWD = "AbC.1"
JOIN = f"https://us05web.zoom.us/j/81234567890?pwd={PWD}"
START = f"https://us05web.zoom.us/s/81234567890?zak={TOKEN}"
WITH_ZAK = f"{JOIN}&zak={TOKEN}"
TEAMS_P = "Tm5Pass9"
TEAMS = f"https://teams.microsoft.com/meet/3741234567890?p={TEAMS_P}"

SEED = [
    # (platform_specific_id, data as JSON text — the spelling Postgres stores is what is compared)
    ("A", (
        '{"constructed_meeting_url": "' + WITH_ZAK + '", '
        '"reason": "page.goto: Timeout 60000ms exceeded. navigating to \\"' + WITH_ZAK + '\\"", '
        '"status_transition": [{"to": "failed", "reason": "Cannot join ' + WITH_ZAK + '."}], '
        '"calendar_sources": [{"id": "c1", "event": {"component": {"properties": {"DESCRIPTION": '
        '[{"value": "Join: ' + WITH_ZAK + '\\nHost: ' + START + '\\n&lt;a href=' + JOIN + '&amp;zak=' + TOKEN + '&gt;"}]}}}}], '
        '"duration_s": 1.10, "recording_enabled": true, "title": "Weekly"}'
    )),
    ("B", '{"constructed_meeting_url": "https://us05web.zoom.us/j/81234567891?pwd=Keep", "n": 2.50}'),
    ("C", '{"title": "Zakaria said zak=1 and pwd=1 outside any link", "notes": "see ' + JOIN + '", "x": 1}'),
    ("D", (
        '{"constructed_meeting_url": "' + TEAMS + '", '
        '"last_error": {"reason": "Navigation failed: ' + TEAMS + '", "stage": "joining"}, '
        '"join_evidence": {"reason": "navigation_failure", "detail": "goto ' + TEAMS + ' timed out", "n": 3.00}}'
    )),
    ("E", '{"reason": "Cannot join https://zoom.us/j/1?pwd=***.", "last_error": {"reason": "x"}}'),
    ("F", '{"last_error": {"reason": "TypeError in a.b(c){for(;;)e==1?P=2:0}"}, "reason": "no link ?pwd=abc"}'),
]


# the expression index on meetings calls this; the deployment creates it beside the schema
_EVENT_TIME = """
CREATE OR REPLACE FUNCTION meeting_event_time(data jsonb, start_time timestamp, created_at timestamp)
RETURNS timestamp LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
BEGIN
    RETURN COALESCE(((data ->> 'scheduled_at')::timestamptz AT TIME ZONE 'UTC'), start_time, created_at);
EXCEPTION WHEN OTHERS THEN
    RETURN COALESCE(start_time, created_at);
END $fn$;
"""


async def _state(conn):
    from sqlalchemy import text
    rows = (await conn.execute(text(
        "SELECT platform_specific_id, data::text, updated_at FROM meetings ORDER BY id"))).all()
    return {r[0]: (r[1], r[2]) for r in rows}


@pytest.fixture
async def db():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from meeting_api.sessions.models import Base

    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.execute(text(_EVENT_TIME))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        for native, data in SEED:
            await conn.execute(text(
                "INSERT INTO meetings (user_id, platform, platform_specific_id, status, data, updated_at) "
                "VALUES (7, 'zoom', :n, 'completed', CAST(:d AS jsonb), '2026-10-01 12:00:00')"),
                {"n": native, "d": data})
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def test_dry_run_counts_and_writes_nothing(db):
    from meeting_api.scrub_host_tokens import run

    async with db.connect() as conn:
        before = await _state(conn)
    report = await run(URL, do_apply=False)
    assert report["mode"] == "dry-run"
    assert report["before"]["rows"] == 2                                   # A and D
    assert report["before"]["host_token"] == {"rows": 1, "keys": {
        "calendar_sources": 1, "constructed_meeting_url": 1, "reason": 1, "status_transition": 1}}
    assert report["before"]["passcode_in_diagnostics"] == {"rows": 2, "keys": {
        "join_evidence": 1, "last_error": 1, "reason": 1, "status_transition": 1}}
    assert "changed_rows" not in report
    assert TOKEN not in str(report) and PWD not in str(report) and TEAMS_P not in str(report)
    async with db.connect() as conn:
        assert await _state(conn) == before


async def test_apply_strips_host_tokens_masks_diagnostics_and_is_idempotent(db):
    from meeting_api.scrub_host_tokens import run

    async with db.connect() as conn:
        before = await _state(conn)
    assert TOKEN in before["A"][0] and TEAMS_P in before["D"][0], "positive control: the seed carries both"

    first = await run(URL, do_apply=True)
    assert first["changed_rows"] == 2 and first["after"]["rows"] == 0
    assert first["after"]["host_token"]["rows"] == 0
    assert first["after"]["passcode_in_diagnostics"]["rows"] == 0
    async with db.connect() as conn:
        after = await _state(conn)

    a_text, a_updated = after["A"]
    a = json.loads(a_text)
    assert TOKEN not in a_text and "zak=" not in a_text
    # the join links auto-join reads keep the attendee passcode
    assert a["constructed_meeting_url"] == JOIN
    desc = a["calendar_sources"][0]["event"]["component"]["properties"]["DESCRIPTION"][0]["value"]
    assert desc == f"Join: {JOIN}\nHost: https://us05web.zoom.us/s/81234567890\n&lt;a href={JOIN}&gt;"
    # the diagnostics keep host and meeting id, not the passcode
    assert a["reason"] == ('page.goto: Timeout 60000ms exceeded. navigating to '
                           '"https://us05web.zoom.us/j/81234567890?pwd=***"')
    assert a["status_transition"][0]["reason"] == "Cannot join https://us05web.zoom.us/j/81234567890?pwd=***."
    assert '"duration_s": 1.10' in a_text, "an untouched number keeps its stored spelling"
    assert a_updated == before["A"][1], "no other column is written"

    d_text = after["D"][0]
    d = json.loads(d_text)
    assert d["constructed_meeting_url"] == TEAMS, "a join link keeps its passcode"
    assert d["last_error"] == {"reason": "Navigation failed: https://teams.microsoft.com/meet/3741234567890?p=***",
                               "stage": "joining"}
    assert d["join_evidence"]["detail"] == "goto https://teams.microsoft.com/meet/3741234567890?p=*** timed out"
    assert '"n": 3.00' in d_text and d_text.count(TEAMS_P) == 1
    assert after["D"][1] == before["D"][1]

    for untouched in ("B", "C", "E", "F"):
        assert after[untouched] == before[untouched], untouched

    second = await run(URL, do_apply=True)
    assert second["changed_rows"] == 0 and second["before"]["rows"] == 0
    async with db.connect() as conn:
        assert await _state(conn) == after

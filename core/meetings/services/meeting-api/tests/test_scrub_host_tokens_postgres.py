"""MIGRATION-0012 on a REAL Postgres: the host-token scrub changes only the strings that carried a
``zak``, in ``meetings.data`` alone, and a second run does nothing.

Seeded: one row whose link, calendar snapshot, bot reason and status history quote a Zoom link with
the host token (and the attendee passcode); one row whose link carries only the passcode; one row
that says "zak" outside any URL. The dry run counts and writes nothing; the apply strips the token
and keeps the passcode, the numbers' stored spelling, ``updated_at`` and every other row byte for
byte; the second apply changes nothing.

Opt-in: set MEETING_API_TEST_DATABASE_URL to an EMPTY database (postgresql+asyncpg://…). The test
creates the tables it needs and drops them afterwards.
"""
from __future__ import annotations

import os

import pytest

URL = os.getenv("MEETING_API_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set MEETING_API_TEST_DATABASE_URL to an empty Postgres database")

TOKEN = "eyJ0eXAiOiJKV1QiLCJob3N0In0.c2VjcmV0LWhvc3QtdG9rZW4.sig"
JOIN = "https://us05web.zoom.us/j/81234567890?pwd=AbC.1"
START = f"https://us05web.zoom.us/s/81234567890?zak={TOKEN}"
WITH_ZAK = f"{JOIN}&zak={TOKEN}"

SEED = [
    # (platform_specific_id, data as JSON text — the spelling Postgres stores is what is compared)
    ("81234567890", (
        '{"constructed_meeting_url": "' + WITH_ZAK + '", '
        '"reason": "page.goto: Timeout 60000ms exceeded. navigating to \\"' + WITH_ZAK + '\\"", '
        '"status_transition": [{"to": "failed", "reason": "Cannot join ' + WITH_ZAK + '."}], '
        '"calendar_sources": [{"id": "c1", "event": {"component": {"properties": {"DESCRIPTION": '
        '[{"value": "Join: ' + WITH_ZAK + '\\nHost: ' + START + '\\n&lt;a href=' + JOIN + '&amp;zak=' + TOKEN + '&gt;"}]}}}}], '
        '"duration_s": 1.10, "recording_enabled": true, "title": "Weekly"}'
    )),
    ("81234567891", '{"constructed_meeting_url": "https://us05web.zoom.us/j/81234567891?pwd=Keep", "n": 2.50}'),
    ("81234567892", '{"title": "Zakaria said zak=1 outside any link", "x": 1}'),
]


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
    assert report["before"]["rows"] == 1
    assert report["before"]["keys"] == {"calendar_sources": 1, "constructed_meeting_url": 1,
                                        "reason": 1, "status_transition": 1}
    assert "changed_rows" not in report and TOKEN not in str(report)
    async with db.connect() as conn:
        assert await _state(conn) == before


async def test_apply_strips_only_the_host_token_and_is_idempotent(db):
    from meeting_api.scrub_host_tokens import run

    async with db.connect() as conn:
        before = await _state(conn)
    assert TOKEN in before["81234567890"][0], "positive control: the seed carries the token"

    first = await run(URL, do_apply=True)
    assert first["changed_rows"] == 1 and first["after"]["rows"] == 0
    async with db.connect() as conn:
        after = await _state(conn)

    scrubbed, updated_at = after["81234567890"]
    assert TOKEN not in scrubbed and "zak=" not in scrubbed
    assert scrubbed.count("pwd=AbC.1") == before["81234567890"][0].count("pwd=AbC.1"), "the passcode stays"
    assert "https://us05web.zoom.us/s/81234567890\\n" in scrubbed, "a start link loses only its token"
    assert '"duration_s": 1.10' in scrubbed, "an untouched number keeps its stored spelling"
    assert updated_at == before["81234567890"][1], "no other column is written"
    # every other key is byte-identical once the token is gone
    assert scrubbed == before["81234567890"][0].replace(f"&zak={TOKEN}", "").replace(f"?zak={TOKEN}", "") \
        .replace(f"&amp;zak={TOKEN}", "")
    assert after["81234567891"] == before["81234567891"]
    assert after["81234567892"] == before["81234567892"]

    second = await run(URL, do_apply=True)
    assert second["changed_rows"] == 0 and second["before"]["rows"] == 0
    async with db.connect() as conn:
        assert await _state(conn) == after

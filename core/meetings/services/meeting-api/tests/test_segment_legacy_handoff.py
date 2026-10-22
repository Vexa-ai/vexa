"""A meeting a 0.12 bot is transcribing when meeting-api is upgraded keeps its transcript.

The 0.12 bot writes unsigned ``transcription_segments`` entries (``{"payload": …}`` only). 0.13 drops
unsigned entries, so without the hand-off (``collector/legacy_unsigned.py``) every segment after the
cut-over is lost. These tests replay the 0.12 entry shape: admitted for a meeting spawned before 0.13
inside the window; refused for any other meeting, after the window, with the window off, and for an
entry that carries a signature that does not verify.
"""
from __future__ import annotations

import json
import time

import fakeredis
import pytest

from meeting_api.collector import consume_segments
from meeting_api.collector import legacy_unsigned as lu
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore
from meeting_api.collector.ingest import STREAM_NAME, reclaim_segments  # noqa: F401

from _segment_auth import admin_token, signed_fields, signed_xadd  # noqa: F401


@pytest.fixture(autouse=True)
def _closed_window():
    lu.WINDOW.close()
    yield
    lu.WINDOW.close()


@pytest.fixture
def store():
    s = InMemoryTranscriptStore()
    s.seed_meeting(user_id=7, platform="google_meet", native_meeting_id="abc-defg-hij")   # meeting 1
    s.seed_meeting(user_id=8, platform="google_meet", native_meeting_id="xyz-wxyz-xyz")   # meeting 2
    return s


@pytest.fixture
async def bus():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    b = FakeRedisBus(client)
    yield b
    await client.aclose()


def _data(meeting_id, text):
    return {"type": "transcription", "meeting_id": meeting_id,
            "segments": [{"segment_id": f"s-{text}", "start": 0.0, "end": 1.0, "text": text, "completed": True}]}


async def _xadd_0_12(bus, data):
    """The entry exactly as a 0.12.28 bot writes it: the payload and nothing else."""
    await bus._client.xadd(STREAM_NAME, {"payload": json.dumps(data)})


async def _texts(store, user, native):
    doc = await store.get_transcript(user, "google_meet", native)
    return sorted(s["text"] for s in (doc or {}).get("segments", []))


async def test_a_0_12_bots_segments_are_kept_inside_the_window(store, bus, admin_token):
    lu.WINDOW.open([1], window_s=3600)
    await _xadd_0_12(bus, _data(1, "after-cutover"))
    assert await consume_segments(store, bus) == 1
    assert await _texts(store, 7, "abc-defg-hij") == ["after-cutover"]


async def test_without_the_hand_off_they_are_dropped(store, bus, admin_token):
    await _xadd_0_12(bus, _data(1, "lost"))
    assert await consume_segments(store, bus) == 0


async def test_only_the_listed_meetings_are_admitted(store, bus, admin_token):
    lu.WINDOW.open([1], window_s=3600)
    await _xadd_0_12(bus, _data(2, "forged"))
    assert await consume_segments(store, bus) == 0
    assert await _texts(store, 8, "xyz-wxyz-xyz") == []


async def test_the_window_ends(store, bus, admin_token):
    lu.WINDOW.open([1], window_s=60, now=time.time() - 61)
    await _xadd_0_12(bus, _data(1, "late"))
    assert await consume_segments(store, bus) == 0


async def test_a_bad_signature_is_never_rescued(store, bus, admin_token):
    lu.WINDOW.open([1], window_s=3600)
    fields = signed_fields(_data(1, "x"))
    fields["payload"] = json.dumps(_data(1, "tampered"))
    await bus._client.xadd(STREAM_NAME, fields)
    assert await consume_segments(store, bus) == 0


async def test_signed_entries_are_unaffected(store, bus, admin_token):
    lu.WINDOW.open([1], window_s=3600)
    await signed_xadd(bus, _data(1, "signed"))
    assert await consume_segments(store, bus) == 1


def test_the_window_setting(monkeypatch):
    # 10-E: off unless the operator opens it for a 0.12 cut-over; a value that is not a number is off.
    assert lu.window_seconds({}) == 0
    assert lu.window_seconds({lu.WINDOW_ENV: ""}) == 0
    assert lu.window_seconds({lu.WINDOW_ENV: "0"}) == 0
    assert lu.window_seconds({lu.WINDOW_ENV: "junk"}) == 0
    assert lu.window_seconds({lu.WINDOW_ENV: "14400"}) == 14400  # the runbook's cut-over value
    w = lu.LegacyUnsignedWindow()
    w.open([1], window_s=0)
    assert not w.admits({"payload": json.dumps(_data(1, "x"))})


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, ids, seen):
        self._ids, self._seen = ids, seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params):
        self._seen.append((str(query), params))
        return _Rows([(i,) for i in self._ids])


async def test_boot_reads_in_flight_meetings_without_the_0_13_marker():
    seen, ids = [], [11, 12]
    w = lu.LegacyUnsignedWindow()
    n = await lu.open_from_db(lambda: _Session(ids, seen), env={lu.WINDOW_ENV: "3600"}, window=w)
    assert n == 2 and w.meeting_ids == {11, 12}
    sql, params = seen[0]
    assert "status IN" in sql and "'active'" in sql and "'stopping'" in sql
    assert params == {"marker": "transcription_language"}


async def test_refresh_adds_0_12_meetings_spawned_during_a_rolling_upgrade_but_never_extends():
    w = lu.LegacyUnsignedWindow()
    await lu.open_from_db(lambda: _Session([11], []), env={lu.WINDOW_ENV: "3600"}, window=w)
    until = w.until
    assert await lu.refresh_from_db(lambda: _Session([11, 13], []), window=w) == 1
    assert w.meeting_ids == {11, 13} and w.until == until
    assert await lu.refresh_from_db(lambda: _Session([14], []), window=w, now=until + 1) == 0
    assert 14 not in w.meeting_ids


async def test_by_default_boot_opens_nothing_and_reads_nothing():
    """10-E: with the setting unset no meeting is read and an unsigned entry of a 0.12 meeting is refused."""
    seen, w = [], lu.LegacyUnsignedWindow()
    assert await lu.open_from_db(lambda: _Session([1], seen), env={}, window=w) == 0
    assert seen == [] and not w.admits({"payload": json.dumps(_data(1, "x"))})


async def test_the_runbook_value_opens_it_over_the_in_flight_0_12_meetings():
    seen, w = [], lu.LegacyUnsignedWindow()
    assert await lu.open_from_db(lambda: _Session([1], seen), env={lu.WINDOW_ENV: "14400"}, window=w) == 1
    assert w.admits({"payload": json.dumps(_data(1, "x"))})


async def test_window_off_reads_nothing():
    seen = []
    w = lu.LegacyUnsignedWindow()
    assert await lu.open_from_db(lambda: _Session([1], seen), env={lu.WINDOW_ENV: "0"}, window=w) == 0
    assert seen == [] and not w.meeting_ids

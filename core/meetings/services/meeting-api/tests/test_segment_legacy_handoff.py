"""A meeting a 0.12 bot is transcribing when meeting-api is upgraded keeps its transcript.

The 0.12 bot writes unsigned ``transcription_segments`` entries (``{"payload": …}`` only). 0.13 drops
unsigned entries, so without the hand-off (``collector/legacy_unsigned.py``) every segment after the
cut-over is lost. These tests replay the 0.12 entry shape: admitted (and counted) for a meeting
spawned before 0.13 inside the window; refused for any other meeting, after the window, and for an
entry that carries a signature that does not verify or names another meeting. The window is fixed
(4 h from boot, no setting) and the hand-off goes in ``removed_config.COMPAT_REMOVED_IN``.
"""
from __future__ import annotations

import json
import time

import fakeredis
import pytest

from meeting_api.collector import consume_segments
from meeting_api import obs, removed_config
from meeting_api.collector import legacy_unsigned as lu
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore
from meeting_api.collector.ingest import STREAM_NAME, reclaim_segments  # noqa: F401

from _segment_auth import admin_token, signed_fields, signed_xadd, token_for  # noqa: F401


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


async def test_a_0_12_bots_segments_are_kept_inside_the_window_and_counted(store, bus, admin_token,
                                                                          monkeypatch):
    events = []
    monkeypatch.setattr(obs, "log_event", lambda name, **kw: events.append((name, kw)))
    monkeypatch.setattr(removed_config, "COMPAT_USE", {})
    lu.WINDOW.open([1], window_s=3600)
    await _xadd_0_12(bus, _data(1, "after-cutover"))
    await _xadd_0_12(bus, _data(1, "still-talking"))
    assert await consume_segments(store, bus) == 2
    assert await _texts(store, 7, "abc-defg-hij") == ["after-cutover", "still-talking"]
    admitted = [kw["fields"] for name, kw in events if name == "segment_entry_unsigned_admitted"]
    assert [f["count"] for f in admitted] == [1, 2]
    assert admitted[0] == {"meeting_id": 1, "count": 1, "removed_in": "0.13.6"}


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


async def test_a_signed_entry_for_another_meeting_is_never_rescued(store, bus, admin_token):
    """Meeting 1's session token signing a payload for meeting 2: refused, inside the window too."""
    lu.WINDOW.open([1, 2], window_s=3600)
    await signed_xadd(bus, _data(2, "cross"), token=token_for(1))
    assert await consume_segments(store, bus) == 0
    assert await _texts(store, 8, "xyz-wxyz-xyz") == []


async def test_signed_entries_are_unaffected(store, bus, admin_token):
    lu.WINDOW.open([1], window_s=3600)
    await signed_xadd(bus, _data(1, "signed"))
    assert await consume_segments(store, bus) == 1


def test_the_window_is_fixed_not_a_setting(monkeypatch):
    # The runbook's cut-over value, four hours; the old setting is not read (removed_config).
    assert lu.WINDOW_S == 14400
    assert not hasattr(lu, "WINDOW_ENV") and not hasattr(lu, "window_seconds")
    assert "VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S" in removed_config.REMOVED_KEYS
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
    n = await lu.open_from_db(lambda: _Session(ids, seen), window=w)
    assert n == 2 and w.meeting_ids == {11, 12}
    assert 0 < w.until - time.time() <= lu.WINDOW_S
    sql, params = seen[0]
    assert "status IN" in sql and "'active'" in sql and "'stopping'" in sql
    assert params == {"marker": "transcription_language"}


async def test_refresh_adds_0_12_meetings_spawned_during_a_rolling_upgrade_but_never_extends():
    w = lu.LegacyUnsignedWindow()
    await lu.open_from_db(lambda: _Session([11], []), window=w, seconds=3600)
    until = w.until
    assert await lu.refresh_from_db(lambda: _Session([11, 13], []), window=w) == 1
    assert w.meeting_ids == {11, 13} and w.until == until
    assert await lu.refresh_from_db(lambda: _Session([14], []), window=w, now=until + 1) == 0
    assert 14 not in w.meeting_ids


async def test_every_boot_opens_it_over_the_in_flight_0_12_meetings(monkeypatch):
    """No setting opens it: a boot reads the in-flight 0.12 meetings, whatever the old key says."""
    monkeypatch.setenv("VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S", "0")
    seen, w = [], lu.LegacyUnsignedWindow()
    assert await lu.open_from_db(lambda: _Session([1], seen), window=w) == 1
    assert w.admits({"payload": json.dumps(_data(1, "x"))})
    assert not w.admits({"payload": json.dumps(_data(2, "x"))})


async def test_without_a_database_nothing_opens():
    w = lu.LegacyUnsignedWindow()
    assert await lu.open_from_db(None, window=w) == 0
    assert not w.meeting_ids


def test_the_production_loop_opens_and_refreshes_it():
    import inspect

    from meeting_api import __main__ as entry

    src = inspect.getsource(entry._attach_background_loops)
    assert "legacy_unsigned.open_from_db(session_factory)" in src
    assert "legacy_unsigned.refresh_from_db(session_factory)" in src

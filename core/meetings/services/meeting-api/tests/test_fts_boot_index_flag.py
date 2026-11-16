"""MEETING_API_FTS_BOOT_INDEX gates the boot FTS index task (default on).

Off where the serving role does not own `transcriptions` (prod), so the build fails on every boot, and
where serving processes run no DDL (G2). Off: the task is not started and one line is logged. Drives
the real `_attach_background_loops` lifespan, as test_main_ensure_fts_index_wiring.py does.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import types

import pytest

import meeting_api.__main__ as main_mod


class _Store:
    def __init__(self):
        self.calls = 0

    async def ensure_fts_index(self):
        self.calls += 1
        return {"status": "present", "index": "ix_transcription_text_fts"}


class _Redis:
    async def publish(self, channel, message):
        return None


async def _boot(store):
    app = types.SimpleNamespace(state=types.SimpleNamespace(), router=types.SimpleNamespace())
    main_mod._attach_background_loops(
        app, transcript_store=store, segment_bus=types.SimpleNamespace(), redis_client=_Redis(),
        meeting_repo=None, runtime=None, service_authority=None, system_webhook_sink=None,
        session_factory=None, storage=None)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.05)


@pytest.mark.parametrize("value", [None, "", "true", "1", "on"])
async def test_on_by_default_and_when_set_on(monkeypatch, value, caplog):
    if value is None:
        monkeypatch.delenv("MEETING_API_FTS_BOOT_INDEX", raising=False)
    else:
        monkeypatch.setenv("MEETING_API_FTS_BOOT_INDEX", value)
    store = _Store()
    with caplog.at_level(logging.INFO):
        await _boot(store)
    assert store.calls == 1
    assert not [r for r in caplog.records if "FTS boot index task not started" in r.getMessage()]


@pytest.mark.parametrize("value", ["false", "0", "off", "no"])
async def test_off_starts_no_task_and_logs_one_line(monkeypatch, value, caplog):
    monkeypatch.setenv("MEETING_API_FTS_BOOT_INDEX", value)
    store = _Store()
    with caplog.at_level(logging.INFO):
        await _boot(store)
    assert store.calls == 0
    lines = [r for r in caplog.records if "FTS boot index task not started" in r.getMessage()]
    assert len(lines) == 1
    started = [r.getMessage() for r in caplog.records if "background loops started" in r.getMessage()]
    assert started and "ensure-fts-index" not in started[0]


def test_the_key_is_declared_defaulted_on():
    doc = json.loads((pathlib.Path(main_mod.__file__).parent / "config.v1.json").read_text())
    row = next(k for k in doc["keys"] if k["key"] == "MEETING_API_FTS_BOOT_INDEX")
    assert row["class"] == "defaulted" and row["default"] == "true"

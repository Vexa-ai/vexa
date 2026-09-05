"""F191 — ``ensure_fts_index`` (``SqlAlchemyTranscriptStore``, collector/adapters.py) built the
transcript FTS GIN index and was called from nowhere: defined, dead, the index absent on every
deployment, and transcript search silently fell back to a sequential scan of ``transcriptions``
since the day the function shipped.

This test drives the REAL ``_attach_background_loops`` wiring in ``meeting_api.__main__`` — not a
reimplementation of it — so it FAILS on the pre-fix tip (the stub's ``ensure_fts_index`` is never
invoked, because nothing calls it) and passes once ``_ensure_fts_index_once`` is registered as a
lifespan task (MIGRATION-0006).

Modelled on ``test_main_auto_join_wiring.py``'s harness (same file, same trick: drive the real
``lifespan`` context manager against bare fakes). No ``asyncio.sleep`` patch is needed here the way
that test needs one: ``_ensure_fts_index_once`` is a ONE-SHOT task, not a ``while True`` loop — it
returns on its own once the (stubbed) index build completes, so a short real sleep is enough to let
it run to completion.
"""
from __future__ import annotations

import asyncio
import types

import meeting_api.__main__ as main_mod


def _fake_app():
    app = types.SimpleNamespace()
    app.state = types.SimpleNamespace()
    app.router = types.SimpleNamespace()
    return app


class _StubTranscriptStore:
    def __init__(self):
        self.calls = 0

    async def ensure_fts_index(self):
        self.calls += 1
        return {"status": "created", "index": "ix_transcription_text_fts"}


class _FakeRedis:
    async def publish(self, channel, message):
        return None


async def test_ensure_fts_index_is_called_from_the_real_lifespan():
    store = _StubTranscriptStore()
    app = _fake_app()

    main_mod._attach_background_loops(
        app,
        transcript_store=store,
        segment_bus=types.SimpleNamespace(),
        redis_client=_FakeRedis(),
        meeting_repo=None,
        runtime=None,
        service_authority=None,
        system_webhook_sink=None,
        session_factory=None,  # _guarded degrades to run-the-tick unconditionally (no real PG)
        storage=None,
    )

    async with app.router.lifespan_context(app):
        # Every other loop here is a `while True`, so sibling tests patch `asyncio.sleep` to end
        # them after one tick. `_ensure_fts_index_once` is one-shot and returns on its own — a
        # short real sleep is enough for `asyncio.create_task` to actually run it before we exit.
        await asyncio.sleep(0.05)

    assert store.calls == 1, "ensure_fts_index was never called — the lifespan task is not wired"


async def test_ensure_fts_index_is_skipped_gracefully_without_the_real_store():
    """A fake/Lite ``transcript_store`` that does not carry ``ensure_fts_index`` (e.g.
    ``InMemoryTranscriptStore``, or the bare ``SimpleNamespace`` the auto-join wiring test uses for
    every OTHER loop) must not crash the lifespan — the same ``hasattr`` guard
    ``_attach_background_loops`` already uses for ``upsert_segments`` / ``create_planned_meeting``.
    """
    app = _fake_app()

    main_mod._attach_background_loops(
        app,
        transcript_store=types.SimpleNamespace(),
        segment_bus=types.SimpleNamespace(),
        redis_client=_FakeRedis(),
        meeting_repo=None,
        runtime=None,
        service_authority=None,
        system_webhook_sink=None,
        session_factory=None,
        storage=None,
    )

    # No assertion beyond "the lifespan starts and tears down without raising" — the proof this
    # test exists for is that it completes at all; a missing `hasattr` guard would raise inside
    # `_ensure_fts_index_once` before ever reaching `_guarded`.
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.05)

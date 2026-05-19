"""Tests for the Pack E.1.a v2 lock-before-snapshot fix.

Background
==========
[PLATFORM] code review on #272 (issuecomment-4327366063, 2026-04-27 13:42Z)
flagged that the v1 shape acquired SELECT … FOR UPDATE AFTER snapshotting
``meeting.data``, allowing a stale-read race:

  1. Request A loads ``meeting.data`` snapshot → media_files = []
  2. Request B loads ``meeting.data`` snapshot → media_files = []
  3. Request A acquires FOR UPDATE → appends [audio] → commits → releases
  4. Request B acquires FOR UPDATE → appends [video] (using its stale
     snapshot still showing []) → commits → AUDIO ENTRY LOST

The v2 fix (commit 686ce5f) moves SELECT FOR UPDATE BEFORE the snapshot
in the meeting_data branch. The snapshot is then re-derived under the
lock, so request B's snapshot includes A's commit.

Test surfaces
=============
This file ships THREE tests at increasing fidelity:

* ``test_lock_acquired_before_snapshot_in_source`` — ast-based static
  assertion on the source. Cheapest, catches the regression even in
  mock-only CI. Runs in <50 ms.

* ``test_sequential_audio_then_video_both_persist`` — sanity test that
  the v2 shape preserves prior-shape behavior: two sequential uploads
  for the same session_uid (audio + video) yield ``media_files == [audio, video]``.

* ``test_concurrent_audio_video_no_lost_entry`` — deterministic
  interleaving simulation using a stateful mock DB. Two coroutines
  upload audio + video concurrently via ``asyncio.gather``; the test
  verifies the final ``media_files`` contains both. With the v1 (broken)
  code this would fail; with the v2 fix it passes.

For real-Postgres concurrency proof, a separate integration test
(skipped here) would run against an actual asyncpg connection. The
[PLATFORM] production-side observer (``len(media_files) <
num_distinct_media_types_in_S3`` audit, see #272 issuecomment-4327676126)
covers that surface in production traffic.
"""

from __future__ import annotations

import asyncio
import ast
import inspect
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api import recordings as recordings_module
from meeting_api.models import Meeting, MeetingSession
from meeting_api.schemas import MeetingStatus

from .conftest import (
    MockResult as _BaseMockResult,
    TEST_MEETING_ID,
    TEST_SESSION_UID,
    TEST_USER_ID,
    make_meeting,
    make_session,
)


class MockResult(_BaseMockResult):
    """Extends conftest.MockResult with ``scalar_one()`` (raises if empty),
    matching SQLAlchemy 2.x semantics needed by the lock-FOR-UPDATE path
    in recordings.py::internal_upload_recording.
    """

    def scalar_one(self):
        if not self._items:
            raise RuntimeError("MockResult.scalar_one() called on empty result")
        return self._items[0]


# ───────────────────────────────────────────────────────────────────────
# Test 1 — static assertion: lock is acquired BEFORE snapshot.
#
# This test parses the source of internal_upload_recording and asserts
# that within the `if use_meeting_data:` branch, the SELECT … FOR
# UPDATE call appears textually BEFORE the first re-derivation of
# meeting_data_dict / recordings_list. Catches the v1-shape regression
# without needing any database. Cheap, fast, deterministic.
# ───────────────────────────────────────────────────────────────────────


def test_lock_acquired_before_snapshot_in_source():
    """The Pack E.1.a v2 invariant: SELECT FOR UPDATE BEFORE snapshot.

    If this fails, someone has reintroduced the stale-snapshot race
    flagged by [PLATFORM] in #272 issuecomment-4327366063. The v2
    contract is explicit:

      1. Phase 1 (no lock): load meeting, derive legacy_id for storage_path
      2. Phase 2 (no lock): S3 upload (idempotent on key)
      3. Phase 3 (LOCKED):  SELECT FOR UPDATE → re-snapshot → JSONB write

    The static check below confirms phase 3's lock acquisition appears
    before the first ``meeting.data`` snapshot DERIVATION inside the
    meeting_data branch (the pre-lock snapshot at ~line 181 is a
    storage_path helper only — not a JSONB write source).
    """
    src = inspect.getsource(recordings_module.internal_upload_recording)
    tree = ast.parse(src)

    # Find the `if use_meeting_data:` branch that owns the JSONB write.
    # We want the LAST one (the JSONB-write block), since the function
    # also has a `if use_meeting_data:` near the top for storage_path
    # derivation that's structurally distinct.
    target_if_node = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "use_meeting_data"
            # The JSONB-write branch contains a `with_for_update` call;
            # the storage_path branch does not.
            and any(
                "with_for_update" in ast.dump(child)
                for child in ast.walk(node)
            )
        ):
            target_if_node = node
            break

    assert target_if_node is not None, (
        "could not locate the `if use_meeting_data:` block that contains "
        "the with_for_update() call. The function shape has changed; "
        "review services/meeting-api/meeting_api/recordings.py and update "
        "this test."
    )

    # Walk the body in source order. Find the first `with_for_update`
    # node and the first `meeting.data` access node. Lock must come
    # first.
    first_lock_lineno: int | None = None
    first_data_access_lineno: int | None = None

    for child in ast.walk(target_if_node):
        # Find with_for_update() method calls
        if (
            first_lock_lineno is None
            and isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "with_for_update"
        ):
            first_lock_lineno = child.lineno
        # Find meeting.data attribute reads (ast.Attribute on Name=meeting)
        # We're looking for the MeetingDataDict re-derivation: dict(meeting.data or {})
        if (
            first_data_access_lineno is None
            and isinstance(child, ast.Attribute)
            and child.attr == "data"
            and isinstance(child.value, ast.Name)
            and child.value.id == "meeting"
        ):
            first_data_access_lineno = child.lineno

    assert first_lock_lineno is not None, (
        "no with_for_update() call found inside the meeting_data branch — "
        "Pack E.1.a v2 contract violated."
    )
    assert first_data_access_lineno is not None, (
        "no meeting.data access found inside the meeting_data branch — "
        "function shape unexpected; review test."
    )
    assert first_lock_lineno < first_data_access_lineno, (
        f"REGRESSION (Pack E.1.a v2): SELECT FOR UPDATE at line "
        f"{first_lock_lineno} must come BEFORE meeting.data access at "
        f"line {first_data_access_lineno} inside the meeting_data write "
        f"branch. The current shape has the lock AFTER the snapshot, "
        f"reintroducing the race [PLATFORM] flagged in #272 "
        f"(issuecomment-4327366063)."
    )


# ───────────────────────────────────────────────────────────────────────
# Test 2 — sanity: two sequential uploads (audio + video) both persist.
#
# This exercises the real endpoint with a stateful mock DB that
# preserves meeting.data across calls. Sequential uploads have no race;
# this is the "v1 + v2 both pass" foundation. If this fails, the v2 fix
# broke the basic invariant that media_files holds one entry per type.
# ───────────────────────────────────────────────────────────────────────


class _StatefulMockDB:
    """Minimal AsyncSession-shaped mock that persists ``meeting.data``
    across execute/commit cycles, so sequential AND concurrent tests can
    inspect the final state.

    Exposes ``shared_meeting`` — the same MagicMock(spec=Meeting) is
    returned by both ``db.get(Meeting, ...)`` and the
    ``select(Meeting)…with_for_update`` execute path. This mirrors real
    SQLAlchemy session behavior where both surfaces hand back the same
    identity-mapped row.

    Concurrency model: a per-meeting ``asyncio.Lock`` simulates the
    Postgres row lock acquired by SELECT FOR UPDATE. Coroutines that
    call the with_for_update path acquire the lock; subsequent
    concurrent callers block until the first one commits.
    """

    def __init__(self, session: MeetingSession, meeting: Meeting):
        self._session = session
        self.shared_meeting = meeting
        self._row_lock = asyncio.Lock()
        # Per-meeting "lock currently held" flag — set when a coroutine
        # holds the row lock (via with_for_update). Cleared on commit.
        # This is what lets the test simulate "B blocks until A commits".
        self._locked_by: object | None = None

        # Counters for assertions in tests
        self.with_for_update_calls = 0
        self.commit_calls = 0

        # Mock methods
        self.execute = AsyncMock(side_effect=self._execute)
        self.get = AsyncMock(side_effect=self._get)
        self.commit = AsyncMock(side_effect=self._commit)
        self.add = MagicMock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.rollback = AsyncMock()
        self.close = AsyncMock()

    async def _get(self, model, pk):
        # First-phase load (no lock).
        if model is Meeting:
            return self.shared_meeting
        return None

    async def _execute(self, stmt):
        # Inspect stmt to decide which path to simulate.
        stmt_str = str(stmt)
        if "FOR UPDATE" in stmt_str.upper() or "with_for_update" in stmt_str:
            # Lock acquisition. Real Postgres serializes here; we
            # simulate by acquiring an asyncio.Lock. The lock is held
            # until commit() is called by this coroutine.
            await self._row_lock.acquire()
            self._locked_by = asyncio.current_task()
            self.with_for_update_calls += 1
            return MockResult([self.shared_meeting])
        if "MeetingSession" in stmt_str or "meeting_session" in stmt_str:
            return MockResult([self._session])
        # Default: empty result
        return MockResult([])

    async def _commit(self):
        self.commit_calls += 1
        # Release the row lock if this coroutine holds it.
        if self._locked_by is asyncio.current_task() and self._row_lock.locked():
            self._locked_by = None
            self._row_lock.release()


def _make_upload_call(media_type: str, media_format: str = "wav"):
    """Build the kwargs the endpoint expects for an internal upload.

    Real endpoint takes a multipart form via FastAPI; here we call the
    underlying coroutine directly with explicit args, bypassing
    FastAPI's request parsing. The function under test is
    ``internal_upload_recording``.
    """
    fake_file = MagicMock()
    fake_file.read = AsyncMock(return_value=b"x" * 1024)  # 1KB dummy
    return dict(
        file=fake_file,
        metadata=None,
        session_uid=TEST_SESSION_UID,
        media_type=media_type,
        media_format=media_format,
        duration_seconds=10.0,
        sample_rate=48000 if media_type == "audio" else None,
        is_final=True,
        chunk_seq=0,
    )


@pytest.mark.asyncio
async def test_sequential_audio_then_video_both_persist():
    """Two sequential uploads on the same recording: both survive."""
    meeting = make_meeting(data={})
    session = make_session()
    mock_db = _StatefulMockDB(session=session, meeting=meeting)

    fake_storage = MagicMock()
    fake_storage.upload_file = MagicMock(return_value=None)

    with patch.object(recordings_module, "get_storage_client", return_value=fake_storage), \
         patch.object(recordings_module, "get_recording_metadata_mode", return_value="meeting_data"), \
         patch.object(recordings_module.attributes, "flag_modified", new=MagicMock()):
        # First upload: audio
        await recordings_module.internal_upload_recording(
            db=mock_db,
            **_make_upload_call("audio", "wav"),
        )
        # Second upload: video
        await recordings_module.internal_upload_recording(
            db=mock_db,
            **_make_upload_call("video", "webm"),
        )

    recs = (meeting.data or {}).get("recordings") or []
    assert len(recs) == 1, f"expected 1 recording row, got {len(recs)}: {recs}"
    media_files = recs[0].get("media_files") or []
    types = sorted(mf.get("type") for mf in media_files)
    assert types == ["audio", "video"], (
        f"expected media_files types == ['audio', 'video'] after sequential "
        f"audio + video uploads, got {types}. Sequential case has no race; "
        f"this is the foundational sanity check on the meeting_data write "
        f"shape — failure means the v2 lock-before-snapshot move broke the "
        f"basic per-type-entry invariant."
    )
    # The lock should have been acquired twice (once per upload).
    assert mock_db.with_for_update_calls == 2
    assert mock_db.commit_calls == 2


# ───────────────────────────────────────────────────────────────────────
# Test 3 — concurrent: two coroutines, audio + video, asyncio.gather.
#
# The simulated row lock serializes the JSONB writes; the v2 code re-
# reads meeting.data under the lock, so coroutine B's snapshot sees
# coroutine A's committed audio entry. Both entries survive.
#
# With the v1 (pre-fix) code, this test would fail — coroutine B's
# pre-lock snapshot would not see A's commit, and B's append would
# overwrite A's audio entry.
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_audio_video_no_lost_entry():
    """Concurrent audio + video upload to same recording → both survive."""
    meeting = make_meeting(data={})
    session = make_session()
    mock_db = _StatefulMockDB(session=session, meeting=meeting)

    fake_storage = MagicMock()
    fake_storage.upload_file = MagicMock(return_value=None)

    with patch.object(recordings_module, "get_storage_client", return_value=fake_storage), \
         patch.object(recordings_module, "get_recording_metadata_mode", return_value="meeting_data"), \
         patch.object(recordings_module.attributes, "flag_modified", new=MagicMock()):
        # Fire both concurrently — the real prod shape that triggered
        # the original race report.
        await asyncio.gather(
            recordings_module.internal_upload_recording(
                db=mock_db,
                **_make_upload_call("audio", "wav"),
            ),
            recordings_module.internal_upload_recording(
                db=mock_db,
                **_make_upload_call("video", "webm"),
            ),
        )

    recs = (meeting.data or {}).get("recordings") or []
    assert len(recs) == 1, f"expected 1 recording row, got {len(recs)}: {recs}"
    media_files = recs[0].get("media_files") or []
    types = sorted(mf.get("type") for mf in media_files)
    assert types == ["audio", "video"], (
        f"REGRESSION (Pack E.1.a v2 race): expected media_files types == "
        f"['audio', 'video'] after concurrent audio + video upload, got "
        f"{types}. This is the [PLATFORM] reproducer shape from #272 "
        f"issuecomment-4327366063 — both coroutines uploaded distinct "
        f"media_types but only one entry survived, meaning the lock-before-"
        f"snapshot invariant has broken. Re-check "
        f"recordings.py::internal_upload_recording — the SELECT FOR UPDATE "
        f"must precede the meeting.data snapshot inside the meeting_data "
        f"branch."
    )

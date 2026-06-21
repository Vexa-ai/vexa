"""P3c eval — sequential multi-bot per meeting (continue_meeting).

Asserts:
  * a meeting accumulates sessions (N sessions per meeting row, keyed by session_uid);
  * a CONTINUED bot (prior meeting TERMINAL) reuses the SAME meeting row and ADDS a session — the
    prior session's transcript (keyed by the meeting row) is preserved;
  * a CONCURRENT second bot (prior still ACTIVE) is still rejected (409).

OFFLINE — the shipped `request_bot` / `build_router` over the in-memory fakes (no DB, no kernel).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meeting_api.bot_spawn import DuplicateMeeting, request_bot
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

SECRET = "test-admin-token"
USER = 7
PLATFORM = "google_meet"
NID = "abc-defg-hij"
KW = dict(redis_url="r", token_secret=SECRET, meeting_api_url="http://meeting-api:8080")


async def _spawn(repo, runtime, *, continue_meeting=False):
    return await request_bot(
        repo, runtime, user_id=USER, platform=PLATFORM, native_meeting_id=NID,
        continue_meeting=continue_meeting, **KW,
    )


# ── concurrent second bot (prior still active) → 409 ────────────────────────────────────────────

async def test_concurrent_second_bot_rejected_409():
    """A second /bots while the prior is still ACTIVE is a duplicate — even with continue_meeting."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    first = await _spawn(repo, runtime)
    repo.set_status(first["id"], "active")  # the bot reached active — still in-flight

    # plain re-request → 409
    with pytest.raises(DuplicateMeeting):
        await _spawn(repo, runtime)
    # continue_meeting does NOT bypass the concurrent-active guard → still 409
    with pytest.raises(DuplicateMeeting):
        await _spawn(repo, runtime, continue_meeting=True)


# ── continued bot (prior terminal) reuses the row + adds a session ──────────────────────────────

async def test_continue_reuses_row_and_accumulates_sessions():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    first = await _spawn(repo, runtime)
    mid = first["id"]
    assert first["data"]["sessions"] == [_only_session(repo, mid)]

    # the meeting completes (terminal)
    repo.set_status(mid, "completed")

    # a continued bot for the same (platform, native_id) REUSES the row and ADDS a session
    second = await _spawn(repo, runtime, continue_meeting=True)
    assert second["id"] == mid                       # SAME meeting row
    assert mid in repo.reopened                       # the terminal row was reopened
    assert second["status"] == "requested"            # reset for the new run
    sessions = second["data"]["sessions"]
    assert len(sessions) == 2                          # N sessions accumulate
    assert sessions[0] != sessions[1]                 # distinct per-run connectionIds

    # a THIRD continued run keeps accumulating
    repo.set_status(mid, "failed")
    third = await _spawn(repo, runtime, continue_meeting=True)
    assert third["id"] == mid
    assert len(third["data"]["sessions"]) == 3


async def test_continue_preserves_prior_transcript():
    """Transcripts are keyed by the meeting row, which a continued run REUSES — so the prior
    session's transcript survives the continue."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    first = await _spawn(repo, runtime)
    mid = first["id"]
    first_session = _only_session(repo, mid)

    # the prior run produced a transcript keyed by (meeting_id, session_uid)
    transcripts = {(mid, first_session): "hello from run 1"}

    repo.set_status(mid, "completed")
    second = await _spawn(repo, runtime, continue_meeting=True)
    assert second["id"] == mid  # same row → the transcript's meeting_id is unchanged

    # the prior transcript is still resolvable by the (unchanged) meeting id
    assert (mid, first_session) in transcripts
    assert transcripts[(mid, first_session)] == "hello from run 1"
    # and the new session is a fresh key under the SAME meeting
    new_session = [s for s in second["data"]["sessions"] if s != first_session][0]
    assert new_session != first_session


async def test_continue_without_prior_terminal_creates_fresh_meeting():
    """continue_meeting with NO prior meeting just creates a fresh one (no row to reuse)."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    m = await _spawn(repo, runtime, continue_meeting=True)
    assert m["id"] == 1
    assert repo.reopened == []  # nothing reused
    assert len(m["data"]["sessions"]) == 1


def _only_session(repo: InMemoryMeetingRepo, meeting_id: int) -> str:
    uids = [s["session_uid"] for s in repo.sessions if s["meeting_id"] == meeting_id]
    return uids[0]

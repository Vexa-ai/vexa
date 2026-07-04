"""P3e eval — max-bots enforcement (per-user concurrency).

Asserts:
  * at-limit → 429 (meeting-api's OWN pre-check, BEFORE the runtime call);
  * under-limit → allowed;
  * a freed slot (a bot/session goes terminal) → allowed again;
  * a `continue_meeting` session counts against the same cap (and never exceeds it);
  * infra `browser_session` workloads are EXCLUDED from the count (parent meetings.py:1091);
  * the runtime kernel's QuotaExceeded remains the defense-in-depth 429 backstop.

OFFLINE — the shipped `request_bot` / `build_router` over the in-memory fakes (no DB, no kernel).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meeting_api.bot_spawn import (
    MaxBotsExceeded,
    QuotaExceeded,
    build_router,
    request_bot,
)
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

SECRET = "test-admin-token"
USER = 7
KW = dict(redis_url="r", token_secret=SECRET, meeting_api_url="http://meeting-api:8080")


async def _spawn(repo, runtime, nid, *, cap=None, continue_meeting=False):
    return await request_bot(
        repo, runtime, user_id=USER, platform="google_meet", native_meeting_id=nid,
        max_concurrent=cap, continue_meeting=continue_meeting, **KW,
    )


# ── service-level: at-limit / under-limit / freed slot ──────────────────────────────────────────

async def test_under_limit_allows_then_at_limit_rejects():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    cap = 2
    m1 = await _spawn(repo, runtime, "m-1", cap=cap)
    repo.set_status(m1["id"], "active")
    m2 = await _spawn(repo, runtime, "m-2", cap=cap)  # 2nd allowed (under limit)
    repo.set_status(m2["id"], "active")

    # the 3rd is the N+1th ACTIVE bot → rejected, BEFORE the runtime call
    spec_count_before = len(runtime.specs)
    with pytest.raises(MaxBotsExceeded) as exc:
        await _spawn(repo, runtime, "m-3", cap=cap)
    assert exc.value.cap == cap
    assert len(runtime.specs) == spec_count_before  # no spawn attempted (pre-check)


async def test_freed_slot_allows_again():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    cap = 1
    m1 = await _spawn(repo, runtime, "m-1", cap=cap)
    repo.set_status(m1["id"], "active")

    with pytest.raises(MaxBotsExceeded):
        await _spawn(repo, runtime, "m-2", cap=cap)

    # free the slot — the first session goes terminal
    repo.set_status(m1["id"], "completed")
    m2 = await _spawn(repo, runtime, "m-2", cap=cap)  # now allowed again
    assert m2["status"] == "requested"


async def test_continue_meeting_session_counts_against_cap():
    """A continued run reuses a TERMINAL row (not counted), so it is allowed at the cap; but a
    SECOND distinct active bot still trips the cap."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    cap = 1
    m1 = await _spawn(repo, runtime, "m-1", cap=cap)
    repo.set_status(m1["id"], "completed")  # terminal

    # continue the (terminal) meeting — allowed: the reused row is excluded from the active count
    cont = await _spawn(repo, runtime, "m-1", cap=cap, continue_meeting=True)
    assert cont["id"] == m1["id"]
    repo.set_status(cont["id"], "active")  # the continued bot is now the 1 active

    # a DIFFERENT meeting now would be the 2nd active → rejected at cap=1
    with pytest.raises(MaxBotsExceeded):
        await _spawn(repo, runtime, "m-2", cap=cap)


async def test_browser_session_excluded_from_count():
    """Infra browser_session workloads do NOT count against the bot cap (parent meetings.py:1091).

    A browser_session is infra, created via a different (non-meeting) path; we seed an ACTIVE
    browser_session row directly, then assert it does not consume a meeting-bot slot.
    """
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    bs = await repo.create_meeting(
        user_id=USER, platform="browser_session", native_meeting_id="bs-1", data={},
    )
    repo.set_status(bs["id"], "active")
    # the active-bot count for the user excludes the browser_session
    assert await repo.count_active_bots(user_id=USER) == 0
    # a real meeting bot is still allowed at cap=1 — the browser_session didn't consume the slot
    m = await _spawn(repo, runtime, "m-1", cap=1)
    assert m["status"] == "requested"


async def test_no_cap_means_no_precheck():
    """max_concurrent=None (no gateway limit header) → no pre-check. Only ABSENCE skips the gate —
    cap=0 is a real value (depleted, see the #456-parity tests below), never "no limit"."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    for i in range(5):
        m = await _spawn(repo, runtime, f"m-{i}", cap=None)
        repo.set_status(m["id"], "active")  # all stay active
    # never raised — no cap, no pre-check


# ── Vexa-ai/vexa#456 parity: cap=0 means DEPLETED (no bots), never unlimited ────────────────────

async def test_cap_zero_rejects_first_spawn():
    """cap=0 → even the FIRST spawn is rejected, before the runtime call and with nothing inserted.

    Regression for the `max_concurrent > 0` guard skipping the check entirely at cap 0, which made
    the most-restricted users unlimited (quota/billing bypass — Vexa-ai/vexa#456)."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    with pytest.raises(MaxBotsExceeded) as exc:
        await _spawn(repo, runtime, "m-1", cap=0)
    assert exc.value.cap == 0
    assert len(runtime.specs) == 0  # rejected BEFORE the runtime call
    assert await repo.count_active_bots(user_id=USER) == 0  # nothing inserted


async def test_negative_cap_also_depleted():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    with pytest.raises(MaxBotsExceeded):
        await _spawn(repo, runtime, "m-1", cap=-1)


async def test_cap_zero_rejects_continue_meeting():
    """The reused-terminal-row path has its own cap gate — cap=0 must reject the continue too
    (a continued run is a new ACTIVE bot)."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    m1 = await _spawn(repo, runtime, "m-1", cap=1)
    repo.set_status(m1["id"], "completed")  # terminal → eligible for continue_meeting
    with pytest.raises(MaxBotsExceeded):
        await _spawn(repo, runtime, "m-1", cap=0, continue_meeting=True)


# ── defense-in-depth: the runtime kernel's QuotaExceeded is the backstop ─────────────────────────

async def test_runtime_quota_is_defense_in_depth_backstop():
    """Even with no meeting-api pre-check (cap=None), the kernel's QuotaExceeded still surfaces 429.

    This is the backstop: if the per-user pre-check is bypassed, the runtime's owner_quota catches
    it (the RuntimeClient raises QuotaExceeded → 429)."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient(quota_exceeded=True)
    with pytest.raises(QuotaExceeded):
        await _spawn(repo, runtime, "m-1", cap=None)


# ── route-level: both pre-check and backstop map to 429 ─────────────────────────────────────────

def _client(repo, runtime):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(build_router(repo, runtime))
    return TestClient(app)


def test_route_429_on_max_bots_precheck(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    headers = {"x-user-id": str(USER), "x-user-limits": "1"}
    r1 = client.post("/bots", headers=headers, json={"platform": "google_meet", "native_meeting_id": "m-1"})
    assert r1.status_code == 201, r1.text
    repo.set_status(r1.json()["id"], "active")
    # at cap=1 → the 2nd is rejected with 429 by the pre-check
    r2 = client.post("/bots", headers=headers, json={"platform": "google_meet", "native_meeting_id": "m-2"})
    assert r2.status_code == 429, r2.text


def test_route_429_on_runtime_backstop(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient(quota_exceeded=True)
    client = _client(repo, runtime)
    # no x-user-limits header → no pre-check; the kernel backstop still yields 429
    r = client.post("/bots", headers={"x-user-id": str(USER)},
                    json={"platform": "google_meet", "native_meeting_id": "m-1"})
    assert r.status_code == 429, r.text


def test_route_429_on_cap_zero_and_cap_one_still_admits_one(monkeypatch):
    """x-user-limits: 0 → the FIRST POST /bots is 429 (was 201/unlimited — the #456 bug); and the
    fix must not over-reject: limits=1 still admits exactly one, then 429s the second."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    depleted = {"x-user-id": str(USER), "x-user-limits": "0"}
    r = client.post("/bots", headers=depleted, json={"platform": "google_meet", "native_meeting_id": "m-0"})
    assert r.status_code == 429, r.text
    assert len(runtime.specs) == 0  # no spawn reached the runtime

    cap_one = {"x-user-id": str(USER), "x-user-limits": "1"}
    r1 = client.post("/bots", headers=cap_one, json={"platform": "google_meet", "native_meeting_id": "m-1"})
    assert r1.status_code == 201, r1.text
    repo.set_status(r1.json()["id"], "active")
    r2 = client.post("/bots", headers=cap_one, json={"platform": "google_meet", "native_meeting_id": "m-2"})
    assert r2.status_code == 429, r2.text


def test_route_429_on_cap_zero_json_form(monkeypatch):
    """The JSON header form must not lose 0 to falsiness: {"max_concurrent_bots": 0} → 429."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    headers = {"x-user-id": str(USER), "x-user-limits": '{"max_concurrent_bots": 0}'}
    r = client.post("/bots", headers=headers, json={"platform": "google_meet", "native_meeting_id": "m-1"})
    assert r.status_code == 429, r.text


def test_route_limits_header_parsed_as_json(monkeypatch):
    """The gateway may forward X-User-Limits as a JSON object — the route parses both forms."""
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    headers = {"x-user-id": str(USER), "x-user-limits": '{"max_concurrent_bots": 1}'}
    r1 = client.post("/bots", headers=headers, json={"platform": "google_meet", "native_meeting_id": "m-1"})
    assert r1.status_code == 201
    repo.set_status(r1.json()["id"], "active")
    r2 = client.post("/bots", headers=headers, json={"platform": "google_meet", "native_meeting_id": "m-2"})
    assert r2.status_code == 429

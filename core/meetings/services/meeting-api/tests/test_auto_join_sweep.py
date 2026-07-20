"""auto-join sweep — a `scheduled` meeting's bot joins at start time, loudly or not at all.

One ``auto_join_tick`` spawns every due scheduled row through the SAME ``request_bot`` flow
POST /bots runs (the claim branch upgrades the row in place → idempotent), skips off-toggle /
link-less / stale rows, stamps ``data.auto_join_error`` (+ retry backoff) on cap rejection or
spawn failure, and treats a concurrent manual spawn (DuplicateMeeting) as success.

Drives the SHIPPED ``auto_join_tick`` over the in-memory fakes, OFFLINE.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meeting_api.bot_spawn.auto_join import auto_join_tick, due_rows
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

USER = 7
PLAT, NID = "google_meet", "abc-defg-hij"
NOW = datetime(2026, 7, 10, 15, 0, 0, tzinfo=timezone.utc)

_NO_GATE = lambda: None  # noqa: E731 — tests bypass the STT capability gate


def _seed(repo, *, mid=1, status="scheduled", at=NOW, native=NID, platform=PLAT,
          data_extra=None, user_id=USER):
    data = {"title": "t", "auto_join": True}
    if at is not None:
        data["scheduled_at"] = at.isoformat()
    data.update(data_extra or {})
    repo._meetings[mid] = {
        "id": mid, "user_id": user_id, "platform": platform,
        "native_meeting_id": native, "platform_specific_id": native,
        "status": status, "bot_container_id": None, "start_time": None, "end_time": None,
        "data": data, "created_at": "2026-07-08T09:00:00Z", "updated_at": "2026-07-08T09:00:00Z",
    }
    return mid


async def _tick(repo, runtime, **kw):
    kw.setdefault("transcribe_gate", _NO_GATE)
    kw.setdefault("now", NOW)
    kw.setdefault("token_secret", "s")
    kw.setdefault("redis_url", "redis://r")
    # Legacy spawn-mechanics tests don't wire an admin edge; opt them into uncapped spawns so they
    # exercise the spawn path. The #656 fail-closed tests pass allow_uncapped=False explicitly.
    kw.setdefault("allow_uncapped", True)
    return await auto_join_tick(repo, runtime, **kw)


# ---- fires at lead time -------------------------------------------------------------

async def test_due_row_spawns_and_claims_in_place():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo, at=NOW + timedelta(seconds=30))  # inside the 60s lead window
    counters = await _tick(repo, runtime)
    assert counters == {"due": 1, "spawned": 1, "already": 0, "errors": 0, "skipped_uncapped": 0}
    row = repo._meetings[mid]
    assert row["status"] == "requested"          # the SAME row was claimed
    assert row["data"]["title"] == "t"           # planned keys survive
    assert len(runtime.specs) == 1


async def test_not_yet_due_row_waits():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, at=NOW + timedelta(seconds=300))  # 5 min out, lead is 60s
    counters = await _tick(repo, runtime)
    assert counters["due"] == 0 and runtime.specs == []


async def test_stale_row_skipped_never_joins_hours_late():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, at=NOW - timedelta(hours=2))  # long past the 600s grace
    counters = await _tick(repo, runtime)
    assert counters["due"] == 0 and runtime.specs == []


async def test_auto_join_off_skipped():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, data_extra={"auto_join": False})
    counters = await _tick(repo, runtime)
    assert counters["due"] == 0 and runtime.specs == []


async def test_auto_join_absent_defaults_on():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo)
    del repo._meetings[mid]["data"]["auto_join"]
    counters = await _tick(repo, runtime)
    assert counters["spawned"] == 1


async def test_linkless_row_skipped():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, native=None, platform="unknown")
    counters = await _tick(repo, runtime)
    assert counters["due"] == 0


# ---- idempotency --------------------------------------------------------------------

async def test_second_tick_is_a_noop():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo)
    await _tick(repo, runtime)
    counters = await _tick(repo, runtime)
    assert counters == {"due": 0, "spawned": 0, "already": 0, "errors": 0, "skipped_uncapped": 0}
    assert len(runtime.specs) == 1  # exactly one spawn ever


async def test_manual_spawn_race_counts_as_already():
    """A row still `scheduled` in the sweep's snapshot but claimed by a manual spawn before the
    sweep's request_bot lands → DuplicateMeeting → success, no error stamp."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo)
    snapshot = [dict(repo._meetings[mid], data=dict(repo._meetings[mid]["data"]))]
    # manual spawn claims it first
    from meeting_api.bot_spawn import request_bot
    await request_bot(repo, runtime, user_id=USER, platform=PLAT, native_meeting_id=NID,
                      redis_url="redis://r", token_secret="s")

    class _FrozenRepo:
        """Delegates to the real repo but serves the STALE scheduled snapshot."""
        def __getattr__(self, name):
            return getattr(repo, name)
        async def list_scheduled_meetings(self):
            return snapshot

    counters = await _tick(_FrozenRepo(), runtime)
    assert counters["already"] == 1 and counters["errors"] == 0
    assert "auto_join_error" not in repo._meetings[mid]["data"]


# ---- loud failures ------------------------------------------------------------------

async def test_cap_rejection_stamps_error_and_backoff():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    # cap fully consumed by another running meeting
    from meeting_api.bot_spawn import request_bot
    await request_bot(repo, runtime, user_id=USER, platform=PLAT,
                      native_meeting_id="yyy-yyyy-yyy", redis_url="redis://r", token_secret="s")
    mid = _seed(repo, mid=50)

    published = []
    async def publish_status(**kw):
        published.append(kw)

    async def ctx(_uid):
        return {"max_concurrent": 1}

    counters = await _tick(repo, runtime, fetch_bot_context=ctx, publish_status=publish_status)
    assert counters["errors"] == 1
    data = repo._meetings[mid]["data"]
    assert "auto_join_error" in data and "auto_join_next_retry" in data
    assert repo._meetings[mid]["status"] == "scheduled"  # NOT silently consumed
    assert published and published[0]["meeting_id"] == mid  # loud in the UI


async def test_error_backoff_suppresses_retry_until_due():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, data_extra={"auto_join_next_retry": (NOW + timedelta(seconds=200)).isoformat()})
    counters = await _tick(repo, runtime)
    assert counters["due"] == 0
    # …and once the backoff expires the row is due again
    counters = await _tick(repo, runtime, now=NOW + timedelta(seconds=201))
    assert counters["due"] == 1


async def test_spawn_success_clears_stale_error_stamp():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo, data_extra={"auto_join_error": "old",
                                  "auto_join_next_retry": (NOW - timedelta(seconds=1)).isoformat()})
    counters = await _tick(repo, runtime)
    assert counters["spawned"] == 1
    assert "auto_join_error" not in repo._meetings[mid]["data"]


async def test_stt_gate_failure_is_loud():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo)
    counters = await _tick(repo, runtime, transcribe_gate=lambda: "STT not configured")
    assert counters["errors"] == 1
    assert "STT" in repo._meetings[mid]["data"]["auto_join_error"]
    assert runtime.specs == []


async def test_unsafe_stored_url_is_stamped_without_aborting_sweep():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(
        repo,
        data_extra={
            "constructed_meeting_url": (
                "https://meet.google.com.attacker.example/abc-defg-hij"
            )
        },
    )

    counters = await _tick(repo, runtime)

    assert counters == {"due": 1, "spawned": 0, "already": 0, "errors": 1, "skipped_uncapped": 0}
    assert repo._meetings[mid]["status"] == "scheduled"
    assert "auto_join_error" in repo._meetings[mid]["data"]
    assert runtime.specs == []


# ---- identity-context tri-state ------------------------------------------------------

async def test_unreachable_identity_skips_fail_closed():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo)

    async def ctx(_uid):
        return None  # configured but unreachable

    counters = await _tick(repo, runtime, fetch_bot_context=ctx)
    assert counters == {"due": 1, "spawned": 0, "already": 0, "errors": 0, "skipped_uncapped": 0}
    assert runtime.specs == []  # never spawns past a cap it could not read


async def test_no_admin_edge_fails_closed_refuses_uncapped_spawn():
    # #656 C2: no admin edge configured → the per-user cap is UNRESOLVABLE. Fail closed:
    # refuse to spawn rather than spawn uncapped (default, no opt-in).
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo)
    counters = await _tick(repo, runtime, fetch_bot_context=None, allow_uncapped=False)
    assert counters == {"due": 1, "spawned": 0, "already": 0, "errors": 0, "skipped_uncapped": 1}
    assert runtime.specs == []  # never spawns uncapped past a cap we cannot resolve


async def test_no_admin_edge_with_explicit_opt_in_spawns_uncapped():
    # AUTO_JOIN_ALLOW_UNCAPPED=1 → the deliberate self-host uncapped mode is chosen, not defaulted.
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo)
    counters = await _tick(repo, runtime, fetch_bot_context=None, allow_uncapped=True)
    assert counters["spawned"] == 1
    assert len(runtime.specs) == 1


# ---- pure filter unit ----------------------------------------------------------------

def test_due_rows_window_edges():
    def row(at, **extra):
        return {"id": 1, "user_id": USER, "platform": PLAT, "native_meeting_id": NID,
                "data": {"scheduled_at": at.isoformat(), **extra}}
    lead, grace = 60, 600
    inside_lead = NOW + timedelta(seconds=59)
    outside_lead = NOW + timedelta(seconds=61)
    inside_grace = NOW - timedelta(seconds=599)
    outside_grace = NOW - timedelta(seconds=601)
    assert due_rows([row(inside_lead)], now=NOW, lead_s=lead, grace_s=grace)
    assert not due_rows([row(outside_lead)], now=NOW, lead_s=lead, grace_s=grace)
    assert due_rows([row(inside_grace)], now=NOW, lead_s=lead, grace_s=grace)
    assert not due_rows([row(outside_grace)], now=NOW, lead_s=lead, grace_s=grace)
    # malformed / missing time → never due
    assert not due_rows([{"id": 2, "user_id": USER, "platform": PLAT,
                          "native_meeting_id": NID, "data": {}}], now=NOW)

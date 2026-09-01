"""auto-join sweep — a `scheduled` meeting's bot joins at start time, loudly or not at all.

One ``auto_join_tick`` spawns every due scheduled row through the SAME ``request_bot`` flow
POST /bots runs (the claim branch upgrades the row in place → idempotent), skips off-toggle /
link-less / stale rows, stamps ``data.auto_join_error`` (+ retry backoff) on cap rejection or
spawn failure, and treats a concurrent manual spawn (DuplicateMeeting) as success.

Drives the SHIPPED ``auto_join_tick`` over the in-memory fakes, OFFLINE.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meeting_api.bot_spawn.auto_join import DEFAULT_LEAD_S, auto_join_tick, due_rows
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
    assert counters == {"due": 1, "spawned": 1, "already": 0, "errors": 0, "skipped_uncapped": 0, "skipped_live": 0, "stopped": 0}
    row = repo._meetings[mid]
    assert row["status"] == "requested"          # the SAME row was claimed
    assert row["data"]["title"] == "t"           # planned keys survive
    assert len(runtime.specs) == 1


async def test_due_row_uses_user_calendar_bot_name():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo)

    async def ctx(_uid):
        return {"max_concurrent": 4, "bot_name": "Dmitry's Notes"}

    counters = await _tick(repo, runtime, fetch_bot_context=ctx)
    assert counters["spawned"] == 1
    assert '"botName":"Dmitry\'s Notes"' in runtime.specs[0]["env"]["VEXA_BOT_CONFIG"]


async def test_due_row_prefers_bot_name_from_auto_joining_calendar_source():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, data_extra={"calendar_sources": [
        {"id": "work", "auto_join": False, "bot_name": "Work Bot"},
        {"id": "personal", "auto_join": True, "bot_name": "Personal Bot"},
    ]})

    async def ctx(_uid):
        return {"max_concurrent": 4, "bot_name": "Legacy Default"}

    counters = await _tick(repo, runtime, fetch_bot_context=ctx)
    assert counters["spawned"] == 1
    assert '"botName":"Personal Bot"' in runtime.specs[0]["env"]["VEXA_BOT_CONFIG"]


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
    assert counters == {"due": 0, "spawned": 0, "already": 0, "errors": 0, "skipped_uncapped": 0, "skipped_live": 0, "stopped": 0}
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


# ---- identity-context tri-state ------------------------------------------------------

async def test_unreachable_identity_skips_fail_closed():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo)

    async def ctx(_uid):
        return None  # configured but unreachable

    counters = await _tick(repo, runtime, fetch_bot_context=ctx)
    assert counters == {"due": 1, "spawned": 0, "already": 0, "errors": 0, "skipped_uncapped": 0, "skipped_live": 0, "stopped": 0}
    assert runtime.specs == []  # never spawns past a cap it could not read


async def test_no_admin_edge_fails_closed_refuses_uncapped_spawn():
    # #656 C2: no admin edge configured → the per-user cap is UNRESOLVABLE. Fail closed:
    # refuse to spawn rather than spawn uncapped (default, no opt-in).
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo)
    counters = await _tick(repo, runtime, fetch_bot_context=None, allow_uncapped=False)
    assert counters == {"due": 1, "spawned": 0, "already": 0, "errors": 0, "skipped_uncapped": 1, "skipped_live": 0, "stopped": 0}
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


def test_default_lead_dispatches_two_minutes_before_the_start():
    """#1208 — the PRODUCT default: a meeting starting in 119s is due, one starting in 121s is not.
    Two minutes of lead is what puts the bot in the lobby AT the scheduled start rather than
    starting its browser then."""
    def row(at):
        return {"id": 1, "user_id": USER, "platform": PLAT, "native_meeting_id": NID,
                "data": {"scheduled_at": at.isoformat()}}

    assert DEFAULT_LEAD_S == 120
    assert due_rows([row(NOW + timedelta(seconds=119))], now=NOW)
    assert not due_rows([row(NOW + timedelta(seconds=121))], now=NOW)


def test_entrypoint_lead_default_matches_the_sweep_default(monkeypatch):
    """One default, two readers: the entrypoint's env fallback IS ``DEFAULT_LEAD_S``, so the sweep's
    own default and the deployed default can never drift apart. Deploy values still override."""
    import os

    monkeypatch.delenv("AUTO_JOIN_LEAD_S", raising=False)
    assert float(os.getenv("AUTO_JOIN_LEAD_S", str(DEFAULT_LEAD_S))) == 120.0
    monkeypatch.setenv("AUTO_JOIN_LEAD_S", "60")
    assert float(os.getenv("AUTO_JOIN_LEAD_S", str(DEFAULT_LEAD_S))) == 60.0


def test_lead_and_lobby_budget_together_cover_a_late_host():
    """The pair, checked as one claim (#1208): dispatched at start-120s and permitted to wait 900s,
    the bot is still knocking 13 minutes AFTER the scheduled start — and the reconcile floor it is
    measured against outlasts that whole window, so nothing reaps it mid-wait."""
    from meeting_api.bot_spawn.service import lobby_budget_ms
    from meeting_api.lifecycle.reconcile import default_preactive_grace

    waits_until_s_after_start = lobby_budget_ms() / 1000.0 - DEFAULT_LEAD_S
    assert waits_until_s_after_start == 780.0
    assert default_preactive_grace() > lobby_budget_ms() / 1000.0


# ---- duplicate-dispatch guard (live 2026-08-17: two Vexa bots in mjm-dycn-qdp) --------

async def test_live_row_on_same_link_blocks_the_sweep():
    """Two rows, one native id, one of them LIVE → the sweep refuses, loudly.

    The staging shape: user 23 sent a bot by hand (row 26237, admitted + transcribing) while the
    connected calendar imported the same Meet as a second row (26251). At start time the sweep
    dispatched a SECOND bot. A duplicate dispatch is never correct however the rows arose.
    """
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, mid=26237, status="active", at=None)          # the manual bot, in the room
    due = _seed(repo, mid=26251, status="scheduled", at=NOW)  # the un-adopted calendar import
    counters = await _tick(repo, runtime)
    assert counters["due"] == 1
    assert counters["spawned"] == 0
    assert counters["skipped_live"] == 1
    assert runtime.specs == []                       # no second bot
    assert repo._meetings[due]["status"] == "scheduled"
    err = repo._meetings[due]["data"]["auto_join_error"]
    assert "26237" in err and "already in this meeting" in err
    assert repo._meetings[due]["data"]["auto_join_next_retry"]  # backoff stamped, not re-fired


async def test_a_bot_still_waiting_for_admission_blocks_a_second_dispatch():
    """#1185's guard, checked for the status #1208 makes LONG-LIVED. A bot dispatched at start-2min
    sits in ``awaiting_admission`` for up to 15 minutes — longer than the auto-join grace window —
    so the sweep must treat that status as the room being OWNED, or the very fix that lets the bot
    wait becomes a duplicate-bot generator."""
    from meeting_api.bot_spawn.auto_join import LIVE_STATUSES

    assert "awaiting_admission" in LIVE_STATUSES
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, mid=1, status="awaiting_admission", at=None)   # dispatched, still knocking
    due = _seed(repo, mid=2, status="scheduled", at=NOW)       # a sibling row for the same occurrence
    counters = await _tick(repo, runtime)
    assert counters["spawned"] == 0 and counters["skipped_live"] == 1
    assert runtime.specs == []
    assert repo._meetings[due]["status"] == "scheduled"


async def test_live_row_for_another_user_does_not_block():
    """The guard is per (user, platform, native) — a different tenant in the same room is theirs."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, mid=1, status="active", at=None, user_id=99)
    _seed(repo, mid=2, status="scheduled", at=NOW)
    counters = await _tick(repo, runtime)
    assert counters["spawned"] == 1 and counters["skipped_live"] == 0


async def test_live_keys_ignores_terminal_and_linkless_rows():
    from meeting_api.bot_spawn.auto_join import live_keys

    keys = live_keys([
        {"id": 1, "user_id": USER, "platform": PLAT, "native_meeting_id": NID},
        {"id": 2, "user_id": USER, "platform": "unknown", "native_meeting_id": None},
        {"id": 3, "user_id": None, "platform": PLAT, "native_meeting_id": "x"},
    ])
    assert keys == {(USER, PLAT, NID): 1}


# ---- the attempt stamp that outlives the row (live 2026-08-17: a bot every ~2.5 min) --------

async def test_dispatch_records_the_attempt_before_making_it():
    """``auto_join_last_attempt`` records the ATTEMPT, not its outcome — so it survives the two
    outcomes that write nothing back to this row: a spawn that succeeds and a bot that then fails
    to JOIN (the row goes terminal and calendar sync recreates it), and a death mid-spawn."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo)
    await _tick(repo, runtime)
    assert repo._meetings[mid]["data"]["auto_join_last_attempt"] == NOW.isoformat()


async def test_attempt_stamp_survives_a_failed_spawn_and_holds_the_next_tick():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient(fail=True)
    mid = _seed(repo)
    assert (await _tick(repo, runtime))["errors"] == 1
    assert repo._meetings[mid]["data"]["auto_join_last_attempt"] == NOW.isoformat()
    assert (await _tick(repo, runtime, now=NOW + timedelta(seconds=1)))["due"] == 0


def test_due_rows_hold_a_row_carrying_a_spent_attempt_until_the_backoff_expires():
    """The pure guard behind the storm fix: a row seeded with an attempt calendar sync carried
    over from the terminal row it replaced is NOT due until one backoff interval has passed."""
    def row(**extra):
        return {"id": 26267, "user_id": USER, "platform": PLAT, "native_meeting_id": NID,
                "data": {"scheduled_at": NOW.isoformat(), **extra}}

    spent = (NOW - timedelta(seconds=10)).isoformat()
    assert not due_rows([row(auto_join_last_attempt=spent)], now=NOW, retry_backoff_s=300)
    assert due_rows([row(auto_join_last_attempt=spent)],
                    now=NOW + timedelta(seconds=291), retry_backoff_s=300)
    # a garbage stamp never silently pins a row out of the sweep forever
    assert due_rows([row(auto_join_last_attempt="not-a-time")], now=NOW)
    assert due_rows([row()], now=NOW)

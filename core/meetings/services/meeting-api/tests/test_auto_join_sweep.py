"""A `scheduled` meeting's bot joins, loudly or not at all — the safeguards the auto-join sweep ran,
now run by the planned-row start route Flows' booked_bot calls (ADR-0052 §3, §5, s2d).

The route spawns a scheduled row through the SAME ``request_bot`` flow POST /bots runs (the claim
branch upgrades THAT row in place → idempotent), answers not_scheduled for off-toggle / link-less /
stopped rows and past_deadline for stale ones, stamps ``data.auto_join_error`` (+ a hold) on a cap
rejection or a spawn failure, and a row a manual spawn claimed first is simply no longer scheduled.
These tests were the sweep's; each now drives the route over every scheduled row
(``_booked_start.start_scheduled``). The clock itself is Flows' (core/flows/tests/test_booked_bot.py).

Over the in-memory fakes, OFFLINE.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from _booked_start import start_scheduled
from meeting_api.bot_spawn import planned_start
from meeting_api.bot_spawn.planned_start import start_planned_meeting
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
    return await start_scheduled(repo, runtime, **kw)


# ---- fires at lead time -------------------------------------------------------------

async def test_due_row_spawns_and_claims_in_place():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo, at=NOW + timedelta(seconds=30))  # inside the 60s lead window
    counters = await _tick(repo, runtime)
    assert counters == {"due": 1, "spawned": 1, "already": 0, "errors": 0, "skipped_uncapped": 0, "skipped_live": 0, "stopped": 0, "blocked": 0}
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
    """Earlier than BOOKED_POD_LEAD_S + BOOKED_JOIN_LEAD_S + 60 s before the start the route answers
    too_early (the bot's MeetingToken must not sit idle, §8); Flows calls at pod_at."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, at=NOW + timedelta(seconds=781))  # 13 min 1 s out: before start − 780 s
    counters = await _tick(repo, runtime)
    assert counters["due"] == 0 and runtime.specs == []
    assert start_scheduled.last_kinds == ["too_early"]


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
    assert counters == {"due": 0, "spawned": 0, "already": 0, "errors": 0, "skipped_uncapped": 0, "skipped_live": 0, "stopped": 0, "blocked": 0}
    assert len(runtime.specs) == 1  # exactly one spawn ever


async def test_manual_spawn_race_is_not_scheduled_and_stamps_nothing():
    """A row still `scheduled` in a stale snapshot but claimed by a manual spawn before the start
    lands: the route reads the row fresh, it is no longer scheduled → nothing to do, no error stamp
    (the sweep counted this race as `already`)."""
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
    assert counters["errors"] == 0 and start_scheduled.last_kinds == ["not_scheduled"]
    assert "auto_join_error" not in repo._meetings[mid]["data"]
    assert len(runtime.specs) == 1, "only the manual bot"


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
    assert counters == {"due": 1, "spawned": 0, "already": 0, "errors": 0, "skipped_uncapped": 0, "skipped_live": 0, "stopped": 0, "blocked": 0}
    assert runtime.specs == []  # never spawns past a cap it could not read


async def test_no_admin_edge_fails_closed_refuses_uncapped_spawn():
    # #656 C2: no admin edge configured → the per-user cap is UNRESOLVABLE. Fail closed:
    # refuse to spawn rather than spawn uncapped (default, no opt-in).
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo)
    counters = await _tick(repo, runtime, fetch_bot_context=None, allow_uncapped=False)
    assert counters == {"due": 1, "spawned": 0, "already": 0, "errors": 0, "skipped_uncapped": 1, "skipped_live": 0, "stopped": 0, "blocked": 0}
    assert runtime.specs == []  # never spawns uncapped past a cap we cannot resolve


async def test_no_admin_edge_with_explicit_opt_in_spawns_uncapped():
    # AUTO_JOIN_ALLOW_UNCAPPED=1 → the deliberate self-host uncapped mode is chosen, not defaulted.
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo)
    counters = await _tick(repo, runtime, fetch_bot_context=None, allow_uncapped=True)
    assert counters["spawned"] == 1
    assert len(runtime.specs) == 1


# ---- the window and the join lead (ADR-0052 §2, §8) ----------------------------------------

async def test_the_route_window_edges():
    """From start − 780 s (pod lead + join lead + 60 s) to start + BOOKED_GRACE_S; a row with no
    readable time never starts."""
    async def code_for(at, **extra):
        repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
        _seed(repo, at=at, data_extra=extra)
        if at is None:
            repo._meetings[1]["data"]["scheduled_at"] = "not-a-time"
        out = await start_planned_meeting(1, user_id=USER, repo=repo, runtime=runtime, now=NOW,
                                          transcribe_gate=_NO_GATE, token_secret="s",
                                          redis_url="redis://r", allow_uncapped=True)
        return out.code

    assert await code_for(NOW + timedelta(seconds=779)) == "started"
    assert await code_for(NOW + timedelta(seconds=781)) == "too_early"
    assert await code_for(NOW - timedelta(seconds=599)) == "started"
    assert await code_for(NOW - timedelta(seconds=601)) == "past_deadline"
    assert await code_for(None) == "not_scheduled"


async def test_the_booked_bot_opens_the_meeting_two_minutes_before_the_start(monkeypatch):
    """#1208 — the PRODUCT default, carried by BOOKED_JOIN_LEAD_S (ADR-0052 §2): the pod starts
    early and waits; it opens the meeting 120 s before the start, so it is in the lobby AT it."""
    import json

    monkeypatch.delenv("BOOKED_JOIN_LEAD_S", raising=False)
    assert planned_start.DEFAULT_BOOKED_JOIN_LEAD_S == 120 == planned_start.booked_join_lead_s()
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, at=NOW + timedelta(seconds=600))
    await _tick(repo, runtime)
    join_at = json.loads(runtime.specs[0]["env"]["VEXA_BOT_CONFIG"])["joinAt"]
    expected = (NOW + timedelta(seconds=480)).isoformat().replace("+00:00", "Z")
    assert join_at == expected


def test_the_join_lead_default_and_its_override(monkeypatch):
    """One default, one reader: ``booked_join_lead_s`` is what the route uses and config.v1 declares
    the same default. Deploy values still override."""
    import json
    from pathlib import Path

    monkeypatch.delenv("BOOKED_JOIN_LEAD_S", raising=False)
    assert planned_start.booked_join_lead_s() == 120
    monkeypatch.setenv("BOOKED_JOIN_LEAD_S", "60")
    assert planned_start.booked_join_lead_s() == 60
    config = json.loads((Path(planned_start.__file__).parents[1] / "config.v1.json").read_text())
    (key,) = [k for k in config["keys"] if k["key"] == "BOOKED_JOIN_LEAD_S"]
    assert str(key["default"]).startswith("120")


def test_lead_and_lobby_budget_together_cover_a_late_host():
    """The pair, checked as one claim (#1208): opening the meeting at start − 120 s and permitted to
    wait 900 s, the bot is still knocking 13 minutes AFTER the scheduled start — and the reconcile
    floor it is measured against outlasts that whole window, so nothing reaps it mid-wait."""
    from meeting_api.bot_spawn.service import lobby_budget_ms
    from meeting_api.lifecycle.reconcile import default_preactive_grace

    waits_until_s_after_start = lobby_budget_ms() / 1000.0 - planned_start.DEFAULT_BOOKED_JOIN_LEAD_S
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
    from meeting_api.bot_spawn.planned_start import LIVE_STATUSES

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
    from meeting_api.bot_spawn.planned_start import live_keys

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


async def test_a_row_carrying_a_spent_attempt_is_held_until_the_backoff_expires():
    """The guard behind the storm fix: a row seeded with an attempt calendar sync carried over from
    the terminal row it replaced does not start until one backoff interval has passed (the route
    answers `backoff` with Retry-After); a garbage stamp never pins a row out forever."""
    spent = (NOW - timedelta(seconds=10)).isoformat()

    async def kinds(now, **extra):
        repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
        _seed(repo, data_extra=extra)
        await _tick(repo, runtime, now=now, retry_backoff_s=300)
        return start_scheduled.last_kinds

    assert await kinds(NOW, auto_join_last_attempt=spent) == ["backoff"]
    assert await kinds(NOW + timedelta(seconds=291), auto_join_last_attempt=spent) == ["spawned"]
    assert await kinds(NOW, auto_join_last_attempt="not-a-time") == ["spawned"]
    assert await kinds(NOW) == ["spawned"]

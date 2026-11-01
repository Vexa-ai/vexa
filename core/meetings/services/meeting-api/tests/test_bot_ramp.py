"""#1962 s2 — the per-account bot ramp beside the concurrency ceiling.

Asserts:
  * ``project_curve`` reproduces the ruled schedule (5, 10, 15, 22, 33, 49, 73, 109, 163, 244, 366);
  * ``BOT_RAMP_BURST=0`` is the old behaviour, and an out-of-range ``BOT_RAMP_*`` refuses the boot;
  * a ceiling at or below the burst never meets a ramp 429, and no ceiling means no ramp;
  * the route admits the burst, then 429s with ``Retry-After`` (ramp: exact, 1..W; ceiling: 60);
  * fresh spawns, the planned-row claim, ``continue_meeting`` reopens and the auto-join sweep all
    obey the ramp, and all three admitting paths stamp ``data.spawn_admitted_at``;
  * ``base`` excludes in-window admissions, and the ramp follows the curve over time;
  * concurrent spawns never exceed the allowance (in-memory, and over real Postgres when
    ``MEETING_API_TEST_DATABASE_URL`` names an empty database);
  * the stamp reaches no response projection and no webhook payload;
  * the account's own dials come from bot-context, and an unreachable bot-context leaves the
    deployment's.

OFFLINE by default — the shipped ``request_bot`` / ``build_router`` / ``auto_join_tick`` over the
in-memory fakes, with the fake repo's clock driven by the test.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from meeting_api.bot_spawn import MaxBotsExceeded, RampExceeded, build_router, request_bot
from meeting_api.bot_spawn import service as spawn_service
from meeting_api.bot_spawn.auto_join import auto_join_tick
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn.ramp import (
    STAMP_KEY,
    RampConfigError,
    RampDials,
    deployment_dials,
    format_stamp,
    project_curve,
    ramp_state,
    resolve_ramp,
    retry_after,
    window_counts,
)

SECRET = "test-admin-token"
USER = 7
KW = dict(redis_url="r", token_secret=SECRET, meeting_api_url="http://meeting-api:8080")
T0 = datetime(2026, 11, 2, 14, 0, 0, tzinfo=timezone.utc)
DEFAULT = RampDials(burst=5, share=0.5, window_s=60)
RAMP_ENV = ("BOT_RAMP_BURST", "BOT_RAMP_SHARE", "BOT_RAMP_WINDOW_S")


@pytest.fixture(autouse=True)
def _clean_ramp_env(monkeypatch):
    for var in RAMP_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("ADMIN_API_URL", raising=False)
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)


class Clock:
    def __init__(self, at: datetime = T0):
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at = self.at + timedelta(seconds=seconds)


def _repo(clock: Clock | None = None) -> InMemoryMeetingRepo:
    repo = InMemoryMeetingRepo()
    repo.clock = clock or Clock()
    return repo


async def _spawn(repo, runtime, nid, *, cap=25, ramp=DEFAULT, continue_meeting=False):
    return await request_bot(
        repo, runtime, user_id=USER, platform="google_meet", native_meeting_id=nid,
        max_concurrent=cap, ramp=ramp, continue_meeting=continue_meeting, **KW,
    )


async def _fill(repo, runtime, n, *, prefix="m", cap=25, ramp=DEFAULT, status="active"):
    rows = []
    for i in range(n):
        row = await _spawn(repo, runtime, f"{prefix}-{i}", cap=cap, ramp=ramp)
        repo.set_status(row["id"], status)
        rows.append(row)
    return rows


def _client(repo, runtime):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(build_router(repo, runtime))
    return TestClient(app)


def _post(client, nid, *, limits="25"):
    return client.post("/bots", headers={"x-user-id": str(USER), "x-user-limits": limits},
                       json={"platform": "google_meet", "native_meeting_id": nid})


# ── the pure rule ────────────────────────────────────────────────────────────────────────────────

def test_project_curve_matches_the_ruled_schedule():
    curve = project_curve(DEFAULT, 10_000, running=0, base=0, window_stamps=[], now=T0,
                          max_entries=11)
    assert [e["total"] for e in curve] == [5, 10, 15, 22, 33, 49, 73, 109, 163, 244, 366]
    assert [e["after_seconds"] for e in curve] == [0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600]


def test_project_curve_caps_at_the_ceiling_and_is_empty_without_a_ramp():
    assert [e["total"] for e in project_curve(DEFAULT, 25, running=0, base=0,
                                              window_stamps=[], now=T0)] == [5, 10, 15, 22, 25]
    assert [e["total"] for e in project_curve(DEFAULT, 100, running=0, base=0,
                                              window_stamps=[], now=T0)][-1] == 100
    assert project_curve(DEFAULT, 5, running=0, base=0, window_stamps=[], now=T0) == []
    assert project_curve(None, 25, running=0, base=0, window_stamps=[], now=T0) == []
    assert project_curve(DEFAULT, None, running=0, base=0, window_stamps=[], now=T0) == []
    assert project_curve(DEFAULT, 25, running=25, base=25, window_stamps=[], now=T0) == []


def test_project_curve_starts_at_the_retry_after_when_the_ramp_blocks():
    stamps = [T0 - timedelta(seconds=19)] * 5          # 5 started 19 s ago
    state = ramp_state(DEFAULT, 25, running=5, base=0, window_stamps=stamps, now=T0)
    assert (state.blocked_by, state.allowance, state.retry_after) == ("ramp", 5, 41)
    curve = project_curve(DEFAULT, 25, running=5, base=0, window_stamps=stamps, now=T0)
    assert curve[0] == {"after_seconds": 41, "total": 10}
    assert [e["after_seconds"] for e in curve] == [41, 101, 161, 221]
    assert [e["total"] for e in curve] == [10, 15, 22, 25]


def test_base_excludes_in_window_admissions():
    now = T0
    rows = [(True, format_stamp(now - timedelta(seconds=s))) for s in (1, 10, 20, 30, 59)] * 2
    rows += [(True, format_stamp(now - timedelta(seconds=61))), (True, None),
             (False, format_stamp(now - timedelta(seconds=120))),   # ended before the window
             (False, format_stamp(now - timedelta(seconds=5)))]     # ended, still in the window
    base, stamps = window_counts(rows, now=now, window_s=60)
    assert base == 2                 # the 61 s-old running bot and the unstamped one
    assert len(stamps) == 11         # every in-window start, including the one that ended
    state = ramp_state(DEFAULT, 100, running=12, base=base, window_stamps=stamps, now=now)
    assert state.allowance == 5      # max(5, floor(0.5 × 2)) — the 10 in-window bots add nothing
    assert state.blocked_by == "ramp"


def test_retry_after_is_when_the_kth_oldest_start_leaves_the_window():
    stamps = [T0 - timedelta(seconds=s) for s in (50, 40, 30.5, 20, 10, 5, 1)]
    # started 7, allowance 5 → k = 3, the third oldest (30.5 s ago) leaves in 29.5 s → 30
    assert retry_after(DEFAULT, 5, stamps, T0) == 30
    # started 7, allowance 7 → k = 1, the oldest (50 s ago) leaves in 10 s
    assert retry_after(DEFAULT, 7, stamps, T0) == 10
    assert retry_after(DEFAULT, 1, [T0 - timedelta(seconds=59.999)], T0) == 1     # clamped up
    assert retry_after(DEFAULT, 1, [T0 + timedelta(seconds=30)], T0) == 60        # clamped to W


def test_resolve_ramp_layers_the_account_over_the_deployment():
    assert resolve_ramp(None, DEFAULT) == DEFAULT
    assert resolve_ramp({}, DEFAULT) == DEFAULT
    mine = resolve_ramp({"burst": 3, "share": 0.2, "window_s": 120}, DEFAULT)
    assert mine == RampDials(burst=3, share=0.2, window_s=120, source="account")
    assert resolve_ramp({"share": 0.2}, DEFAULT) == RampDials(5, 0.2, 60, source="account")
    # an account cannot turn the ramp off, nor carry a value out of range
    assert resolve_ramp({"burst": 0, "share": 7, "window_s": 1}, DEFAULT) == DEFAULT
    # BOT_RAMP_BURST=0 turns it off for every account
    assert resolve_ramp({"burst": 3}, RampDials(0, 0.5, 60)) is None


# ── dials and the boot ───────────────────────────────────────────────────────────────────────────

def test_deployment_dials_default_and_empty_values():
    assert deployment_dials({}) == DEFAULT
    assert deployment_dials({"BOT_RAMP_BURST": "", "BOT_RAMP_SHARE": " ",
                             "BOT_RAMP_WINDOW_S": ""}) == DEFAULT
    assert deployment_dials({"BOT_RAMP_BURST": "0", "BOT_RAMP_SHARE": "1",
                             "BOT_RAMP_WINDOW_S": "3600"}) == RampDials(0, 1.0, 3600)


@pytest.mark.parametrize("var,value", [
    ("BOT_RAMP_BURST", "-1"), ("BOT_RAMP_BURST", "five"), ("BOT_RAMP_BURST", "2.5"),
    ("BOT_RAMP_SHARE", "1.5"), ("BOT_RAMP_SHARE", "-0.1"), ("BOT_RAMP_SHARE", "nan"),
    ("BOT_RAMP_WINDOW_S", "5"), ("BOT_RAMP_WINDOW_S", "3601"), ("BOT_RAMP_WINDOW_S", "1m"),
])
def test_out_of_range_env_refuses_boot(var, value):
    from meeting_api.__main__ import _bot_ramp_at_boot
    from meeting_api.config_preflight import ConfigError

    with pytest.raises(RampConfigError):
        deployment_dials({var: value})
    with pytest.raises(ConfigError) as exc:
        _bot_ramp_at_boot({var: value})
    assert var in str(exc.value)


def test_boot_accepts_defaults_and_in_range_values():
    from meeting_api.__main__ import _bot_ramp_at_boot

    assert _bot_ramp_at_boot({}) == DEFAULT
    assert _bot_ramp_at_boot({"BOT_RAMP_BURST": "0"}).burst == 0


def test_boot_hook_is_wired_into_the_production_entrypoint():
    import inspect

    import meeting_api.__main__ as main

    assert "_bot_ramp_at_boot()" in inspect.getsource(main)


def test_config_declares_the_dials_as_defaulted():
    import json
    from pathlib import Path

    import meeting_api

    decl = json.loads((Path(meeting_api.__file__).parent / "config.v1.json").read_text())
    keys = {k["key"]: k for k in decl["keys"]}
    for var, default in zip(RAMP_ENV, ("5", "0.5", "60")):
        assert keys[var]["class"] == "defaulted"
        assert keys[var]["default"] == default


# ── the route ────────────────────────────────────────────────────────────────────────────────────

def test_route_admits_the_burst_then_ramp_429s_with_retry_after():
    repo, runtime = _repo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    answers = [_post(client, f"m-{i}") for i in range(25)]
    assert [r.status_code for r in answers].count(201) == 5
    refused = [r for r in answers if r.status_code != 201]
    assert len(refused) == 20
    for r in refused:
        assert r.status_code == 429
        assert set(r.json()) == {"detail"}
        assert r.json()["detail"].startswith("Bot ramp limit reached")
        assert "5 bots started in the last 60 seconds" in r.json()["detail"]
        assert 1 <= int(r.headers["Retry-After"]) <= 60
    assert len(runtime.specs) == 5


def test_route_ceiling_429_carries_retry_after_60_and_its_old_detail():
    repo, runtime = _repo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    assert _post(client, "m-1", limits="1").status_code == 201
    r = _post(client, "m-2", limits="1")
    assert r.status_code == 429
    assert r.json() == {"detail": "User has reached the maximum concurrent bot limit (1)."}
    assert r.headers["Retry-After"] == "60"


def test_route_runtime_backstop_429_carries_retry_after():
    repo, runtime = _repo(), FakeRuntimeClient(quota_exceeded=True)
    r = _client(repo, runtime).post("/bots", headers={"x-user-id": str(USER)},
                                    json={"platform": "google_meet", "native_meeting_id": "m-1"})
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "60"


def test_route_past_both_dials_is_told_about_the_ceiling():
    clock = Clock()
    repo, runtime = _repo(clock), FakeRuntimeClient()
    client = _client(repo, runtime)
    for i in range(5):
        assert _post(client, f"m-{i}", limits="7").status_code == 201
    assert _post(client, "m-5", limits="7").json()["detail"].startswith("Bot ramp limit reached")
    clock.advance(61)
    assert [_post(client, f"n-{i}", limits="7").status_code for i in range(3)] == [201, 201, 429]
    r = _post(client, "n-9", limits="7")
    assert "maximum concurrent bot limit (7)" in r.json()["detail"]
    assert r.headers["Retry-After"] == "60"


def test_route_burst_zero_is_the_old_behaviour(monkeypatch):
    monkeypatch.setenv("BOT_RAMP_BURST", "0")
    assert resolve_ramp(None, deployment_dials()) is None
    repo, runtime = _repo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    answers = [_post(client, f"m-{i}", limits="17").status_code for i in range(18)]
    assert answers == [201] * 17 + [429]


def test_route_no_ceiling_means_no_ramp():
    repo, runtime = _repo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    answers = [client.post("/bots", headers={"x-user-id": str(USER)},
                           json={"platform": "google_meet", "native_meeting_id": f"m-{i}"})
               for i in range(12)]
    assert all(r.status_code == 201 for r in answers)


def test_route_reads_the_account_dials_from_bot_context(monkeypatch):
    async def ctx(_uid):
        return {"bot_ramp": {"burst": 3, "share": 0.2, "window_s": 120}}

    monkeypatch.setattr(spawn_service, "_fetch_bot_context", ctx)
    repo, runtime = _repo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    answers = [_post(client, f"m-{i}") for i in range(5)]
    assert [r.status_code for r in answers] == [201, 201, 201, 429, 429]
    assert "in the last 120 seconds" in answers[3].json()["detail"]
    assert 1 <= int(answers[3].headers["Retry-After"]) <= 120


def test_route_unreachable_bot_context_falls_back_to_the_deployment_defaults(monkeypatch):
    # A real lookup against a port nothing listens on: the best-effort fetch returns {}.
    monkeypatch.setenv("ADMIN_API_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("INTERNAL_API_SECRET", "x" * 32)
    repo, runtime = _repo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    answers = [_post(client, f"m-{i}").status_code for i in range(7)]
    assert answers == [201] * 5 + [429] * 2


# ── the service: ceilings, stamps, every admitting path ──────────────────────────────────────────

async def test_ceiling_at_or_below_burst_never_ramp_429s_even_with_respawns():
    clock = Clock()
    repo, runtime = _repo(clock), FakeRuntimeClient()
    for cap in (3, 5):
        for i in range(12):                      # spawn-and-stop inside one window
            row = await _spawn(repo, runtime, f"c{cap}-{i}", cap=cap)
            repo.set_status(row["id"], "completed")
            clock.advance(1)
    # and at the ceiling, the answer is the ceiling's
    for i in range(3):
        await _spawn(repo, runtime, f"held-{i}", cap=3)
    with pytest.raises(MaxBotsExceeded) as exc:
        await _spawn(repo, runtime, "held-3", cap=3)
    assert not isinstance(exc.value, RampExceeded)


async def test_no_ceiling_no_ramp_and_no_dials_no_ramp():
    repo, runtime = _repo(), FakeRuntimeClient()
    await _fill(repo, runtime, 12, prefix="none", cap=None)
    await _fill(repo, runtime, 12, prefix="plain", cap=100, ramp=None)


async def test_every_admitting_path_stamps_the_admission_time():
    clock = Clock()
    repo, runtime = _repo(clock), FakeRuntimeClient()
    fresh = await _spawn(repo, runtime, "fresh", ramp=None, cap=None)
    assert repo._meetings[fresh["id"]]["data"][STAMP_KEY] == "2026-11-02T14:00:00.000Z"
    # the planned-row claim
    repo._meetings[99] = {
        "id": 99, "user_id": USER, "platform": "google_meet", "native_meeting_id": "planned",
        "platform_specific_id": "planned", "status": "scheduled", "bot_container_id": None,
        "start_time": None, "end_time": None, "data": {"title": "t"},
        "created_at": "2026-11-01T09:00:00Z", "updated_at": "2026-11-01T09:00:00Z",
    }
    clock.advance(1.5)
    claimed = await _spawn(repo, runtime, "planned", ramp=None, cap=None)
    assert claimed["id"] == 99
    assert repo._meetings[99]["data"][STAMP_KEY] == "2026-11-02T14:00:01.500Z"
    # the reopen overwrites the row's stamp
    repo.set_status(fresh["id"], "completed")
    clock.advance(2)
    again = await _spawn(repo, runtime, "fresh", ramp=None, cap=None, continue_meeting=True)
    assert again["id"] == fresh["id"]
    assert repo._meetings[fresh["id"]]["data"][STAMP_KEY] == "2026-11-02T14:00:03.500Z"


async def test_ramp_over_time_follows_the_curve_when_each_refusal_retries_at_retry_after():
    clock = Clock()
    repo, runtime = _repo(clock), FakeRuntimeClient()
    pending = [f"m-{i}" for i in range(25)]
    admitted_at: list[float] = []
    retry_at = {nid: 0.0 for nid in pending}
    elapsed = 0.0
    while pending:
        due = [nid for nid in pending if retry_at[nid] <= elapsed]
        for nid in due:
            try:
                row = await _spawn(repo, runtime, nid)
            except RampExceeded as e:
                assert 1 <= e.retry_after <= 60
                retry_at[nid] = elapsed + e.retry_after
                continue
            repo.set_status(row["id"], "active")
            admitted_at.append(elapsed)
            pending.remove(nid)
        nxt = min(retry_at[nid] for nid in pending) if pending else elapsed
        clock.advance(nxt - elapsed)
        elapsed = nxt
    by_minute = [sum(1 for t in admitted_at if t <= m * 60) for m in range(5)]
    assert by_minute == [5, 10, 15, 22, 25]
    # in any trailing window, admissions never exceed max(5, floor(0.5 × base))
    for t in admitted_at:
        in_window = sum(1 for a in admitted_at if t - 60 < a <= t)
        base = sum(1 for a in admitted_at if a <= t - 60)
        assert in_window <= max(5, base // 2)


async def test_planned_row_claim_obeys_the_ramp():
    repo, runtime = _repo(), FakeRuntimeClient()
    await _fill(repo, runtime, 5)
    repo._meetings[99] = {
        "id": 99, "user_id": USER, "platform": "google_meet", "native_meeting_id": "planned",
        "platform_specific_id": "planned", "status": "scheduled", "bot_container_id": None,
        "start_time": None, "end_time": None, "data": {"title": "t"},
        "created_at": "2026-11-01T09:00:00Z", "updated_at": "2026-11-01T09:00:00Z",
    }
    with pytest.raises(RampExceeded):
        await _spawn(repo, runtime, "planned")
    assert repo._meetings[99]["status"] == "scheduled"
    assert STAMP_KEY not in repo._meetings[99]["data"]


async def test_reopen_obeys_the_ramp_and_admits_once_the_window_passes():
    clock = Clock()
    repo, runtime = _repo(clock), FakeRuntimeClient()
    old = await _spawn(repo, runtime, "old")
    repo.set_status(old["id"], "completed")
    clock.advance(120)
    await _fill(repo, runtime, 5)
    with pytest.raises(RampExceeded) as exc:
        await _spawn(repo, runtime, "old", continue_meeting=True)
    assert exc.value.retry_after == 60
    assert repo._meetings[old["id"]]["status"] == "completed"
    assert repo.reopened == []
    clock.advance(60)
    row = await _spawn(repo, runtime, "old", continue_meeting=True)
    assert row["id"] == old["id"] and repo.reopened == [old["id"]]


async def test_auto_join_obeys_the_ramp_and_reads_the_account_dials():
    clock = Clock()
    repo, runtime = _repo(clock), FakeRuntimeClient()
    await _fill(repo, runtime, 5)
    repo._meetings[99] = {
        "id": 99, "user_id": USER, "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
        "platform_specific_id": "abc-defg-hij", "status": "scheduled", "bot_container_id": None,
        "start_time": None, "end_time": None,
        "data": {"title": "t", "auto_join": True, "scheduled_at": T0.isoformat()},
        "created_at": "2026-11-01T09:00:00Z", "updated_at": "2026-11-01T09:00:00Z",
    }
    contexts = {"value": {"max_concurrent": 25}}

    async def ctx(_uid):
        return contexts["value"]

    kw = dict(fetch_bot_context=ctx, transcribe_gate=lambda: None, now=T0, token_secret="s",
              redis_url="redis://r")
    counters = await auto_join_tick(repo, runtime, **kw)
    assert counters["errors"] == 1 and counters["spawned"] == 0
    assert repo._meetings[99]["data"]["auto_join_error"].startswith("Bot ramp limit reached")
    assert repo._meetings[99]["status"] == "scheduled"
    # the account's own burst, from bot-context, admits it
    repo._meetings[99]["data"].pop("auto_join_next_retry")
    repo._meetings[99]["data"].pop("auto_join_last_attempt", None)
    contexts["value"] = {"max_concurrent": 25, "bot_ramp": {"burst": 6}}
    counters = await auto_join_tick(repo, runtime, **kw)
    assert counters["spawned"] == 1
    assert repo._meetings[99]["status"] == "requested"
    assert STAMP_KEY in repo._meetings[99]["data"]


async def test_auto_join_with_the_ramp_off_spawns_past_the_burst(monkeypatch):
    monkeypatch.setenv("BOT_RAMP_BURST", "0")
    repo, runtime = _repo(), FakeRuntimeClient()
    await _fill(repo, runtime, 5, ramp=None)
    repo._meetings[99] = {
        "id": 99, "user_id": USER, "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
        "platform_specific_id": "abc-defg-hij", "status": "scheduled", "bot_container_id": None,
        "start_time": None, "end_time": None,
        "data": {"title": "t", "auto_join": True, "scheduled_at": T0.isoformat()},
        "created_at": "2026-11-01T09:00:00Z", "updated_at": "2026-11-01T09:00:00Z",
    }

    async def ctx(_uid):
        return {"max_concurrent": 25, "bot_ramp": {"burst": 1}}

    counters = await auto_join_tick(repo, runtime, fetch_bot_context=ctx,
                                    transcribe_gate=lambda: None, now=T0, token_secret="s",
                                    redis_url="redis://r")
    assert counters["spawned"] == 1


async def test_concurrent_spawns_never_exceed_the_allowance_in_memory():
    repo, runtime = _repo(), FakeRuntimeClient()
    results = await asyncio.gather(*(_spawn(repo, runtime, f"m-{i}") for i in range(20)),
                                   return_exceptions=True)
    admitted = [r for r in results if isinstance(r, dict)]
    assert len(admitted) == 5
    assert all(isinstance(r, RampExceeded) for r in results if not isinstance(r, dict))


def test_ramp_exceeded_is_a_max_bots_exceeded_with_a_retry_after():
    e = RampExceeded(USER, started=5, window_s=60, allowance=5, running=5, retry_after=41)
    assert isinstance(e, MaxBotsExceeded)
    assert e.retry_after == 41
    assert str(e) == ("Bot ramp limit reached: 5 bots started in the last 60 seconds; this account "
                      "may start 5 now while running 5. Retry in 41 seconds.")
    assert MaxBotsExceeded(USER, 3).retry_after == 60


# ── the stamp stays internal ─────────────────────────────────────────────────────────────────────

def test_the_stamp_reaches_no_response_and_no_webhook():
    from meeting_api.collector.projection import project_list_data, project_response_data
    from meeting_api.webhooks.delivery import clean_meeting_data

    repo, runtime = _repo(), FakeRuntimeClient()
    r = _post(_client(repo, runtime), "m-1")
    assert r.status_code == 201
    assert STAMP_KEY not in (r.json().get("data") or {})
    stored = repo._meetings[r.json()["id"]]["data"]
    assert STAMP_KEY in stored
    for owner in (True, False):
        assert STAMP_KEY not in project_response_data(stored, viewer_is_owner=owner)
        assert STAMP_KEY not in project_list_data(stored, viewer_is_owner=owner)
    assert STAMP_KEY not in clean_meeting_data(stored)


async def test_the_stamp_reaches_no_status_webhook_envelope():
    from meeting_api.webhooks.delivery import build_envelope, clean_meeting_data

    repo, runtime = _repo(), FakeRuntimeClient()
    row = await _spawn(repo, runtime, "m-1")
    stored = dict(repo._meetings[row["id"]])
    envelope = build_envelope("meeting.status_change", {"meeting": {
        **stored, "data": clean_meeting_data(stored["data"])}})
    assert STAMP_KEY not in repr(envelope)


# ── over real Postgres (opt-in) ──────────────────────────────────────────────────────────────────

PG_URL = os.getenv("MEETING_API_TEST_DATABASE_URL")
_EVENT_TIME = """
CREATE OR REPLACE FUNCTION meeting_event_time(data jsonb, start_time timestamp, created_at timestamp)
RETURNS timestamp LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
BEGIN
    RETURN COALESCE(((data ->> 'scheduled_at')::timestamptz AT TIME ZONE 'UTC'), start_time, created_at);
EXCEPTION WHEN OTHERS THEN
    RETURN COALESCE(start_time, created_at);
END $fn$;
"""


@pytest.fixture
async def pg_repo():
    if not PG_URL:
        pytest.skip("set MEETING_API_TEST_DATABASE_URL to an empty Postgres database")
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from meeting_api.bot_spawn.adapters import SqlAlchemyMeetingRepo
    from meeting_api.sessions.models import Base

    engine = create_async_engine(PG_URL, pool_size=25, max_overflow=0)
    async with engine.begin() as conn:
        await conn.execute(text(_EVENT_TIME))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield SqlAlchemyMeetingRepo(async_sessionmaker(engine, expire_on_commit=False)), engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _pg_guarded(repo, nid, *, cap=25, ramp=DEFAULT, user_id=USER):
    return await repo.create_meeting_guarded(user_id=user_id, platform="google_meet",
                                             native_meeting_id=nid, data={}, max_concurrent=cap,
                                             ramp=ramp)


async def test_pg_concurrent_spawns_never_exceed_the_allowance(pg_repo):
    repo, engine = pg_repo
    results = await asyncio.gather(*(_pg_guarded(repo, f"m-{i}") for i in range(20)),
                                   return_exceptions=True)
    admitted = [r for r in results if isinstance(r, dict)]
    refused = [r for r in results if isinstance(r, RampExceeded)]
    assert (len(admitted), len(refused)) == (5, 15), results
    assert all(1 <= e.retry_after <= 60 for e in refused)
    from sqlalchemy import text

    async with engine.connect() as conn:
        stamps = (await conn.execute(text(
            "SELECT data->>'spawn_admitted_at' FROM meetings ORDER BY id"))).scalars().all()
    assert len(stamps) == 5 and all(s and s.endswith("Z") and len(s) == 24 for s in stamps)
    # another account is not paced by this one
    other = await asyncio.gather(*(_pg_guarded(repo, f"o-{i}", user_id=8) for i in range(5)))
    assert len(other) == 5


async def test_pg_base_counts_bots_admitted_before_the_window_and_reopen_obeys(pg_repo):
    repo, engine = pg_repo
    from sqlalchemy import text

    async with engine.begin() as conn:
        # 20 running bots admitted 10 minutes ago, plus a finished one to reopen
        for i in range(20):
            await conn.execute(text(
                "INSERT INTO meetings (user_id, platform, platform_specific_id, status, data) "
                "VALUES (:u, 'google_meet', :n, 'active', jsonb_build_object('spawn_admitted_at', "
                "to_char((clock_timestamp() - interval '10 minutes') AT TIME ZONE 'UTC', "
                "'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"')))"), {"u": USER, "n": f"base-{i}"})
        await conn.execute(text(
            "INSERT INTO meetings (user_id, platform, platform_specific_id, status, data) "
            "VALUES (:u, 'google_meet', 'done', 'completed', '{}'::jsonb)"), {"u": USER})
    # allowance = max(5, floor(0.5 × 20)) = 10
    results = await asyncio.gather(*(_pg_guarded(repo, f"m-{i}", cap=100) for i in range(14)),
                                   return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 10
    async with engine.connect() as conn:
        done_id = (await conn.execute(text(
            "SELECT id FROM meetings WHERE platform_specific_id = 'done'"))).scalar()
    with pytest.raises(RampExceeded):
        await repo.reopen_meeting(meeting_id=done_id, data_patch={}, ramp=DEFAULT,
                                  user_id=USER, max_concurrent=100)
    row = await repo.reopen_meeting(meeting_id=done_id, data_patch={}, ramp=None)
    assert row["status"] == "requested" and row["data"][STAMP_KEY].endswith("Z")

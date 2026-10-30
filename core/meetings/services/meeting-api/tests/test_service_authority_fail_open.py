"""#1923 s1: an authority outage never cuts an admitted bot.

Debt minutes are booked without moving the boundary, the outage costs one
authority call per tick, recovery settles each debt minute exactly once, and
a catch-up deny still stops the bot while later minutes stay in debt.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from meeting_api.service_authority import (
    ServiceAuthorityUnavailable,
    run_service_authority_sweep,
)

from test_service_authority import (
    ADMITTED_AT,
    RecordingAuthority,
    _active,
    _decision,
    _spawn,
)


def _minute(n: int):
    return ADMITTED_AT + timedelta(minutes=n)


def _continue_requests(authority):
    return [r for r in authority.requests if r.action == "continue"]


@pytest.mark.asyncio
async def test_outage_costs_one_authority_call_per_tick() -> None:
    # B2: two live meetings, three due minutes each, authority down: the
    # sweep asks once, trips the per-tick circuit, and books the rest as debt.
    authority = RecordingAuthority(
        continuation=ServiceAuthorityUnavailable("fixture outage"),
    )
    repo, runtime, first = await _active(authority)
    _repo2, _runtime2, second_meeting = await _spawn(
        authority, repo=repo, runtime=runtime,
        native_meeting_id="authority-fixture-second",
    )
    second = repo._meetings[second_meeting["id"]]
    second["status"] = "active"
    second["data"]["status_transition"] = list(first["data"]["status_transition"])
    authority.requests.clear()

    observed = await run_service_authority_sweep(
        repo, runtime, authority, now=_minute(3) + timedelta(seconds=1),
    )

    assert len(_continue_requests(authority)) == 1
    assert observed.decisions == 0
    assert observed.debt_open is True
    for row in (first, second):
        debt = row["data"]["service_authority"]["debt_boundaries"]
        assert [d["boundary"] for d in debt] == [
            _minute(m).isoformat() for m in (1, 2, 3)
        ]
        assert row["data"]["service_authority"].get("last_boundary_at") is None
        assert row["status"] == "active"
    assert runtime.deleted == []

    # The next tick, still down, asks once more and books only the new minute.
    again = await run_service_authority_sweep(
        repo, runtime, authority, now=_minute(4) + timedelta(seconds=1),
    )
    assert len(_continue_requests(authority)) == 2
    assert again.decisions == 0
    assert len(first["data"]["service_authority"]["debt_boundaries"]) == 4


@pytest.mark.asyncio
async def test_recovery_settles_each_debt_minute_once() -> None:
    # B3: after three debt minutes the authority answers again. The sweep
    # replays b1..b3 with the same request_ids and the stored concurrency,
    # each debt entry is settled exactly once, and the boundary catches up.
    authority = RecordingAuthority(
        continuation=ServiceAuthorityUnavailable("fixture outage"),
    )
    repo, runtime, row = await _active(authority)
    for minute in (1, 2, 3):
        await run_service_authority_sweep(
            repo, runtime, authority, now=_minute(minute) + timedelta(seconds=1),
        )
    debt_before = list(row["data"]["service_authority"]["debt_boundaries"])
    assert len(debt_before) == 3
    outage_request_ids = [d["request_id"] for d in debt_before]
    # Concurrency changes during the outage; replay must use the stored value.
    _r, _rt, other = await _spawn(
        authority, repo=repo, runtime=runtime,
        native_meeting_id="authority-fixture-other",
    )
    repo._meetings[other["id"]]["status"] = "active"
    repo._meetings[other["id"]]["data"]["service_authority"]["mode"] = "unconfigured"
    authority.requests.clear()

    authority.continuation = _decision(allow=True, action="continue")
    recovered = await run_service_authority_sweep(
        repo, runtime, authority, now=_minute(4) + timedelta(seconds=1),
    )
    replay = await run_service_authority_sweep(
        repo, runtime, authority, now=_minute(4) + timedelta(seconds=20),
    )
    # A fresh authority instance plus a repeated sweep (a restart) sends none.
    fresh = RecordingAuthority()
    restarted = await run_service_authority_sweep(
        repo, runtime, fresh, now=_minute(4) + timedelta(seconds=40),
    )

    replayed = _continue_requests(authority)
    assert [r.request_id for r in replayed][:3] == outage_request_ids
    assert [r.active_concurrency for r in replayed] == [
        d["active_concurrency"] for d in debt_before
    ] + [replayed[3].active_concurrency]
    assert replayed[3].active_concurrency == 2
    assert recovered.decisions == 4
    assert recovered.debt_settled_count == 3
    assert recovered.debt_open is False
    assert replay.decisions == 0
    assert replay.debt_settled_count == 0
    assert _continue_requests(fresh) == []
    assert restarted.decisions == 0
    assert restarted.debt_settled_count == 0
    assert restarted.debt_open is False
    persisted = row["data"]["service_authority"]
    assert persisted["debt_boundaries"] == []
    assert persisted["debt_open"] is False
    assert persisted["debt_settled_count"] == 3
    assert persisted["last_boundary_at"] == _minute(4).isoformat()
    assert row["status"] == "active"
    assert runtime.deleted == []


class ScriptedAuthority(RecordingAuthority):
    """Answers each continuation minute from a per-boundary script."""

    def __init__(self, script, **kwargs) -> None:
        super().__init__(**kwargs)
        self.script = script

    async def decide(self, request):
        if request.action == "continue" and request.boundary_at in self.script:
            self.continuation = self.script[request.boundary_at]
        return await super().decide(request)


@pytest.mark.asyncio
async def test_catch_up_deny_after_outage_stops_live_bot() -> None:
    # B6: three debt minutes. On recovery the authority allows b1 and denies
    # b2: b1 is settled, b2 is recorded as the stop, the bot is torn down and
    # b3 stays in debt with debt_open on the row. Replaying b3 of the stopped
    # row comes with the debt_open listing (next slice).
    authority = ScriptedAuthority(
        {}, continuation=ServiceAuthorityUnavailable("fixture outage"),
    )
    repo, runtime, row = await _active(authority)
    for minute in (1, 2, 3):
        await run_service_authority_sweep(
            repo, runtime, authority, now=_minute(minute) + timedelta(seconds=1),
        )
    assert runtime.deleted == []
    assert len(row["data"]["service_authority"]["debt_boundaries"]) == 3
    authority.requests.clear()

    authority.script = {
        _minute(1): _decision(
            allow=True, action="continue", decision_id="decision-b1-allow",
        ),
        _minute(2): _decision(
            allow=False,
            action="continue",
            reason="opaque_limit",
            decision_id="decision-catch-up-stop",
            stop_scope="billable_service",
        ),
    }
    recovered = await run_service_authority_sweep(
        repo, runtime, authority, now=_minute(3) + timedelta(seconds=20),
    )

    persisted = row["data"]["service_authority"]
    assert [r.boundary_at for r in _continue_requests(authority)] == [
        _minute(1), _minute(2),
    ]
    assert recovered.decisions == 2
    assert recovered.debt_settled_count == 2
    assert recovered.debt_open is True
    assert recovered.teardowns_confirmed == 1
    assert persisted["last_boundary_at"] == _minute(2).isoformat()
    assert persisted["last_decision_id"] == "decision-catch-up-stop"
    assert [d["boundary"] for d in persisted["debt_boundaries"]] == [
        _minute(3).isoformat(),
    ]
    assert persisted["debt_open"] is True
    assert persisted["debt_settled_count"] == 2
    assert row["data"]["stop_requested"] is True
    assert row["status"] not in ("active", "needs_help")
    assert runtime.deleted == [row["bot_container_id"]]

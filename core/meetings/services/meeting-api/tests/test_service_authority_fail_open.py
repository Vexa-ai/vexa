"""#1923 s1: an authority outage never cuts an admitted bot.

Debt minutes are booked without moving the boundary, the outage costs one
authority call per tick, recovery settles each debt minute exactly once, a
catch-up deny still stops the bot, and admission stays fail-closed (503).
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
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
    _r, _rt, other = await _spawn(authority, repo=repo, runtime=runtime)
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
    persisted = row["data"]["service_authority"]
    assert "debt_boundaries" not in persisted
    assert persisted["last_boundary_at"] == _minute(4).isoformat()
    assert row["status"] == "active"
    assert runtime.deleted == []


@pytest.mark.asyncio
async def test_catch_up_deny_after_outage_stops_live_bot() -> None:
    # B6: the authority comes back with an enforced deny for the first debt
    # minute. The bot is stopped like any enforced deny.
    authority = RecordingAuthority(
        continuation=ServiceAuthorityUnavailable("fixture outage"),
    )
    repo, runtime, row = await _active(authority)
    for minute in (1, 2):
        await run_service_authority_sweep(
            repo, runtime, authority, now=_minute(minute) + timedelta(seconds=1),
        )
    assert runtime.deleted == []

    authority.continuation = _decision(
        allow=False,
        action="continue",
        reason="opaque_limit",
        decision_id="decision-catch-up-stop",
        stop_scope="billable_service",
    )
    recovered = await run_service_authority_sweep(
        repo, runtime, authority, now=_minute(3) + timedelta(seconds=1),
    )

    persisted = row["data"]["service_authority"]
    assert recovered.decisions == 1
    assert recovered.debt_settled_count == 1
    assert recovered.teardowns_confirmed == 1
    assert persisted["last_boundary_at"] == _minute(1).isoformat()
    assert persisted["last_decision_id"] == "decision-catch-up-stop"
    assert row["data"]["stop_requested"] is True
    assert runtime.deleted == [row["bot_container_id"]]


def test_admission_during_outage_answers_503() -> None:
    # Admission stays fail-closed: the router maps the outage to 503.
    authority = RecordingAuthority(
        admit=ServiceAuthorityUnavailable("fixture outage"),
    )
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    client = TestClient(create_app(
        meeting_repo=repo,
        runtime=runtime,
        service_authority=authority,
        token_secret="fixture-token-secret",
    ))

    response = client.post(
        "/bots",
        headers={"x-user-id": "41"},
        json={
            "platform": "google_meet",
            "native_meeting_id": "authority-outage-admit",
            "transcribe_enabled": False,
            "recording_enabled": False,
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "service_authority_unavailable"
    assert repo._meetings == {}
    assert runtime.specs == []

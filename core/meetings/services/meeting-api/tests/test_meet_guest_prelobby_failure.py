"""A guest Meet bot that fails BEFORE THE LOBBY → policy ``guest_prelobby_failure`` (lifecycle/meet_respawn.py).

Founder, 17 Nov 2026 («flaky bot, no auth retry»; P20: a policy with a default, never hard-coded): any
guest pre-lobby failure — a refusal, the page with no name field (prod 38827), an empty page, a timeout,
a crash — retries AT ONCE with a free company bot account. The meeting fails only when that signed-in
bot fails too, or the pool is busy (and the reason says so). ``meet_respawn.state`` never stays
``running`` on a terminal meeting. OFFLINE: the shipped routes and sweep over the in-memory fakes.
"""
from __future__ import annotations

import pytest

from meeting_api.lifecycle import meet_respawn
from test_meet_respawn import (  # noqa: F401 — the fixtures and helpers of the respawn suite
    ACCOUNT, LIFECYCLE, _company, _due, _event, _joining, _refused, _session, _spawn, world,
)

# Prod meeting 38827 (17 Nov 2026 20:57:15Z, TRUSTED room icq-mrxf-pue), the bot's terminal verbatim.
NO_NAME_FIELD = ("Error: Could not locate name input by any locale-agnostic or English selector after 120000ms "
                 "(url=https://meet.google.com/icq-mrxf-pue?hl=en html.lang=en navigator.language=en-US; "
                 "visible text buttons: (none))")
# Paris meeting 533: Meet refused the guest («You can't join this video call», CreateMeetingDevice 403).
REFUSED_533 = ("google_meet_join_refused: Google Meet refused the join on 10 of 10 knocks (CreateMeetingDevice "
               "answered HTTP 403; Meet showed \"You can't join this video call\"); the host never received a "
               "request to admit the bot")


def _no_name_field(sid):
    return _event(sid, "failed", exit_code=1, failure_stage="joining", completion_reason="join_failure",
                  reason=NO_NAME_FIELD, error_details=f"Bot exited with code 1; reason: {NO_NAME_FIELD}",
                  join_evidence={"reason": "never_reached_lobby", "attribution": "system_fault",
                                 "source": "bot", "stage": "joining", "detail": NO_NAME_FIELD, "node": "n1"})


def _refused_533(sid):
    return _event(sid, "failed", exit_code=1, failure_stage="joining", completion_reason="awaiting_admission_rejected",
                  reason=REFUSED_533,
                  join_evidence={"reason": "awaiting_admission_rejected", "attribution": "platform_policy",
                                 "source": "bot", "stage": "joining", "detail": REFUSED_533, "node": "n1"})


async def _pool_busy(_row):
    return None


def _block(repo, mid):
    return repo._meetings[mid]["data"]["meet_respawn"]


@pytest.mark.parametrize("terminal", [_no_name_field, _refused_533], ids=["38827-no-name-field", "533-refused"])
def test_a_guest_prelobby_failure_retries_signed_in_at_once_and_joins(world, terminal):
    repo, runtime, client = world
    mid, sid = _joining(world)
    assert _block(repo, mid)["rule"] == "retry_signed_in", "the default, with no setting anywhere"
    r = client.post(LIFECYCLE, json=terminal(sid))
    assert r.status_code == 200 and r.json().get("respawn") == "scheduled", r.text
    row = repo._meetings[mid]
    assert row["status"] == "joining", "the meeting does not fail on the first page"
    assert _block(repo, mid)["signed_in_pod"] is True
    assert _due(repo, runtime, after_s=0) == 1, "at once"
    row = repo._meetings[mid]
    assert row["data"]["bot_account_id"] == ACCOUNT["id"]
    sid2 = _session(repo, mid)
    assert client.post(LIFECYCLE, json=_event(sid2, "joining")).status_code == 200
    assert client.post(LIFECYCLE, json=_event(sid2, "active")).status_code == 200
    assert repo._meetings[mid]["status"] == "active"
    assert len(runtime.specs) == 2


@pytest.mark.parametrize("terminal", [_no_name_field, _refused_533], ids=["no-name-field", "refused"])
def test_the_signed_in_bot_failing_too_fails_the_meeting_and_closes_the_block(world, terminal):
    repo, runtime, client = world
    mid, sid = _joining(world)
    client.post(LIFECYCLE, json=terminal(sid))
    _due(repo, runtime, after_s=0)
    sid2 = _session(repo, mid)
    client.post(LIFECYCLE, json=_event(sid2, "joining"))
    r = client.post(LIFECYCLE, json=terminal(sid2))
    assert "respawn" not in r.json()
    row = repo._meetings[mid]
    assert row["status"] == "failed"
    block = _block(repo, mid)
    assert block["state"] == "exhausted" and [a["signed_in"] for a in block["attempts"]] == [False, True]
    assert len(runtime.specs) == 2, "no third pod"


def test_pool_busy_falls_back_to_a_guest_retry(world):
    repo, runtime, client = world
    mid, sid = _joining(world)
    client.post(LIFECYCLE, json=_no_name_field(sid))
    assert _due(repo, runtime, after_s=0, resolve_account=_pool_busy) == 1
    row = repo._meetings[mid]
    assert row["status"] == "joining", "not failed: a guest retry"
    assert not row["data"].get("bot_account_id")
    assert _block(repo, mid)["pool_busy"] is True and _block(repo, mid)["signed_in_pod"] is False
    assert len(runtime.specs) == 2


def test_pool_busy_then_every_guest_failing_says_so_in_the_reason(world):
    repo, runtime, client = world
    mid, sid = _joining(world)
    for _ in range(2):                                   # pods 2 and 3: pool busy, guests, both dead-end
        client.post(LIFECYCLE, json=_no_name_field(sid))
        assert _due(repo, runtime, after_s=0, resolve_account=_pool_busy) == 1
        sid = _session(repo, mid)
        client.post(LIFECYCLE, json=_event(sid, "joining"))
    r = client.post(LIFECYCLE, json=_no_name_field(sid))
    assert "respawn" not in r.json()
    row = repo._meetings[mid]
    assert row["status"] == "failed" and _block(repo, mid)["state"] == "exhausted"
    assert meet_respawn.POOL_BUSY_REASON in str(row["data"])
    assert len(runtime.specs) == 3


def test_pool_busy_then_a_freed_account_is_taken(world):
    repo, runtime, client = world
    mid, sid = _joining(world)
    client.post(LIFECYCLE, json=_refused_533(sid))
    _due(repo, runtime, after_s=0, resolve_account=_pool_busy)
    sid = _session(repo, mid)
    client.post(LIFECYCLE, json=_event(sid, "joining"))
    assert client.post(LIFECYCLE, json=_refused_533(sid)).json().get("respawn") == "scheduled"
    assert _due(repo, runtime, after_s=0) == 1
    assert repo._meetings[mid]["data"]["bot_account_id"] == ACCOUNT["id"]


def test_policy_guest_retry_retries_as_a_guest(world, monkeypatch):
    repo, runtime, client = world
    mid, sid = _joining(world)
    repo._meetings[mid]["data"]["meet_respawn"]["rule"] = "guest_retry"
    assert client.post(LIFECYCLE, json=_no_name_field(sid)).json().get("respawn") == "scheduled"
    assert _block(repo, mid)["signed_in_pod"] is False
    assert _due(repo, runtime, after_s=0) == 0, "a guest retry keeps the policy's wait"
    assert _due(repo, runtime) == 1
    assert not repo._meetings[mid]["data"].get("bot_account_id")


def test_policy_fail_fails_the_meeting_at_the_first_failure(world):
    repo, runtime, client = world
    mid, sid = _joining(world)
    repo._meetings[mid]["data"]["meet_respawn"]["rule"] = "fail"
    r = client.post(LIFECYCLE, json=_no_name_field(sid))
    assert "respawn" not in r.json()
    assert repo._meetings[mid]["status"] == "failed" and _block(repo, mid)["state"] == "exhausted"


def test_the_rule_is_read_from_the_owners_bot_context_at_the_first_spawn(world, monkeypatch):
    from meeting_api.bot_spawn import service

    async def ctx(_uid):
        return {"guest_prelobby_failure": "guest_retry"}
    monkeypatch.setattr(service, "_fetch_bot_context", ctx)
    repo, runtime, _ = world
    m = _spawn(repo, runtime)
    assert repo._meetings[m["id"]]["data"]["meet_respawn"]["rule"] == "guest_retry"


def test_an_unknown_rule_reads_the_default():
    assert meet_respawn.rule_of("nonsense") == meet_respawn.RETRY_SIGNED_IN
    assert meet_respawn.rule_of(None) == meet_respawn.RETRY_SIGNED_IN
    assert meet_respawn.rule_of(" FAIL ") == meet_respawn.FAIL


def test_a_crash_with_no_report_retries_signed_in_too(world):
    """The pod dies before the lobby without its own terminal: the runtime's destroy drives it."""
    repo, runtime, client = world
    mid, sid = _joining(world)
    workload = repo._meetings[mid]["bot_container_id"]
    r = client.post("/runtime/callback", json={"workloadId": workload, "state": "destroyed"})
    assert r.status_code == 200
    assert repo._meetings[mid]["status"] == "joining" and _block(repo, mid)["signed_in_pod"] is True


def test_a_stop_is_never_retried(world):
    repo, runtime, client = world
    mid, sid = _joining(world)
    r = client.post(LIFECYCLE, json=_event(sid, "failed", failure_stage="joining", completion_reason="stopped",
                                           reason="stopped by the user"))
    assert "respawn" not in r.json()
    assert _block(repo, mid)["state"] == "exhausted"


@pytest.mark.parametrize("end", ["failed", "completed"])
def test_the_block_is_never_left_running_on_a_terminal_meeting(world, end):
    repo, runtime, client = world
    mid, sid = _joining(world)
    if end == "failed":
        client.post(LIFECYCLE, json=_event(sid, "awaiting_admission"))
        client.post(LIFECYCLE, json=_event(sid, "failed", failure_stage="awaiting_admission",
                                           completion_reason="awaiting_admission_timeout", reason="nobody admitted"))
        assert _block(repo, mid)["state"] == "exhausted"
    else:
        client.post(LIFECYCLE, json=_event(sid, "active"))
        client.post(LIFECYCLE, json=_event(sid, "completed", completion_reason="left_alone"))
        assert _block(repo, mid)["state"] == "ended"
    assert repo._meetings[mid]["status"] == end


def test_a_dead_end_is_not_the_rooms_memory():
    from datetime import datetime, timezone
    from meeting_api.bot_spawn import room_memory
    at = datetime.now(timezone.utc).isoformat()
    row = {"status": "failed", "data": {"meet_respawn": {"attempts": [
        {"at": at, "signed_in": False, "refused": False}]}}}
    assert room_memory.verdict([row]) == room_memory.UNSEEN
    row["data"]["meet_respawn"]["attempts"][0]["refused"] = True
    assert room_memory.verdict([row]) == room_memory.NEEDS_SIGN_IN

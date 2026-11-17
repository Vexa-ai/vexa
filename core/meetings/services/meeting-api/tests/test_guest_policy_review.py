"""Review of the guest_prelobby_failure transplant onto Paris meeting-api (17 Nov 2026), findings 2-5 (prod line: no Zoom respawn here, so finding 7 is Paris-only).

OFFLINE: the shipped routes and sweep over the in-memory fakes (the fixtures of test_meet_respawn).
"""
from __future__ import annotations

from meeting_api.lifecycle import meet_respawn
from test_meet_respawn import (  # noqa: F401 — the fixtures and helpers of the respawn suite
    ACCOUNT, LIFECYCLE, _due, _event, _joining, _run, _session, _spawn, world,
)
from test_meet_guest_prelobby_failure import _block, _no_name_field, _pool_busy, _refused_533


def test_2_an_explicit_none_keeps_guest_pods(world):
    repo, runtime, client = world
    m = _spawn(repo, runtime, bot_account_choice="none")
    mid, sid = m["id"], _session(repo, m["id"])
    client.post(LIFECYCLE, json=_event(sid, "joining"))
    assert _block(repo, mid)["rule"] == "guest_retry", "the person asked for a guest bot"
    assert client.post(LIFECYCLE, json=_refused_533(sid)).json().get("respawn") == "scheduled"
    assert _block(repo, mid)["signed_in_pod"] is False
    assert _due(repo, runtime, after_s=120) == 1
    assert not repo._meetings[mid]["data"].get("bot_account_id"), "never an account"


def test_2_default_choice_keeps_the_policy(world):
    repo, runtime, client = world
    m = _spawn(repo, runtime, bot_account_choice="default")
    assert _block(repo, m["id"])["rule"] == "retry_signed_in"


def test_3_a_pod_that_got_an_account_clears_pool_busy(world):
    repo, runtime, client = world
    mid, sid = _joining(world)
    client.post(LIFECYCLE, json=_refused_533(sid))
    _due(repo, runtime, after_s=0, resolve_account=_pool_busy)
    assert _block(repo, mid)["pool_busy"] is True
    sid = _session(repo, mid)
    client.post(LIFECYCLE, json=_event(sid, "joining"))
    client.post(LIFECYCLE, json=_refused_533(sid))
    assert _due(repo, runtime, after_s=0) == 1
    assert "pool_busy" not in _block(repo, mid)
    sid = _session(repo, mid)
    client.post(LIFECYCLE, json=_event(sid, "joining"))
    client.post(LIFECYCLE, json=_refused_533(sid))           # the signed-in pod fails: the meeting ends
    row = repo._meetings[mid]
    assert row["status"] == "failed"
    assert meet_respawn.POOL_BUSY_REASON not in str(row.get("data")), "not told as a busy pool"


def test_4_a_dead_end_whose_next_bot_cannot_start_is_not_a_refusal(world, monkeypatch):
    repo, runtime, client = world
    mid, sid = _joining(world)
    client.post(LIFECYCLE, json=_no_name_field(sid))
    from meeting_api.bot_spawn import service

    async def boom(*a, **k):
        raise RuntimeError("runtime down")
    monkeypatch.setattr(service, "request_bot", boom)
    _due(repo, runtime, after_s=0)
    row = repo._meetings[mid]
    assert row["status"] == "failed"
    assert row["data"]["completion_reason"] == "join_failure", row["data"]["completion_reason"]
    assert not row["data"]["failure_reason"].startswith("Google Meet refused"), row["data"]["failure_reason"]


def test_5_a_redelivered_terminal_keeps_the_scheduled_retry(world):
    repo, runtime, client = world
    mid, sid = _joining(world)
    assert client.post(LIFECYCLE, json=_no_name_field(sid)).json().get("respawn") == "scheduled"
    r = client.post(LIFECYCLE, json=_no_name_field(sid))      # the same bot's terminal again
    assert r.status_code == 200
    assert repo._meetings[mid]["status"] == "joining"
    assert _block(repo, mid)["state"] == "pending" and len(_block(repo, mid)["attempts"]) == 1
    assert _due(repo, runtime, after_s=0) == 1

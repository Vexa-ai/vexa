"""A Google Meet room's join mode, from its own past meetings (bot_spawn/room_memory.py, D402).

Pinned: a room that refused an anonymous bot in the last 30 days needs sign-in; one that admitted an
anonymous bot stays anonymous; an unseen room starts anonymous; the latest decisive outcome wins; a
signed-in bot's outcome never decides; outcomes older than 30 days are forgotten. And the spawn: a
default Meet join follows the verdict, and a room that needs sign-in with every account busy is a 409."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from meeting_api.bot_spawn import room_memory as rm
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat()


def refused(ago, signed_in=False):
    return {"status": "failed", "data": {"meet_respawn": {"attempts": [{"at": iso(NOW - ago), "signed_in": signed_in}]}}}


def admitted(ago, signed_in=False):
    return {"status": "completed", "start_time": iso(NOW - ago),
            "data": {"auth_userdata_path": "bot-account:x" if signed_in else None}}


def test_an_unseen_room_starts_anonymous():
    assert rm.verdict([], NOW) == rm.UNSEEN


def test_a_room_that_refused_an_anonymous_bot_needs_sign_in():
    assert rm.verdict([refused(timedelta(days=2))], NOW) == rm.NEEDS_SIGN_IN


def test_a_room_that_admitted_an_anonymous_bot_stays_anonymous():
    assert rm.verdict([admitted(timedelta(days=2))], NOW) == rm.ANONYMOUS_OK


def test_the_latest_decisive_outcome_wins():
    assert rm.verdict([refused(timedelta(days=3)), admitted(timedelta(days=1))], NOW) == rm.ANONYMOUS_OK
    assert rm.verdict([admitted(timedelta(days=3)), refused(timedelta(days=1))], NOW) == rm.NEEDS_SIGN_IN


def test_a_signed_in_bots_outcome_never_decides():
    assert rm.verdict([refused(timedelta(days=1)), admitted(timedelta(hours=1), signed_in=True)], NOW) == rm.NEEDS_SIGN_IN
    assert rm.verdict([refused(timedelta(hours=1), signed_in=True)], NOW) == rm.UNSEEN


def test_outcomes_older_than_the_window_are_forgotten():
    assert rm.verdict([refused(timedelta(days=31))], NOW) == rm.UNSEEN


def test_a_refusal_recorded_without_a_respawn_block_counts():
    row = {"status": "failed", "end_time": iso(NOW - timedelta(hours=2)),
           "data": {"completion_reason": "platform_join_refused"}}
    assert rm.verdict([row], NOW) == rm.NEEDS_SIGN_IN


def test_one_pods_refusal_then_its_anonymous_admission_reads_admitted():
    """The old 3-pod path on one row: pod 1 refused, pod 2 (anonymous) admitted later → anonymous_ok."""
    row = {"status": "completed", "start_time": iso(NOW - timedelta(hours=1)),
           "data": {"meet_respawn": {"attempts": [{"at": iso(NOW - timedelta(hours=1, minutes=2))}]}}}
    assert rm.verdict([row], NOW) == rm.ANONYMOUS_OK


def test_room_verdict_reads_every_persons_rows_of_that_room_only():
    repo = InMemoryMeetingRepo()
    a = asyncio.run(repo.create_meeting(user_id=1, platform="google_meet", native_meeting_id="aaa-bbbb-ccc",
                                        data=refused(timedelta(days=1))["data"]))
    repo._meetings[a["id"]]["created_at"] = iso(NOW - timedelta(days=1))
    other = asyncio.run(repo.create_meeting(user_id=2, platform="google_meet", native_meeting_id="zzz-zzzz-zzz",
                                            data=refused(timedelta(days=1))["data"]))
    repo._meetings[other["id"]]["created_at"] = iso(NOW - timedelta(days=1))
    assert asyncio.run(rm.room_verdict(repo, "google_meet", "aaa-bbbb-ccc", NOW)) == rm.NEEDS_SIGN_IN
    assert asyncio.run(rm.room_verdict(repo, "google_meet", "qqq-qqqq-qqq", NOW)) == rm.UNSEEN
    assert asyncio.run(rm.room_verdict(repo, "zoom", "aaa-bbbb-ccc", NOW)) is None


def test_a_lookup_that_fails_reads_unseen_never_fails_the_spawn():
    class Broken:
        async def room_history(self, *a):
            raise RuntimeError("db down")
    assert asyncio.run(rm.room_verdict(Broken(), "google_meet", "aaa-bbbb-ccc", NOW)) == rm.UNSEEN


# ── resolve_for_spawn follows the room ──────────────────────────────────────────────────────────────

class _Broker:
    def __init__(self, accounts):
        self.accounts = accounts

    async def list(self, owners, actor):
        return [a for a in self.accounts if a["owner"] in owners]


class _Admin:
    async def roles(self, user_id):
        return {"is_admin": True}


def _ba(accounts):
    from meeting_api.bot_accounts.service import BotAccounts
    ba = BotAccounts.__new__(BotAccounts)
    ba.broker, ba.admin, ba.policy = _Broker(accounts), _Admin(), (lambda: "admins")
    return ba


def _acct(n, owner="company", verified="2026-11-13T09:00:00Z"):
    return {"id": f"{n:032x}", "owner": owner, "platform": "google", "state": "ok", "verified_at": verified}


def test_a_default_meet_join_follows_the_room():
    ba = _ba([_acct(1), _acct(2, verified="2026-11-13T10:00:00Z")])
    run = lambda room, busy=None: asyncio.run(ba.resolve_for_spawn(7, "google_meet", None, room=room, busy=busy))
    assert run(rm.UNSEEN) is None
    assert run(rm.ANONYMOUS_OK) is None
    assert run(rm.NEEDS_SIGN_IN)["id"] == f"{2:032x}"           # newest first


def test_a_busy_account_is_skipped_and_all_busy_is_a_409():
    from meeting_api.bot_accounts.service import BotAccountRefused
    ba = _ba([_acct(1), _acct(2, verified="2026-11-13T10:00:00Z")])

    async def two_busy(a):
        return a["id"] == f"{2:032x}"

    async def all_busy(a):
        return True
    got = asyncio.run(ba.resolve_for_spawn(7, "google_meet", "default", room=rm.NEEDS_SIGN_IN, busy=two_busy))
    assert got["id"] == f"{1:032x}"
    with pytest.raises(BotAccountRefused) as e:
        asyncio.run(ba.resolve_for_spawn(7, "google_meet", "default", room=rm.NEEDS_SIGN_IN, busy=all_busy))
    assert e.value.status == 409


def test_the_callers_own_account_still_comes_first_and_none_means_anonymous():
    own = _acct(9, owner="user:7")
    ba = _ba([_acct(1), own])
    assert asyncio.run(ba.resolve_for_spawn(7, "google_meet", None, room=rm.UNSEEN))["id"] == own["id"]
    assert asyncio.run(ba.resolve_for_spawn(7, "google_meet", "none", room=rm.NEEDS_SIGN_IN)) is None


def test_many_signed_in_rows_do_not_push_the_refusal_out():
    rows = [admitted(timedelta(hours=h), signed_in=True) for h in range(1, 120)] + [refused(timedelta(days=6))]
    assert len(rows) < rm.HISTORY_LIMIT and rm.verdict(rows, NOW) == rm.NEEDS_SIGN_IN

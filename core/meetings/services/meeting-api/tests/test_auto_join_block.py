"""Auto-join block lists — a gated organisation's meeting never gets an auto-joined bot.

The sweep refuses a due row when its organiser, an invitee or the meeting link's host matches the
deployment's ``VEXA_AUTO_JOIN_BLOCK`` or the owner's ``auto_join_block`` (from bot-context). Every
refusal is stamped on the row, never silent. Offline, over the in-memory fakes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from meeting_api.bot_spawn.auto_join import auto_join_tick
from meeting_api.bot_spawn.auto_join_block import (
    blocked_reason, deployment_block, normalize_entry, parse_block,
)
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

USER = 7
NOW = datetime(2026, 7, 10, 15, 0, 0, tzinfo=timezone.utc)


def _event(organiser: str) -> dict:
    return {"resolved_start": NOW.isoformat(),
            "component": {"name": "VEVENT", "properties": {
                "ORGANIZER": [{"value": f"mailto:{organiser}", "parameters": {"CN": "Host"}}]}}}


def _seed(repo, *, mid=1, organiser="host@partner.example", attendees=(), platform="teams",
          native="1234567890123", url="https://teams.microsoft.com/l/meetup-join/x"):
    repo._meetings[mid] = {
        "id": mid, "user_id": USER, "platform": platform,
        "native_meeting_id": native, "platform_specific_id": native,
        "status": "scheduled", "bot_container_id": None, "start_time": None, "end_time": None,
        "data": {"title": "t", "auto_join": True, "scheduled_at": (NOW + timedelta(seconds=30)).isoformat(),
                 "constructed_meeting_url": url,
                 "attendees": [{"email": a} for a in attendees],
                 "calendar_sources": [{"id": "c1", "uid": "u1", "auto_join": True,
                                       "event": _event(organiser)}]},
        "created_at": "2026-07-08T09:00:00Z", "updated_at": "2026-07-08T09:00:00Z",
    }
    return mid


async def _tick(repo, runtime, *, ctx=None, deployment=None, allow_uncapped=False):
    async def fetch(_uid):
        return ctx
    return await auto_join_tick(
        repo, runtime, transcribe_gate=lambda: None, now=NOW, token_secret="s",
        redis_url="redis://r", fetch_bot_context=(fetch if ctx is not None else None),
        allow_uncapped=allow_uncapped, deployment_block=deployment)


async def test_a_deployment_blocked_organiser_domain_is_never_auto_joined():
    # Deny: the old sweep spawned this bot (no block list existed).
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo, organiser="Host@Gated.Example")
    counters = await _tick(repo, runtime, ctx={"max_concurrent": 5},
                           deployment=parse_block("gated.example"))
    assert runtime.specs == []
    assert counters["blocked"] == 1 and counters["spawned"] == 0
    row = repo._meetings[mid]
    assert row["status"] == "scheduled"
    assert "gated.example" in row["data"]["auto_join_error"]
    assert "organiser" in row["data"]["auto_join_error"]


async def test_an_owner_blocked_invitee_subdomain_is_never_auto_joined():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo, attendees=["me@vexa.example", "someone@dept.gated.example"])
    counters = await _tick(repo, runtime, ctx={"max_concurrent": 5,
                                               "auto_join_block": ["gated.example"]})
    assert runtime.specs == [] and counters["blocked"] == 1
    assert "invitee" in repo._meetings[mid]["data"]["auto_join_error"]


async def test_a_blocked_meeting_link_host_is_never_auto_joined():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, platform="jitsi", native="Standup", url="https://meet.gated.example/Standup")
    counters = await _tick(repo, runtime, ctx={"max_concurrent": 5},
                           deployment=parse_block("gated.example"))
    assert runtime.specs == [] and counters["blocked"] == 1


async def test_an_exact_address_entry_blocks_only_that_address():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, mid=1, organiser="ceo@partner.example")
    _seed(repo, mid=2, organiser="other@partner.example", native="9999999999999")
    counters = await _tick(repo, runtime, ctx={"max_concurrent": 5,
                                               "auto_join_block": ["ceo@partner.example"]})
    assert counters["blocked"] == 1 and counters["spawned"] == 1
    assert len(runtime.specs) == 1


async def test_an_unrelated_meeting_still_joins():
    # Negative control: the lists change nothing for a meeting that touches no blocked party, and
    # a domain is not matched as a substring (notgated.example is not gated.example).
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, organiser="host@notgated.example", attendees=["a@partner.example"])
    counters = await _tick(repo, runtime, ctx={"max_concurrent": 5,
                                               "auto_join_block": ["gated.example"]},
                           deployment=parse_block("gated.example"))
    assert counters["spawned"] == 1 and counters["blocked"] == 0


async def test_the_deployment_list_holds_even_without_an_admin_edge():
    # Uncapped self-host (no identity) still honours the operator's list.
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, organiser="x@gated.example")
    counters = await _tick(repo, runtime, deployment=parse_block("gated.example"),
                           allow_uncapped=True)
    assert runtime.specs == [] and counters["blocked"] == 1


async def test_an_unreadable_owner_list_refuses_instead_of_being_ignored():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo)
    counters = await _tick(repo, runtime, ctx={"max_concurrent": 5,
                                               "auto_join_block": ["not a domain!"]})
    assert runtime.specs == [] and counters["blocked"] == 1
    assert "could not be read" in repo._meetings[mid]["data"]["auto_join_error"]


@pytest.mark.parametrize("entry,expected", [
    ("Example.COM", "example.com"), ("@example.com", "example.com"),
    ("*.example.com", "example.com"), ("Person@Example.com", "person@example.com"),
])
def test_entries_normalize(entry, expected):
    assert normalize_entry(entry) == expected


@pytest.mark.parametrize("entry", ["localhost", "example", "http://example.com", "a@b", "*", "ex ample.com"])
def test_a_bad_entry_is_refused(entry):
    with pytest.raises(ValueError):
        parse_block([entry])


def test_a_bad_deployment_list_refuses_the_boot():
    from meeting_api.__main__ import _auto_join_block_at_boot
    from meeting_api.config_preflight import ConfigError

    with pytest.raises(ConfigError, match="VEXA_AUTO_JOIN_BLOCK"):
        _auto_join_block_at_boot({"VEXA_AUTO_JOIN_BLOCK": "gated.example, *"})
    assert deployment_block("gated.example ceo@partner.example").emails == {"ceo@partner.example"}


def test_no_list_blocks_nothing():
    assert blocked_reason({"data": {}}, None, parse_block("")) is None


def test_the_organiser_is_read_from_what_calendar_sync_actually_stores():
    # The row shape above is not a guess: parse an ICS event and build its source exactly as
    # calendar sync does, then match it.
    from meeting_api.calendar_sync.service import _source_entry, parse_ics

    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nBEGIN:VEVENT\r\nUID:e1\r\n"
           "DTSTART:20261020T090000Z\r\nSUMMARY:s\r\n"
           "LOCATION:https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0\r\n"
           "ORGANIZER;CN=Host:mailto:Host@Gated.Example\r\nATTENDEE;CN=X:mailto:x@other.example\r\n"
           "END:VEVENT\r\nEND:VCALENDAR\r\n")
    ev = parse_ics(ics, now=datetime(2026, 10, 19, tzinfo=timezone.utc))["events"][0]
    row = {"data": {"attendees": ev["attendees"],
                    "calendar_sources": [_source_entry("c", "C", ev["uid"], True, metadata=ev["metadata"])],
                    "constructed_meeting_url": ev["meeting_url"]}}
    assert "organiser" in blocked_reason(row, parse_block("gated.example"))
    assert "invitee" in blocked_reason(row, parse_block("other.example"))
    assert blocked_reason(row, parse_block("nothing.example")) is None

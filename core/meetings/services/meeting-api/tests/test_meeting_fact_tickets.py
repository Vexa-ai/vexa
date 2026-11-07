"""Every meeting fact meeting-api hands flows carries a meetings subject ticket for its owner.

The gateway door refuses a flows call that acts for a person unless a ticket a producer signed
names that person; for post-meeting work that producer is meeting-api. These tests verify the
ticket with the matching PUBLIC key (`subject_ticket.read`), exactly as the door does, and pin the
missing-key profile: the fact is still published, without a ticket, and the log says why.
"""
from __future__ import annotations

import logging

import pytest

from meeting_api import events as events_mod
from meeting_api import identity_token, subject_ticket


@pytest.fixture()
def published(monkeypatch):
    sent = []

    async def fake(event_type, source_event_id, refs, **kw):
        sent.append({"event_type": event_type, "source_event_id": source_event_id, "refs": refs})
        return True

    monkeypatch.setattr(events_mod, "publish", fake)
    monkeypatch.setattr(events_mod, "_ticket_signer_cache", {})
    return sent


@pytest.fixture()
def key(tmp_path, monkeypatch):
    k = identity_token.generate_signing_key()
    path = tmp_path / "meetings.pem"
    path.write_bytes(identity_token.private_key_pem(k))
    monkeypatch.setenv(events_mod.TICKET_KEY_ENV, str(path))
    return k


def _checks(k):
    pub = k.public_key()

    def check(message, signature):
        try:
            pub.verify(signature, message)
            return True
        except Exception:  # noqa: BLE001
            return False

    return {subject_ticket.MEETINGS: check}


def _only_ticket(refs):
    tickets = subject_ticket.tickets_of(refs)
    assert len(tickets) == 1, refs
    return tickets[0]


async def test_meeting_completed_carries_an_owner_ticket_the_public_key_verifies(published, key):
    await events_mod.publish_meeting_completed(9, "abc-defg-hij", "google_meet", 17, "stopped")
    [fact] = published
    claims = subject_ticket.read(_only_ticket(fact["refs"]), _checks(key))
    assert claims["iss"] == "meetings" and claims["kind"] == "event"
    assert claims["sub"] == "17" == fact["refs"]["uid"]
    assert claims["mid"] == "9"
    assert claims["native"] == "google_meet/abc-defg-hij"
    assert claims["evt"] == "done-9" == fact["source_event_id"]
    assert set(claims["cls"]) == {"transcript.read", "chat.autonomous", "desk.write",
                                  "room.bills_to"}
    assert claims["exp"] - claims["iat"] == 3 * 86400


async def test_meeting_started_carries_an_owner_ticket(published, key):
    await events_mod.publish_meeting_started(9, "abc-defg-hij", "google_meet", 17)
    claims = subject_ticket.read(_only_ticket(published[0]["refs"]), _checks(key))
    assert (claims["sub"], claims["mid"], claims["evt"]) == ("17", "9", "live-9")


async def test_meeting_shared_ticket_names_the_owner_flows_mints_the_link_as(published, key):
    await events_mod.publish_meeting_shared(9, 17, "guest@example.com", "g-1", title="Sync")
    [fact] = published
    claims = subject_ticket.read(_only_ticket(fact["refs"]), _checks(key))
    assert claims["sub"] == "17" == fact["refs"]["uid"]
    assert claims["mid"] == "9" and "native" not in claims
    assert claims["evt"] == fact["source_event_id"]
    assert fact["refs"]["email"] == "guest@example.com"


async def test_a_ticket_from_another_key_does_not_verify(published, key):
    await events_mod.publish_meeting_completed(9, "n", "zoom", 17, "stopped")
    with pytest.raises(subject_ticket.TicketError) as e:
        subject_ticket.read(_only_ticket(published[0]["refs"]),
                            _checks(identity_token.generate_signing_key()))
    assert e.value.reason == "bad_signature"


async def test_no_key_publishes_without_a_ticket_and_says_the_door_will_refuse(
        published, monkeypatch, caplog):
    monkeypatch.delenv(events_mod.TICKET_KEY_ENV, raising=False)
    with caplog.at_level(logging.WARNING, logger="meeting_api.events"):
        await events_mod.publish_meeting_completed(9, "n", "zoom", 17, "stopped")
        await events_mod.publish_meeting_completed(10, "n", "zoom", 17, "stopped")
    assert len(published) == 2
    assert all(subject_ticket.REFS_KEY not in f["refs"] for f in published)
    warned = [r.getMessage() for r in caplog.records if "subject_ticket_key_missing" in r.getMessage()]
    assert len(warned) == 1 and "subject_ticket_required" in warned[0]


async def test_an_unreadable_key_publishes_without_a_ticket(published, monkeypatch, tmp_path, caplog):
    monkeypatch.setenv(events_mod.TICKET_KEY_ENV, str(tmp_path / "absent.pem"))
    with caplog.at_level(logging.WARNING, logger="meeting_api.events"):
        await events_mod.publish_meeting_started(9, "n", "zoom", 17)
    assert len(published) == 1 and subject_ticket.REFS_KEY not in published[0]["refs"]
    assert any("subject_ticket_key_unreadable" in r.getMessage() for r in caplog.records)

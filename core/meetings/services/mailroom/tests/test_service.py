"""L2/L3 — the loop's properties: idempotency, resume-safety, fail-safe, and workspace resolution.

The corpus asserts *what each message does*. This asserts the things a corpus cannot: that the
same message twice does it once, that a restart does not re-do it, that a refusing control plane
produces a notice instead of a lie, and that resolution happens on the invited address alone.
"""
from __future__ import annotations

import json

import pytest
from conftest import NOW, WORKSPACE_ADDRESS, WORKSPACE_ID, FakeMailSource, FakeMeetingApi, envelope, read_ics

from vexa_mailroom import FileStore, Mailroom, MemoryStore, normalize_address
from vexa_mailroom.service import Outcome


async def test_same_invitation_twice_creates_one_meeting(source, meetings, store, mailroom):
    """The same UID+SEQUENCE delivered twice (a resend, a dual delivery) acts once."""
    source.add(envelope(read_ics("google-request-meet.ics"), message_id="<a>"))
    source.add(envelope(read_ics("google-request-meet.ics"), message_id="<b>"))
    result = await mailroom.poll_once()
    assert result.counts == {"created": 1, "duplicate": 1}
    assert len(meetings.of("create")) == 1
    assert len(await store.all()) == 1


async def test_cursor_resumes_and_never_reacts(source, meetings, store):
    """A restart re-reads a tail; the seen list makes the overlap a no-op, not a second meeting."""
    mailroom = Mailroom(source=source, meetings=meetings, store=store, notices=store,
                        workspaces={WORKSPACE_ADDRESS: WORKSPACE_ID}, now=lambda: NOW)
    source.add(envelope(read_ics("google-request-meet.ics"), message_id="<a>"))
    await mailroom.poll_once()
    assert await store.cursor() == source.messages[0].created

    # A second poll asks only for what is newer, and does nothing.
    second = await mailroom.poll_once()
    assert second.outcomes == []
    assert source.fetches[-1] == source.messages[0].created

    # A fresh process over the SAME state (the store is the durable half) re-reads nothing.
    restarted = Mailroom(source=source, meetings=meetings, store=store, notices=store,
                         workspaces={WORKSPACE_ADDRESS: WORKSPACE_ID}, now=lambda: NOW)
    assert (await restarted.poll_once()).outcomes == []
    assert len(meetings.of("create")) == 1


async def test_file_store_survives_a_restart(tmp_path, meetings):
    """The binding + cursor are durable: a new process on the same file re-acts on nothing."""
    path = tmp_path / "state.json"
    source = FakeMailSource()
    source.add(envelope(read_ics("google-request-meet.ics"), message_id="<a>"))

    first_store = FileStore(path)
    first = Mailroom(source=source, meetings=meetings, store=first_store, notices=first_store,
                     workspaces={WORKSPACE_ADDRESS: WORKSPACE_ID}, now=lambda: NOW)
    assert (await first.poll_once()).counts == {"created": 1}

    written = json.loads(path.read_text())
    assert written["cursor"] == source.messages[0].created
    assert written["bindings"][0]["uid"] == "google-one-off-001@google.com"

    second_store = FileStore(path)
    second = Mailroom(source=source, meetings=meetings, store=second_store, notices=second_store,
                      workspaces={WORKSPACE_ADDRESS: WORKSPACE_ID}, now=lambda: NOW)
    assert (await second.poll_once()).outcomes == []
    assert len(meetings.of("create")) == 1


async def test_a_seen_message_is_skipped_even_when_the_cursor_rewinds(source, meetings, store, mailroom):
    """Two messages in the same second: the cursor alone would drop one, the seen list saves it."""
    source.add(envelope(read_ics("google-request-meet.ics"), message_id="<a>"),
               created="2026-08-16T09:00:00Z")
    source.add(envelope(read_ics("zoom-request.ics"), message_id="<b>"),
               created="2026-08-16T09:00:00Z")
    result = await mailroom.poll_once()
    assert result.counts == {"created": 2}
    # The source hands both back (their stamp is not > the cursor) — neither is acted on again.
    await store.set_cursor(None, await store.seen())
    assert (await mailroom.poll_once()).outcomes == []


async def test_a_forwarded_invite_does_not_bind(source, meetings, store, mailroom):
    """The ATTENDEE list is authoritative; the SMTP envelope is not.

    An invitation that reached the mailbox by forward or BCC never named us. Binding on the
    envelope would let anyone put a bot into a third party's meeting by forwarding its invite.
    It reached us, so the organizer gets an explanation — it did not invite us, so nothing binds.
    """
    source.add(envelope(read_ics("negative-not-invited.ics"), to=WORKSPACE_ADDRESS,
                        message_id="<a>"))
    result = await mailroom.poll_once()
    assert result.counts == {"rejected": 1}
    assert meetings.calls == []
    assert list(await store.all()) == []
    notices = await store.recent()
    assert [n.reason for n in notices] == ["unknown_workspace"]
    assert notices[0].message_id == "msg-001"
    assert notices[0].to == "organizer@example.com"      # the notice is addressed to the organizer


async def test_a_message_that_never_reached_us_is_silent(source, meetings, store, mailroom):
    """Neither invited nor delivered to us: no binding AND no notice — not ours to answer."""
    source.add(envelope(read_ics("negative-not-invited.ics"), to="someone@example.com",
                        message_id="<a>"))
    result = await mailroom.poll_once()
    assert result.counts == {"ignored": 1}
    assert meetings.calls == []
    assert list(await store.recent()) == []


async def test_refusing_control_plane_produces_a_notice_not_a_binding(source, meetings, store, mailroom):
    """A 409/422 from the control plane is a notice — never a binding claiming a meeting exists."""
    meetings.create_error = "409: A meeting already exists for google_meet/abc-defg-hij"
    source.add(envelope(read_ics("google-request-meet.ics"), message_id="<a>"))
    result = await mailroom.poll_once()
    assert result.counts == {"failed": 1}
    assert list(await store.all()) == []
    assert [n.reason for n in await store.recent()] == ["meeting_api_refused"]


async def test_cancel_on_an_fsm_owned_row_is_reported_not_claimed(source, meetings, store, mailroom):
    """When the row is no longer planned, the binding retires AND the operator is told."""
    source.add(envelope(read_ics("google-request-meet.ics"), message_id="<a>"))
    await mailroom.poll_once()
    meetings.cancel_result = False
    source.add(envelope(read_ics("google-cancel.ics"), message_id="<b>"))
    result = await mailroom.poll_once()
    assert result.counts == {"cancelled": 1}
    assert result.outcomes[0].reason == "row_not_planned"
    assert [n.reason for n in await store.recent()] == ["meeting_api_refused"]


async def test_a_newer_request_after_a_cancel_rebinds(source, meetings, store, mailroom):
    """Reinstating a called-off meeting is real: a HIGHER sequence after a cancel binds again."""
    source.add(envelope(read_ics("google-request-meet.ics"), message_id="<a>"))
    await mailroom.poll_once()
    source.add(envelope(read_ics("google-cancel.ics"), message_id="<b>"))     # SEQUENCE:1
    await mailroom.poll_once()

    reinstated = read_ics("google-request-meet.ics").replace("SEQUENCE:0", "SEQUENCE:2")
    source.add(envelope(reinstated, message_id="<c>"))
    result = await mailroom.poll_once()
    assert result.counts == {"created": 1}
    binding = await store.get(WORKSPACE_ID, "google-one-off-001@google.com")
    assert binding.state == "active" and binding.sequence == 2
    assert len(meetings.of("create")) == 2       # a fresh planned row; the cancelled one is gone


async def test_cancel_for_an_unbound_series_changes_nothing(source, meetings, store, mailroom):
    source.add(envelope(read_ics("google-cancel.ics"), message_id="<a>"))
    result = await mailroom.poll_once()
    assert result.counts == {"ignored": 1}
    assert result.outcomes[0].reason == "no_binding"
    assert meetings.calls == []
    assert list(await store.recent()) == []      # nothing to notify about — we were never coming


async def test_one_bad_message_never_stops_the_batch(source, meetings, store, mailroom):
    """A message that explodes is a notice and the batch continues — the loop is not a chain."""
    source.add(b"To: mk-dev@dev.vexa.ai\r\nContent-Type: text/calendar\r\n\r\nBEGIN:VCAL",
               id="msg-bad")
    source.add(envelope(read_ics("google-request-meet.ics"), message_id="<good>"))
    result = await mailroom.poll_once()
    assert [o.action for o in result.outcomes] == ["rejected", "created"]
    assert len(meetings.of("create")) == 1


async def test_two_workspaces_resolve_independently(source, meetings, store):
    """The map is the resolution: two addresses, two workspaces, no inference between them."""
    mailroom = Mailroom(source=source, meetings=meetings, store=store, notices=store,
                        workspaces={WORKSPACE_ADDRESS: WORKSPACE_ID,
                                    "other-workspace@dev.vexa.ai": "ws-other"},
                        now=lambda: NOW)
    source.add(envelope(read_ics("google-request-meet.ics"), message_id="<a>"))
    source.add(envelope(read_ics("negative-not-invited.ics"), to="someone@example.com",
                        message_id="<b>"))
    result = await mailroom.poll_once()
    assert result.counts == {"created": 2}
    assert {b.workspace_id for b in await store.all()} == {WORKSPACE_ID, "ws-other"}
    assert {c["workspace_id"] for c in meetings.of("create")} == {WORKSPACE_ID, "ws-other"}


@pytest.mark.parametrize("raw,expected", [
    ("MK-Dev@Dev.Vexa.AI", "mk-dev@dev.vexa.ai"),
    ("mk-dev+notes@dev.vexa.ai", "mk-dev@dev.vexa.ai"),
    ("  mk-dev@dev.vexa.ai  ", "mk-dev@dev.vexa.ai"),
    ("not-an-address", "not-an-address"),
])
def test_address_normalization(raw, expected):
    assert normalize_address(raw) == expected


async def test_poll_result_shape(source, meetings, store, mailroom):
    """``PollResult.as_dict`` is what the operator route renders — keep it JSON-able."""
    source.add(envelope(read_ics("google-request-meet.ics"), message_id="<a>"))
    result = await mailroom.poll_once()
    payload = json.loads(json.dumps(result.as_dict()))
    assert payload["counts"] == {"created": 1}
    assert payload["outcomes"][0]["action"] == "created"
    assert payload["cursor"] == source.messages[0].created
    assert isinstance(Outcome("m", "created").as_dict(), dict)

"""A room's planned call follows its creator's calendar (PLAN-8H 8.4): the calendar sensor's changes
are handed to agent-api's room door as the owner. Fake transport only: no calendar, no network."""
from __future__ import annotations

from flows_sensors import planned_sync as ps


def test_a_moved_event_is_handed_on_as_the_owner_with_its_new_time():
    sent = []
    code = ps.on_change("calendar.event.updated", {"uid": "7", "event_id": "evt1", "start": "2026-10-20T09:00:00Z",
                                                    "end": "2026-10-20T09:30:00Z", "title": "x"},
                        post=lambda uid, path, body: sent.append((uid, path, body)) or 200)
    assert code == 200
    assert sent == [("7", "/api/rooms/calendar-sync", {"event_id": "evt1", "status": "updated",
                                                       "start": "2026-10-20T09:00:00Z", "end": "2026-10-20T09:30:00Z"})]


def test_a_cancel_is_handed_on_without_a_time():
    sent = []
    ps.on_change("calendar.event.cancelled", {"uid": "7", "event_id": "evt1", "start": "x"},
                 post=lambda uid, path, body: sent.append(body) or 200)
    assert sent == [{"event_id": "evt1", "status": "cancelled"}]


def test_a_created_event_or_one_without_an_owner_is_not_a_sync():
    calls = []
    post = lambda *a: calls.append(a) or 200  # noqa: E731
    assert ps.on_change("calendar.event.created", {"uid": "7", "event_id": "e"}, post=post) is None
    assert ps.on_change("calendar.event.updated", {"event_id": "e"}, post=post) is None
    assert ps.on_change("mail.received", {"uid": "7"}, post=post) is None
    assert calls == []


def test_a_failure_never_raises():
    def boom(*_a):
        raise OSError("down")
    assert ps.on_change("calendar.event.cancelled", {"uid": "7", "event_id": "e"}, post=boom) == 0

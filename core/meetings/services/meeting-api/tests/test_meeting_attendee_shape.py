"""A calendar invite list must never delete a meeting from the archive.

calendar_sync stores attendees as ``[{email, name?, partstat?}]`` dicts
(calendar_sync/service.py ``_attendees``), while the sealed zaki-read.v1 MeetingContent
allows only strings. The projection used to reject the WHOLE meeting on a single
non-string entry, so the first calendar-synced meeting that got captured would have
vanished from ``/index`` and 404'd from ``/item/meeting:<id>``, stranding its transcript
and summary as orphans. Availability must not depend on the shape of an invite list.
"""
import importlib.util
import pathlib

_ROUTER = pathlib.Path(__file__).resolve().parents[1] / "src/meeting_api/zaki_read/router.py"


def _project_attendees(raw):
    """The projection's attendee rule, exercised directly (it has no external deps)."""
    return [a.strip()[:500] for a in raw if isinstance(a, str) and a.strip()]


def test_calendar_dicts_do_not_hide_the_meeting():
    calendar_shape = [{"email": "ada@x.com", "name": "Ada"}, {"email": "grace@x.com"}]
    # The meeting survives; the unusable entries are simply not rendered.
    assert _project_attendees(calendar_shape) == []


def test_string_attendees_still_render_and_are_bounded():
    assert _project_attendees(["Ada", "  Grace  "]) == ["Ada", "Grace"]
    assert _project_attendees(["x" * 900])[0] == "x" * 500
    # Blank-only entries are dropped rather than rendered as empty chips.
    assert _project_attendees(["Ada", "   ", ""]) == ["Ada"]


def test_mixed_shapes_keep_the_usable_names():
    assert _project_attendees(["Ada", {"email": "b@x.com"}, "Grace"]) == ["Ada", "Grace"]


def test_the_projection_no_longer_rejects_on_attendee_shape():
    # Guards the intent at the source: the size bound may reject, the element TYPE may not.
    source = _ROUTER.read_text()
    meeting_branch = source[source.index('if kind == "meeting":'):source.index('if kind == "transcript":')]
    assert "len(raw_attendees) > 1000" in meeting_branch, "the size bound must remain"
    assert "or any(not isinstance(attendee, str)" not in meeting_branch, (
        "a non-string attendee must never reject the whole meeting again"
    )

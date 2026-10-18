"""A calendar event carries its meeting link (founder 2026-10-16: the agent could not find the Zoom
link of an event whose Zoom came through Calendar's Zoom add-on — it lives in conferenceData)."""
import httpx

from credential_broker import providers

ZOOM_ADDON = {
    "id": "e1", "summary": "Gizem / CircleCI", "description": "Booked by someone",
    "start": {"dateTime": "2026-10-16T12:00:00+01:00"}, "end": {"dateTime": "2026-10-16T12:30:00+01:00"},
    "location": None,
    "conferenceData": {
        "conferenceSolution": {"key": {"type": "addOn"}, "name": "Zoom Meeting"},
        "entryPoints": [
            {"entryPointType": "video", "uri": "https://us02web.zoom.us/j/81234567890?pwd=abc", "label": "us02web.zoom.us/j/81234567890"},
            {"entryPointType": "phone", "uri": "tel:+1-555-0100"},
        ],
    },
}


def _events(*items):
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"items": list(items)}))) as c:
        return providers.read_account("google_calendar", {"access_token": "x"}, "calendar.events",
                                      time_min="2026-10-16T00:00:00Z", time_max="2026-10-17T00:00:00Z", http=c)["events"]


def test_a_zoom_add_on_event_carries_its_meeting_url():
    (e,) = _events(ZOOM_ADDON)
    assert e["meeting_url"] == "https://us02web.zoom.us/j/81234567890?pwd=abc"


def test_the_conference_entry_point_wins_then_hangout_then_text():
    both = {**ZOOM_ADDON, "hangoutLink": "https://meet.google.com/abc-defg-hij"}
    assert _events(both)[0]["meeting_url"].startswith("https://us02web.zoom.us/")
    meet = {"id": "e2", "summary": "x", "hangoutLink": "https://meet.google.com/abc-defg-hij"}
    assert _events(meet)[0]["meeting_url"] == "https://meet.google.com/abc-defg-hij"
    text = {"id": "e3", "summary": "x", "description": "Join: https://teams.microsoft.com/l/meetup-join/19%3a1 thanks"}
    assert _events(text)[0]["meeting_url"].startswith("https://teams.microsoft.com/")
    none = {"id": "e4", "summary": "x", "description": "lunch"}
    assert _events(none)[0].get("meeting_url") is None

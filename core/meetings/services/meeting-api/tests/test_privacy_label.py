"""D6: meeting-api labels transcripts, meetings, recordings and in-call chat private."""
from meeting_api.privacy_label import label_for


def test_meeting_data_is_private_and_bot_acts_are_not():
    for path in ("/transcripts/search", "/transcripts/google_meet/abc", "/meetings", "/meetings/1",
                 "/meetings/google_meet/abc/participants", "/recordings/3", "/bots/google_meet/abc/chat"):
        assert label_for(path) == b"private; class=transcript", path
    for path in ("/bots", "/bots/status", "/bots/google_meet/abc", "/bot-accounts", "/health"):
        assert label_for(path) == b"none", path

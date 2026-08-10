"""A Teams id that cannot be joined must not become a URL.

A joinable Teams deep link is ``…/l/meetup-join/19:meeting_<b64>@thread.v2/0?context={…}`` — the
thread id, plus a context naming tenant and organizer. The numeric dial-in style meeting id a person
reads off an invite carries neither, and formatting it into the template produces a URL that Teams
answers with a redirect to ``login.microsoftonline.com/…/oauth2/v2.0/authorize``.

What that costs, observed on production 2026-07-20 (#501): the bot navigates to the login page, then
every pre-join step reports its control "not found" because it is probing a login form, admission
polls 31s and finds no admitted/rejected/lobby indicator, and the pod exits 1. The caller sees a
spawned bot and no transcript, with nothing saying the link was never joinable. 8 of 34 visible bot
pods were in this state.

The rule is the one already written above ``_URL_TEMPLATES`` for jitsi — a bare id does not identify
a joinable meeting — and it applies harder to Teams: a jitsi room name at least resolves to SOME
room, while a Teams id without its thread resolves to nothing.
"""
from meeting_api.bot_spawn.service import construct_meeting_url, is_joinable_teams_id


def test_numeric_meeting_id_is_not_joinable():
    # The exact id from the production failures.
    assert not is_joinable_teams_id("389141384648269")
    assert construct_meeting_url("teams", "389141384648269") is None


def test_thread_id_constructs_plain_and_url_encoded():
    for nid in ("19:meeting_ZGZhYjc@thread.v2", "19%3ameeting_ZGZhYjc%40thread.v2"):
        assert is_joinable_teams_id(nid)
        url = construct_meeting_url("teams", nid)
        assert url and url.endswith(nid)


def test_empty_and_junk_are_refused():
    for nid in ("", "   ", "meeting", "thread.v2"):      # 'thread.v2' alone lacks the 19: prefix
        assert construct_meeting_url("teams", nid) is None


def test_other_platforms_are_untouched():
    # The Teams guard must not change what any other platform does.
    assert construct_meeting_url("google_meet", "abc-defg-hij") == "https://meet.google.com/abc-defg-hij"
    assert construct_meeting_url("zoom", "1234567890") is None     # needs an explicit meeting_url
    assert construct_meeting_url("jitsi", "some-room") is None     # deployment-scoped, by design

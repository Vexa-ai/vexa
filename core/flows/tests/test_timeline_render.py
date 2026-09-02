"""The timeline in a person's own zone — the rendering both readers share (PRD decision 31).

The control-MCP tool and the dispatch preamble render the SAME payload through these functions, so
what is pinned here is pinned for both.
"""
from __future__ import annotations

from flows_timeline.render import clock, line, render_preamble, render_text, when

# 2026-09-02 11:23:05Z — the DNA invite. Lisbon is UTC+1 in September, so the two zones disagree,
# which is the point: a stamp that reads the same in both proves nothing.
INVITE = 1_788_348_185.0
NOW = 1_788_362_400.0                  # same day, 15:20Z / 16:20 in Lisbon
TOMORROW = NOW + 86_400
LISBON = "Europe/Lisbon"


def _payload(events):
    return {"now": "2026-09-02T15:20:00Z", "now_epoch": NOW, "events": events}


def _ev(at, kind, title, status="done", produced=None, meeting_id=None):
    e = {"at_epoch": at, "kind": kind, "title": title, "status": status,
         "produced": produced or {}}
    if meeting_id:
        e["meeting_id"] = meeting_id
    return e


def test_a_time_always_carries_its_zone():
    """Never a bare HH:MM — the Lisbon standup that joined at '19:15' when it was 17:15."""
    assert clock(INVITE, LISBON) == "12:23 WEST"
    assert clock(INVITE, "") == "11:23 UTC"
    assert clock(INVITE, "Not/AZone") == "11:23 UTC"     # a bad setting degrades, never raises


def test_another_day_is_dated_not_just_clocked():
    today = "2026-09-02"
    assert when(INVITE, LISBON, today) == "12:23 WEST"
    assert when(TOMORROW, LISBON, today).startswith("Thu 03 Sep ")


def test_today_is_the_persons_today_not_the_servers():
    """23:30 UTC is already tomorrow in Lisbon. The day boundary that matters is theirs."""
    late = 1_788_391_800.0                                # 2026-09-02 23:30Z → 00:30 on the 3rd
    assert when(late, LISBON, "2026-09-02").startswith("Thu 03 Sep ")
    assert when(late, "", "2026-09-02") == "23:30 UTC"


def test_a_line_hides_the_boring_status_and_shows_the_interesting_one():
    ok = line(_ev(INVITE, "report.delivered", "ASWF DNA TSC"), LISBON, "2026-09-02")
    skipped = line(_ev(INVITE, "report.delivered", "ASWF DNA TSC", status="skipped"),
                   LISBON, "2026-09-02")
    assert "[done]" not in ok and "[skipped]" in skipped


def test_a_line_shows_what_the_event_produced():
    got = line(_ev(INVITE, "report.delivered", "DNA", produced={"link": "https://app/x"}),
               LISBON, "2026-09-02")
    assert "https://app/x" in got


def test_render_text_states_now_first_and_splits_at_it():
    out = render_text(_payload([_ev(INVITE, "invite.received", "ASWF DNA TSC"),
                                _ev(NOW + 3600, "meeting.scheduled", "Standup", "scheduled")]),
                      LISBON)
    lines = out.splitlines()
    assert lines[0].startswith("now  Wed 02 Sep  16:20 WEST")
    assert out.index("already happened") < out.index("still ahead")
    assert out.index("ASWF DNA TSC") < out.index("Standup")


def test_render_text_says_the_zone_is_unset_rather_than_pretending_utc_is_theirs():
    out = render_text(_payload([]), "")
    assert "no timezone set" in out.splitlines()[0]
    assert "nothing in this window" in out and "nothing scheduled" in out


def test_the_preamble_is_a_handful_of_lines_capped_both_ways():
    events = [_ev(NOW - i * 600, "mail.sent", f"past-{i}") for i in range(9, 0, -1)]
    events += [_ev(NOW + i * 600, "meeting.scheduled", f"next-{i}", "scheduled")
               for i in range(1, 10)]
    out = render_preamble(_payload(events), LISBON)
    assert "past-5" in out and "past-1" in out and "past-6" not in out
    assert "next-1" in out and "next-5" in out and "next-6" not in out


def test_the_preamble_states_now_and_the_zone_rule():
    out = render_preamble(_payload([_ev(INVITE, "invite.received", "ASWF DNA TSC")]), LISBON)
    assert "**Now: Wednesday 02 September 2026, 16:20 WEST.**" in out
    assert "timeline(since, until)" in out                  # the deeper read is named
    assert "12:23 WEST" in out                              # the event, in THEIR zone


def test_an_empty_preamble_says_so_rather_than_implying_a_history():
    out = render_preamble(_payload([]), LISBON)
    assert "Nothing recorded" in out


def test_no_payload_renders_nothing_at_all():
    """A heading over an empty list teaches the model the section is noise."""
    assert render_preamble({}, LISBON) == ""
    assert render_preamble({"events": []}, LISBON) == ""

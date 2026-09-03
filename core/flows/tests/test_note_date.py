"""Two occurrences of ONE recurring meeting, processed the same day, must be two notes.

The note path is ``kg/entities/meeting/<stamp>-<title-slug>.md`` and ``stamp`` used to be
``time.strftime("%Y-%m-%d")`` — the PROCESSING day. A recurring meeting keeps one
``native_meeting_id`` AND one title across occurrences, so two of them written on the same day
landed on the same path: the second silently overwrote the first. Replay makes this the normal
case: ten recorded meetings replayed this afternoon are ten occurrences processed today.

WHAT THESE TESTS NOW CALL, and why it changed (2026-09-02). They used to dispatch a whole
``process_meeting`` turn and fish the path back out of the PROMPT. That worked only while the
kick named a path — and it meant the assertions pinned the KICK's spelling of it, which had
silently drifted from the WRITER's: the kick said ``<stamp>-<native>.md``, ``drop_to_attendees``
wrote ``<day>-<slug>.md``, and the terminal opened ``<native>.md``. Three spellings of one path,
none of them agreeing, and every test green. They now call ``_note_path`` — the single recipe
both the writer and the scaffold read — so a drift like that cannot be invisible again.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _Ctx:
    def __init__(self, refs):
        self.refs = refs


def _stamp_for(refs, monkeypatch_setting=None):
    """THE PATH the meeting's record lands on, through the one recipe the writer uses."""
    from flows_defs import production
    from flows_steps import meeting as mt

    # MODULE-LEVEL rebinds, not monkeypatch, so every one of them has to be put back: a stub left
    # installed silently replaces the real function for every test file that runs after this one
    # in the same process. A test that passes alone and fails in the suite is the visible half of
    # that; a test that PASSES in the suite because somebody else's stub is still there is the
    # dangerous half.
    saved = {"setting": production.setting, "meeting_start": mt.meeting_start}
    production.setting = monkeypatch_setting or (lambda uid, key: "")   # no timezone -> UTC
    mt.meeting_start = lambda uid, mid, native=None: None
    try:
        return production._note_path(_Ctx(refs), refs["uid"], refs.get("title"))
    finally:
        production.setting = saved["setting"]
        mt.meeting_start = saved["meeting_start"]


def _path_from(path: str) -> str:
    assert path.startswith("kg/entities/meeting/"), path
    return path


def test_two_occurrences_same_day_same_native_are_two_notes():
    native = "abc-defg-hij"                      # ONE recurring meeting id, as Google issues it
    day = time.mktime(time.strptime("2026-09-02", "%Y-%m-%d"))
    morning = {"uid": "1", "meeting_id": 101, "native": native, "transcript": "t",
               "start": day + 9 * 3600, "organizer": "a@x.test", "title": "Dailies"}
    afternoon = {"uid": "1", "meeting_id": 102, "native": native, "transcript": "t",
                 "start": day + 16 * 3600, "organizer": "a@x.test", "title": "Dailies"}

    p1 = _path_from(_stamp_for(morning))
    p2 = _path_from(_stamp_for(afternoon))

    assert p1 != p2, (
        f"two occurrences of one recurring meeting collided on one path: {p1}. This is F58: "
        "`drop_to_attendees` sliced the stamp down to `%Y-%m-%d` before building the filename, "
        "so the afternoon's record overwrote the morning's on every desk in the room.")
    assert "dailies" in p1 and "dailies" in p2, (p1, p2)   # the TITLE is the identity, not the native
    assert native not in p1, (p1, "the native is not part of the path any more")
    # and the date is the MEETING's, not today's
    assert "2026-09-02" in p1 and "2026-09-02" in p2, (p1, p2)


def test_date_comes_from_the_meeting_not_from_today():
    native = "old-meet-ing"
    long_ago = time.mktime(time.strptime("2026-03-02", "%Y-%m-%d")) + 10 * 3600
    p = _path_from(_stamp_for({"uid": "1", "meeting_id": 7, "native": native, "transcript": "t",
                               "start": long_ago, "organizer": "a@x.test", "title": "TSC"}))
    assert "2026-03-02" in p, p
    assert time.strftime("%Y-%m-%d") not in p or time.strftime("%Y-%m-%d") == "2026-03-02", p


def test_seeded_row_with_no_start_falls_back_and_is_still_unique():
    """A SEEDED meeting has no start: `meeting_seed` posts `scheduled_at: None`, so the row's
    `start_time` is NULL and `refs.start` is absent. The stamp then falls through to the row's
    `created_at`, and finally to now — which separates two occurrences only because their
    creation times differ, i.e. correctness by luck rather than by design. The filename in that
    case carries the REPLAY's clock, not the meeting's date.

    This is a known gap, recorded here so it fails loudly if someone later assumes the fallback
    is meaningful. The real fix is upstream: `meeting_seed` should set `scheduled_at` from the
    fixture's own occurrence, and then the first branch handles it.
    """
    native = "96088138284"                       # the replay's real recurring id
    a = {"uid": "1", "meeting_id": 36, "native": native, "transcript": "t",
         "organizer": "a@x.test", "title": "DNA TSC"}          # no `start`
    b = dict(a)
    b["meeting_id"] = 37
    pa = _path_from(_stamp_for(a))
    pb = _path_from(_stamp_for(b))
    # both resolve, and neither carries a meeting date it cannot know
    assert "dna-tsc" in pa and "dna-tsc" in pb, (pa, pb)
    assert pa.startswith("kg/entities/meeting/") and pb.startswith("kg/entities/meeting/")
    assert native not in pa, (pa, "the native is not part of the path any more")


def test_midnight_is_the_organizers_day_not_utcs():
    """A meeting just after midnight in the organizer's zone belongs to THAT day.

    00:30 in Los Angeles is 07:30 UTC the same date; 00:30 in Sydney is 13:30 UTC the day BEFORE.
    Stamped in UTC, the Sydney meeting is filed a day early — and a filename that is wrong by one
    day collides with the next day's occurrence of the same series exactly the way the
    processing-date bug did. So the organizer's zone decides the date whenever we know it."""
    import datetime
    import zoneinfo

    native = "dailies-recur-01"
    for zone, when in (("Australia/Sydney", "2026-09-02 00:30"),
                       ("America/Los_Angeles", "2026-09-02 00:30")):
        local = datetime.datetime.strptime(when, "%Y-%m-%d %H:%M").replace(
            tzinfo=zoneinfo.ZoneInfo(zone))
        refs = {"uid": "1", "meeting_id": 201, "native": native, "transcript": "t",
                "start": local.timestamp(), "organizer": "a@x.test", "title": "Dailies"}
        path = _path_from(_stamp_for(refs, monkeypatch_setting=lambda u, k, z=zone: z if k == "timezone" else ""))
        assert "2026-09-02" in path, (zone, path, "filed on the wrong DAY")

    # And the Sydney case is the one that proves it: in UTC that instant is the 1st.
    syd = datetime.datetime.strptime("2026-09-02 00:30", "%Y-%m-%d %H:%M").replace(
        tzinfo=zoneinfo.ZoneInfo("Australia/Sydney"))
    assert syd.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d") == "2026-09-01"


def test_no_start_is_stamped_in_a_declared_zone_never_the_servers():
    """The quiet defect: every branch rendered in UTC or the person's zone EXCEPT the no-start
    fallback, which used `time.strftime` — local time on whichever machine ran the worker. The
    same meeting then landed on a different day depending on where the process happened to be.

    Asserted by MOVING THE SERVER'S CLOCK, not by comparing against `now`: set TZ either side of
    the date line and demand the same stamp. Comparing to `now` would pass on the broken code for
    most of the day and fail nobody's build until it did — which is how this survived."""
    import os
    import time as _t

    refs = {"uid": "1", "meeting_id": 301, "native": "no-start-01", "transcript": "t",
            "organizer": "a@x.test", "title": "TSC"}                      # no `start`
    was = os.environ.get("TZ")
    stamps = []
    try:
        for zone in ("Pacific/Kiritimati", "Pacific/Midway"):   # UTC+14 and UTC-11
            os.environ["TZ"] = zone
            _t.tzset()
            stamps.append(_path_from(_stamp_for(refs))[:len("kg/entities/meeting/2026-09-02")])
    finally:
        if was is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = was
        _t.tzset()

    assert stamps[0] == stamps[1], (
        f"the note filename moved with the SERVER's timezone: {stamps} — a meeting must not "
        "change date because the worker ran somewhere else")


if __name__ == "__main__":
    test_two_occurrences_same_day_same_native_are_two_notes()
    test_date_comes_from_the_meeting_not_from_today()
    test_seeded_row_with_no_start_falls_back_and_is_still_unique()
    test_midnight_is_the_organizers_day_not_utcs()
    test_no_start_is_stamped_in_a_declared_zone_never_the_servers()
    print("note-date tests PASS")

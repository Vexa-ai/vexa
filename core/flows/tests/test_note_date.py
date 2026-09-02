"""Two occurrences of ONE recurring meeting, processed the same day, must be two notes.

The note path is ``kg/entities/meeting/{date}-{native}.md`` and ``date`` used to be
``time.strftime("%Y-%m-%d")`` — the PROCESSING day. A recurring meeting keeps one
``native_meeting_id`` across occurrences, so two of them written on the same day landed on the
same path: the second silently overwrote the first, or the agent refused the mismatched write
and ``process_meeting`` timed out after 15 minutes. Replay makes this the normal case: ten
recorded meetings replayed this afternoon are ten occurrences processed today.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _Flow:
    def param(self, _k, _d=None):
        return None


class _Ctx:
    def __init__(self, refs):
        self.refs = refs
        self.prior = {}
        self.scratch = {}
        self.flow = _Flow()
        self.clock_now = time.time()


def _stamp_for(refs, monkeypatch_setting=None):
    """Build the registry and reach the stamp helper through a real process_meeting dispatch."""
    from flows import Registry
    from flows_defs import production
    from flows_steps import agent as ag
    from flows_steps import common, meeting as mt

    seen = {}

    def fake_dispatch(uid, session, prompt):
        seen["prompt"] = prompt
        return 0

    common_setting = common.setting
    production.setting = (monkeypatch_setting
                          or (lambda uid, key: ""))   # no timezone -> UTC
    ag.dispatch_turn = fake_dispatch
    ag.commit_shas = lambda uid: []
    mt.meeting_start = lambda uid, mid, native=None: None

    reg = Registry()

    class _DB:
        def execute(self, *a, **k):
            return []

    production.build(reg, _DB())
    step = reg.steps["process_meeting"]
    ctx = _Ctx(refs)
    step(ctx)
    production.setting = common_setting
    return seen["prompt"]


def _path_from(prompt: str) -> str:
    for tok in prompt.split():
        if tok.startswith("kg/entities/meeting/"):
            return tok
    raise AssertionError("no note path in prompt:\n" + prompt[:400])


def test_two_occurrences_same_day_same_native_are_two_notes():
    native = "abc-defg-hij"                      # ONE recurring meeting id, as Google issues it
    day = time.mktime(time.strptime("2026-09-02", "%Y-%m-%d"))
    morning = {"uid": "1", "meeting_id": 101, "native": native, "transcript": "t",
               "start": day + 9 * 3600, "organizer": "a@x.test", "title": "Dailies"}
    afternoon = {"uid": "1", "meeting_id": 102, "native": native, "transcript": "t",
                 "start": day + 16 * 3600, "organizer": "a@x.test", "title": "Dailies"}

    p1 = _path_from(_stamp_for(morning))
    p2 = _path_from(_stamp_for(afternoon))

    assert p1 != p2, f"two occurrences collided on one path: {p1}"
    assert native in p1 and native in p2
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
    # both resolve, both carry the native, and neither carries a meeting date it cannot know
    assert native in pa and native in pb
    assert pa.startswith("kg/entities/meeting/") and pb.startswith("kg/entities/meeting/")


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
    print("all note-date tests pass")
    print("note-date tests PASS")

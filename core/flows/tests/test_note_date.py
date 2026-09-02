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
    production.setting = lambda uid, key: ""          # no timezone -> UTC
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


if __name__ == "__main__":
    test_two_occurrences_same_day_same_native_are_two_notes()
    test_date_comes_from_the_meeting_not_from_today()
    print("note-date tests PASS")

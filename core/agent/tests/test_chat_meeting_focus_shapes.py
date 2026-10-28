"""Chat grounding folds exactly the meeting row its access check approved, whatever the focus shape.

The meeting focus arrives from the client in several shapes — flat (`context.focus`), wrapped
(`context.focus.meeting`), and the legacy `active` field in both — and any level may carry extra
keys. The access check and the fold must read the SAME row: the grounding is built from the
checked server row alone, so no client key survives into what is folded.

Offline: fakeredis holds two transcripts; the access check approves only the caller's own row.
"""
from __future__ import annotations

import json

import fakeredis
import pytest

from control_plane.api import ChatBody, ChatContextBody, _context_grounding, _meeting_grounding

OWN, OTHER = "7101", "7102"
OWN_LINE = "our roadmap review"
OTHER_LINE = "the acquisition closes on Friday"


@pytest.fixture
def fake_redis(monkeypatch):
    import redis
    r = fakeredis.FakeRedis(decode_responses=True)
    for row, line in ((OWN, OWN_LINE), (OTHER, OTHER_LINE)):
        r.xadd(f"tc:meeting:{row}", {"payload": json.dumps({"type": "transcription", "segments": [
            {"segment_id": f"s-{row}", "speaker": "Ana", "text": line}]})})
    monkeypatch.setattr(redis, "from_url", lambda *a, **k: r)
    return r


def _own_row(status="completed"):
    return {"id": int(OWN), "status": status, "platform": "google_meet",
            "native_meeting_id": "own-defg-hij", "data": {"title": "Roadmap review"}}


def _access(seen=None):
    """The caller's access check: their own row and nothing else."""
    def check(mid):
        if seen is not None:
            seen.append(mid)
        return _own_row() if mid == OWN else None
    return check


def _level(row, *, status="completed", native=None, **extra):
    return {"meeting_id": row, "native_id": native or f"n-{row}", "platform": "google_meet",
            "status": status, **extra}


def _ground_context(focus, access):
    body = ChatBody(prompt="what was decided?", context=ChatContextBody(focus=focus))
    return _context_grounding(body, "s1", "redis://fake", schedule_rows=lambda: [],
                              workspace_mounts=lambda: [], meeting_access=access)[2]


def _ground_legacy(active, access):
    body = ChatBody(prompt="what was decided?", active=active)
    return _context_grounding(body, "s1", "redis://fake", schedule_rows=lambda: [],
                              workspace_mounts=lambda: [], meeting_access=access)[2]


GROUNDERS = pytest.mark.parametrize("ground", [_ground_context, _ground_legacy],
                                    ids=["context.focus", "legacy active"])


# ── the shapes a meeting focus arrives in ────────────────────────────────────────────────────────

@GROUNDERS
def test_flat_focus_folds_the_callers_own_row(fake_redis, ground):
    prompt = ground({"kind": "meeting", **_level(OWN)}, _access())
    assert OWN_LINE in prompt and OTHER_LINE not in prompt


@GROUNDERS
def test_wrapped_focus_folds_the_callers_own_row(fake_redis, ground):
    prompt = ground({"kind": "meeting", "meeting": _level(OWN)}, _access())
    assert OWN_LINE in prompt and OTHER_LINE not in prompt


@GROUNDERS
@pytest.mark.parametrize("focus", [
    {"kind": "meeting", **_level(OTHER)},
    {"kind": "meeting", "meeting": _level(OTHER)},
], ids=["flat", "wrapped"])
def test_a_row_the_caller_cannot_read_is_never_folded(fake_redis, ground, focus):
    prompt = ground(focus, _access())
    assert OTHER_LINE not in prompt and OWN_LINE not in prompt
    assert prompt == "what was decided?"


@GROUNDERS
@pytest.mark.parametrize("status", ["completed", "active", ""])
def test_a_second_meeting_nested_under_the_checked_level_is_ignored(fake_redis, ground, status):
    """The level that is checked is the level that is folded: a `meeting` object nested inside it
    names nothing."""
    focus = {"kind": "meeting", "meeting": _level(OWN, status=status, meeting=_level(OTHER))}
    prompt = ground(focus, _access())
    assert OTHER_LINE not in prompt
    assert OWN_LINE in prompt


@GROUNDERS
def test_a_nested_meeting_three_levels_down_is_ignored(fake_redis, ground):
    focus = {"kind": "meeting", "meeting": _level(OWN, meeting={"meeting": _level(OTHER)})}
    prompt = ground(focus, _access())
    assert OTHER_LINE not in prompt and OWN_LINE in prompt


@GROUNDERS
def test_a_flat_focus_carrying_a_nested_meeting_checks_and_folds_one_row(fake_redis, ground):
    """`{kind, meeting_id: own, meeting: {...other}}`: the wrapped level is the one that names the
    meeting, so it is the one checked — and refused — and the caller's own top-level id folds
    nothing either."""
    seen: list = []
    focus = {"kind": "meeting", **_level(OWN), "meeting": _level(OTHER)}
    prompt = ground(focus, _access(seen))
    assert seen == [OTHER]
    assert OTHER_LINE not in prompt and OWN_LINE not in prompt


@GROUNDERS
def test_a_planned_meeting_reads_no_transcript_even_with_a_nested_meeting(fake_redis, ground):
    """The prep phase reads nothing server-side and is not checked. A live or ended meeting nested
    under a planned one must not turn it into a fold."""
    seen: list = []
    focus = {"kind": "meeting", "meeting": _level(OWN, status="scheduled",
                                                  meeting=_level(OTHER, status="completed"))}
    prompt = ground(focus, _access(seen))
    assert seen == []
    assert OTHER_LINE not in prompt and OWN_LINE not in prompt
    assert "PREPARE" in prompt


# ── what is folded comes from the server row, not the client ─────────────────────────────────────

def test_the_folded_title_and_native_id_are_the_server_rows(fake_redis):
    focus = {"kind": "meeting", "meeting": _level(OWN, native="client-native", title="Client title")}
    prompt = _ground_context(focus, _access())
    assert "Roadmap review" in prompt and "own-defg-hij" in prompt
    assert "Client title" not in prompt and "client-native" not in prompt


def test_the_server_rows_status_decides_the_phase_for_a_nested_focus(fake_redis):
    planned = {**_own_row(status="scheduled")}
    focus = {"kind": "meeting", "meeting": _level(OWN, status="active")}
    prompt = _ground_context(focus, lambda mid: planned if mid == OWN else None)
    assert OWN_LINE not in prompt and "PREPARE" in prompt


@pytest.mark.parametrize("bad", [["7102"], {"id": "7102"}, 7102.5, True])
def test_a_meeting_id_that_is_not_a_scalar_row_id_is_refused(fake_redis, bad):
    seen: list = []
    focus = {"kind": "meeting", "meeting": {**_level(OWN), "meeting_id": bad, "native_id": None}}
    prompt = _ground_context(focus, _access(seen))
    assert OTHER_LINE not in prompt and OWN_LINE not in prompt
    assert all(isinstance(s, str) and s.isdigit() for s in seen)


def test_the_renderer_reads_only_the_level_it_is_given(fake_redis):
    """`_meeting_grounding` folds the row named at its own level; a nested `meeting` is not read."""
    flat = {"kind": "meeting", **_level(OWN), "meeting": _level(OTHER)}
    _c, _t, prompt = _meeting_grounding(flat, "s1", "what was decided?", "redis://fake")
    assert OWN_LINE in prompt and OTHER_LINE not in prompt

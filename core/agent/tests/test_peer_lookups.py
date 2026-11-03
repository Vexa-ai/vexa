"""The meeting access decision asks its lookup one way: as the caller, with the caller's workspaces.

`peer_lookups.meeting_access_check` and `meeting_transcript_reader` call the injected lookup as
``(subject, meeting_id, workspaces)``. There is no second, narrower calling convention to detect, so
a fault raised inside a lookup reaches the caller as itself.
"""
from __future__ import annotations

import pytest

from control_plane.peer_lookups import meeting_access_check, meeting_transcript_reader


def test_the_caller_s_workspaces_are_always_handed_to_the_lookup(tmp_path):
    seen = []

    def lookup(subject, meeting_id, workspaces=None):
        seen.append((subject, meeting_id, workspaces))
        return {"id": meeting_id}

    assert meeting_access_check(lookup, tmp_path)("u1", "10") == {"id": "10"}
    assert meeting_transcript_reader(lookup, tmp_path)("u1", "10") == {"id": "10"}
    assert seen == [("u1", "10", []), ("u1", "10", [])]


def test_a_lookup_that_cannot_take_the_workspaces_is_a_fault_not_a_narrower_answer(tmp_path):
    def two_args(subject, meeting_id):
        return {"id": meeting_id}

    with pytest.raises(TypeError):
        meeting_access_check(two_args, tmp_path)("u1", "10")


def test_a_fault_inside_the_lookup_is_not_swallowed(tmp_path):
    def broken(subject, meeting_id, workspaces=None):
        raise TypeError("a bug inside the lookup")

    with pytest.raises(TypeError, match="a bug inside the lookup"):
        meeting_access_check(broken, tmp_path)("u1", "10")

"""Each meeting list_meetings returns carries its FULL REFERENCE (founder 2026-10-18: "we need full
reference for everything"; many meetings are called the same). The agent copies `ref` into its reply,
where it renders as a badge with the meeting time; the id is never read out."""
from __future__ import annotations

from vexa_mcp.app import with_meeting_refs


def test_every_meeting_row_carries_its_reference():
    out = with_meeting_refs({"meetings": [
        {"id": 201, "data": {"title": "Call with Vexa"}},
        {"id": 202, "data": {"title": "Call | with ] Vexa"}},
        {"id": 203, "data": {}},
        {"id": 204, "ref": "kept"},
    ]})
    refs = [m["ref"] for m in out["meetings"]]
    assert refs == ["[[meeting:201|Call with Vexa]]", "[[meeting:202|Call   with   Vexa]]", "[[meeting:203]]", "kept"]


def test_anything_else_passes_through():
    assert with_meeting_refs({"detail": "x"}) == {"detail": "x"}
    assert with_meeting_refs(None) is None

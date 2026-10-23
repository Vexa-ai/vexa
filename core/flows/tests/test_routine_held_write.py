"""A routine run whose write was HELD for the person's confirm ends done, not failed (app.dev
2026-10-18, uid 2 `ait-lisbon-freshness`: a 45-minute run that worked ended `failed` with
"refused: private_sources_to_shared (entity_upsert)", and the founder read it as routines not
working). The hold itself is agent-api's (D6); this only decides how the run ends and what it says."""
from __future__ import annotations

from test_routine_shared_pause import _run, _version  # noqa: E402 — same harness

HELD = {"state": "done", "ok": True, "acted": ["workspace_write", "entity_upsert"], "failed": [],
        "refused": {"tool": "mcp__vexa__entity_upsert", "reason": "private_sources_to_shared",
                    "count": 2}}


def test_a_held_write_ends_the_run_done_with_one_line_naming_what_waits(monkeypatch):
    api, db, *_rest, status, reason = _run(monkeypatch, HELD)
    assert status == "done", reason
    assert len(api.notices) == 1
    assert api.notices[0].startswith("Ran. 2 writes are waiting for your approval")
    assert "Approve it in this chat" in api.notices[0]
    assert _version(db)[0] == "active", "a held write never pauses the routine"


def test_a_real_refusal_still_fails_the_run(monkeypatch):
    other = {**HELD, "refused": {"tool": "request_meeting_bot", "reason": "human_session_required",
                                 "count": 1}}
    api, db, *_rest, status, reason = _run(monkeypatch, other)
    assert status == "failed" and "human_session_required" in reason

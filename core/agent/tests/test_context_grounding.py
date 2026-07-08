"""_context_grounding — the terminal-state context-bundle orchestrator (slice 1).

Prompt = [ambient <schedule> digest (surface-gated)] + [focus fold] + user prompt.
Covers: the ambient gate matrix (explicit toggle beats surface), meeting-focus SERVER-ROW
enrichment (a cold client store must not ground a planned meeting as live — the regression this
slice fixes), workspace focus (fail-closed on unknown slug), today focus (full-day digest
replaces ambient), and back-compat (legacy ``active``-only bodies behave exactly as before;
``context.focus: null`` suppresses grounding).
"""
from __future__ import annotations

from types import SimpleNamespace

from control_plane.api import ChatBody, ChatContextBody, _ambient_gated, _context_grounding


def _body(prompt="hi", active=None, context=None):
    return ChatBody(prompt=prompt, active=active,
                    context=ChatContextBody(**context) if context is not None else None)


def _ground(body, *, rows=None, mounts=None):
    return _context_grounding(
        body, "s1", None,
        schedule_rows=lambda: rows or [],
        workspace_mounts=lambda: mounts or [],
    )


def _sched_row(rid=51, status="scheduled", title="Acme intro", native="abc-defg-hij"):
    return {"id": rid, "status": status, "platform": "google_meet", "native_meeting_id": native,
            "data": {"title": title, "scheduled_at": "2026-07-09T09:00:00Z",
                     "workspace_id": "acme-deal"},
            "end_time": None, "start_time": None, "updated_at": None}


# ── ambient gate matrix ───────────────────────────────────────────────────────────────

def test_gate_no_context_is_off():
    assert _ambient_gated(None) is False


def test_gate_surface_meetings_list_on_doc_tab_off():
    assert _ambient_gated(ChatContextBody(surface={"list": "meetings"})) is True
    assert _ambient_gated(ChatContextBody(surface={"list": "files", "tab": {"kind": "doc"}})) is False
    for kind in ("today", "meeting", "meetingPrep"):
        assert _ambient_gated(ChatContextBody(surface={"tab": {"kind": kind}})) is True


def test_gate_explicit_toggle_beats_surface():
    on_surface = {"list": "meetings"}
    assert _ambient_gated(ChatContextBody(surface=on_surface, include={"schedule": False})) is False
    assert _ambient_gated(ChatContextBody(surface={"list": "files"}, include={"schedule": True})) is True


# ── ambient digest in the prompt ──────────────────────────────────────────────────────

def test_ambient_digest_prepended_when_gated():
    body = _body(context={"tz": "UTC", "surface": {"list": "meetings"}})
    _c, _t, prompt = _ground(body, rows=[_sched_row()])
    assert prompt.startswith("<schedule ")
    assert '"Acme intro"' in prompt
    assert "my next meeting" in prompt        # the schedule steering line
    assert prompt.endswith("hi")


def test_no_digest_when_gated_off_or_rows_empty():
    off = _body(context={"surface": {"list": "files"}})
    assert _ground(off, rows=[_sched_row()])[2] == "hi"
    on_empty = _body(context={"surface": {"list": "meetings"}})
    assert _ground(on_empty, rows=[])[2] == "hi"


def test_schedule_rows_failure_never_fails_the_turn():
    body = _body(context={"surface": {"list": "meetings"}})

    def boom():
        raise OSError("meeting-api down")

    _c, _t, prompt = _context_grounding(body, "s1", None, schedule_rows=boom,
                                        workspace_mounts=lambda: [])
    assert prompt == "hi"


# ── meeting focus: server-row enrichment (the cold-store regression fix) ──────────────

def test_planned_meeting_grounds_prep_even_with_statusless_client_focus():
    # client store was cold: no status/title sent — the SERVER row says scheduled
    focus = {"kind": "meeting", "native_id": "abc-defg-hij", "platform": "google_meet"}
    body = _body(context={"surface": {"tab": {"kind": "meetingPrep"}}, "focus": focus})
    _c, _t, prompt = _ground(body, rows=[_sched_row()])
    assert "PREPARE" in prompt                # prep steering, not the live fold
    assert '"Acme intro"' in prompt
    assert "acme-deal" in prompt


def test_linkless_planned_row_enriches_via_row_id_in_native_slot():
    """The terminal's tab param is the ROW id; a link-less planned meeting has NO native id, so
    the id arrives in native_id — enrichment must still find the row (the live-verify gap)."""
    row = {"id": 76, "status": "scheduled", "platform": "unknown", "native_meeting_id": None,
           "data": {"title": "Context bundle smoke", "scheduled_at": "2026-07-09T10:00:00Z"},
           "end_time": None, "start_time": None, "updated_at": None}
    focus = {"kind": "meeting", "native_id": "76", "platform": "google_meet"}
    body = _body(context={"surface": {"tab": {"kind": "meetingPrep"}}, "focus": focus})
    _c, _t, prompt = _ground(body, rows=[row])
    assert "PREPARE" in prompt and "Context bundle smoke" in prompt
    assert "live meeting" not in prompt


def test_client_status_loses_to_server_row():
    focus = {"kind": "meeting", "native_id": "abc-defg-hij", "platform": "google_meet",
             "status": "active"}              # client asserts live; server says scheduled
    body = _body(context={"focus": focus, "surface": {"tab": {"kind": "meeting"}}})
    _c, _t, prompt = _ground(body, rows=[_sched_row()])
    assert "PREPARE" in prompt


def test_meeting_focus_without_rows_falls_back_to_client_fields():
    focus = {"kind": "meeting", "native_id": "abc-defg-hij", "platform": "google_meet",
             "status": "scheduled", "title": "Client title"}
    body = _body(context={"focus": focus})
    _c, _t, prompt = _ground(body, rows=[])
    assert "PREPARE" in prompt and "Client title" in prompt


# ── workspace focus ───────────────────────────────────────────────────────────────────

def test_workspace_focus_folds_purpose_and_readme(tmp_path):
    ws = tmp_path / "acme-deal"
    ws.mkdir()
    (ws / "README.md").write_text("# Acme deal\nEverything about Acme.", encoding="utf-8")
    mount = SimpleNamespace(slug="acme-deal", workspace_id="acme-deal", name="Acme deal",
                            path=str(ws))
    body = _body(context={"focus": {"kind": "workspace", "slug": "acme-deal"}})
    _c, _t, prompt = _ground(body, mounts=[mount])
    assert 'workspace "Acme deal" (acme-deal)' in prompt
    assert "Everything about Acme." in prompt
    assert prompt.endswith("hi")


def test_workspace_focus_unknown_slug_folds_nothing():
    body = _body(context={"focus": {"kind": "workspace", "slug": "not-mine"}})
    assert _ground(body, mounts=[])[2] == "hi"


def test_workspace_focus_no_readme_is_honest(tmp_path):
    ws = tmp_path / "empty-ws"
    ws.mkdir()
    mount = SimpleNamespace(slug="empty-ws", workspace_id="empty-ws", name="Empty", path=str(ws))
    body = _body(context={"focus": {"kind": "workspace", "slug": "empty-ws"}})
    _c, _t, prompt = _ground(body, mounts=[mount])
    assert "no README yet" in prompt


# ── today focus ───────────────────────────────────────────────────────────────────────

def test_today_focus_uses_full_day_digest_and_replaces_ambient():
    body = _body(context={"tz": "UTC", "surface": {"tab": {"kind": "today"}},
                          "focus": {"kind": "today"}})
    row_past = {"id": 1, "status": "completed", "platform": "google_meet",
                "native_meeting_id": "x", "data": {"title": "standup"},
                "end_time": None, "start_time": None, "updated_at": None}
    _c, _t, prompt = _ground(body, rows=[_sched_row(), row_past])
    assert prompt.count("<schedule tz=") == 1  # ONE digest block (full-day), not ambient+focus
    assert "ended today:" not in prompt        # past row has no timestamps -> honest omission


# ── back-compat ───────────────────────────────────────────────────────────────────────

def test_legacy_active_only_body_unchanged():
    active = {"kind": "meeting", "native_id": "abc", "platform": "google_meet",
              "status": "scheduled", "title": "Legacy"}
    body = _body(active=active)               # no context at all
    _c, _t, prompt = _ground(body, rows=[])
    assert "PREPARE" in prompt and "Legacy" in prompt
    assert "<schedule" not in prompt          # legacy clients never get the digest


def test_context_focus_null_suppresses_legacy_active():
    active = {"kind": "meeting", "native_id": "abc", "platform": "google_meet"}
    body = _body(active=active, context={"focus": None, "surface": {"list": "files"}})
    assert _ground(body)[2] == "hi"


def test_file_focus_untouched():
    body = _body(context={"focus": {"kind": "file", "ref": "@file:notes.md"},
                          "surface": {"tab": {"kind": "doc"}}})
    assert _ground(body)[2] == "hi"

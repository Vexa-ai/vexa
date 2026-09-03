"""The rig's friction tools (#1510's C2/C3) — report_friction/friction_so_far/friction_dump/
friction_fixed all forward to flows-api directly now, not to agent-api's retired Redis store
(`test_rd21_friction_instance_wide_routes_are_gated` in `test_authorization.py` covers the
operator gate itself; this file covers the flows-forward shape)."""
from __future__ import annotations

import json

from conftest import STATE, as_user, tool
import vexa_control_mcp as rig


def test_report_friction_refuses_with_no_session(monkeypatch):
    as_user(monkeypatch, "126")
    out = json.loads(tool("report_friction")(
        session="", what_i_was_doing="reading the queue", what_went_wrong="bot_say returned 404"))
    assert "session" in out.get("error", "").lower()


def test_report_friction_posts_query_params_to_flows_with_the_operator_key_and_uid(monkeypatch):
    http = as_user(monkeypatch, "126", routes={
        "/friction": (201, {"id": "fr_abc123", "recorded": True}),
    })
    out = json.loads(tool("report_friction")(
        session="chat-42", what_i_was_doing="reading the queue",
        what_went_wrong="bot_say returned 404", tool="bot_say", severity="blocker"))
    assert out["recorded"] is True and out["published"] is True and out["id"] == "fr_abc123"
    call = [c for c in http.calls if c["url"].startswith(f"{rig.FLOWS_API}/friction?")][0]
    assert call["method"] == "POST"
    assert call["headers"]["X-Flows-Operator-Key"] == rig.FLOWS_KEY
    assert call["headers"]["X-User-Id"] == "126"
    assert "session=chat-42" in call["url"]
    assert "what_i_tried=reading" in call["url"]
    assert "severity=blocker" in call["url"]
    assert call["body"] is None


def test_report_friction_folds_workspace_path_and_error_into_the_free_text(monkeypatch):
    """flows' own route has no `workspace`/`path`/`scaffold_id`/`error` slot — the rig folds them
    into `tried`/`happened` rather than dropping them, since the report is a lead, not a form."""
    http = as_user(monkeypatch, "126", routes={"/friction": (201, {"id": "fr_1"})})
    tool("report_friction")(
        session="s1", what_i_was_doing="opened the desk README",
        what_went_wrong="the panel says no page here yet", workspace="dna", path="README.md",
        error="404")
    call = [c for c in http.calls if c["url"].startswith(f"{rig.FLOWS_API}/friction?")][0]
    import urllib.parse
    q = dict(urllib.parse.parse_qsl(call["url"].split("?", 1)[1]))
    assert "workspace=dna" in q["what_i_tried"] and "path=README.md" in q["what_i_tried"]
    assert "404" in q["what_happened"]


def test_report_friction_writes_the_local_ledger_first_even_when_flows_is_down(monkeypatch, tmp_path):
    monkeypatch.setattr(rig, "FRICTION_LOG", tmp_path / "friction.jsonl")
    as_user(monkeypatch, "126", routes={"/friction": (503, "flows is down")})
    out = json.loads(tool("report_friction")(
        session="s1", what_i_was_doing="x", what_went_wrong="y"))
    assert out["published"] is False
    assert out["recorded"] is True          # the local write still landed
    assert "y" in (tmp_path / "friction.jsonl").read_text()


def test_report_friction_notes_when_there_is_no_account_to_attribute_it_to(monkeypatch, tmp_path):
    monkeypatch.setattr(rig, "FRICTION_LOG", tmp_path / "friction.jsonl")
    http = as_user(monkeypatch, "126", routes={"/friction": (401, "no subject")})
    rig.CURRENT.set(None)     # as_user signs a uid in; undo it to exercise the anonymous path
    out = json.loads(tool("report_friction")(
        session="s1", what_i_was_doing="x", what_went_wrong="y"))
    assert "no account" in out.get("note", "")
    call = [c for c in http.calls if c["url"].startswith(f"{rig.FLOWS_API}/friction?")][0]
    assert "X-User-Id" not in call["headers"]


def test_friction_so_far_reads_the_callers_own_flows_reports(monkeypatch):
    http = as_user(monkeypatch, "126", routes={
        "/friction?": (200, {"count": 1, "reports": [{"id": "fr_1", "session": "s1"}]}),
    })
    out = json.loads(tool("friction_so_far")())
    assert out["count"] == 1 and out["reports"][0]["id"] == "fr_1"
    call = [c for c in http.calls if c["url"].startswith(f"{rig.FLOWS_API}/friction?")][0]
    assert call["method"] == "GET"
    assert call["headers"]["X-User-Id"] == "126"
    assert call["headers"]["X-Flows-Operator-Key"] == rig.FLOWS_KEY


def test_friction_dump_reads_the_whole_instance_and_groups_by_kind_and_tool(monkeypatch):
    rows = [
        {"id": "fr_1", "at": "2026-09-03T10:00:00Z", "at_epoch": 100.0, "subject": "126",
         "session": "s1", "severity": "annoyance", "tried": "a", "happened": "b",
         "context": {"kind": "error", "tool": "bot_say"}, "status": "open"},
        {"id": "fr_2", "at": "2026-09-03T10:05:00Z", "at_epoch": 200.0, "subject": "999",
         "session": "s2", "severity": "blocker", "tried": "c", "happened": "d",
         "context": {"kind": "error", "tool": "bot_say"}, "status": "open"},
        {"id": "fr_3", "at": "2026-09-03T10:01:00Z", "at_epoch": 150.0, "subject": "126",
         "session": "s1", "severity": "annoyance", "tried": "e", "happened": "f",
         "context": {"kind": "no-page"}, "status": "fixed"},
    ]
    as_user(monkeypatch, "126", admin=True, routes={
        "/friction?": (200, {"count": 3, "reports": rows}),
    })
    out = json.loads(tool("friction_dump")())      # status="open" default excludes fr_3
    assert out["count"] == 2
    finding = out["findings"][0]
    assert finding["kind"] == "error" and finding["tool"] == "bot_say" and finding["occurrences"] == 2
    assert finding["newest"]["id"] == "fr_2"        # the later at_epoch
    assert finding["also_ids"] == ["fr_1"]


def test_friction_fixed_forwards_to_the_flows_close_out_route(monkeypatch):
    http = as_user(monkeypatch, "126", admin=True, routes={
        "/friction/fr_1/fix": (201, {"id": "fr_1", "status": "fixed", "fix_ref": "PR #1"}),
    })
    out = json.loads(tool("friction_fixed")(["fr_1"], "PR #1"))
    assert out["closed"] == 1 and out["results"][0]["ok"] is True
    call = [c for c in http.calls if "/friction/fr_1/fix" in c["url"]][0]
    assert call["method"] == "POST"
    assert call["headers"]["X-Flows-Operator-Key"] == rig.FLOWS_KEY
    assert "fix_ref=PR" in call["url"]


def test_friction_fixed_requires_a_fix_ref(monkeypatch):
    as_user(monkeypatch, "126", admin=True)
    out = json.loads(tool("friction_fixed")(["fr_1"], "  "))
    assert "fix_ref is required" in out["error"]

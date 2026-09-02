"""The rough-edges loop (PRD decision 33) — shape, dedup, status, the dump, and the two hooks.

Every test here is offline: the record module is pure, the store's in-memory fallback needs no
redis, and the API tests drive the FastAPI app directly. The one thing that is NOT asserted is the
network — `worker.friction.report` is proven not to raise, which is the only property a `finally`
block around somebody's turn can depend on.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from control_plane import friction as store_mod
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from shared import friction as fr
from worker import friction as wfr

T0 = 1_788_400_000.0          # a fixed clock: every `at` in here is derived from it


# ── the record shape ─────────────────────────────────────────────────────────────────────────────

def test_normalize_accepts_todays_rig_arguments():
    """Backwards compatibility is not optional: the live machinery note names the old signature."""
    rec = fr.normalize({"what_i_was_doing": "reading the queue",
                        "what_went_wrong": "bot_say returned 404",
                        "what_would_have_helped": "a working bot_say",
                        "tool": "bot_say", "severity": "blocker", "uid": "126"}, now=T0)
    assert rec["tried"] == "reading the queue"
    assert rec["happened"] == "bot_say returned 404"
    assert rec["would_help"] == "a working bot_say"
    assert rec["context"]["tool"] == "bot_say"
    assert rec["severity"] == "blocker"
    assert rec["subject"] == "126"
    assert rec["reporter"] == "agent"


def test_normalize_carries_the_decision_33_shape():
    rec = fr.normalize({
        "reporter": "person", "subject": "126", "session": "meet-104", "kind": "no-page",
        "tried": "opened the desk README", "happened": "the panel says no page here yet",
        "context": {"workspace": "personal", "path": "README.md", "meeting_id": "104",
                    "scaffold_id": "sc1", "tool": "", "error": "404",
                    "surface": {"chat": "meet-104", "view": "README.md"}},
        "log_refs": [{"container": "c", "since": "2026-09-02T10:00:00", "grep": "README"}],
    }, now=T0)
    assert rec["reporter"] == "person" and rec["kind"] == "no-page"
    assert rec["context"]["surface"] == {"chat": "meet-104", "view": "README.md"}
    assert rec["log_refs"][0]["container"] == "c"


def test_an_unknown_kind_falls_back_and_is_never_a_refusal():
    """A reporter that cannot classify must still be able to file. Nothing here raises."""
    rec = fr.normalize({"kind": "catastrophe", "happened": "hmm"}, now=T0)
    assert rec["kind"] in fr.KINDS
    assert fr.normalize(None, now=T0)["kind"] == "other"
    assert fr.normalize({"happened": "no such tool as bot_teleport"}, now=T0)["kind"] == "missing-tool"


def test_unknown_context_keys_are_dropped():
    rec = fr.normalize({"context": {"path": "a.md", "api_key": "sk-secret"}}, now=T0)
    assert rec["context"] == {"path": "a.md"}
    assert "sk-secret" not in json.dumps(rec)


# ── dedup ────────────────────────────────────────────────────────────────────────────────────────

def test_dedup_ignores_volatile_ids_inside_the_error():
    """The same failure carrying a different meeting row is ONE edge, not two."""
    a = fr.normalize({"kind": "error", "tool": "meeting_transcript",
                      "context": {"tool": "meeting_transcript",
                                  "error": "500 while reading meeting 104"}}, now=T0)
    b = fr.normalize({"kind": "error", "tool": "meeting_transcript",
                      "context": {"tool": "meeting_transcript",
                                  "error": "500 while reading meeting 991"}}, now=T0)
    assert fr.dedup_key(a) == fr.dedup_key(b)


def test_dedup_separates_a_different_tool_and_a_different_failure():
    base = {"kind": "error", "context": {"tool": "bot_say", "error": "404 Not Found"}}
    other_tool = {"kind": "error", "context": {"tool": "bot_send", "error": "404 Not Found"}}
    other_err = {"kind": "error", "context": {"tool": "bot_say", "error": "503 upstream down"}}
    k = fr.dedup_key(fr.normalize(base, now=T0))
    assert k != fr.dedup_key(fr.normalize(other_tool, now=T0))
    assert k != fr.dedup_key(fr.normalize(other_err, now=T0))


def test_grouping_is_coarser_than_dedup():
    """Two different failures of one tool are two rows and ONE finding — a fixing agent opens that
    tool's code once."""
    rows = [fr.normalize({"kind": "error", "context": {"tool": "bot_say", "error": "404"}}, now=T0),
            fr.normalize({"kind": "error", "context": {"tool": "bot_say", "error": "503"}}, now=T0)]
    assert fr.dedup_key(rows[0]) != fr.dedup_key(rows[1])
    assert fr.group_key(rows[0]) == fr.group_key(rows[1])


# ── the status machine ───────────────────────────────────────────────────────────────────────────

def test_a_new_report_is_open_with_a_recurrence_of_one():
    rec = fr.apply_report(None, fr.normalize({"happened": "x"}, now=T0), now=T0)
    assert rec["status"] == "open" and rec["recurrence"] == 1 and rec["fix_ref"] == ""
    assert rec["first_at"] == T0


def test_a_repeat_counts_and_keeps_the_first_timestamp():
    first = fr.apply_report(None, fr.normalize({"happened": "x"}, now=T0), now=T0)
    again = fr.apply_report(first, fr.normalize({"happened": "x, and now also y"}, now=T0 + 60),
                            now=T0 + 60)
    assert again["recurrence"] == 2 and again["status"] == "open"
    assert again["first_at"] == T0 and again["at"] == T0 + 60
    assert again["happened"] == "x, and now also y"      # newest wording wins


def test_a_report_after_a_fix_flips_to_recurring_and_keeps_the_fix_reference():
    """The whole reason status exists: a fix that did not hold, and WHICH fix it was."""
    rec = fr.apply_report(None, fr.normalize({"happened": "x"}, now=T0), now=T0)
    rec = fr.apply_fix(rec, "abc1234", now=T0 + 10)
    assert rec["status"] == "fixed" and rec["fix_ref"] == "abc1234"
    rec = fr.apply_report(rec, fr.normalize({"happened": "x again"}, now=T0 + 20), now=T0 + 20)
    assert rec["status"] == "recurring"
    assert rec["fix_ref"] == "abc1234"
    assert rec["regressed_at"] == T0 + 20


def test_a_fix_without_a_reference_is_refused():
    rec = fr.apply_report(None, fr.normalize({"happened": "x"}, now=T0), now=T0)
    with pytest.raises(ValueError):
        fr.apply_fix(rec, "  ")


# ── log pointers and the dump ────────────────────────────────────────────────────────────────────

def test_log_pointers_are_derived_and_pasteable():
    rec = fr.apply_report(None, fr.normalize(
        {"kind": "error", "context": {"tool": "bot_say", "error": "404"}}, now=T0), now=T0)
    refs = fr.derived_log_refs(rec)
    assert refs and refs[0]["container"] == fr.GATEWAY      # bot_* crosses the gateway
    cmd = fr.log_command(refs[0])
    assert cmd.startswith("docker logs --since 2026-") and "grep -F 'bot_say'" in cmd


def test_a_reporter_supplied_pointer_is_believed():
    rec = fr.normalize({"log_refs": [{"container": "worker-abc", "since": "1h", "grep": "boom"}]},
                       now=T0)
    assert fr.derived_log_refs(rec)[0]["container"] == "worker-abc"


def test_the_dump_is_in_the_ledgers_finding_shape_and_names_how_to_close():
    rows = []
    for err in ("404 Not Found", "404 Not Found", "503 upstream"):
        rec = fr.apply_report(None, fr.normalize(
            {"kind": "error", "tried": "speak into the meeting", "happened": f"bot_say → {err}",
             "context": {"tool": "bot_say", "error": err, "meeting_id": "104"}}, now=T0), now=T0)
        rec["id"] = f"fr_{len(rows)}"
        rows.append(rec)
    md = fr.render_markdown(rows, since="2h", status="open", now=T0)
    assert "## FR-1 · error · `bot_say`" in md
    for label in ("**Symptom**", "**Tried**", "**Exact context**", "**Likely cause**",
                  "**Logs**", "**Repro**"):
        assert label in md
    assert "docker logs --since" in md and "grep -F 'bot_say'" in md
    assert 'friction_fixed(["fr_0"' in md or 'friction_fixed(["fr_2"' in md
    assert "not a diagnosis" in md          # a likely cause is a candidate, and the dump says so


def test_the_dump_sorts_recurring_first():
    old = fr.apply_report(None, fr.normalize(
        {"kind": "error", "context": {"tool": "a", "error": "x"}}, now=T0 + 100), now=T0 + 100)
    old["recurrence"] = 9
    regressed = fr.apply_report(None, fr.normalize(
        {"kind": "error", "context": {"tool": "b", "error": "y"}}, now=T0), now=T0)
    regressed = fr.apply_fix(regressed, "sha", now=T0)
    regressed = fr.apply_report(regressed, fr.normalize({"happened": "y"}, now=T0 + 1), now=T0 + 1)
    groups = fr.group([old, regressed])
    assert groups[0]["status"] == "recurring"
    md = fr.render_markdown([old, regressed], now=T0 + 200)
    assert "**Fix that did not hold** — sha" in md


def test_an_empty_dump_says_so_without_pretending_it_is_an_error():
    md = fr.render_markdown([], since="1h", now=T0)
    assert "Nothing filed in this window" in md


# ── the store ────────────────────────────────────────────────────────────────────────────────────

def test_the_store_folds_a_duplicate_into_one_row():
    st = store_mod.FrictionStore()
    a = st.file({"kind": "error", "tool": "bot_say", "context": {"error": "404"}}, now=T0)
    b = st.file({"kind": "error", "tool": "bot_say", "context": {"error": "404"}}, now=T0 + 5)
    assert a["id"] == b["id"] and b["recurrence"] == 2
    assert len(st.since(0)) == 1


def test_open_includes_recurring_because_that_is_the_most_urgent_work():
    st = store_mod.FrictionStore()
    rec = st.file({"kind": "error", "tool": "t", "context": {"error": "e"}}, now=T0)
    st.fix(rec["id"], "sha", now=T0)
    assert st.since(0, status="open") == []
    st.file({"kind": "error", "tool": "t", "context": {"error": "e"}}, now=T0 + 1)
    rows = st.since(0, status="open")
    assert len(rows) == 1 and rows[0]["status"] == "recurring"
    assert st.since(0, status="fixed") == []


def test_since_is_forgiving_and_never_silently_narrows():
    assert store_mod.parse_since("", now=T0) == 0.0
    assert store_mod.parse_since("2h", now=T0) == T0 - 7200
    assert store_mod.parse_since("900", now=T0) == T0 - 900
    assert store_mod.parse_since("not a time", now=T0) == 0.0        # everything, never a guess
    assert store_mod.parse_since("2026-09-02T00:00:00Z", now=T0) > 0


# ── the routes ───────────────────────────────────────────────────────────────────────────────────

class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _app_client(tmp_path):
    """agent-api over fakes, no redis — the store's in-memory fallback is the point."""
    root = tmp_path / "workspaces"
    (root / "_global" / "asks").mkdir(parents=True)
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(root / "_global"),
                             redis_url="")
    app = create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                     reader=WorkspaceReader(str(root)))
    return TestClient(app)


def test_a_person_can_file_without_being_identified(tmp_path):
    """The most valuable report available is the one from a session too broken to have an identity."""
    c = _app_client(tmp_path)
    r = c.post("/api/friction", json={"reporter": "person", "kind": "ux",
                                      "happened": "the panel looked stale"})
    assert r.status_code == 201
    assert r.json()["status"] == "open" and r.json()["known"] is False


def test_filing_the_same_edge_twice_says_it_is_known(tmp_path):
    c = _app_client(tmp_path)
    body = {"kind": "error", "tool": "bot_say", "context": {"error": "404"}}
    first = c.post("/api/friction", json=body).json()
    second = c.post("/api/friction", json=body).json()
    assert first["id"] == second["id"]
    assert second["known"] is True and second["recurrence"] == 2


def test_the_dump_route_returns_markdown_and_the_fix_route_closes(tmp_path):
    c = _app_client(tmp_path)
    hdr = {"X-User-Id": "126"}
    rid = c.post("/api/friction", json={"kind": "no-page", "path": "kg/x.md",
                                        "happened": "no page here yet"}).json()["id"]
    md = c.get("/api/friction/dump", headers=hdr)
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "FR-1 · no-page" in md.text and rid in md.text

    assert c.post(f"/api/friction/{rid}/fix", json={}, headers=hdr).status_code == 400
    fixed = c.post(f"/api/friction/{rid}/fix", json={"fix_ref": "PR #1409"}, headers=hdr)
    assert fixed.status_code == 200 and fixed.json()["status"] == "fixed"
    assert c.post("/api/friction/nope/fix", json={"fix_ref": "x"}, headers=hdr).status_code == 404

    assert "Nothing filed" in c.get("/api/friction/dump", headers=hdr).text
    assert c.get("/api/friction/dump?status=fixed", headers=hdr).text.count("FR-1") == 1


def test_the_dump_json_format_carries_the_same_grouping(tmp_path):
    c = _app_client(tmp_path)
    c.post("/api/friction", json={"kind": "error", "tool": "t", "context": {"error": "e"}})
    body = c.get("/api/friction/dump?format=json", headers={"X-User-Id": "126"}).json()
    assert body["count"] == 1 and body["findings"][0]["kind"] == "error"


def test_reading_the_dump_needs_an_identity_even_though_filing_does_not(tmp_path, monkeypatch):
    # the L2 harness sets a fallback subject (conftest `_default_subject`); clear it, or every
    # request is identified and this proves nothing.
    monkeypatch.setenv("VEXA_AGENT_DEFAULT_SUBJECT", "")
    c = _app_client(tmp_path)
    assert c.post("/api/friction", json={"happened": "x"}).status_code == 201
    assert c.get("/api/friction/dump").status_code == 401


# ── the agent side ───────────────────────────────────────────────────────────────────────────────

def test_the_rule_ships_and_names_the_silent_workaround():
    text = wfr.friction_preamble()
    assert "report_friction" in text
    assert "silent workaround is the defect" in text.lower()
    for word in ("tried", "happened", "session"):
        assert word in text.lower()


def test_a_turn_that_ends_on_a_tool_error_files_by_itself():
    recs = wfr.scan_turn([
        {"type": "tool-call", "tool": "Read", "args": {"path": "a.md"}, "callId": "1"},
        {"type": "tool-result", "callId": "1", "ok": True, "summary": "ok"},
        {"type": "tool-call", "tool": "Write", "args": {"path": "b.md"}, "callId": "2"},
        {"type": "tool-result", "callId": "2", "ok": False, "summary": "no such file or directory"},
    ], session="meet-104", subject="126")
    assert len(recs) == 1
    assert recs[0]["kind"] == "no-page" and recs[0]["context"]["tool"] == "Write"
    assert recs[0]["session"] == "meet-104" and recs[0]["auto"] is True


def test_a_4xx_from_a_vexa_tool_files_even_when_the_turn_recovered():
    """A workaround is invisible from outside — which is exactly why this does not wait for one."""
    recs = wfr.scan_turn([
        {"type": "tool-call", "tool": "mcp__vexa__bot_say", "args": {}, "callId": "1"},
        {"type": "tool-result", "callId": "1", "ok": False, "summary": "404 {'detail':'Not Found'}"},
        {"type": "tool-call", "tool": "Read", "args": {}, "callId": "2"},
        {"type": "tool-result", "callId": "2", "ok": True, "summary": "ok"},
    ])
    assert len(recs) == 1 and recs[0]["context"]["tool"] == "mcp__vexa__bot_say"


def test_a_clean_turn_files_nothing():
    assert wfr.scan_turn([
        {"type": "tool-call", "tool": "Read", "args": {}, "callId": "1"},
        {"type": "tool-result", "callId": "1", "ok": True, "summary": "ok"},
    ]) == []
    assert wfr.scan_turn([]) == []


def test_a_missing_toolbelt_files_at_spawn_because_the_model_could_not():
    """Ledger F70: the one failure that silences the reporting channel is the one it cannot report."""
    rec = wfr.spawn_gap(url="https://rig/mcp", token="", config_written=False, subject="126")
    assert rec and rec["kind"] == "missing-tool" and rec["severity"] == "blocker"
    assert "VEXA_MCP_DELEGATION_TOKEN" in rec["happened"]
    # a turn that was never meant to have a toolbelt is not a defect
    assert wfr.spawn_gap(url="", token="", config_written=False) is None
    # nor is one that got it
    assert wfr.spawn_gap(url="u", token="t", config_written=True) is None


def test_reporting_never_raises_into_a_turn(monkeypatch, tmp_path):
    monkeypatch.setattr(wfr, "FALLBACK_LOG", tmp_path / "f.jsonl")
    monkeypatch.setenv("VEXA_AGENT_API_SELF_URL", "http://127.0.0.1:1")   # nothing listens
    assert wfr.report({"happened": "boom"}, subject="126", timeout=0.05) is None
    assert "boom" in (tmp_path / "f.jsonl").read_text()      # the file is written FIRST, always

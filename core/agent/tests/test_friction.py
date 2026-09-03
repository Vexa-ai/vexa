"""The rough-edges loop (PRD decision 33; #1510) — shape, redaction, the publishing route, and the
two agent-side hooks.

#1510 retired agent-api's own store (`control_plane/friction.py`'s `FrictionStore`) — the carrier
is flows' `friction.reported` (`friction-sink-in-flows`), and `POST /api/friction` here is an HTTP
CLIENT of flows' own `/friction` route (`control_plane/publish.py::post_friction`), for the two
callers that cannot reach flows directly. It is a CLIENT rather than a second publisher on purpose:
a carrier has exactly one producing domain (flows), enforced by `gate:config-contract` against
every service's config.v1 declaration — so this route, and the in-process refused-model-endpoint
path, both forward onto flows' existing route rather than registering a second one. The route
tests below assert the QUERY PARAMETERS/headers handed to `post_friction`'s HTTP call — the store's
own dedup/status-machine/dump tests are gone with it (see `shared/friction.py`'s module docstring
for why they no longer belong here).

Every test here is offline: the record module is pure, the route tests monkeypatch
`control_plane.publish.post_friction` so no network is touched, and `worker.friction.report` is
proven not to raise, which is the only property a `finally` block around somebody's turn can
depend on.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane import publish as publish_mod
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


def test_a_reporter_supplied_log_pointer_is_believed():
    rec = fr.normalize({"log_refs": [{"container": "worker-abc", "since": "1h", "grep": "boom"}]},
                       now=T0)
    assert rec["log_refs"][0]["container"] == "worker-abc"
    assert fr.normalize({}, now=T0)["log_refs"] == []


# ── control_plane.publish.post_friction — the HTTP client of flows' own route ──────────────────

def test_post_friction_builds_the_query_shape_flows_route_reads(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}

        class _R:
            status = 201

            def read(self):
                return b'{"id": "fr_abc", "recorded": true}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _R()

    monkeypatch.setattr(publish_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("VEXA_FLOWS_API_URL", "http://flows:18200")
    monkeypatch.setenv("VEXA_FLOWS_API_KEY", "test-operator-key")
    rec = fr.normalize({"subject": "126", "session": "chat-1", "tried": "x", "happened": "y",
                        "severity": "blocker", "context": {"tool": "bot_say", "meeting_id": "104"}},
                       now=T0)
    ok, body = publish_mod.post_friction(rec, deployment="prod-lite", worker_image="vexa-bot:1")
    assert ok is True and body == {"id": "fr_abc", "recorded": True}
    assert seen["url"].startswith("http://flows:18200/friction?")
    assert "session=chat-1" in seen["url"]
    assert "what_i_tried=x" in seen["url"]
    assert "what_happened=y" in seen["url"]
    assert "severity=blocker" in seen["url"]
    assert "tool=bot_say" in seen["url"]
    assert "meeting_id=104" in seen["url"]
    assert "deployment=prod-lite" in seen["url"]
    assert "worker_image=vexa-bot%3A1" in seen["url"]
    assert seen["headers"]["x-flows-operator-key"] == "test-operator-key"
    assert seen["headers"]["x-user-id"] == "126"


def test_post_friction_omits_x_user_id_when_the_record_has_no_subject(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        raise __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
            req.full_url, 401, "no subject", {}, None)

    monkeypatch.setattr(publish_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("VEXA_FLOWS_API_URL", "http://flows:18200")
    monkeypatch.setenv("VEXA_FLOWS_API_KEY", "test-operator-key")
    rec = fr.normalize({"session": "s1", "tried": "x", "happened": "y"}, now=T0)
    ok, body = publish_mod.post_friction(rec)
    assert ok is False
    assert "x-user-id" not in seen["headers"]


def test_post_friction_is_a_no_op_with_no_flows_domain_configured(monkeypatch):
    monkeypatch.delenv("VEXA_FLOWS_API_URL", raising=False)
    rec = fr.normalize({"session": "s1", "tried": "x", "happened": "y"}, now=T0)
    assert publish_mod.post_friction(rec) == (False, {})


def test_post_friction_never_raises_when_flows_is_unreachable(monkeypatch):
    monkeypatch.setattr(publish_mod.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setenv("VEXA_FLOWS_API_URL", "http://flows:18200")
    monkeypatch.setenv("VEXA_FLOWS_API_KEY", "test-operator-key")
    rec = fr.normalize({"session": "s1", "tried": "x", "happened": "y"}, now=T0)
    assert publish_mod.post_friction(rec) == (False, {})


def test_file_friction_report_normalizes_and_forwards(monkeypatch):
    seen = {}
    monkeypatch.setattr(publish_mod, "post_friction",
                        lambda rec, **kw: seen.update(rec=rec) or (True, {"id": "fr_1"}))
    ok = publish_mod.file_friction_report({"subject": "u_7", "session": "dispatch-u_7",
                                           "tried": "dispatch a turn",
                                           "happened": "endpoint refused", "kind": "refusal"})
    assert ok is True
    assert seen["rec"]["subject"] == "u_7" and seen["rec"]["session"] == "dispatch-u_7"
    assert seen["rec"]["happened"] == "endpoint refused"


# ── the route ────────────────────────────────────────────────────────────────────────────────────

class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _app_client(tmp_path):
    """agent-api over fakes, no redis — there is no store any more, so no fallback to prove."""
    root = tmp_path / "workspaces"
    (root / "_global" / "asks").mkdir(parents=True)
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(root / "_global"),
                             redis_url="")
    app = create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                     reader=WorkspaceReader(str(root)))
    return TestClient(app)


def test_a_report_with_no_session_is_refused(tmp_path):
    """Before #1510: the old store had no such rule and filed it anyway with `session=""` — which
    is precisely the gap the flows carrier exists to close, so the route must refuse it too now
    that it forwards onto that carrier."""
    c = _app_client(tmp_path)
    r = c.post("/api/friction", json={"tried": "x", "happened": "y"})
    assert r.status_code == 400
    assert "session" in r.json()["detail"]


def test_a_report_with_no_tried_or_happened_is_refused(tmp_path):
    c = _app_client(tmp_path)
    assert c.post("/api/friction", json={"session": "s1", "happened": "y"}).status_code == 400
    assert c.post("/api/friction", json={"session": "s1", "tried": "x"}).status_code == 400


def test_a_well_formed_report_forwards_and_returns_flows_own_response(tmp_path, monkeypatch):
    seen = {}

    def fake_post(rec, **kw):
        seen["rec"] = rec
        seen["kw"] = kw
        return True, {"id": "fr_xyz", "recorded": True}

    monkeypatch.setattr(publish_mod, "post_friction", fake_post)
    c = _app_client(tmp_path)
    r = c.post("/api/friction", json={"reporter": "person", "session": "chat-42",
                                      "tried": "opened the page", "happened": "got a 404",
                                      "kind": "no-page"}, headers={"X-User-Id": "126"})
    assert r.status_code == 201
    assert r.json() == {"id": "fr_xyz", "recorded": True}     # exactly flows' own shape, passed through
    assert seen["rec"]["subject"] == "126" and seen["rec"]["session"] == "chat-42"
    assert seen["rec"]["tried"] == "opened the page" and seen["rec"]["happened"] == "got a 404"


def test_a_person_can_file_without_being_identified(tmp_path, monkeypatch):
    """The most valuable report available is the one from a session too broken to have an
    identity — unchanged from the store era (`_friction_subject` is BEST-EFFORT, never a refusal).
    flows' own route may still refuse an unattributed report; the route here does not pre-empt it."""
    monkeypatch.setattr(publish_mod, "post_friction", lambda rec, **kw: (True, {"id": "fr_1"}))
    c = _app_client(tmp_path)
    r = c.post("/api/friction", json={"reporter": "person", "session": "s1",
                                      "tried": "used the terminal", "happened": "it looked stale"})
    assert r.status_code == 201


def test_deployment_and_worker_image_ride_off_the_raw_body(tmp_path, monkeypatch):
    """Neither is part of `shared.friction`'s CONTEXT_KEYS shape, so the route must read them off
    the raw body rather than lose them to `normalize()`'s "everything unknown is dropped" rule."""
    seen = {}
    monkeypatch.setattr(publish_mod, "post_friction",
                        lambda rec, **kw: seen.update(kw=kw) or (True, {"id": "fr_1"}))
    c = _app_client(tmp_path)
    c.post("/api/friction", json={"session": "s1", "tried": "x", "happened": "y",
                                  "deployment": "prod-lite", "worker_image": "vexa-bot:0.12.3"})
    assert seen["kw"]["deployment"] == "prod-lite"
    assert seen["kw"]["worker_image"] == "vexa-bot:0.12.3"


def test_a_failed_forward_never_breaks_the_route_but_says_so(tmp_path, monkeypatch):
    """A publish is not a dependency (`control_plane/publish.py`'s own module docstring) — a
    deployment with no flows domain, or one where flows is briefly down, still returns 201, with
    an empty id rather than one nothing durable backs."""
    monkeypatch.setattr(publish_mod, "post_friction", lambda rec, **kw: (False, {}))
    c = _app_client(tmp_path)
    r = c.post("/api/friction", json={"session": "s1", "tried": "x", "happened": "y"})
    assert r.status_code == 201
    assert r.json() == {"id": "", "recorded": False}


def test_there_is_no_more_dump_or_fix_route(tmp_path):
    """A1 (#1510): the old whole-instance dump and the store-backed fix verb no longer exist on
    agent-api at all — they moved to flows (`friction_so_far`) and the rig (`friction_dump`/
    `friction_fixed`, #1510's C2/C3)."""
    c = _app_client(tmp_path)
    assert c.get("/api/friction/dump").status_code == 404
    assert c.post("/api/friction/fr_x/fix", json={"fix_ref": "x"}).status_code == 404


def test_no_frictionstore_module_or_live_import_remains():
    """A4 (#1510): the store module is deleted, and nothing imports it or constructs it."""
    import pathlib

    agent_root = pathlib.Path(__file__).resolve().parents[1]
    assert not (agent_root / "control_plane" / "friction.py").exists()
    for py in agent_root.rglob("*.py"):
        if "/tests/" in str(py) or py.name == "friction.py":
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        assert "import friction as friction_store_mod" not in text, py
        assert "FrictionStore(" not in text, py


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


# ── the refused-model-endpoint path (in-process, no HTTP hop) ──────────────────────────────────

def test_a_refused_model_endpoint_forwards_with_a_session(monkeypatch):
    """dispatch.py's `overlay_model_config` → `model_endpoint.refusal_friction` → the `friction`
    callable (`Dispatcher.attach_friction`, wired to `publish_mod.file_friction_report` in
    `create_app`, #1510's C1/C5). This is the in-process door — no HTTP hop into agent-api's own
    route, no store — and it must carry a session exactly like the HTTP route does."""
    from control_plane import model_endpoint
    rec = model_endpoint.refusal_friction("http://redis:6379", "not allow-listed",
                                          subject="u_7", session="dispatch-u_7")
    assert rec["session"] == "dispatch-u_7" and rec["subject"] == "u_7"
    seen = {}
    monkeypatch.setattr(publish_mod, "post_friction",
                        lambda normalized_rec, **kw: seen.update(rec=normalized_rec) or (True, {}))
    assert publish_mod.file_friction_report(rec) is True
    assert seen["rec"]["session"] == "dispatch-u_7"


def test_fallback_session_prefers_the_chat_session(monkeypatch):
    monkeypatch.setenv("VEXA_CHAT_SESSION", "main")
    monkeypatch.setenv("VEXA_UNIT_ID", "agent-abc")
    assert wfr.fallback_session() == "main"


def test_fallback_session_falls_back_to_the_unit_id_with_no_chat_session(monkeypatch):
    """#1510: `spawn_gap` is filed before a turn's session exists at all (scheduled/event/
    transcription dispatches never set VEXA_CHAT_SESSION) -- the flows carrier this record is
    published onto refuses a report with no session, so there must always be SOMETHING."""
    monkeypatch.delenv("VEXA_CHAT_SESSION", raising=False)
    monkeypatch.setenv("VEXA_UNIT_ID", "agent-meet-104")
    assert wfr.fallback_session() == "agent-meet-104"


def test_fallback_session_never_returns_empty(monkeypatch):
    monkeypatch.delenv("VEXA_CHAT_SESSION", raising=False)
    monkeypatch.delenv("VEXA_UNIT_ID", raising=False)
    assert wfr.fallback_session() == "unknown"

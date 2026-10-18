"""The per-turn MCP reconnect guard (F153) — RED-FIRST for the worker surviving a control-server
restart between (or within) turns of one chat session.

THE INCIDENT (2026-09-03 ~13:46Z, founder hit it live). The control MCP server is a stateless
streamable-HTTP endpoint by design (PRD 40.10: a client reconnects). Each worker turn is already a
FRESH ``claude``/``codex`` subprocess that re-reads ``.mcp.json`` and re-attaches from scratch — so
a restart BETWEEN turns should be invisible. It was not: a turn's subprocess attached to a server
mid-restart, got nothing back, and the harness silently ran the whole turn with no vexa tools. The
model then told the founder its own guess ("the workspace-creation tool isn't available in this
session anymore") instead of the truth. Nothing upstream of the model checked, so nothing could
correct it.

THIS FILE proves the fix at two levels:
  1. ``mcp_preflight`` — the raw JSON-RPC ``initialize`` handshake, retried with bounded backoff,
     against a REAL local stub streamable-HTTP MCP server that this file starts, stops and restarts
     (no ``claude``/``codex`` binary, no network, no credentials — a plain ``http.server``).
  2. ``run_turn_over_workspace`` — the turn engine wired to that guard: a turn attaches when the
     server answers, drops the attachment and files a TYPED failure (never a silent one) when it
     does not, and regains the toolbelt on the very next turn once the server is back — the exact
     "second turn has the tools" claim F153 makes.
"""
from __future__ import annotations

import http.server
import json
import socket
import threading
import time

import pytest

import worker.engine as engine
from worker.friction import mcp_unreachable


# ── a real, local, stateless streamable-HTTP MCP stub ────────────────────────────────────────────

class _StubMCPHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # keep pytest output quiet
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            req = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            req = {}
        resp = {
            "jsonrpc": "2.0", "id": req.get("id"),
            "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "stub-mcp", "version": "1"}},
        }
        body = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_stub(port: int) -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _StubMCPHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _stop_stub(server: http.server.ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()


def _mcp_config_file(tmp_path, url: str, token: str = "t") -> str:
    d = tmp_path / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"vexa": {
        "type": "http", "url": url, "headers": {"Authorization": f"Bearer {token}"}}}}))
    return str(path)


# ── level 1: mcp_preflight against the real stub ─────────────────────────────────────────────────

def test_mcp_preflight_succeeds_against_a_live_server():
    port = _free_port()
    server = _start_stub(port)
    try:
        ok, detail = engine.mcp_preflight(f"http://127.0.0.1:{port}/mcp", {}, delays=(), timeout=2.0)
        assert ok is True and detail == ""
    finally:
        _stop_stub(server)


def test_mcp_preflight_fails_bounded_when_nothing_is_listening():
    port = _free_port()  # nothing ever bound here
    t0 = time.monotonic()
    ok, detail = engine.mcp_preflight(f"http://127.0.0.1:{port}/mcp", {}, delays=(0.01, 0.01, 0.01),
                                      timeout=1.0)
    elapsed = time.monotonic() - t0
    assert ok is False
    assert detail
    assert elapsed < 2.0, "a down server must never hang the turn past its retry budget"


def test_mcp_preflight_recovers_the_moment_the_server_is_back():
    """THE RED-FIRST CLAIM, at the transport level: kill/restart a local stub streamable-HTTP MCP
    server — the next preflight must see it as reachable again, with no state carried over."""
    port = _free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    server = _start_stub(port)
    assert engine.mcp_preflight(url, {}, delays=(), timeout=2.0) == (True, "")

    _stop_stub(server)
    ok, detail = engine.mcp_preflight(url, {}, delays=(0.01, 0.01), timeout=1.0)
    assert ok is False and detail

    server2 = _start_stub(port)
    try:
        assert engine.mcp_preflight(url, {}, delays=(), timeout=2.0) == (True, "")
    finally:
        _stop_stub(server2)


# ── config-file parsing the preflight is built on ────────────────────────────────────────────────

def test_mcp_endpoint_reads_url_and_bearer_header(tmp_path):
    cfg = _mcp_config_file(tmp_path, "https://rig.example/mcp", token="abc123")
    url, headers = engine._mcp_endpoint(cfg)
    assert url == "https://rig.example/mcp"
    assert headers["Authorization"] == "Bearer abc123"


@pytest.mark.parametrize("write", [
    lambda p: None,                                   # file never written
    lambda p: p.write_text("not json"),                # malformed
    lambda p: p.write_text(json.dumps({"mcpServers": {}})),  # no server entry
])
def test_mcp_endpoint_is_none_when_there_is_nothing_usable(tmp_path, write):
    path = tmp_path / "mcp.json"
    write(path)
    assert engine._mcp_endpoint(str(path)) is None


def test_first_sse_json_reads_the_data_line():
    raw = 'event: message\ndata: {"jsonrpc": "2.0", "id": 1, "result": {}}\n\n'
    assert engine._first_sse_json(raw) == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_first_sse_json_is_none_when_no_data_line_parses():
    assert engine._first_sse_json("event: message\n\n") is None


# ── the friction record ───────────────────────────────────────────────────────────────────────────

def test_mcp_unreachable_record_is_a_blocker_naming_what_was_tried():
    rec = mcp_unreachable(url="http://x/mcp", detail="ConnectionRefusedError: x", attempts=5,
                          session="s1", subject="58")
    assert rec["kind"] == "missing-tool"
    assert rec["severity"] == "blocker"
    assert "http://x/mcp" in rec["tried"]
    assert rec["auto"] is True


# ── level 2: the turn engine wired to the guard ──────────────────────────────────────────────────

class _FakeHarness:
    name = "fake"

    def prepare(self, work, chat_root=None):
        pass

    def transcript_bytes(self, work, session_id):
        return 0

    def preflight(self):
        return None


def _patch_turn_plumbing(monkeypatch, seen):
    """Bypass everything BUT the MCP guard: no real subprocess, no real harness."""
    def fake_run_harness_turn(work, prompt, harness, **kw):
        seen["prompt"] = prompt
        seen.update(kw)
        yield {"type": "done", "ok": True, "sessionId": "s1"}
    monkeypatch.setattr(engine, "run_harness_turn", fake_run_harness_turn)
    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)
    monkeypatch.setattr(engine, "harness_from_env", lambda: _FakeHarness())


def test_turn_attaches_the_toolbelt_when_the_server_answers(monkeypatch, tmp_path):
    port = _free_port()
    server = _start_stub(port)
    try:
        cfg = _mcp_config_file(tmp_path, f"http://127.0.0.1:{port}/mcp")
        seen = {}
        _patch_turn_plumbing(monkeypatch, seen)
        filed = []
        monkeypatch.setattr(engine, "report_friction", lambda rec, **kw: filed.append(rec))
        events = list(engine.run_turn_over_workspace(tmp_path, "hi", mcp_config=cfg,
                                                      session_continuity=False))
        assert seen["mcp_config"] == cfg               # the live attachment reaches the harness
        assert not filed                                # nothing wrong — nothing to report
        assert not any(e.get("type") == "mcp-unavailable" for e in events)
        done = [e for e in events if e.get("type") == "done"][-1]
        assert done["mcp_ok"] is True
    finally:
        _stop_stub(server)


def test_turn_drops_the_toolbelt_and_files_a_typed_failure_when_the_server_never_answers(
        monkeypatch, tmp_path):
    """THE TYPED-FAILURE CLAIM: a turn that lost its MCP attachment must never look like a normal
    turn — no silent degrade into a 'plausible answer'."""
    port = _free_port()  # nothing ever listens on it — the server never comes back
    cfg = _mcp_config_file(tmp_path, f"http://127.0.0.1:{port}/mcp")
    monkeypatch.setattr(engine, "MCP_PREFLIGHT_DELAYS", (0.01, 0.01))
    seen = {}
    _patch_turn_plumbing(monkeypatch, seen)
    filed = []
    monkeypatch.setattr(engine, "report_friction", lambda rec, **kw: filed.append(rec))

    events = list(engine.run_turn_over_workspace(tmp_path, "hi", mcp_config=cfg,
                                                  session_continuity=False))

    assert seen["mcp_config"] is None, "a confirmed-dead attachment must never reach the harness"
    assert len(filed) == 1
    assert filed[0]["kind"] == "missing-tool" and filed[0]["severity"] == "blocker"
    unavailable = [e for e in events if e.get("type") == "mcp-unavailable"]
    assert len(unavailable) == 1
    done = [e for e in events if e.get("type") == "done"][-1]
    assert done["mcp_ok"] is False
    # the model is TOLD the truth in its own opening context rather than left to invent one
    assert "toolbelt unavailable" in seen["prompt"].lower()


def test_the_second_turn_has_the_tools_after_the_server_restarts_between_turns(monkeypatch, tmp_path):
    """THE RED-FIRST SCENARIO, verbatim: simulate the control server going away BETWEEN two turns of
    one chat session, then coming back — the very next turn must regain the toolbelt with no
    operator action and no stale state."""
    port = _free_port()
    server = _start_stub(port)
    cfg = _mcp_config_file(tmp_path, f"http://127.0.0.1:{port}/mcp")
    monkeypatch.setattr(engine, "MCP_PREFLIGHT_DELAYS", (0.01, 0.01))
    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)
    monkeypatch.setattr(engine, "harness_from_env", lambda: _FakeHarness())
    monkeypatch.setattr(engine, "report_friction", lambda rec, **kw: None)

    def _run(prompt: str) -> dict:
        seen: dict = {}

        def fake(work, p, harness, **kw):
            seen.update(kw)
            yield {"type": "done", "ok": True, "sessionId": "s1"}
        monkeypatch.setattr(engine, "run_harness_turn", fake)
        list(engine.run_turn_over_workspace(tmp_path, prompt, mcp_config=cfg,
                                            session_continuity=False))
        return seen

    try:
        # turn 1 — server up, tools attached
        assert _run("t1")["mcp_config"] == cfg

        # F153: the control server restarts BETWEEN turns of this same session
        _stop_stub(server)

        # turn 2 — confirmed dead, runs without the toolbelt rather than stalling or faking it
        assert _run("t2")["mcp_config"] is None

        server = _start_stub(port)  # the server comes back
        # turn 3 — THE ASSERTION: the very next turn has the tools again
        assert _run("t3")["mcp_config"] == cfg
    finally:
        _stop_stub(server)

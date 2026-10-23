"""A chat message that is only a meeting link sends the bot (`worker/meeting_link.py`).

The founder asked that the "Send Vexa to a meeting" chip go: a link pasted into the chat box should
simply send the bot. Before this, a bare link reached the model like any other sentence and the send
depended on the model choosing to make it. These tests run the real turn engine against a real local
streamable-HTTP MCP stub that parses with the product's own link parser
(`core/gateway/services/mcp/src/vexa_mcp/link_parser.py`) and keeps meeting-api's one rule that
matters here — one active bot per person per meeting.

What is proven, against the old code where each of these fails:
  * a bare supported link sends the bot before the model runs, and the model is told the outcome;
  * a link with words around it, and a URL that is not a meeting link, are ordinary turns;
  * pasting the same link twice sends one bot, and the second turn says a bot is already there;
  * a refusal is reported with the deciding service's own reason, never silently;
  * the only credential used is this unit's own attachment.
"""
from __future__ import annotations

import http.server
import importlib.util
import json
import pathlib
import socket
import sys
import threading

import pytest
from fastapi import HTTPException

import worker.engine as engine
from shared.marks import MACHINERY_MARK, act_mark, job_mark
from worker import meeting_link

_PARSER = (pathlib.Path(__file__).resolve().parents[3]
           / "core/gateway/services/mcp/src/vexa_mcp/link_parser.py")
_spec = importlib.util.spec_from_file_location("vexa_mcp_link_parser", _PARSER)
link_parser = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = link_parser          # pydantic resolves the module's annotations by name
_spec.loader.exec_module(link_parser)

MEET = "https://meet.google.com/abc-defg-hij"
TEAMS_SHORT = "https://teams.live.com/meet/9876543210123"
REFUSED_TOKEN = "person-at-quota"


class _Stub:
    """The vexa MCP as the worker sees it: initialize, then `tools/call`, sessioned."""

    def __init__(self) -> None:
        self.calls: list[dict] = []          # every tools/call: tool, args, the Authorization header
        self.auth: list[str] = []            # every request's Authorization header
        self.active: dict[tuple, dict] = {}  # (token, platform, native) → the meeting row
        self.next_id = 100

    def handle(self, req: dict, auth: str) -> "tuple[dict | None, dict]":
        self.auth.append(auth)
        method = req.get("method")
        if method == "initialize":
            return ({"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                     "serverInfo": {"name": "stub", "version": "1"}}, {"mcp-session-id": "s-1"})
        if method != "tools/call":
            return None, {}
        name = req["params"]["name"]
        args = req["params"]["arguments"]
        self.calls.append({"tool": name, "args": args, "auth": auth})
        try:
            parsed = link_parser.parse_meeting_url(args["meeting_url"])
        except HTTPException as e:
            return self._error(f"HTTP {e.status_code}\n{e.detail}"), {}
        if name == "parse_meeting_link":
            return self._ok(parsed.model_dump()), {}
        token = auth.removeprefix("Bearer ")
        if token == REFUSED_TOKEN:
            body = {"code": "bot_quota", "reason": "quota_exceeded",
                    "message": "You already have 3 bots running.", "decision_id": "d-1"}
            return self._error(f"quota_exceeded: You already have 3 bots running.\n"
                               f"{json.dumps(body)}"), {}
        key = (token, parsed.platform, parsed.native_meeting_id)
        if key in self.active:
            return self._ok({"status": "already_exists", "meeting": self.active[key]}), {}
        self.next_id += 1
        row = {"id": self.next_id, "platform": parsed.platform,
               "native_meeting_id": parsed.native_meeting_id, "status": "requested"}
        self.active[key] = row
        return self._ok(row), {}

    @staticmethod
    def _ok(obj: dict) -> dict:
        return {"content": [{"type": "text", "text": json.dumps(obj)}], "isError": False}

    @staticmethod
    def _error(text: str) -> dict:
        return {"content": [{"type": "text", "text": text}], "isError": True}


def _serve(stub: _Stub) -> "tuple[http.server.ThreadingHTTPServer, str]":
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("content-length") or 0))
            req = json.loads(raw or b"{}")
            result, extra = stub.handle(req, self.headers.get("authorization") or "")
            if "id" not in req:
                self.send_response(202)
                self.send_header("content-length", "0")
                self.end_headers()
                return
            body = json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result or {}}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            for k, v in extra.items():
                self.send_header(k, v)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}/mcp"


@pytest.fixture
def stub():
    s = _Stub()
    server, url = _serve(s)
    s.url = url
    yield s
    server.shutdown()
    server.server_close()


def _config(tmp_path, url: str, token: str) -> str:
    d = tmp_path / "secrets"
    d.mkdir(exist_ok=True)
    p = d / "mcp.json"
    p.write_text(json.dumps({"mcpServers": {"vexa": {
        "type": "http", "url": url, "headers": {"Authorization": f"Bearer {token}"}}}}))
    return str(p)


class _Harness:
    name = "fake"

    def prepare(self, work, chat_root=None):
        pass

    def transcript_bytes(self, work, sid):
        return 0


@pytest.fixture
def turn(monkeypatch, tmp_path):
    """Run one chat turn; returns (events, the prompt the model was handed)."""
    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)
    monkeypatch.setattr(engine, "timeline_preamble", lambda: "")
    monkeypatch.setattr(engine, "MCP_PREFLIGHT_DELAYS", ())
    monkeypatch.setattr(engine, "report_friction", lambda rec, **kw: None)
    monkeypatch.setattr(engine, "active_mounts", lambda: [
        {"slug": "personal", "path": str(tmp_path), "role": "private", "write": True,
         "primary": True}])

    def run(prompt: str, cfg: str, **kw):
        seen: dict = {}

        def fake(work, p, harness, **_kw):
            seen["prompt"] = p
            yield {"type": "done", "ok": True, "reply": "ok", "sessionId": "sid"}

        monkeypatch.setattr(engine, "run_harness_turn", fake)
        events = list(engine.run_turn_over_workspace(tmp_path, prompt, mcp_config=cfg,
                                                     harness=_Harness(), commit=False, **kw))
        return events, seen.get("prompt", "")
    return run


def _sends(stub: _Stub) -> list[dict]:
    return [c for c in stub.calls if c["tool"] == "request_meeting_bot"]


# ── the bare link sends the bot ─────────────────────────────────────────────────────────────────

def test_a_bare_meeting_link_requests_the_bot_before_the_model_runs(stub, turn, tmp_path):
    events, prompt = turn(MEET, _config(tmp_path, stub.url, "tok-a"))
    assert [c["args"] for c in _sends(stub)] == [{"meeting_url": MEET}]
    link = [e for e in events if e.get("type") == "meeting-link"]
    assert link == [{"type": "meeting-link", "outcome": "requested", "url": MEET,
                     "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                     "meeting_id": "101", "status": "requested"}]
    # the same events a model's own send produces: a step, its result, and the meeting artifact
    # agent-api binds the chat to
    call = next(e for e in events if e.get("type") == "tool-call")
    assert call["tool"] == "mcp__vexa__request_meeting_bot"
    result = next(e for e in events if e.get("type") == "tool-result")
    assert result["ok"] is True and result["callId"] == call["callId"]
    assert {"type": "artifact", "path": "meeting:101", "pin": True, "focus": True,
            "native": "abc-defg-hij"} in events
    # the send happened BEFORE the turn's done, and the model is told the outcome first
    assert events.index(call) < events.index(next(e for e in events if e.get("type") == "done"))
    assert prompt.startswith("## The person's message is a meeting link")
    assert "a bot was requested for the Google Meet meeting abc-defg-hij" in prompt
    assert "Do not call `request_meeting_bot` again" in prompt


def test_a_teams_meet_short_link_is_sent_too(stub, turn, tmp_path):
    events, _ = turn(TEAMS_SHORT, _config(tmp_path, stub.url, "tok-a"))
    ev = next(e for e in events if e.get("type") == "meeting-link")
    assert ev["outcome"] == "requested"
    assert ev["platform"] == "teams" and ev["native_meeting_id"] == "9876543210123"


def test_the_link_after_the_grounding_sentinel_still_counts(stub, turn, tmp_path):
    composed = "Active context: the user is viewing a file.\n\n---\n" + engine.CONTEXT_SENTINEL + MEET
    events, _ = turn(composed, _config(tmp_path, stub.url, "tok-a"))
    assert len(_sends(stub)) == 1
    assert any(e.get("type") == "meeting-link" for e in events)


# ── everything else is an ordinary turn ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("prompt", [
    f"can you summarise {MEET} later",
    f"{MEET} please",
    f"{MEET}\nand prepare notes",
])
def test_a_link_with_words_is_a_normal_turn(stub, turn, tmp_path, prompt):
    events, sent_prompt = turn(prompt, _config(tmp_path, stub.url, "tok-a"))
    assert stub.calls == []
    assert not any(e.get("type") in ("meeting-link", "tool-call") for e in events)
    assert "the platform already acted on it" not in sent_prompt


@pytest.mark.parametrize("url", ["https://example.com/agenda", "https://zoom.us.evil.example/j/123456789"])
def test_a_url_that_is_not_a_meeting_link_is_a_normal_turn(stub, turn, tmp_path, url):
    events, sent_prompt = turn(url, _config(tmp_path, stub.url, "tok-a"))
    assert [c["tool"] for c in stub.calls] == ["parse_meeting_link"]   # asked, never sent
    assert _sends(stub) == []
    assert not any(e.get("type") in ("meeting-link", "tool-call") for e in events)
    assert "the platform already acted on it" not in sent_prompt


@pytest.mark.parametrize("prompt", [
    MACHINERY_MARK + " " + MEET,
    act_mark("extend", "README.md") + MEET,
    job_mark("extend", "README.md") + MEET,
])
def test_a_marked_turn_is_never_read_as_a_pasted_link(stub, turn, tmp_path, prompt):
    turn(prompt, _config(tmp_path, stub.url, "tok-a"))
    assert stub.calls == []


def test_a_turn_outside_the_chat_conversation_sends_nothing(stub, turn, tmp_path):
    turn(MEET, _config(tmp_path, stub.url, "tok-a"), session_continuity=False)
    assert stub.calls == []


# ── idempotent ──────────────────────────────────────────────────────────────────────────────────

def test_pasting_the_same_link_twice_sends_one_bot(stub, turn, tmp_path):
    cfg = _config(tmp_path, stub.url, "tok-a")
    turn(MEET, cfg)
    events, prompt = turn(MEET, cfg)
    assert len(stub.active) == 1, "a second bot was created for the same meeting"
    ev = next(e for e in events if e.get("type") == "meeting-link")
    assert ev["outcome"] == "already_there" and ev["meeting_id"] == "101"
    assert "a bot is already in the Google Meet meeting abc-defg-hij" in prompt
    assert "no second bot was sent" in prompt


# ── a refusal is typed and loud ─────────────────────────────────────────────────────────────────

def test_a_refused_send_is_reported_with_the_deciding_services_reason(stub, turn, tmp_path):
    events, prompt = turn(MEET, _config(tmp_path, stub.url, REFUSED_TOKEN))
    result = next(e for e in events if e.get("type") == "tool-result")
    assert result["ok"] is False
    ev = next(e for e in events if e.get("type") == "meeting-link")
    assert ev["outcome"] == "refused"
    assert ev["refusal"] == {"source": "request_meeting_bot", "kind": "bot_quota", "status": None,
                             "detail": "quota_exceeded: You already have 3 bots running."}
    assert not any(e.get("type") == "artifact" for e in events)
    assert "was refused (bot_quota): quota_exceeded: You already have 3 bots running." in prompt


def test_an_unreachable_parser_is_reported_and_the_turn_still_runs(turn, tmp_path, monkeypatch):
    def broken(self, tool, args):
        raise meeting_link.ToolError("HTTP 503 from the vexa MCP: down")
    monkeypatch.setattr(meeting_link.McpCaller, "call", broken)
    monkeypatch.setattr(engine, "mcp_preflight", lambda *a, **k: (True, ""))
    events, _ = turn(MEET, _config(tmp_path, "http://127.0.0.1:9/mcp", "tok-a"))
    ev = next(e for e in events if e.get("type") == "meeting-link")
    assert ev["outcome"] == "unchecked"
    assert ev["refusal"]["source"] == "parse_meeting_link"
    assert any(e.get("type") == "done" for e in events)


# ── whose access ────────────────────────────────────────────────────────────────────────────────

def test_only_this_units_own_credential_is_used(stub, turn, tmp_path, monkeypatch):
    """The send goes out on the attachment this unit's turn holds — nothing else is in reach, not an
    API key in the environment and not another person's token."""
    monkeypatch.setenv("VEXA_API_KEY", "someone-elses-key")
    monkeypatch.setenv("VEXA_MCP_DELEGATION_TOKEN", "someone-elses-token")
    turn(MEET, _config(tmp_path, stub.url, "tok-person-a"))
    assert stub.auth and set(stub.auth) == {"Bearer tok-person-a"}


def test_two_people_pasting_the_same_link_each_get_their_own_bot(stub, turn, tmp_path):
    turn(MEET, _config(tmp_path, stub.url, "tok-person-a"))
    events, _ = turn(MEET, _config(tmp_path, stub.url, "tok-person-b"))
    assert next(e for e in events if e.get("type") == "meeting-link")["outcome"] == "requested"
    assert {c["auth"] for c in _sends(stub)} == {"Bearer tok-person-a", "Bearer tok-person-b"}


# ── the pure pieces ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("person,expected", [
    (MEET, MEET), (f"  {MEET}\n", MEET), ("abc-defg-hij", None), ("ftp://meet.google.com/x", None),
    (f"{MEET} {MEET}", None), ("", None),
])
def test_candidate_is_one_http_url_and_nothing_else(person, expected):
    assert meeting_link.candidate(person, person) == expected

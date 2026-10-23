"""logevent.v1 tracing — admin-api (identity) reads X-Trace-Id, binds it, echoes it, emits
conforming lines, forwards it on its Vexa-internal publish edge, and never logs a credential.

Container-free: the app's /health and an unauthenticated /internal/validate need no database.
"""
import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from admin_api import obs
from admin_api.app.main import create_app

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[4] / "gateway/contracts/logevent.v1/logevent.schema.json")
    .read_text()
)
LOGEVENT = SCHEMA["$defs"]["LogEvent"]
SECRET = "vxa_SECRET_token_value_must_never_appear"


def _conforms(line: dict) -> None:
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        jsonschema = None
    if jsonschema is not None:
        jsonschema.validate(line, {"$defs": SCHEMA["$defs"], **LOGEVENT})
        return
    assert set(LOGEVENT["required"]) <= set(line), line
    assert set(line) <= set(LOGEVENT["properties"]), line
    assert line["level"] in SCHEMA["$defs"]["Level"]["enum"]
    assert line["audience"] in SCHEMA["$defs"]["Audience"]["enum"]
    for k in ("service", "trace_id", "event"):
        assert isinstance(line[k], str) and line[k]
    if "fields" in line:
        assert isinstance(line["fields"], dict)


def _run(headers=None, path="/health", method="get"):
    sink: list = []
    obs.capture(sink)
    try:
        client = TestClient(create_app(), raise_server_exceptions=False)
        r = getattr(client, method)(path, headers=headers or {})
    finally:
        obs.capture(None)
    return r, sink


def test_inbound_trace_id_is_bound_and_echoed():
    r, lines = _run({"X-Trace-Id": "trace-abc-123"})
    assert r.status_code == 200
    assert r.headers["x-trace-id"] == "trace-abc-123"
    events = [l["event"] for l in lines]
    assert "request_received" in events and "request_completed" in events
    assert all(l["trace_id"] == "trace-abc-123" for l in lines)
    assert all(l["service"] == "identity" for l in lines)
    done = next(l for l in lines if l["event"] == "request_completed")
    assert done["fields"]["status"] == 200
    for l in lines:
        _conforms(l)


def test_missing_trace_id_is_minted():
    r, lines = _run()
    minted = r.headers["x-trace-id"]
    assert minted and len(minted) == 32
    assert lines and all(l["trace_id"] == minted for l in lines)


def test_query_string_and_authorization_never_logged():
    r, lines = _run({"Authorization": f"Bearer {SECRET}", "X-API-Key": SECRET},
                    path=f"/health?token={SECRET}")
    blob = json.dumps(lines)
    assert SECRET not in blob
    rec = next(l for l in lines if l["event"] == "request_received")
    assert rec["fields"] == {"method": "GET", "path": "/health"}
    assert rec["level"] == "debug" and rec["audience"] == "system"
    for l in lines:
        _conforms(l)


def test_validate_outcome_lines_carry_no_token():
    r, lines = _run({"Authorization": f"Bearer {SECRET}", "X-Trace-Id": "t-validate"},
                    path="/internal/validate", method="post")
    assert SECRET not in json.dumps(lines)
    # Without a database bound the route errors; the outcome line still names the trace and
    # never the bearer.
    assert all(l["trace_id"] == "t-validate" for l in lines)
    assert {"request_completed", "request_failed"} & {l["event"] for l in lines}
    for l in lines:
        _conforms(l)


def test_set_session_binds_session_onto_lines():
    sink: list = []
    obs.capture(sink)
    t_tok = obs.set_trace_id("t-sess")
    s_tok = obs.set_session("agent-u1-chat-th1")
    try:
        assert obs.get_session() == "agent-u1-chat-th1"
        obs.log_event("validate_allowed", audience="system")
    finally:
        obs.reset_session(s_tok)
        obs.reset_trace_id(t_tok)
        obs.capture(None)
    assert obs.get_session() is None
    (line,) = sink
    assert line["session"] == "agent-u1-chat-th1" and line["trace_id"] == "t-sess"
    _conforms(line)
    # unbound -> the key is absent, not empty
    obs.capture(sink := [])
    try:
        obs.log_event("x", audience="system")
    finally:
        obs.capture(None)
    assert "session" not in sink[0]


def test_session_is_never_read_from_a_header():
    _, lines = _run({"X-Session": "forged", "x-user-delegation-unit": "forged"})
    assert all("session" not in l for l in lines)


def test_flows_publish_forwards_trace_id(monkeypatch):
    import httpx

    from admin_api.app import events

    seen = {}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            seen.update(headers or {})
            return httpx.Response(202)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setenv(events.FLOWS_API_URL_ENV, "http://flows-api:8000")
    tok = obs.set_trace_id("t-flows")
    try:
        ok = asyncio.run(events.publish("onboarding.completed", "onboarding-1", {"subject": "1"}))
    finally:
        obs.reset_trace_id(tok)
    assert ok
    assert seen.get("X-Trace-Id") == "t-flows"

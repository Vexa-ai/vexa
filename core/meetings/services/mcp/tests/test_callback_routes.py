"""The routes this service calls back into the gateway are the routes the gateway admits it on.

A worker's delegation token reaches this service on `/mcp`; every tool then calls the gateway with
that same token and the identity the gateway signed onto the `/mcp` request (`reentry.py`). The
gateway admits that call only on a route whose `routes.v1` row says `"mcp_reentry": true` — so the
set of routes this service's tools call and the set of rows flagged there must be the same set:

  * a tool that calls an unflagged route is a tool that fails for every worker;
  * a flagged row no tool calls is a door held open for nothing.

So these tests DRIVE every tool that reaches the gateway — the hand-written meeting tools, the
identity read an `auth: admin` tool asks first, and every assembled agent tool, which goes back
through the gateway at its declared `forward` — record the URL each one actually sent, and hold the
two sets equal. The rows are read from the files the gateway assembles its table from.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Dict, List, Optional, Set, Tuple

import httpx
import pytest
from fastapi.testclient import TestClient

from vexa_mcp import create_app, register

REPO = pathlib.Path(__file__).resolve().parents[5]
GATEWAY_HOST = "gateway.test"
MANIFESTS = {
    "gateway": REPO / "core" / "gateway" / "services" / "gateway" / "routes.v1.json",
    "meetings": REPO / "core" / "meetings" / "routes.v1.json",
    "agent": REPO / "core" / "agent" / "routes.v1.json",
}
AGENT_TOOLS = REPO / "core" / "agent" / "mcp.tools.v1.json"
AGENT_OPENAPI = REPO / "core" / "agent" / "mcp.tools.v1.openapi.json"
CATCH_ALL = "{path:path}"

Row = Tuple[str, str]


def _rows(domain: str) -> List[dict]:
    path = MANIFESTS[domain]
    return json.loads(path.read_text())["routes"] if path.exists() else []


def _flagged(domains) -> Set[Row]:
    return {(r["method"], r["path"]) for d in domains for r in _rows(d) if r.get("mcp_reentry")}


def _specificity(template: str, path: str) -> Optional[int]:
    """How specifically `template` matches the raw `path`, or None. A whole `{name}` segment matches
    one non-empty segment and the catch-all any tail; literal segments are what make a row win, as
    a literal route wins over a parameter or the catch-all at the gateway."""
    t, p = template.split("/"), path.split("/")
    if t[-1] == CATCH_ALL:
        head = t[:-1]
        if len(p) > len(head) and p[:len(head)] == head and all(p[len(head):]):
            return -1
        return None
    if len(t) != len(p):
        return None
    literal = 0
    for a, b in zip(t, p):
        if a.startswith("{") and a.endswith("}"):
            if not b:
                return None
        elif a != b:
            return None
        else:
            literal += 1
    return literal


def _row_for(method: str, path: str, domains) -> Optional[Row]:
    """The row the gateway serves `method path` from: the most specific one that matches."""
    best: Optional[Tuple[int, Row]] = None
    for d in domains:
        for r in _rows(d):
            if r["method"] != method:
                continue
            s = _specificity(r["path"], path)
            if s is not None and (best is None or s > best[0]):
                best = (s, (r["method"], r["path"]))
    return best[1] if best else None


class Recorder:
    def __init__(self) -> None:
        self.sent: List[httpx.Request] = []
        self.routes: Dict[Row, Tuple[int, object]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        if request.url.path == "/auth/me":
            return httpx.Response(200, json={"user_id": 1, "is_admin": True})
        status, body = self.routes.get((request.method, request.url.path), (200, {"ok": True}))
        return httpx.Response(status, json=body)

    def to_gateway(self) -> List[Row]:
        return [(r.method, r.url.raw_path.decode("ascii").partition("?")[0])
                for r in self.sent if r.url.host == GATEWAY_HOST]


AUTH = {"Authorization": "Bearer vxd_a.b.c"}

#: Every hand-written tool that reaches the gateway, called the way an agent calls it. The tricky
#: ids are deliberate: the URL must still land on its row with them in it.
HAND_WRITTEN = [
    ("POST", "/request-meeting-bot", {"json": {"native_meeting_id": "abc-defg-hij"}}),
    ("GET", "/bot-status", {}),
    ("PUT", "/bot-config", {"params": {"native_meeting_id": "a/b"}, "json": {"language": "es"}}),
    ("DELETE", "/bot", {"params": {"native_meeting_id": ".."}}),
    ("GET", "/meetings", {}),
    ("GET", "/meeting-transcript", {"params": {"native_meeting_id": "a?b#c"}}),
    ("GET", "/meeting-transcript", {"params": {"meeting_db_id": 7}}),
    ("GET", "/transcript-search", {"params": {"q": "renewal"}}),
    ("POST", "/meeting-annotate", {"params": {"native_meeting_id": "%2F"}, "json": {"title": "t"}}),
    ("POST", "/meeting-annotate", {"params": {"meeting_db_id": 7}, "json": {"title": "t"}}),
    ("POST", "/meeting-speak", {"params": {"native_meeting_id": "."},
                                "json": {"text": "hi", "asked_by_a_human": True}}),
    ("GET", "/meeting-chat", {"params": {"native_meeting_id": "abc"}}),
    ("GET", "/recordings", {}),
    ("GET", "/recordings/5", {}),
]


def _hand_written_calls(monkeypatch) -> List[Row]:
    rec = Recorder()
    # request_meeting_bot's idempotent path: a 409 on POST /bots reads GET /meetings
    rec.routes[("POST", "/bots")] = (409, {"detail": "exists"})
    client = TestClient(create_app(f"http://{GATEWAY_HOST}", transport=httpx.MockTransport(rec.handler)))
    for method, path, kwargs in HAND_WRITTEN:
        r = client.request(method, path, headers=AUTH, **kwargs)
        assert r.status_code == 200, (path, r.status_code, r.text)
    monkeypatch.setenv("VEXA_TICKET_SINK_URL", "http://sink.test/tickets")
    r = client.post("/report-issue", headers=AUTH, json={
        "what_i_tried": "x", "what_happened": "y", "deployment": "cloud",
        "native_meeting_id": "abc-defg-hij", "platform": "google_meet"})
    assert r.status_code == 200, r.text
    # what an `auth: admin` tool asks before it spends the deployment's key
    asyncio.run(register._require_instance_admin(
        "vxd_a.b.c", f"http://{GATEWAY_HOST}", httpx.MockTransport(rec.handler)))
    return rec.to_gateway()


def test_every_hand_written_tool_lands_on_a_row_that_admits_the_re_entry(monkeypatch):
    domains = ("gateway", "meetings")
    for method, path in _hand_written_calls(monkeypatch):
        row = _row_for(method, path, domains)
        assert row is not None, f"{method} {path} is no route the gateway serves"
        assert row in _flagged(domains), f"{method} {path} lands on {row}, which does not say mcp_reentry"


def test_every_flagged_meetings_and_gateway_row_is_one_a_tool_calls(monkeypatch):
    domains = ("gateway", "meetings")
    called = {_row_for(m, p, domains) for m, p in _hand_written_calls(monkeypatch)}
    assert _flagged(domains) == called, {"flagged, never called": sorted(_flagged(domains) - called),
                                         "called, not flagged": sorted(called - _flagged(domains))}


@pytest.mark.skipif(not (AGENT_TOOLS.exists() and MANIFESTS["agent"].exists()),
                    reason="this cut ships no agent domain")
def test_every_assembled_agent_tool_lands_on_a_row_that_admits_the_re_entry_and_no_other_row_does():
    tools = json.loads(AGENT_TOOLS.read_text())
    openapi = json.loads(AGENT_OPENAPI.read_text())

    def boot(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if not url.startswith("http://agent"):
            return httpx.Response(404)
        if url.endswith("/.well-known/mcp-tools.json"):
            return httpx.Response(200, json=tools)
        if url.endswith("/openapi.json"):
            return httpx.Response(200, json=openapi)
        return httpx.Response(404)

    rec = Recorder()
    app = create_app(f"http://{GATEWAY_HOST}", transport=httpx.MockTransport(rec.handler),
                     assembly_env={"ADMIN_API_URL": "http://identity", "AGENT_API_URL": "http://agent"},
                     assembly_transport=httpx.MockTransport(boot))
    client = TestClient(app)
    for t in tools["tools"]:
        route = t["route"]
        params = {seg[1:-1]: "op/../1" for seg in route["path"].split("/")
                  if seg.startswith("{") and seg.endswith("}")}
        r = client.request(route["method"], f"/tools/{t['name']}", params=params, headers=AUTH,
                           json={} if route["method"] in ("POST", "PUT", "PATCH") else None)
        assert r.status_code == 200, (t["name"], r.status_code, r.text)

    sent = rec.to_gateway()
    assert len(sent) == len(tools["tools"]), "every agent tool goes back through the gateway"
    called = set()
    for method, path in sent:
        row = _row_for(method, path, ("agent",))
        assert row is not None and row in _flagged(("agent",)), (method, path, row)
        called.add(row)
    assert called == _flagged(("agent",))

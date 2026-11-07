"""Shared eval fixtures — an in-process fake GATEWAY behind httpx.MockTransport.

All autonomous (the repo idiom): no docker, no network. ``create_app`` takes the transport
as an injected port, so the tests drive the SHIPPED forwarding path; the fake gateway
records every request (method, path, headers, params, json) and replies from a
configurable route table.
"""
from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pytest
from fastapi.testclient import TestClient

from vexa_mcp import create_app

# The suite runs as a dev stack: every switchable feature on (`VEXA_FEATURES=all`), so the tool counts
# are the full surface. tests/test_features.py passes its own `features_env` for the release default.
os.environ.setdefault("VEXA_FEATURES", "all")

GATEWAY_URL = "http://gateway.test"
API_KEY = "test-api-key-123"

# THE MEETINGS DOMAIN'S OWN TOOLS (`core/meetings/mcp.tools.v1.json`), assembled against the slice of
# meeting-api's OpenAPI those routes occupy (`core/meetings/mcp.tools.v1.openapi.json`, held true by
# meeting-api's tests/test_mcp_manifest_routes.py). A deployment always carries meetings, so the
# suite's default app does too: `meetings_assembly()` is the discovery half of that.
#: `core/` is four levels up wherever the MCP package sits (core/meetings/services/mcp before 0.13.3,
#: core/gateway/services/mcp from it), so the meetings module is named, not counted to.
_MEETINGS = pathlib.Path(__file__).resolve().parents[4] / "meetings"
MEETINGS_MANIFEST = json.loads((_MEETINGS / "mcp.tools.v1.json").read_text())
MEETINGS_OPENAPI = json.loads((_MEETINGS / "mcp.tools.v1.openapi.json").read_text())
MEETING_API_URL = "http://meeting-api.test"
MEETINGS_TOOLS = frozenset(t["name"] for t in MEETINGS_MANIFEST["tools"])


def meetings_assembly(env: Optional[dict] = None, answer: Optional[Any] = None) -> Dict[str, Any]:
    """`create_app` keyword arguments that assemble meetings' manifest (and `env`'s other domains,
    answered by `answer(request)` when given, else 404 — "no manifest here")."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "meeting-api.test":
            if request.url.path == "/.well-known/mcp-tools.json":
                return httpx.Response(200, json=MEETINGS_MANIFEST)
            if request.url.path == "/openapi.json":
                return httpx.Response(200, json=MEETINGS_OPENAPI)
            return httpx.Response(404)
        return answer(request) if answer else httpx.Response(404)

    return {
        "assembly_env": {"ADMIN_API_URL": "http://identity.test", "MEETING_API_URL": MEETING_API_URL,
                         "VEXA_MCP_BOOT_PROBE_PAUSE_SECONDS": "0", **(env or {})},
        "assembly_transport": httpx.MockTransport(handler),
    }


@dataclass
class FakeGateway:
    """Records every hop the MCP service makes; replies from `routes`."""
    requests: List[httpx.Request] = field(default_factory=list)
    # (method, path) -> (status_code, json_body)
    routes: Dict[Tuple[str, str], Tuple[int, Any]] = field(default_factory=dict)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key in self.routes:
            status, body = self.routes[key]
            return httpx.Response(status, json=body)
        return httpx.Response(200, json={"ok": True, "path": request.url.path})

    def last_json(self) -> Any:
        return json.loads(self.requests[-1].content)


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def client(gateway: FakeGateway) -> TestClient:
    app = create_app(GATEWAY_URL, transport=httpx.MockTransport(gateway.handler), **meetings_assembly())
    return TestClient(app)


@pytest.fixture
def auth() -> Dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"}

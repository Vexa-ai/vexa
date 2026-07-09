"""Shared eval fixtures — an in-process fake GATEWAY behind httpx.MockTransport.

All autonomous (the repo idiom): no docker, no network. ``create_app`` takes the transport
as an injected port, so the tests drive the SHIPPED forwarding path; the fake gateway
records every request (method, path, headers, params, json) and replies from a
configurable route table.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

import httpx
import pytest
from fastapi.testclient import TestClient

from vexa_mcp import create_app

GATEWAY_URL = "http://gateway.test"
API_KEY = "test-api-key-123"


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
    app = create_app(GATEWAY_URL, transport=httpx.MockTransport(gateway.handler))
    return TestClient(app)


@pytest.fixture
def auth() -> Dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"}

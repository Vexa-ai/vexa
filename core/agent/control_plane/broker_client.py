"""The one HTTP client agent-api uses to reach the credential broker (credential-broker.v1).

Two callers share it: `routers/connections.py` (role `agent`) and `git_secret_store.py` (role
`git`). Each names its own door and key — the two roles stay separate keys, held by this process
alone — and this module signs with the vendored contract signer (`broker_assertion.py`).

A failure is a `BrokerFault` with a `kind` — `config` (no door or no usable key), `transport` (the
broker did not answer), `http_<status>` (it answered with a refusal), `parse` (it answered with
something that is not JSON) — and is logged once, here, as one structured line naming the source,
role, kind and route. Never a value: not the key, not the body, not the response.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Optional

import httpx

from control_plane import broker_assertion

log = logging.getLogger(__name__)
_CID = re.compile(r"/[a-f0-9]{32}(?=/|$)")


class BrokerFault(Exception):
    def __init__(self, kind: str, status: Optional[int] = None) -> None:
        super().__init__(f"credential broker fault: {kind}")
        self.kind = kind
        self.status = status


def route_of(path: str) -> str:
    return _CID.sub("/{cid}", path.split("?", 1)[0])


def fault(kind: str, *, role: str, method: str, path: str, status: Optional[int] = None) -> BrokerFault:
    log.warning(json.dumps({"event": "broker_fault", "source": "credential-broker", "kind": kind, "role": role,
                            "method": method, "route": route_of(path), "status": status},
                           separators=(",", ":")))
    return BrokerFault(kind, status)


def request(*, base_url: str, key_file: str, role: str, actor: str, method: str, path: str,
            payload: Any = None, timeout: float = 60) -> httpx.Response:
    """Sign and send one request. Returns the broker's response whatever its status; raises
    BrokerFault only when there is no response to interpret."""
    if not base_url or not key_file:
        raise fault("config", role=role, method=method, path=path)
    try:
        key = broker_assertion.load_key(key_file)
    except broker_assertion.KeyUnavailable:
        raise fault("config", role=role, method=method, path=path) from None
    body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else b""
    header = broker_assertion.sign(key, role=role, actor=str(actor), session=f"{role}-request-{uuid.uuid4().hex}",
                                   method=method, path=path, body=body)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            return client.request(method, base_url.rstrip("/") + path, content=body,
                                  headers={broker_assertion.HEADER: header, "Content-Type": "application/json"})
    except httpx.HTTPError:
        raise fault("transport", role=role, method=method, path=path) from None


def json_of(response: httpx.Response, *, role: str, method: str, path: str) -> Any:
    try:
        return response.json()
    except ValueError:
        raise fault("parse", role=role, method=method, path=path, status=response.status_code) from None

"""The one HTTP client agent-api uses to reach the credential broker (credential-broker.v1).

Two callers share it: `routers/connections.py` (role `agent`) and `git_secret_store.py` (role
`git`). Each names its own door and key — the two roles stay separate keys, held by this process
alone — and this module signs with the vendored contract signer (`broker_assertion.py`).

Every request, in either role, also carries the gateway's signed identity (gateway-identity.v1
`X-Vexa-Identity`) for the person it acts for, forwarded exactly as agent-api received it: the
broker verifies it with the gateway's public key and refuses the call without it, so neither key
alone acts for anybody. The connection routes read it off their request; `ForwardedIdentity` holds
it for the life of a request so the Git store, which is called deep inside workspace operations,
can forward it too (`forwarded()`).

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
from contextvars import ContextVar
from typing import Any, Optional, Tuple

import httpx

from control_plane import broker_assertion, identity_token

log = logging.getLogger(__name__)
_CID = re.compile(r"/[a-f0-9]{32}(?=/|$)")


class BrokerFault(Exception):
    def __init__(self, kind: str, status: Optional[int] = None) -> None:
        super().__init__(f"credential broker fault: {kind}")
        self.kind = kind
        self.status = status


def route_of(path: str) -> str:
    return _CID.sub("/{cid}", path.split("?", 1)[0])


def fault(kind: str, *, role: str, method: str, path: str, status: Optional[int] = None,
          reason: Optional[str] = None) -> BrokerFault:
    line = {"event": "broker_fault", "source": "credential-broker", "kind": kind, "role": role,
            "method": method, "route": route_of(path), "status": status}
    if reason:
        line["reason"] = reason
    log.warning(json.dumps(line, separators=(",", ":")))
    return BrokerFault(kind, status)


#: The person the request being served acts for, as the gateway signed them: (subject, token).
_FORWARDED: ContextVar[Tuple[str, str]] = ContextVar("broker_forwarded_identity", default=("", ""))
_TOKEN_HEADER = identity_token.HEADER.encode("latin-1")
_SUBJECT_HEADER = identity_token.CLAIM_HEADERS["sub"].encode("latin-1")


class ForwardedIdentity:
    """ASGI: hold the request's gateway-signed person for the broker calls made while serving it.

    Installed inside `IdentityGuard`, so what it holds has passed the guard: the token verified,
    and `x-user-id` rebuilt from its claims. A request without both (the internal tier, a probe)
    holds nothing, and a git-role call made for it is refused before anything is sent. The context
    is the request's; a thread a request starts sees it only when started under
    `contextvars.copy_context()`. The same shape as the MCP edge's `reentry.py`."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        token = subject = ""
        for name, value in scope.get("headers") or ():
            if name.lower() == _TOKEN_HEADER:
                token = value.decode("latin-1").strip()
            elif name.lower() == _SUBJECT_HEADER:
                subject = value.decode("latin-1").strip()
        held = _FORWARDED.set((subject, token) if subject and token else ("", ""))
        try:
            return await self.app(scope, receive, send)
        finally:
            _FORWARDED.reset(held)


def forwarded() -> Tuple[str, str]:
    """``(subject, token)`` for the request being served, or ``("", "")`` outside one."""
    return _FORWARDED.get()


def request(*, base_url: str, key_file: str, role: str, actor: str, method: str, path: str,
            payload: Any = None, timeout: float = 60, identity: str = "") -> httpx.Response:
    """Sign and send one request. Returns the broker's response whatever its status; raises
    BrokerFault only when there is no response to interpret. ``identity`` is the gateway's signed
    identity for ``actor``; the agent role is refused by the broker without it."""
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
            headers = {broker_assertion.HEADER: header, "Content-Type": "application/json"}
            if identity:
                headers[identity_token.HEADER] = identity
            return client.request(method, base_url.rstrip("/") + path, content=body, headers=headers)
    except httpx.HTTPError:
        raise fault("transport", role=role, method=method, path=path) from None


def outage_sentence(response: httpx.Response) -> str:
    """What to say about a broker 502/503 (an upstream or its store is down): the broker's own fixed
    sentence when it sent one, and that it is an outage — never a reason to reconnect. A body that
    is not the broker's sentence is dropped, never echoed."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return ((detail if isinstance(detail, str) else "The connected service is unavailable") +
            " — an outage, not an authorization problem: retry later, and do not ask the person to reconnect.")


def json_of(response: httpx.Response, *, role: str, method: str, path: str) -> Any:
    try:
        return response.json()
    except ValueError:
        raise fault("parse", role=role, method=method, path=path, status=response.status_code) from None

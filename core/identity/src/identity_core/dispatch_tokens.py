"""Per-dispatch signed tokens — the chain of custody for one dispatch (identity.v1 ``DispatchToken``).

A dispatch is often launched by a non-human (a 3am schedule entry, a Gmail webhook, the meetings
integration). We cannot trust the launcher by virtue of it running — the Identity service **mints a
short-lived SIGNED token** carrying ``(subject, launcher, workspace grants, tool grants)``, the Runtime
**attests the workload** and injects it, and every **boundary verifies** it — never the agent.

The token is the SOURCE OF AUTHORITY: a boundary re-derives what a dispatch may do from the *verified
token*, not from the (tamperable) request — so a prompt-injected agent cannot escalate beyond the
token's grants.

Dev tier = **HS256 over a shared key** (``VEXA_DISPATCH_SIGNING_KEY``), behind the same
``mint``/``verify`` interface that k8s fills with SPIRE-issued SVIDs + Keycloak (RFC 8693) token
exchange. The wire form is a compact ``header.payload.sig`` (JWT-shaped, ``typ=vxd``).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass


class DispatchTokenError(Exception):
    """A dispatch token failed to verify. ``code`` is a stable machine reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkspaceGrant:
    """One workspace the dispatch may mount, at a mode. ``mode`` is the write-access source of truth."""

    id: str
    mode: str  # "ro" | "rw"

    def __post_init__(self) -> None:
        if self.mode not in ("ro", "rw"):
            raise ValueError(f"workspace grant mode must be ro|rw, got {self.mode!r}")


@dataclass(frozen=True)
class DispatchClaims:
    """The verified claims of a dispatch token — what the boundaries enforce against."""

    subject: str
    launcher: str
    workspaces: tuple[WorkspaceGrant, ...]
    tools: tuple[str, ...]
    iat: int
    exp: int

    def may_mount(self, workspace_id: str, mode: str) -> bool:
        """The workspace-store boundary: rw only where the token granted rw; ro where granted ro|rw."""
        for g in self.workspaces:
            if g.id == workspace_id:
                return g.mode == "rw" if mode == "rw" else True
        return False

    def may_call(self, tool: str) -> bool:
        """The MCP-gateway boundary: a tool call is allowed only if the token granted it."""
        return tool in self.tools


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _key(key: str | bytes) -> bytes:
    return key.encode("utf-8") if isinstance(key, str) else key


def mint_dispatch_token(
    subject: str,
    launcher: str,
    workspaces: list[WorkspaceGrant] | tuple[WorkspaceGrant, ...],
    tools: list[str] | tuple[str, ...] = (),
    *,
    key: str | bytes,
    ttl_sec: int = 900,
    now: int | None = None,
) -> str:
    """Mint a short-lived signed dispatch token. The Identity service does this at dispatch time."""
    if not subject:
        raise ValueError("dispatch token subject is required")
    if not launcher:
        raise ValueError("dispatch token launcher is required")
    iat = int(now if now is not None else time.time())
    payload = {
        "sub": subject,
        "lch": launcher,
        "ws": [{"id": g.id, "mode": g.mode} for g in workspaces],
        "tools": list(tools),
        "iat": iat,
        "exp": iat + int(ttl_sec),
    }
    header = {"alg": "HS256", "typ": "vxd"}
    signing_input = _b64u(_canon(header)) + "." + _b64u(_canon(payload))
    sig = hmac.new(_key(key), signing_input.encode("ascii"), hashlib.sha256).digest()
    return signing_input + "." + _b64u(sig)


def verify_dispatch_token(token: str, *, key: str | bytes, now: int | None = None) -> DispatchClaims:
    """Verify signature + expiry and return the claims. Boundaries call this; the agent never does.

    Raises ``DispatchTokenError`` with a stable code: ``malformed`` | ``bad-signature`` | ``expired``.
    """
    try:
        h_b64, p_b64, sig_b64 = token.split(".")
    except ValueError as e:
        raise DispatchTokenError("malformed", "dispatch token is not header.payload.sig") from e
    signing_input = f"{h_b64}.{p_b64}"
    expected = hmac.new(_key(key), signing_input.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64u_decode(sig_b64)):
        raise DispatchTokenError("bad-signature", "dispatch token signature mismatch")
    payload = json.loads(_b64u_decode(p_b64))
    now_i = int(now if now is not None else time.time())
    if int(payload.get("exp", 0)) <= now_i:
        raise DispatchTokenError("expired", "dispatch token expired")
    return DispatchClaims(
        subject=payload["sub"],
        launcher=payload["lch"],
        workspaces=tuple(WorkspaceGrant(w["id"], w["mode"]) for w in payload.get("ws", [])),
        tools=tuple(payload.get("tools", ())),
        iat=int(payload["iat"]),
        exp=int(payload["exp"]),
    )


def _canon(obj: dict) -> bytes:
    """Deterministic JSON for signing (sorted keys, no whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")

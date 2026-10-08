"""gateway-identity.v1 — the identity the gateway resolved, signed, and the door that checks it.

The gateway resolves a bearer (an API key, or a worker's delegation token) through identity's
``/internal/validate`` and forwards the request to a domain service. The services behind it read
WHO is calling from ``x-user-*`` headers. A header is a claim anybody on the network can make, so the
gateway also sends ``X-Vexa-Identity``: the same identity, HMAC-SHA256-signed with a secret only the
gateway and the verifying services hold (``VEXA_GATEWAY_IDENTITY_SECRET``), with an issue time and a
short expiry. A service believes ``x-user-*`` only when that signature verifies, and rewrites the
headers from the signed claims so no code behind the door can read an unsigned one.

There is exactly one other way to assert an identity: the INTERNAL TIER. A service that holds
``INTERNAL_API_SECRET`` (flows, agent-api calling meeting-api, the terminal's server-side admin
routes) presents it as ``X-Internal-Secret`` and names the subject it acts for in ``X-User-Id``. That
is the trust boundary the internal tier already is everywhere else in this codebase; the guard below
honours it and nothing weaker.

    token   = "v1." + b64u(claims) + "." + b64u(HMAC_SHA256(secret, "v1." + b64u(claims)))
    claims  = see identity.schema.json — sub, iat, exp, plus what the services read

THIS FILE IS VENDORED, byte for byte, into every package that signs or verifies (the gateway,
agent-api, meeting-api). ``gate:fact-parity`` compares the copies; edit the canonical copy in
``core/gateway/contracts/gateway-identity.v1/`` and copy it out. Standard library only, so it can be.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Iterable, Mapping, Optional

VERSION = "v1"
TYPE = "vexa-identity"
#: The header the gateway signs into and the guard reads.
HEADER = "x-vexa-identity"
#: How long a signed identity lives. One hop, from the gateway to the service it forwards to.
DEFAULT_TTL_SEC = 60
#: The longest lifetime a verifier accepts, whatever the token says about itself.
MAX_TTL_SEC = 300
#: Clock disagreement tolerated between the signer and the verifier, in seconds.
MAX_SKEW_SEC = 30
#: The prefix every identity header the services read carries.
USER_HEADER_PREFIX = "x-user-"
INTERNAL_SECRET_HEADER = "x-internal-secret"

#: claim -> the header a service reads it from. The ONE mapping, used by the signer to stamp the
#: plain headers it sends beside the token and by the guard to rebuild them from verified claims.
CLAIM_HEADERS = {
    "sub": "x-user-id",
    "email": "x-user-email",
    "scopes": "x-user-scopes",
    "limits": "x-user-limits",
    "workspaces": "x-user-workspaces",
    "webhook_url": "x-user-webhook-url",
    "webhook_secret": "x-user-webhook-secret",
    "webhook_events": "x-user-webhook-events",
}
#: The delegation ceiling a worker's token carried, stated to the service so it can refuse a verb
#: that needs a person in the loop. Present only on a delegated identity.
DELEGATION_HEADERS = {
    "regime": "x-user-regime",
    "workspaces": "x-user-delegation-workspaces",
    "target": "x-user-delegation-target",
}

log = logging.getLogger("vexa.identity")


class IdentityError(Exception):
    """A signed identity was offered and is not acceptable. ``reason`` is safe to return to a caller:
    it names the failure class, never the token or the secret."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key(secret: "str | bytes") -> bytes:
    if not secret:
        raise ValueError("the identity secret is required (VEXA_GATEWAY_IDENTITY_SECRET)")
    return secret.encode("utf-8") if isinstance(secret, str) else secret


def claims_from_validation(user: Mapping[str, Any]) -> dict:
    """The claims for a ``/internal/validate`` answer — the shape the gateway already holds.

    Carries every field a service behind the gateway reads, and nothing the services do not."""
    claims: dict = {"sub": str(user["user_id"])}
    if user.get("email"):
        claims["email"] = str(user["email"])
    claims["scopes"] = [str(s) for s in (user.get("scopes") or [])]
    # 0 is a real limit (quota depleted) and an absent field is identity's default of 3; anything
    # that is not a number states no limit at all, which meeting-api reads as "no pre-check".
    limits = user.get("max_concurrent", 3)
    if isinstance(limits, int) and not isinstance(limits, bool):
        claims["limits"] = limits
    if user.get("workspaces"):
        claims["workspaces"] = [str(w) for w in user["workspaces"]]
    if user.get("webhook_url"):
        claims["webhook_url"] = str(user["webhook_url"])
        if user.get("webhook_secret"):
            claims["webhook_secret"] = str(user["webhook_secret"])
        if user.get("webhook_events"):
            claims["webhook_events"] = user["webhook_events"]
    dlg = user.get("delegation")
    if isinstance(dlg, Mapping):
        ws = dlg.get("workspaces")
        claims["delegation"] = {
            "regime": str(dlg.get("regime") or ""),
            "workspaces": "*" if ws == "*" else [str(w) for w in (ws or [])],
        }
        if dlg.get("target"):
            claims["delegation"]["target"] = str(dlg["target"])
    return claims


def sign(secret: "str | bytes", claims: Mapping[str, Any], *, now: Optional[int] = None,
         ttl_sec: int = DEFAULT_TTL_SEC) -> str:
    """Sign ``claims`` (which must name ``sub``) into a token that expires ``ttl_sec`` from now."""
    if not str(claims.get("sub") or "").strip():
        raise ValueError("an identity names a subject")
    iat = int(now if now is not None else time.time())
    payload = {**claims, "typ": TYPE, "iat": iat, "exp": iat + int(ttl_sec)}
    body = VERSION + "." + _b64u(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    sig = hmac.new(_key(secret), body.encode("ascii"), hashlib.sha256).digest()
    return body + "." + _b64u(sig)


def verify(secret: "str | bytes", token: str, *, now: Optional[int] = None,
           max_skew_sec: int = MAX_SKEW_SEC) -> dict:
    """The claims of a token this deployment signed, or :class:`IdentityError`.

    The signature is checked, in constant time, BEFORE any claim is read: until it verifies, the
    payload is text the caller wrote."""
    key = _key(secret)
    parts = (token or "").strip().split(".")
    if len(parts) != 3 or parts[0] != VERSION:
        raise IdentityError("malformed")
    body = parts[0] + "." + parts[1]
    expect = hmac.new(key, body.encode("ascii", "replace"), hashlib.sha256).digest()
    try:
        got = _unb64u(parts[2])
    except (ValueError, TypeError):
        raise IdentityError("malformed") from None
    if not hmac.compare_digest(expect, got):
        raise IdentityError("bad_signature")
    try:
        claims = json.loads(_unb64u(parts[1]))
    except (ValueError, TypeError):
        raise IdentityError("malformed") from None
    if not isinstance(claims, dict) or claims.get("typ") != TYPE:
        raise IdentityError("malformed")
    iat, exp = claims.get("iat"), claims.get("exp")
    if not isinstance(iat, int) or not isinstance(exp, int) or exp <= iat:
        raise IdentityError("malformed")
    if exp - iat > MAX_TTL_SEC:
        raise IdentityError("lifetime_too_long")
    t = int(now if now is not None else time.time())
    if iat > t + max_skew_sec:
        raise IdentityError("not_yet_valid")
    if t >= exp + max_skew_sec:
        raise IdentityError("expired")
    if not isinstance(claims.get("sub"), str) or not claims["sub"].strip():
        raise IdentityError("malformed")
    return claims


def _join(values: Iterable[Any]) -> str:
    return ",".join(str(v) for v in values)


def headers_from_claims(claims: Mapping[str, Any]) -> dict:
    """The plain ``x-user-*`` headers a set of claims stands for, lower-cased names."""
    out: dict = {}
    for claim, header in CLAIM_HEADERS.items():
        if claim not in claims or claims[claim] in (None, "", []):
            continue
        value = claims[claim]
        if claim in ("scopes", "workspaces"):
            out[header] = _join(value)
        elif claim == "webhook_events":
            out[header] = value if isinstance(value, str) else json.dumps(value)
        else:
            out[header] = str(value)
    dlg = claims.get("delegation")
    if isinstance(dlg, Mapping):
        if dlg.get("regime"):
            out[DELEGATION_HEADERS["regime"]] = str(dlg["regime"])
        ws = dlg.get("workspaces")
        if ws == "*":
            out[DELEGATION_HEADERS["workspaces"]] = "*"
        elif isinstance(ws, list):
            out[DELEGATION_HEADERS["workspaces"]] = _join(ws)
        if dlg.get("target"):
            out[DELEGATION_HEADERS["target"]] = str(dlg["target"])
    return out


def signed_headers(secret: "str | bytes", user: Mapping[str, Any], *,
                   now: Optional[int] = None) -> dict:
    """Everything the gateway stamps onto a forward for a resolved ``user``: the plain headers and
    the token that makes them believable."""
    claims = claims_from_validation(user)
    return {**headers_from_claims(claims), HEADER: sign(secret, claims, now=now)}


class IdentityGuard:
    """ASGI middleware: the service-side door for ``x-user-*``.

    Per request, exactly one of:

    * ``X-Vexa-Identity`` present — verified; every ``x-user-*`` header the caller sent is dropped
      and rebuilt from the signed claims. A token that does not verify is a 401.
    * no token, but an ``x-user-*`` header — believed only from the internal tier (a correct
      ``X-Internal-Secret``); otherwise a 401. An unsigned identity is refused, never ignored.
    * neither — the request carries no identity and passes through untouched (health probes,
      callbacks that authenticate themselves, internal-tier routes that name no person).
    """

    def __init__(self, app, *, secret: "str | bytes", internal_secret: str = "",
                 service: str = "") -> None:
        _key(secret)  # refuses to build with no secret: a door with no key opens for everybody
        self.app = app
        self._secret = secret
        self._internal = internal_secret or ""
        self._service = service or "service"

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        raw = list(scope.get("headers") or [])
        token = ""
        asserted = False
        internal = ""
        for name, value in raw:
            n = name.decode("latin-1").lower()
            if n == HEADER:
                token = value.decode("latin-1")
            elif n.startswith(USER_HEADER_PREFIX):
                asserted = True
            elif n == INTERNAL_SECRET_HEADER:
                internal = value.decode("latin-1")
        if token:
            try:
                claims = verify(self._secret, token)
            except IdentityError as e:
                return await self._refuse(scope, receive, send, f"invalid_identity:{e.reason}")
            kept = [(k, v) for k, v in raw
                    if not k.decode("latin-1").lower().startswith(USER_HEADER_PREFIX)
                    and k.decode("latin-1").lower() != HEADER]
            stamped = [(k.encode("latin-1"), v.encode("latin-1"))
                       for k, v in headers_from_claims(claims).items()]
            scope = {**scope, "headers": kept + stamped}
            return await self.app(scope, receive, send)
        if asserted:
            if self._internal and internal and hmac.compare_digest(
                    internal.encode("latin-1"), self._internal.encode("utf-8")):
                return await self.app(scope, receive, send)
            return await self._refuse(scope, receive, send, "unsigned_identity")
        return await self.app(scope, receive, send)

    async def _refuse(self, scope, receive, send, reason: str):
        log.warning("%s refused a request: %s %s (%s)", self._service, scope.get("method", "WS"),
                    scope.get("path", ""), reason)
        if scope.get("type") == "websocket":
            await receive()  # the websocket.connect a close answers
            await send({"type": "websocket.close", "code": 4401})
            return
        body = json.dumps({"detail": f"identity refused: {reason}"}).encode()
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

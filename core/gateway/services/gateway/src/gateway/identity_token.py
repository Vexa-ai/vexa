"""gateway-identity.v1 — the identity the gateway resolved, signed with Ed25519, and the door that checks it.

The gateway resolves a bearer (an API key, or a worker's delegation token) through identity's
``/internal/validate`` and forwards the request to a domain service. The services behind it read
WHO is calling from ``x-user-*`` headers. A header is a claim anybody on the network can make, so the
gateway also sends ``X-Vexa-Identity``: the same identity, signed with the gateway's Ed25519 PRIVATE
key, with an issue time and a short expiry. The services behind it hold only the PUBLIC key: they
can check a signature and cannot make one, so no process except the gateway can name a person this
way. A service believes ``x-user-*`` only when the signature verifies, and rewrites the headers from
the signed claims so no code behind the door can read an unsigned one.

There is exactly one other way to assert an identity to agent-api or meeting-api: the INTERNAL TIER.
A service that holds ``INTERNAL_API_SECRET`` (flows, agent-api calling meeting-api, the terminal's
server-side admin routes) presents it as ``X-Internal-Secret`` and names the subject it acts for in
``X-User-Id``. The guard below honours it and nothing weaker. The credential broker does not: an
agent-role call there must carry the gateway's signature itself (credential-broker.v1).

    token   = "v1." + b64u(claims) + "." + b64u(Ed25519_sign(private_key, "v1." + b64u(claims)))
    claims  = see identity.schema.json — sub, iat, exp, plus what the services read

``v1`` fixes the scheme. The token names no algorithm and none is negotiated: a verifier holds an
Ed25519 public key, accepts exactly a 64-byte Ed25519 signature, and refuses every other key type
at load time — so a token MACed with the public key as a secret, or signed with any other scheme,
is refused like a forgery.

Keys are PEM: the signing key PKCS#8 (``openssl genpkey -algorithm ed25519``), the verification
key SubjectPublicKeyInfo (``openssl pkey -pubout``). ``python identity_token.py keygen SIGNING
PUBLIC`` writes a pair, or derives the public half from a signing key that already exists.

THIS FILE IS VENDORED, byte for byte, into every package that signs or verifies (the gateway,
agent-api, meeting-api, the credential broker). ``gate:fact-parity`` compares the copies; edit the
canonical copy in ``core/gateway/contracts/gateway-identity.v1/`` and copy it out. Standard library plus
``cryptography`` (OpenSSL's Ed25519), which every one of those images carries.
"""
from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import re
import sys
import time
from typing import Any, Iterable, Mapping, Optional

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

VERSION = "v1"
TYPE = "vexa-identity"
#: The header the gateway signs into and the guard reads.
HEADER = "x-vexa-identity"
#: An Ed25519 signature is exactly this long; anything else is refused before it is checked.
SIGNATURE_BYTES = 64
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

_B64U = re.compile(r"[A-Za-z0-9_-]+")

log = logging.getLogger("vexa.identity")


class IdentityError(Exception):
    """A signed identity was offered and is not acceptable. ``reason`` is safe to return to a caller:
    it names the failure class, never the token or a key."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class KeyUnavailable(ValueError):
    """A key is missing, unreadable, or not the kind this role holds (a configuration fault). The
    message names the fault, never the key material."""


# ── keys ────────────────────────────────────────────────────────────────────────────────────────
def _bytes(pem: "str | bytes") -> bytes:
    return pem.encode("utf-8") if isinstance(pem, str) else bytes(pem or b"")


def load_signing_key(pem: "str | bytes") -> Ed25519PrivateKey:
    """The gateway's signing key from PKCS#8 PEM. Any other key type is refused."""
    try:
        key = serialization.load_pem_private_key(_bytes(pem), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise KeyUnavailable("the identity signing key is not an unencrypted PEM private key") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise KeyUnavailable("the identity signing key is not an Ed25519 key")
    return key


def load_verify_key(pem: "str | bytes") -> Ed25519PublicKey:
    """A verifier's key from SubjectPublicKeyInfo PEM. A private key is refused even though the
    public half could be derived from it: a verifier that holds the private key can sign, which is
    the property this key split exists to deny. Any key type but Ed25519 is refused too."""
    data = _bytes(pem)
    if b"PRIVATE KEY" in data:
        raise KeyUnavailable("a verifier holds the identity PUBLIC key only; this is a private key")
    try:
        key = serialization.load_pem_public_key(data)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise KeyUnavailable("the identity verification key is not a PEM public key") from None
    if not isinstance(key, Ed25519PublicKey):
        raise KeyUnavailable("the identity verification key is not an Ed25519 key")
    return key


def _read(path: "str | os.PathLike") -> bytes:
    if not path:
        raise KeyUnavailable("no identity key file is configured")
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        raise KeyUnavailable("the identity key file is unreadable") from None


def read_signing_key(path: "str | os.PathLike") -> Ed25519PrivateKey:
    return load_signing_key(_read(path))


def read_verify_key(path: "str | os.PathLike") -> Ed25519PublicKey:
    return load_verify_key(_read(path))


def generate_signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def private_key_pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def public_key_pem(key: "Ed25519PrivateKey | Ed25519PublicKey") -> bytes:
    pub = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def _write(path: str, data: bytes, mode: int) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def write_keypair(signing_path: str, public_path: str) -> bool:
    """Make sure a signing key exists at ``signing_path`` (0600) and its public half at
    ``public_path`` (0644). An existing signing key is kept and its public half rewritten from it,
    so a lost or stale public file heals; a signing key that does not load is an error, never
    silently replaced. True when a new key was generated."""
    generated = not os.path.exists(signing_path)
    if generated:
        key = generate_signing_key()
        _write(signing_path, private_key_pem(key), 0o600)
    else:
        key = read_signing_key(signing_path)
    _write(public_path, public_key_pem(key), 0o644)
    return generated


# ── the token ───────────────────────────────────────────────────────────────────────────────────
def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64u(text: str) -> bytes:
    """Strict, canonical base64url: the alphabet only, no padding, and the text must be the one
    encoding of what it decodes to — so one signature has one spelling."""
    if not _B64U.fullmatch(text or ""):
        raise ValueError("not base64url")
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if _b64u(raw) != text:
        raise ValueError("not canonical base64url")
    return raw


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


def sign(signing_key: Ed25519PrivateKey, claims: Mapping[str, Any], *, now: Optional[int] = None,
         ttl_sec: int = DEFAULT_TTL_SEC) -> str:
    """Sign ``claims`` (which must name ``sub``) into a token that expires ``ttl_sec`` from now.
    Only the gateway's private key signs; a public key is refused with TypeError."""
    if not isinstance(signing_key, Ed25519PrivateKey):
        raise TypeError("only the gateway's Ed25519 private key signs an identity")
    if not str(claims.get("sub") or "").strip():
        raise ValueError("an identity names a subject")
    iat = int(now if now is not None else time.time())
    payload = {**claims, "typ": TYPE, "iat": iat, "exp": iat + int(ttl_sec)}
    body = VERSION + "." + _b64u(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return body + "." + _b64u(signing_key.sign(body.encode("ascii")))


def verify(verify_key: Ed25519PublicKey, token: str, *, now: Optional[int] = None,
           max_skew_sec: int = MAX_SKEW_SEC) -> dict:
    """The claims of a token the gateway signed, or :class:`IdentityError`.

    The signature is checked BEFORE any claim is read: until it verifies, the payload is text the
    caller wrote. ``verify_key`` is the gateway's public key and nothing else; any other key object
    is a programming error (TypeError), not a refusal."""
    if not isinstance(verify_key, Ed25519PublicKey):
        raise TypeError("an identity is verified with the gateway's Ed25519 public key")
    parts = (token or "").strip().split(".")
    if len(parts) != 3 or parts[0] != VERSION:
        raise IdentityError("malformed")
    try:
        payload = _unb64u(parts[1])
        sig = _unb64u(parts[2])
    except (ValueError, TypeError):
        raise IdentityError("malformed") from None
    if len(sig) != SIGNATURE_BYTES:
        raise IdentityError("bad_signature")
    try:
        verify_key.verify(sig, (parts[0] + "." + parts[1]).encode("ascii"))
    except InvalidSignature:
        raise IdentityError("bad_signature") from None
    try:
        claims = json.loads(payload)
    except (ValueError, TypeError):
        raise IdentityError("malformed") from None
    if not isinstance(claims, dict) or claims.get("typ") != TYPE:
        raise IdentityError("malformed")
    iat, exp = claims.get("iat"), claims.get("exp")
    if (not isinstance(iat, int) or not isinstance(exp, int) or isinstance(iat, bool)
            or isinstance(exp, bool) or exp <= iat):
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


def signed_headers(signing_key: Ed25519PrivateKey, user: Mapping[str, Any], *,
                   now: Optional[int] = None) -> dict:
    """Everything the gateway stamps onto a forward for a resolved ``user``: the plain headers and
    the token that makes them believable."""
    claims = claims_from_validation(user)
    return {**headers_from_claims(claims), HEADER: sign(signing_key, claims, now=now)}


# ── the door ────────────────────────────────────────────────────────────────────────────────────
class IdentityGuard:
    """ASGI middleware: the service-side door for ``x-user-*``.

    Per request, exactly one of:

    * ``X-Vexa-Identity`` present — verified; every ``x-user-*`` header the caller sent is dropped
      and rebuilt from the signed claims. A token that does not verify is a 401. The verified token
      stays on the request, so a route can forward it unchanged (agent-api to the credential
      broker); any ``X-Vexa-Identity`` a route reads has therefore passed this check.
    * no token, but an ``x-user-*`` header — believed only from the internal tier (a correct
      ``X-Internal-Secret``); otherwise a 401. An unsigned identity is refused, never ignored.
    * neither — the request carries no identity and passes through untouched (health probes,
      callbacks that authenticate themselves, internal-tier routes that name no person).

    The guard holds the gateway's PUBLIC key and refuses to be built with anything else, the private
    key included: a door with no key opens for everybody, and a door holding the signing key could
    mint the identities it is meant to check.
    """

    def __init__(self, app, *, verify_key: Ed25519PublicKey, internal_secret: str = "",
                 service: str = "") -> None:
        if not isinstance(verify_key, Ed25519PublicKey):
            raise TypeError("IdentityGuard holds the gateway's Ed25519 public key and nothing else")
        self.app = app
        self._key = verify_key
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
                claims = verify(self._key, token)
            except IdentityError as e:
                return await self._refuse(scope, receive, send, f"invalid_identity:{e.reason}")
            kept = [(k, v) for k, v in raw
                    if not k.decode("latin-1").lower().startswith(USER_HEADER_PREFIX)
                    and k.decode("latin-1").lower() != HEADER]
            stamped = [(k.encode("latin-1"), v.encode("latin-1"))
                       for k, v in headers_from_claims(claims).items()]
            stamped.append((HEADER.encode("latin-1"), token.strip().encode("latin-1")))
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


def _main(argv: list) -> int:
    """``identity_token.py keygen SIGNING PUBLIC`` — the install-time generator (compose's one-shot,
    the Lite entrypoint). Prints which file it wrote, never a key."""
    if len(argv) != 4 or argv[1] != "keygen":
        print("usage: identity_token.py keygen <signing-key.pem> <public-key.pem>", file=sys.stderr)
        return 2
    try:
        generated = write_keypair(argv[2], argv[3])
    except (KeyUnavailable, OSError) as e:
        print(f"identity keygen: {e}", file=sys.stderr)
        return 1
    print(f"identity keygen: {'generated a new signing key' if generated else 'kept the signing key'};"
          f" public key at {argv[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))

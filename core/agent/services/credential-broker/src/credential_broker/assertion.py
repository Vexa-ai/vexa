"""credential-broker.v1 assertion — the ONE Python signer and verifier.

This file is the canonical copy. Every Python package that signs or verifies a broker assertion
vendors it VERBATIM, and gate:fact-parity (scripts/parity.json, fact
`credential-broker-assertion`) fails the build on a single differing byte. The TypeScript twin is
clients/terminal/src/app/api/connections/assertion.ts; both reproduce the golden vectors in
`golden/SignedAssertionVector.*.json`, which is how the two languages are held to one wire.

Wire: one request header,

    X-Vexa-Assertion: <base64url(claims JSON), unpadded>.<hex HMAC-SHA256(role key, encoded part)>

The claims (schema `#/$defs/Assertion`) bind the role, the actor and session, the time, a
single-use nonce, the HTTP method, the exact path with its query, and the SHA-256 of the exact body
bytes. An assertion is accepted for 30 seconds after `at` (5 seconds of clock skew ahead), once.

Each role signs with its own key. A key file holds at least 32 bytes; surrounding whitespace is
stripped, so a key generated as hex text and saved with a trailing newline reads the same in both
languages. Stdlib only: this module is vendored into images that must not grow a dependency.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path
from typing import Callable, Mapping, Optional

HEADER = "X-Vexa-Assertion"
ROLES = ("agent", "human", "git")
METHODS = ("GET", "POST")
MAX_AGE_S = 30
MAX_SKEW_S = 5
NONCE_TTL_S = MAX_AGE_S + MAX_SKEW_S + 25
MIN_KEY_BYTES = 32
_FIELDS = ("role", "actor", "session", "at", "nonce", "method", "path", "body")
_TEXT_LIMIT = 160
_PATH_LIMIT = 16384


class AssertionRefused(ValueError):
    """An assertion was refused. `kind` names why (malformed · role · signature · expired ·
    binding · replay) for a typed log line; the message never carries a claim value, a key or a
    body."""

    def __init__(self, kind: str, message: str = "Product identity refused") -> None:
        super().__init__(message)
        self.kind = kind


class KeyUnavailable(ValueError):
    """A role key is missing, unreadable or shorter than MIN_KEY_BYTES (a configuration fault)."""


def load_key(path: "str | Path") -> bytes:
    """Read one role key file. Refuses a short key rather than signing with it."""
    if not path:
        raise KeyUnavailable("no key file configured")
    try:
        key = Path(path).read_bytes().strip()
    except OSError:
        raise KeyUnavailable("key file unreadable") from None
    if len(key) < MIN_KEY_BYTES:
        raise KeyUnavailable("key shorter than %d bytes" % MIN_KEY_BYTES)
    return key


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _encode(claims: Mapping[str, object]) -> str:
    raw = json.dumps({k: claims[k] for k in _FIELDS}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _signature(key: bytes, encoded: str) -> str:
    return hmac.new(key, encoded.encode(), hashlib.sha256).hexdigest()


def sign(
    key: bytes,
    *,
    role: str,
    actor: str,
    session: str,
    method: str,
    path: str,
    body: bytes = b"",
    at: Optional[int] = None,
    nonce: Optional[str] = None,
) -> str:
    """Return the header value for one request. `path` is the exact path plus `?query`."""
    if role not in ROLES:
        raise ValueError("unknown role")
    if method not in METHODS:
        raise ValueError("unsupported method")
    if len(key) < MIN_KEY_BYTES:
        raise KeyUnavailable("key shorter than %d bytes" % MIN_KEY_BYTES)
    claims = {
        "role": role,
        "actor": str(actor),
        "session": str(session),
        "at": int(time.time()) if at is None else int(at),
        "nonce": nonce or uuid.uuid4().hex,
        "method": method,
        "path": path,
        "body": body_digest(body),
    }
    encoded = _encode(claims)
    return encoded + "." + _signature(key, encoded)


def verify(
    header: str,
    *,
    key_for: Callable[[str], bytes],
    method: str,
    path: str,
    body: bytes,
    now: Optional[float] = None,
    remember: Optional[Callable[[str, float], bool]] = None,
) -> dict:
    """Return the verified claims, or raise AssertionRefused.

    `key_for(role)` returns that role's key and raises KeyUnavailable when the role is not
    configured here. `remember(nonce, expires_at)` records a nonce and returns False when it was
    already seen; without it replay is not checked, which only a unit test should want.
    """
    try:
        encoded, signature = header.split(".")
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        claims = json.loads(raw)
    except (ValueError, TypeError, AttributeError):
        raise AssertionRefused("malformed") from None
    if not isinstance(claims, dict) or set(claims) != set(_FIELDS):
        raise AssertionRefused("malformed")
    role = claims["role"]
    if role not in ROLES:
        raise AssertionRefused("role")
    try:
        key = key_for(role)
    except KeyUnavailable:
        raise AssertionRefused("role") from None
    if not hmac.compare_digest(_signature(key, encoded), signature):
        raise AssertionRefused("signature")
    if (
        claims["method"] not in METHODS
        or not isinstance(claims["path"], str)
        or not claims["path"].startswith("/api/")
        or len(claims["path"]) > _PATH_LIMIT
        or not isinstance(claims["body"], str)
        or not isinstance(claims["at"], int)
        or isinstance(claims["at"], bool)
        or any(
            not isinstance(claims[k], str) or not 1 <= len(claims[k]) <= _TEXT_LIMIT
            for k in ("actor", "session", "nonce")
        )
    ):
        raise AssertionRefused("malformed")
    current = time.time() if now is None else now
    if not current - MAX_AGE_S <= claims["at"] <= current + MAX_SKEW_S:
        raise AssertionRefused("expired")
    if claims["method"] != method or claims["path"] != path or claims["body"] != body_digest(body):
        raise AssertionRefused("binding")
    if remember is not None and not remember(claims["nonce"], current + NONCE_TTL_S):
        raise AssertionRefused("replay")
    return dict(claims)

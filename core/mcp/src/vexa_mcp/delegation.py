"""delegation.py — the short-lived, scoped, revocable token a chat worker presents to the Vexa MCP.

THE PROBLEM THIS REPLACES. A worker that needs the vexa-control MCP used to need a DURABLE user
credential (a ``vxa_mcp_…`` token minted by the rig and pasted into a config). A durable credential in
a spawned container is the wrong shape three ways: it does not expire, so a leaked worker env is a
permanent account takeover; it carries the FULL account, so an autonomous routine gets exactly the
same reach as the human sitting in chat; and it cannot be withdrawn without rotating the human's own
token. The dispatch already knows WHO it acts for and WHY it fired — so it can mint a credential that
says only that, lives minutes, and can be struck off by id.

THE TOKEN. A compact HS256 JWS, deliberately the same shape as ``adapters.LocalIdentityMinter``'s
dispatch token (one signing idiom in this codebase, not two), with a ``vxd_`` prefix so a verifier can
tell a delegation token from the rig's opaque ``vxa_mcp_…`` durable tokens WITHOUT trying to parse it:

    vxd_<b64u(header)>.<b64u(payload)>.<b64u(sig)>

    header  {"alg":"HS256","typ":"vxdlg"}
    payload {"sub": "<uid>",            the Vexa uid the worker acts for — resolved, never asserted
             "aud": "vexa-mcp",         audience pin: this token is for the control MCP and nothing else
             "scope": {"regime": "human"|"autonomous",
                       "workspaces": "*" | ["slug", …]},
             "iat": <unix>, "exp": <unix>, "jti": "<random>"}

REGIME IS THE POINT. ``human`` = a person is in the loop this turn, so the scope is SOFT: ``workspaces:
"*"`` — everything that is already theirs, because the human can see and correct what the agent does.
``autonomous`` = a schedule/event/transcription fired with nobody watching, so the scope is HARD: the
exact isolation set, and a workspace outside it is refused at the rig. The regime is DERIVED from
``unit.v1.trigger`` (``message`` ⇒ human; ``scheduled``/``event``/``transcription`` ⇒ autonomous) — the
same field the contract already uses to derive input-trust, so the two trust axes cannot drift apart.

REVOCATION. Stateless verification plus a small denylist of ``jti`` values. This is the honest trade:
the signature+expiry check needs no shared store (the rig and agent-api share only a secret), while a
token that must die BEFORE its exp is struck off by id in a file the verifier reads per call. The
denylist stays small because entries older than the longest TTL can be pruned — an expired token is
already refused by the exp check, so its jti no longer needs listing.

THE SECRET is symmetric and lives in the environment on both sides (``VEXA_MCP_DELEGATION_SECRET``);
this module never reads it — callers pass it in, which keeps the module pure, testable, and out of the
config.v1 undeclared-read scan. An EMPTY secret is fatal on both mint and verify: a zero-length HMAC
key would "work" and authenticate anyone who guessed the format.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Iterable, Optional

PREFIX = "vxd_"
AUDIENCE = "vexa-mcp"
DEFAULT_TTL_SEC = 3600

# unit.v1 triggers that mean A HUMAN IS IN THE LOOP this turn. Everything else runs unwatched.
HUMAN_TRIGGERS = frozenset({"message"})


class DelegationError(Exception):
    """Base: a delegation token was offered and is not acceptable. ``reason`` is SAFE to show a caller
    (it names the failure class, never the token or the secret)."""

    reason = "invalid_delegation"


class NotDelegated(DelegationError):
    """Not a delegation token at all (no ``vxd_`` prefix) — the caller should try its other schemes."""

    reason = "not_delegated"


class BadSignature(DelegationError):
    reason = "bad_signature"


class Expired(DelegationError):
    reason = "expired"


class Revoked(DelegationError):
    reason = "revoked"


class BadAudience(DelegationError):
    reason = "bad_audience"


class Malformed(DelegationError):
    reason = "malformed"


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64u(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def _canon(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _key(secret: str | bytes) -> bytes:
    return secret.encode("utf-8") if isinstance(secret, str) else secret


def regime_for_trigger(trigger: str) -> str:
    """``unit.v1.trigger`` → the delegation regime. The ONE place the mapping lives, so the dispatcher
    and any future verifier cannot disagree about what counts as human-in-the-loop."""
    return "human" if trigger in HUMAN_TRIGGERS else "autonomous"


def is_delegation_token(token: str) -> bool:
    """Cheap discriminator — does this bearer value even claim to be a delegation token? Lets a verifier
    fall through to its OTHER token schemes without paying a parse, and without a failed parse being
    mistaken for a failed AUTH (the distinction the rig's 401 reasons depend on)."""
    return isinstance(token, str) and token.startswith(PREFIX)


def mint_delegation(
    secret: str | bytes,
    *,
    subject: str,
    regime: str = "human",
    workspaces: "str | Iterable[str]" = "*",
    ttl_sec: int = DEFAULT_TTL_SEC,
    now: Optional[int] = None,
    jti: Optional[str] = None,
) -> str:
    """Mint a delegation token for ``subject``. ``workspaces`` is ``"*"`` (all of theirs — only sane for
    the human regime) or an explicit list of slugs (the isolation set). Raises ``ValueError`` on a
    missing secret/subject, an unknown regime, or the contradiction ``autonomous`` + ``"*"`` — an
    unwatched dispatch must never carry an unbounded grant, and silently narrowing it would hide a
    dispatcher bug instead of surfacing it."""
    if not secret:
        raise ValueError("delegation secret is required (VEXA_MCP_DELEGATION_SECRET)")
    if not subject:
        raise ValueError("delegation subject is required")
    if regime not in ("human", "autonomous"):
        raise ValueError(f"regime must be human|autonomous, got {regime!r}")
    if workspaces != "*":
        workspaces = sorted({str(w) for w in workspaces})
    elif regime == "autonomous":
        raise ValueError('an autonomous dispatch may not carry workspaces="*" — pass its isolation set')
    iat = int(now if now is not None else time.time())
    payload = {
        "sub": str(subject),
        "aud": AUDIENCE,
        "scope": {"regime": regime, "workspaces": workspaces},
        "iat": iat,
        "exp": iat + int(ttl_sec),
        "jti": jti or secrets.token_urlsafe(12),
    }
    header = {"alg": "HS256", "typ": "vxdlg"}
    body = _b64u(_canon(header)) + "." + _b64u(_canon(payload))
    sig = hmac.new(_key(secret), body.encode("ascii"), hashlib.sha256).digest()
    return PREFIX + body + "." + _b64u(sig)


def verify_delegation(
    secret: str | bytes,
    token: str,
    *,
    now: Optional[int] = None,
    revoked: "Optional[Iterable[str]]" = None,
) -> dict:
    """Verify a delegation token and return its claims. Raises a ``DelegationError`` subclass naming the
    failure — the caller turns ``.reason`` into its own refusal.

    ORDER MATTERS: signature BEFORE any claim is trusted (an unverified payload is attacker-controlled
    text, so reading exp/jti out of it first would let a forged token steer the check that is supposed
    to catch it)."""
    if not secret:
        raise ValueError("delegation secret is required (VEXA_MCP_DELEGATION_SECRET)")
    if not is_delegation_token(token):
        raise NotDelegated("not a delegation token")
    parts = token[len(PREFIX):].split(".")
    if len(parts) != 3:
        raise Malformed("delegation token must have three parts")
    body = parts[0] + "." + parts[1]
    expect = hmac.new(_key(secret), body.encode("ascii"), hashlib.sha256).digest()
    try:
        got = _unb64u(parts[2])
    except Exception:
        raise Malformed("delegation signature is not base64url")
    if not hmac.compare_digest(expect, got):
        raise BadSignature("delegation signature does not verify")
    try:
        claims = json.loads(_unb64u(parts[1]))
    except Exception:
        raise Malformed("delegation payload is not JSON")
    if not isinstance(claims, dict):
        raise Malformed("delegation payload is not an object")
    if claims.get("aud") != AUDIENCE:
        raise BadAudience("delegation token is for a different audience")
    if not claims.get("sub"):
        raise Malformed("delegation token names no subject")
    exp = claims.get("exp")
    if not isinstance(exp, int) or int(now if now is not None else time.time()) >= exp:
        raise Expired("delegation token has expired")
    if revoked and claims.get("jti") in set(revoked):
        raise Revoked("delegation token has been revoked")
    return claims


def scope_allows_workspace(claims: dict, slug: str) -> bool:
    """May this token touch workspace ``slug``? ``"*"`` allows everything the ACCOUNT already allows —
    the grant is a ceiling on the dispatch, never a grant of something the uid could not otherwise
    reach; the rig still applies its own per-uid ownership checks underneath. A malformed/absent scope
    fails CLOSED."""
    scope = claims.get("scope")
    if not isinstance(scope, dict):
        return False
    ws = scope.get("workspaces")
    if ws == "*":
        return True
    return isinstance(ws, list) and str(slug) in {str(w) for w in ws}

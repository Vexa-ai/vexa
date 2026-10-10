"""delegation.py — the short-lived, scoped token a worker presents as the person it acts for (delegation.v1).

WHY A TOKEN PER DISPATCH. A worker runs in a spawned container. A durable account credential there
would not expire, would carry the whole account into an unwatched routine, and could not be withdrawn
without rotating the person's own key. The dispatch already knows WHO it acts for and WHY it fired,
so agent-api mints a credential that says only that and expires with the worker: its lifetime is the
chat warm window plus one turn (1800 s by default, ``VEXA_MCP_DELEGATION_TTL_SEC``), and the token
is revoked when the worker's unit ends.

THE TOKEN. A compact HS256 JWS, the same signing idiom as ``adapters.LocalIdentityMinter``'s dispatch
token, with a ``vxd_`` prefix so a verifier can tell it from an API key WITHOUT trying to parse it:

    vxd_<b64u(header)>.<b64u(payload)>.<b64u(sig)>

    header  {"alg":"HS256","typ":"vxdlg"}
    payload {"sub": "<uid>",            the Vexa uid the worker acts for — resolved, never asserted
             "aud": "vexa-mcp",         audience pin: the MCP edge and its re-entry, nothing else
             "scope": {"regime": "human"|"autonomous",
                       "workspaces": "*" | ["slug", …]},
             "iat": <unix>, "exp": <unix>, "jti": "<random>",
             "target": "<slug>"}        optional: the chat's default workspace, never a grant

REGIME IS THE POINT. ``human`` = a person is in the loop this turn, so the scope is SOFT: ``workspaces:
"*"`` — everything that is already theirs, because the person can see and correct what the agent does.
``autonomous`` = a schedule/event/transcription fired with nobody watching, so the scope is HARD: the
exact isolation set, and the services behind the gateway refuse a workspace outside it. The regime is
DERIVED from ``unit.v1.trigger`` (``message`` ⇒ human; ``scheduled``/``event``/``transcription`` ⇒
autonomous) — the same field the contract uses to derive input-trust, so the two trust axes cannot
drift apart.

VERIFICATION AND REVOCATION. Identity's ``/internal/validate`` verifies the token: signature,
audience, expiry, then revocation. When a worker's unit ends, agent-api writes the token's ``jti``
to the service Redis for the token's remaining life (``control_plane/delegation_revocation.py``), and
identity refuses a token so written, and refuses every delegation token while it cannot read that
store (``admin_api/app/delegation_revocation.py``). ``verify_delegation`` also accepts an optional
``revoked`` set of ``jti`` values, for a verifier that holds the set itself.

THE SECRET is symmetric and lives in the environment of the minter (agent-api) and the verifier
(admin-api) as ``VEXA_MCP_DELEGATION_SECRET``; this module never reads it — callers pass it in, which
keeps the module pure, testable, and out of the config.v1 undeclared-read scan. An EMPTY secret is
fatal on both mint and verify: a zero-length HMAC key would "work" and authenticate anyone who guessed
the format.

This file is the canonical copy in ``core/identity/contracts/delegation.v1/``, vendored byte for byte
into agent-api and admin-api (gate:fact-parity, fact ``delegation-token``).
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


#: Workspaces every subject READS whatever its dispatch's ceiling: the company layer, mounted
#: read-only into every worker. A write there is held to the ceiling like any other.
READ_BY_EVERYONE = frozenset({"_global"})


def ceiling_allows(workspaces: "str | Iterable[str] | None", workspace: object, *, subject: object = "") -> bool:
    """May a worker dispatched with the ceiling ``workspaces`` (a token's ``scope.workspaces``:
    ``"*"``, or the list of workspace ids it was granted) address ``workspace``?

    ``"*"`` is bounded by the account alone. An empty id, or the subject's own id, is the subject's
    own workspace and always in scope: the uid decides it, not the caller. Any other id must be in
    the list. THE ONE DEFINITION of the ceiling, called by agent-api's resolvers
    (``control_plane/ceiling.py``) and, through :func:`ceiling_reads`, by identity's answer for a
    delegation token (``/internal/validate`` narrows the person's memberships with it)."""
    w = str(workspace or "").strip()
    own = str(subject or "").strip()
    if workspaces == "*" or not w or (own and w == own):
        return True
    if workspaces is None or isinstance(workspaces, (str, bytes)):
        return False
    return w in {str(x).strip() for x in workspaces if str(x).strip()}


def ceiling_reads(workspaces: "str | Iterable[str] | None", workspace: object, *, subject: object = "") -> bool:
    """May that worker READ ``workspace``? :func:`ceiling_allows`, plus the workspaces every subject
    reads (:data:`READ_BY_EVERYONE`). A read that walks a person-wide set — their memberships, the
    meetings bound to them — keeps only what this answers True for."""
    return str(workspace or "").strip() in READ_BY_EVERYONE or ceiling_allows(
        workspaces, workspace, subject=subject)


def is_delegation_token(token: str) -> bool:
    """Cheap discriminator — does this bearer value even claim to be a delegation token? Lets a verifier
    fall through to its OTHER token schemes without paying a parse, and without a failed parse being
    mistaken for a failed AUTH (the distinction a verifier's 401 reasons depend on)."""
    return isinstance(token, str) and token.startswith(PREFIX)


def mint_delegation(
    secret: str | bytes,
    *,
    subject: str,
    regime: str = "human",
    workspaces: "str | Iterable[str]" = "*",
    target: str = "",
    ttl_sec: int = DEFAULT_TTL_SEC,
    now: Optional[int] = None,
    jti: Optional[str] = None,
) -> str:
    """Mint a delegation token for ``subject``. ``workspaces`` is ``"*"`` (all of theirs — only sane for
    the human regime) or an explicit list of slugs (the isolation set). Raises ``ValueError`` on a
    missing secret/subject, an unknown regime, or the contradiction ``autonomous`` + ``"*"`` — an
    unwatched dispatch must never carry an unbounded grant, and silently narrowing it would hide a
    dispatcher bug instead of surfacing it.

    ``target`` is the chat's TARGET WORKSPACE (Vexa-ai/vexa#1611) — the slug a workspace verb with
    no ``slug`` of its own defaults to. It is DELIBERATELY NOT PART OF ``scope``: a scope is a
    ceiling and this is a default, and putting a default where a permission lives is how a default
    becomes a grant. The claim is omitted entirely when there is none, so a token for a chat that
    writes to its person's own desk is byte-identical to the one it has always been."""
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
    if str(target or "").strip():
        payload["target"] = str(target).strip()
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
    reach; the service still applies the account's own ownership checks underneath. A malformed/absent scope
    fails CLOSED."""
    scope = claims.get("scope")
    if not isinstance(scope, dict):
        return False
    ws = scope.get("workspaces")
    if ws == "*":
        return True
    return isinstance(ws, list) and str(slug) in {str(w) for w in ws}

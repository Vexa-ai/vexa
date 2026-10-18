"""delegation.py — the short-lived, scoped token a worker presents as the person it acts for (delegation.v1).

WHY A TOKEN PER DISPATCH. A worker runs in a spawned container. A durable account credential there
would not expire, would carry the whole account into an unwatched routine, and could not be withdrawn
without rotating the person's own key. The dispatch already knows WHO it acts for and WHY it fired,
so agent-api mints a credential that says only that and expires with the worker: its lifetime is the
chat warm window plus one turn (1800 s by default, ``VEXA_MCP_DELEGATION_TTL_SEC``), a worker still
running at half of that is handed a fresh one, and the token is revoked when the worker's unit ends.

THE TOKEN. A compact HS256 JWS, the same signing idiom as ``adapters.LocalIdentityMinter``'s dispatch
token, with a ``vxd_`` prefix so a verifier can tell it from an API key WITHOUT trying to parse it:

    vxd_<b64u(header)>.<b64u(payload)>.<b64u(sig)>

    header  {"alg":"HS256","typ":"vxdlg"}
    payload {"sub": "<uid>",            the Vexa uid the worker acts for — resolved, never asserted
             "aud": "vexa-mcp",         audience pin: the MCP edge and its re-entry, nothing else
             "scope": {"regime": "human"|"autonomous",
                       "workspaces": "*" | ["slug", …],
                       "shared": true,          optional: other people read this turn's answer
                       "audience": "cross_org",  optional: some of them are outside the org
                       "grants": ["bot.request", …],   optional: unattended verbs a routine's
                       "flow": "user:<uid>/<name>"},   person granted it (only with both keys)
             "iat": <unix>, "exp": <unix>, "jti": "<random>",
             "target": "<slug>",        optional: the chat's default workspace, never a grant
             "unit": "<unit id>"}       optional: the agent unit (session) it was minted for

REGIME IS THE POINT. ``human`` = a person is in the loop this turn, so the scope is SOFT: ``workspaces:
"*"`` — everything that is already theirs, because the person can see and correct what the agent does.
``autonomous`` = a schedule/event/transcription fired with nobody watching, so the scope is HARD: the
exact isolation set, and the services behind the gateway refuse a workspace outside it. The regime is
DERIVED from ``unit.v1.trigger`` (``message`` ⇒ human; ``scheduled``/``event``/``transcription`` ⇒
autonomous) — the same field the contract uses to derive input-trust, so the two trust axes cannot
drift apart.

SHARED IS THE OTHER AXIS (ADR-0048 decision 4). ``scope.shared`` is present, and true, when the turn
answers into a conversation other people read. The regime still says whether a person is in the loop;
``shared`` says the answer is not that person's alone, so the services refuse every person-wide read
(mail, calendar, any other connection, the person's meetings, transcripts and recordings) to such a
token: only what every reader can read may reach the answer. Absent on every other token, which is
therefore byte-identical to the one it has always been.

GRANTS ARE A ROUTINE'S DELEGATED AUTHORITY (flows phase 0, founder 2026-10-14). A routine runs with
nobody in the loop, so the services refuse it every verb that needs a person (``human_session_required``).
A routine a person made to do one such thing ("send a bot to this meeting at 12:13") could therefore
never do it. ``scope.grants`` names, from :data:`UNATTENDED_GRANTS` only, the verbs the person granted
that routine when they created it in their chat, and ``scope.flow`` names the routine. Both are minted
only for an ``autonomous`` turn of that routine and only together; a service that refuses an unwatched
caller lets a verb through when :func:`granted` says its grant is on the token, and nothing else. The
grant is recorded on the flow version (``params.grants``), capped by ``_global/POLICIES.md``
``routine_unattended_actions`` at creation and again at every run, and withdrawn by revoking it on the
flow. Absent on every other token.

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


#: A ceiling entry that grants a READ only: ``ro:<workspace id>`` (Vexa-ai/vexa#1783). An unwatched
#: dispatch's ceiling lists every workspace it has mounted — the read-write ones by id, the read-only
#: ones with this prefix — so its tools read what is on its disk and write only what it may write.
#: A verifier that predates the prefix matches no workspace with it: it fails closed, never wider.
READ_ONLY_PREFIX = "ro:"


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
    if workspaces is None or isinstance(workspaces, (str, bytes)) or w.startswith(READ_ONLY_PREFIX):
        return False
    return w in {str(x).strip() for x in workspaces
                 if str(x).strip() and not str(x).strip().startswith(READ_ONLY_PREFIX)}


def ceiling_reads(workspaces: "str | Iterable[str] | None", workspace: object, *, subject: object = "") -> bool:
    """May that worker READ ``workspace``? :func:`ceiling_allows`, plus the workspaces every subject
    reads (:data:`READ_BY_EVERYONE`), plus the ones its ceiling lists read-only
    (:data:`READ_ONLY_PREFIX`). A read that walks a person-wide set — their memberships, the
    meetings bound to them — keeps only what this answers True for."""
    w = str(workspace or "").strip()
    if w in READ_BY_EVERYONE or ceiling_allows(workspaces, workspace, subject=subject):
        return True
    if not w or w.startswith(READ_ONLY_PREFIX) or workspaces is None or isinstance(workspaces, (str, bytes)):
        return False
    return f"{READ_ONLY_PREFIX}{w}" in {str(x).strip() for x in workspaces}


#: THE VERBS A ROUTINE MAY BE GRANTED to run with nobody in the loop — the whole vocabulary. A name
#: outside it is refused at mint, at identity's answer and at submission, so a typo never grants.
#:   bot.request       put a bot into a meeting (meeting-api ``POST /bots``; flows' ``request_meeting_bot``)
#:   bot.stop          take that bot out again
#:   flow.retire_self  retire or pause the routine itself (flows-api, its own flow only)
UNATTENDED_GRANTS = ("bot.request", "bot.stop", "flow.retire_self")


def clean_grants(grants: "Iterable[str] | None") -> list:
    """``grants`` as a token carries them: sorted, unique, every name in :data:`UNATTENDED_GRANTS`.
    Raises ``ValueError`` naming a grant outside the vocabulary — a grant is never guessed at."""
    out = sorted({str(g).strip() for g in (grants or ()) if str(g).strip()})
    unknown = [g for g in out if g not in UNATTENDED_GRANTS]
    if unknown:
        raise ValueError(f"not an unattended grant: {', '.join(unknown)} (known: "
                         f"{', '.join(UNATTENDED_GRANTS)})")
    return out


def granted(scope: object, action: str) -> bool:
    """Does this ceiling (a token's ``scope``, or a ceiling copied from it) carry the grant
    ``action`` for its routine? Only a list naming it, beside a ``flow``, does."""
    if not isinstance(scope, dict) or not str(scope.get("flow") or "").strip():
        return False
    grants = scope.get("grants")
    return isinstance(grants, list) and action in UNATTENDED_GRANTS and action in grants


def is_shared(scope: object) -> bool:
    """Does this ceiling (a token's ``scope``, or a ceiling copied from it) mark a turn other people
    read? Only a literal ``True`` does; anything else is a private turn."""
    return isinstance(scope, dict) and scope.get("shared") is True


#: A shared turn read by people from outside the org (``scope.audience``): crm-api answers its reads
#: with each record's public fields only.
AUDIENCE_CROSS_ORG = "cross_org"


def is_cross_org(scope: object) -> bool:
    """Does this ceiling mark a turn read by people outside the org? Only the literal value does."""
    return isinstance(scope, dict) and scope.get("audience") == AUDIENCE_CROSS_ORG


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
    unit: str = "",
    ttl_sec: int = DEFAULT_TTL_SEC,
    now: Optional[int] = None,
    jti: Optional[str] = None,
    shared: bool = False,
    grants: "Iterable[str] | None" = None,
    flow: str = "",
    audience: str = "",
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
    writes to its person's own desk is byte-identical to the one it has always been.

    ``unit`` is the agent unit the token was minted for (Vexa-ai/vexa#1783) — the dispatch id of a
    chat thread or a background job, the SESSION every call the worker makes belongs to. It is
    signed here so that no worker can name another session: identity answers it, the gateway signs
    it into the identity it forwards, the services log it as ``session`` and the credential broker
    records it as ``audit.session``. Omitted when empty, like ``target``.

    ``shared`` marks a turn whose answer other people read (``scope.shared``): a narrowing, so it IS
    part of the ceiling. Omitted when false.

    ``grants`` and ``flow`` are a routine's delegated authority (``scope.grants``, ``scope.flow``):
    minted only for an ``autonomous`` turn and only together — a grant with no routine, or on a turn
    a person is watching, raises ``ValueError``. Omitted when there are none."""
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
    scope = {"regime": regime, "workspaces": workspaces}
    if shared:
        scope["shared"] = True
    if audience:
        # WHO READS THE ANSWER, BEYOND "OTHER PEOPLE" (rooms, 2026-10-16): ``cross_org`` when some
        # of them are outside the org whose CRM the turn reads. A narrowing, so it is part of the
        # ceiling; only beside ``shared`` (a private turn has one reader, from its own org).
        if audience != AUDIENCE_CROSS_ORG:
            raise ValueError(f"audience is {AUDIENCE_CROSS_ORG!r} or absent, got {audience!r}")
        if not shared:
            raise ValueError("an audience is a shared turn's: pass shared=True")
        scope["audience"] = AUDIENCE_CROSS_ORG
    grants = clean_grants(grants)
    if grants:
        if regime != "autonomous" or not str(flow or "").strip():
            raise ValueError("grants are a routine's: an autonomous turn naming its flow")
        scope["grants"] = grants
        scope["flow"] = str(flow).strip()
    payload = {
        "sub": str(subject),
        "aud": AUDIENCE,
        "scope": scope,
        "iat": iat,
        "exp": iat + int(ttl_sec),
        "jti": jti or secrets.token_urlsafe(12),
    }
    if str(target or "").strip():
        payload["target"] = str(target).strip()
    if str(unit or "").strip():
        payload["unit"] = str(unit).strip()
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
    return isinstance(ws, list) and not str(slug).startswith(READ_ONLY_PREFIX) and str(slug) in {
        str(w) for w in ws if not str(w).startswith(READ_ONLY_PREFIX)}

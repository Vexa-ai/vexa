"""subject-ticket.v1 — the evidence that a person's own event or grant caused a flows call.

The flows tier reaches the other domains only through the gateway's service door, and on the calls
that act FOR a person it names that person (`X-User-Id`, or a field such as `bills_to`). A name is a
claim anybody holding the flows key can make. A subject ticket is the producer's signature over it:
the service that emitted the person's fact (meeting-api for a meeting, agent-api for a routine the
person turned on, the mail intake for an authenticated invite, the gateway for a person's own
bearer) signs WHO the fact is about, WHICH calls it lets flows make for them, and until WHEN. The
door holds only the producers' PUBLIC keys; it refuses a bound call unless one of the tickets the
call carries names that subject for that call class, unexpired, signed by a producer allowed to
grant that class (`ISSUER_CLASSES`). Flows cannot mint one: it holds no producer's private key.

    token  = "st1." + b64u(claims) + "." + b64u(Ed25519_sign(private_key, "st1." + b64u(claims)))
    claims = {v, iss, kind, sub, cls, evt, iat, exp, jti} + optional {mid, native, addr, peers, once}

* `iss`   the producer: `meetings`, `agent`, `mail` or `gateway` (`ISSUERS`).
* `kind`  `event` (one fact's consequences, short) or `grant` (a routine the person turned on).
* `sub`   the platform user id the ticket is about ("" only on a `mail` ticket, which names `addr`).
* `cls`   the call classes it opens (`CLASSES`); the issuer must be allowed each (`ISSUER_CLASSES`).
* `evt`   the fact or routine it was issued for (the source event id, `user:<uid>/<routine>`).
* `mid`   a meeting id: a row that addresses a meeting by id must address THIS one.
* `native` `"<platform>/<native_meeting_id>"`: a row that addresses a meeting by its native id must
          address this one.
* `addr`  an authenticated mail address: the subject's own address must be this one.
* `peers` addresses the fact names besides the subject (an invite's attendees): a `PEER_CLASSES`
          call may act for a person whose address is one of them.
* `once`  classes that may be USED once (the door records the ticket id on a 2xx answer).

The signature is checked before any claim is read. Lifetimes are capped per (issuer, kind)
(`MAX_LIFETIME`), so a producer cannot hand out a ticket that outlives what its fact can justify.

Standard library only: the producer injects `sign(message) -> signature` and the verifier
`checks = {issuer: check(message, signature) -> bool}`, built from its own PEM keys
(`identity_token.read_signing_key` / `read_verify_key`, or `fact_keys`).

THIS FILE IS VENDORED, byte for byte, into the door (gateway), the producers (meeting-api,
agent-api, the flows mail intake) and flows (which only carries tickets); `gate:fact-parity`
(`subject-ticket`) compares the copies. Edit the canonical copy in
`core/gateway/contracts/subject-ticket.v1/` and copy it out.
"""
from __future__ import annotations

import base64
import json
import re
import secrets
import time
from typing import Any, Callable, Iterable, Mapping, Optional

VERSION = "st1"
TYPE = "subject-ticket.v1"
#: The header a ticket travels in, flows → door and caller → flows-api. Several tickets are joined
#: with "," (a token never contains one).
HEADER = "X-Vexa-Subject-Ticket"
#: The reaction refs key that carries a fact's tickets through flows (admission keeps it, a child
#: fact inherits it, a merge unions it).
REFS_KEY = "_tickets"
SIGNATURE_BYTES = 64
MAX_SKEW_SEC = 60
MAX_TOKEN_BYTES = 4096
MAX_TICKETS_PER_CALL = 16

# ── the call classes ────────────────────────────────────────────────────────────────────────────
TRANSCRIPT_READ = "transcript.read"      # a meeting's transcript, or a share link to it
BOT_DISPATCH = "bot.dispatch"            # send / stop a bot, plan a meeting row
CHAT_AUTONOMOUS = "chat.autonomous"      # an autonomous agent turn in the person's session
DESK_WRITE = "desk.write"                # write, create or reset a person's desk
ROOM_BILLS_TO = "room.bills_to"          # name who pays for a room
ROUTINE_TURN = "routine.turn"            # a routine's turn, message or notice in the person's chat
CLASSES = (TRANSCRIPT_READ, BOT_DISPATCH, CHAT_AUTONOMOUS, DESK_WRITE, ROOM_BILLS_TO, ROUTINE_TURN)
#: Classes a ticket may open for a person named in `peers` rather than as `sub`/`addr`.
PEER_CLASSES = frozenset({DESK_WRITE})

# ── the issuers and what each may grant ─────────────────────────────────────────────────────────
MEETINGS, AGENT, MAIL, GATEWAY = "meetings", "agent", "mail", "gateway"
ISSUERS = (MEETINGS, AGENT, MAIL, GATEWAY)
ISSUER_CLASSES = {
    # a meeting the person owns ended / started / was shared: post-meeting processing
    MEETINGS: frozenset({TRANSCRIPT_READ, CHAT_AUTONOMOUS, DESK_WRITE, ROOM_BILLS_TO}),
    # a routine the person turned on in their own session; a message an agent sent them
    AGENT: frozenset({ROUTINE_TURN, BOT_DISPATCH}),
    # an authenticated invite or reply: the bot for that meeting, the reply's turn, attendee desks
    MAIL: frozenset({BOT_DISPATCH, TRANSCRIPT_READ, CHAT_AUTONOMOUS, DESK_WRITE}),
    # the person's own bearer, presented to flows-api and validated through the door
    GATEWAY: frozenset({DESK_WRITE, ROUTINE_TURN, BOT_DISPATCH}),
}
EVENT, GRANT = "event", "grant"
KINDS = (EVENT, GRANT)
_DAY = 86400
#: The longest a ticket may live, per (issuer, kind). A (issuer, kind) absent here cannot be issued.
MAX_LIFETIME = {
    (MEETINGS, EVENT): 3 * _DAY,       # post-meeting processing, its retries and its fan-out
    (AGENT, EVENT): 1 * _DAY,          # a message to a person's chat
    (AGENT, GRANT): 400 * _DAY,        # a routine, until it is turned on again
    (MAIL, EVENT): 60 * _DAY,          # an invite is a ticket until the meeting it names
    (GATEWAY, EVENT): 600,             # one request the person made
    (GATEWAY, GRANT): 400 * _DAY,      # a routine the person armed with their own session
}

_B64U = re.compile(r"[A-Za-z0-9_-]*")
_ISSUER = re.compile(r"[a-z][a-z0-9_-]{0,31}")


class TicketError(Exception):
    """A ticket that does not hold. `reason` is one stable word: malformed, unknown_issuer,
    bad_signature, lifetime_too_long, not_yet_valid, expired, issuer_not_allowed."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64u(text: str) -> bytes:
    if not _B64U.fullmatch(text or ""):
        raise ValueError("not base64url")
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if _b64u(raw) != text:
        raise ValueError("not canonical base64url")
    return raw


def _str_list(values: Optional[Iterable[Any]], *, lower: bool = False) -> list:
    out = []
    for v in values or ():
        s = str(v or "").strip()
        if s:
            out.append(s.lower() if lower else s)
    return sorted(set(out))


def native_of(platform: Any, native_meeting_id: Any) -> str:
    """The `native` claim for a meeting: `"<platform>/<native_meeting_id>"`, or "" if either is
    missing."""
    p, n = str(platform or "").strip(), str(native_meeting_id or "").strip()
    return f"{p}/{n}" if p and n else ""


def mint(sign: Callable[[bytes], bytes], *, iss: str, sub: Any = "", cls: Iterable[str],
         evt: str, ttl: int, kind: str = EVENT, now: Optional[int] = None, mid: Any = None,
         native: str = "", addr: str = "", peers: Optional[Iterable[str]] = None,
         once: Optional[Iterable[str]] = None) -> str:
    """A ticket signed by `sign` (the producer's injected Ed25519 signer). Refuses (ValueError) a
    ticket its issuer could not have issued: an unknown issuer or kind, a class the issuer may not
    grant, a lifetime past the cap, no subject and no address."""
    if iss not in ISSUER_CLASSES:
        raise ValueError(f"unknown issuer {iss!r}")
    if kind not in KINDS or (iss, kind) not in MAX_LIFETIME:
        raise ValueError(f"{iss} does not issue {kind!r} tickets")
    classes = _str_list(cls)
    if not classes or any(c not in ISSUER_CLASSES[iss] for c in classes):
        raise ValueError(f"{iss} may grant only {sorted(ISSUER_CLASSES[iss])}, not {classes}")
    ttl = int(ttl)
    if ttl <= 0 or ttl > MAX_LIFETIME[(iss, kind)]:
        raise ValueError(f"a {iss} {kind} ticket lives at most {MAX_LIFETIME[(iss, kind)]}s")
    sub_s, addr_s = str(sub or "").strip(), str(addr or "").strip().lower()
    if not sub_s and not addr_s:
        raise ValueError("a ticket names a subject or an authenticated address")
    iat = int(now if now is not None else time.time())
    claims: dict = {"v": TYPE, "iss": iss, "kind": kind, "sub": sub_s, "cls": classes,
                    "evt": str(evt or "").strip(), "iat": iat, "exp": iat + ttl,
                    "jti": secrets.token_hex(12)}
    if mid not in (None, ""):
        claims["mid"] = str(mid).strip()
    if native:
        claims["native"] = str(native).strip()
    if addr_s:
        claims["addr"] = addr_s
    p = _str_list(peers, lower=True)
    if p:
        claims["peers"] = p
    o = _str_list(once)
    if o:
        claims["once"] = o
    body = VERSION + "." + _b64u(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    sig = sign(body.encode("ascii"))
    if not isinstance(sig, (bytes, bytearray)) or len(sig) != SIGNATURE_BYTES:
        raise ValueError("the signer did not produce an Ed25519 signature")
    return body + "." + _b64u(bytes(sig))


def read(token: str, checks: Mapping[str, Callable[[bytes, bytes], bool]], *,
         now: Optional[int] = None, max_skew_sec: int = MAX_SKEW_SEC) -> dict:
    """The claims of a ticket one of `checks` verifies, or TicketError. The issuer named in the
    token picks the check; the signature is verified before any other claim is believed."""
    token = (token or "").strip()
    if not token or len(token) > MAX_TOKEN_BYTES:
        raise TicketError("malformed")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != VERSION:
        raise TicketError("malformed")
    try:
        payload, sig = _unb64u(parts[1]), _unb64u(parts[2])
        claims = json.loads(payload)
    except (ValueError, TypeError):
        raise TicketError("malformed") from None
    if not isinstance(claims, dict) or claims.get("v") != TYPE:
        raise TicketError("malformed")
    iss = claims.get("iss")
    if not isinstance(iss, str) or not _ISSUER.fullmatch(iss) or iss not in checks:
        raise TicketError("unknown_issuer")
    if len(sig) != SIGNATURE_BYTES or not checks[iss]((parts[0] + "." + parts[1]).encode("ascii"),
                                                      sig):
        raise TicketError("bad_signature")
    # signed by `iss` from here on; now hold it to what `iss` may issue
    kind, iat, exp = claims.get("kind"), claims.get("iat"), claims.get("exp")
    if (not isinstance(iat, int) or not isinstance(exp, int) or isinstance(iat, bool)
            or isinstance(exp, bool) or exp <= iat):
        raise TicketError("malformed")
    cap = MAX_LIFETIME.get((iss, kind))
    if cap is None:
        raise TicketError("issuer_not_allowed")
    if exp - iat > cap:
        raise TicketError("lifetime_too_long")
    cls = claims.get("cls")
    if (not isinstance(cls, list) or not cls
            or any(not isinstance(c, str) or c not in ISSUER_CLASSES.get(iss, ()) for c in cls)):
        raise TicketError("issuer_not_allowed")
    if not isinstance(claims.get("sub"), str) or not isinstance(claims.get("jti"), str):
        raise TicketError("malformed")
    if not claims["sub"].strip() and not str(claims.get("addr") or "").strip():
        raise TicketError("malformed")
    t = int(now if now is not None else time.time())
    if iat > t + max_skew_sec:
        raise TicketError("not_yet_valid")
    if t >= exp:
        raise TicketError("expired")
    return claims


def split(header_value: Optional[str]) -> list:
    """The tickets in one header value (comma-joined), at most `MAX_TICKETS_PER_CALL`."""
    out = [t.strip() for t in str(header_value or "").split(",") if t.strip()]
    return out[:MAX_TICKETS_PER_CALL]


def join(tickets: Iterable[Any]) -> str:
    """One header value for `tickets` (duplicates and blanks dropped, order kept)."""
    seen, out = set(), []
    for t in tickets or ():
        s = str(t or "").strip()
        if s and s not in seen and "," not in s:
            seen.add(s)
            out.append(s)
    return ",".join(out[:MAX_TICKETS_PER_CALL])


def tickets_of(refs: Optional[Mapping[str, Any]]) -> list:
    """The tickets a reaction's refs carry (`REFS_KEY`), as a clean list."""
    raw = (refs or {}).get(REFS_KEY) if isinstance(refs, Mapping) else None
    if isinstance(raw, str):
        raw = [raw]
    return [str(t).strip() for t in (raw or []) if isinstance(t, str) and t.strip()]


def merged(*lists: Iterable[Any]) -> list:
    """Several ticket lists as one (union, first-seen order) — a fact delivered twice keeps the
    tickets of both deliveries."""
    seen, out = set(), []
    for lst in lists:
        for t in lst or ():
            s = str(t or "").strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out

"""service_door.py — the flows tier's one way into the other domains, on a listener of its own.

The flows services (api, worker, mailbox) act for people: they read a meeting's transcript for its
owner, write a desk, post a notice into a chat, put an invite's attendees on the platform. They hold
no credential that can do any of that for an arbitrary person on an arbitrary route — not
`INTERNAL_API_SECRET` (the whole internal tier, every `/internal/*` and every `/api/*` route as any
`X-User-Id`), not the admin key (mints any person's tokens). They hold `VEXA_FLOWS_SERVICE_KEY`, and
that key opens exactly this door:

* **Outbound** — ``/{domain}/{path}``: a fixed allow-list of the calls flows makes, per domain
  (`FLOWS_OUTBOUND`). A call on the list is forwarded to that domain's service over the internal
  tier, which this process already holds; anything else is a 403 that never leaves this process.
  The meetings domain is reached with the subject's identity SIGNED by this edge, exactly as the
  edge stamps it for the person's own key, with the door's fixed scopes (`DOOR_SCOPES`) in place of
  a token's — so meeting-api checks scopes, limits and workspace writes as it does for anyone.
* **Inbound** — ``/to-flows/{path}``: the internal-tier callers of flows-api (the signals service's
  wake, the terminal's sign-in mail) present `X-Internal-Secret` HERE, never to flows, and the door
  forwards the allow-listed calls (`FLOWS_INBOUND`) with the flows key. A flows process therefore
  never receives the internal secret either.

The door is not the public edge: it listens on its own port (`VEXA_GATEWAY_SERVICE_DOOR_PORT`),
which no deploy surface publishes, and it exists only where `VEXA_FLOWS_SERVICE_KEY` is set.

Subject tickets (`subject_ticket.py`, subject-ticket.v1): on the calls that act FOR a person
(`BOUND`) the subject flows names is not enough. The call must carry a ticket signed by the producer
of that person's fact or grant — meeting-api, agent-api, the mail intake, or this gateway for a
bearer the person presented — that names the subject, lists the call's class, matches the meeting
the call addresses, has not expired and, for a `once` class, has not been used. The door holds only
those producers' PUBLIC keys (`VEXA_SUBJECT_TICKET_KEYS_DIR/<issuer>.pem`); its own issuer is the
identity key it already signs identities with. Without a matching ticket the call is a 403 that
never leaves this process.

Headers: only the ones a listed call needs cross (`OUTBOUND_HEADERS`, `OUTBOUND_PREFIXES`); every
other `x-user-*` (scopes, credential class, delegation, workspaces) and every authority header the
caller sent is dropped, so the subject is the ONLY identity fact flows can name.
"""
from __future__ import annotations

import hmac
import json
import re
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

import httpx
from fastapi import FastAPI, Request, Response

from . import identity_token, subject_ticket
from .meeting_link import parse_meeting_url
from .obs import log_event
from .paths import tail_path


SERVICE_KEY_HEADER = "x-vexa-service-key"
INTERNAL_SECRET_HEADER = "x-internal-secret"
FLOWS_KEY_ENV = "VEXA_FLOWS_SERVICE_KEY"
DOOR_PORT_ENV = "VEXA_GATEWAY_SERVICE_DOOR_PORT"
DEFAULT_DOOR_PORT = 8002
#: The scopes the meetings domain sees on a door call. Flows schedules bots and reads transcripts
#: (`bot`, `tx`); `browser` grants nothing at this edge and is not given.
DOOR_SCOPES = ("bot", "tx")
#: The placeholder literals this repository has published for a secret; a key holding one is refused.
PLACEHOLDERS = frozenset({"vexa-internal-secret", "lite-internal-secret", "changeme", "change-me",
                          "CHANGE-ME", "default", "secret"})

_S = "{}"   # exactly one path segment
#: THE ALLOW-LIST — every call the flows tier makes, and nothing else. `{}` is one opaque segment.
#: A call absent here is refused at the door; adding one is a reviewed change to this table.
FLOWS_OUTBOUND: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "agent": (
        ("POST", "/internal/scaffolds"),                 # mint_scaffold: a mail's sign-in link
        ("POST", "/internal/notifications"),             # the bell: notify_in_app, delivery notices
        ("POST", "/internal/notifications/state"),       # the delivery gate's read of a bell
        ("GET", "/internal/meetings/{}/listen"),         # meeting_signal: is the room listening
        ("POST", "/events"),                             # meeting_signal: the wake's event.v1
        ("GET", "/api/workspace/file"),                  # desks, prompts, policies, mail templates
        ("PUT", "/api/workspace/file"),                  # desk writes, the mail admin's templates
        ("POST", "/api/workspace/init"),                 # an attendee's desk
        ("GET", "/api/workspace/git"),                   # process_meeting: the desk's head
        ("POST", "/api/workspace/git/reset"),            # process_meeting: reset_desk
        ("GET", "/api/sessions/{}/history"),             # dispatch_turn's read-back
        ("POST", "/api/sessions/{}/proposals"),          # post_meeting: propose_call_changes
        ("POST", "/api/chat"),                           # dispatch_turn (autonomous regime)
        ("POST", "/api/chat/submit"),                    # routines: turns, messages, notices
        ("GET", "/api/chat/pending"),                    # routines: thread_state
        ("GET", "/api/chat/turn-outcome"),               # routines: await_outcome
        ("POST", "/api/meeting/note"),                   # meeting_prep: the meeting note
        ("POST", "/api/proposals"),                      # post_meeting: commitments
        ("POST", "/api/home/compose"),                   # home_compose
        ("POST", "/api/rooms/calendar-sync"),            # the calendar sensor's room calls
    ),
    "identity": (
        ("GET", "/internal/users/by-email/{}"),          # platform_user_id: an address → an id
        ("GET", "/internal/users/{}/email"),             # address_for_uid: an id → its address
        ("POST", "/internal/users"),                     # ensure_platform_user
        ("GET", "/internal/users/{}/is-admin"),          # mail kinds set to `admins`
        ("GET", "/internal/users/{}/settings"),          # a person's mail and notice settings
        ("POST", "/internal/principals/workspace"),      # a room's principal, for its cards
        ("POST", "/internal/validate"),                  # flows-api: the bearer presented to it
    ),
    "meetings": (
        ("GET", "/meetings"),
        ("POST", "/meetings"),                           # meeting_prep: the planned row
        ("POST", "/meetings/{}/share"),                  # a transcript share for an attendee
        ("GET", "/transcripts/by-id/{}"),
        ("GET", "/transcripts/{}/{}"),
        ("GET", "/bots"),
        ("POST", "/bots"),
        ("DELETE", "/bots/{}/{}"),
    ),
    "crm": (
        ("POST", "/internal/references"),                # the crm pack: workspace references
        ("POST", "/internal/cards/offer"),               # post_meeting: the follow-up cards
    ),
}
T = subject_ticket
#: THE BOUND ROWS — the calls that act for a person, the class a ticket must open for each, and how
#: the call names its meeting: `mid` (the first `{}` of the path is a meeting id), `native` (the two
#: `{}` are platform and native id), `native_body` (the body's `meeting_url`). `subject` is where the
#: person is named when it is not `X-User-Id`. Every other row is a lookup or a system call.
BOUND: Dict[Tuple[str, str, str], Dict[str, str]] = {
    ("agent", "PUT", "/api/workspace/file"): {"cls": T.DESK_WRITE},
    ("agent", "POST", "/api/workspace/init"): {"cls": T.DESK_WRITE},
    ("agent", "POST", "/api/workspace/git/reset"): {"cls": T.DESK_WRITE},
    ("agent", "POST", "/api/chat"): {"cls": T.CHAT_AUTONOMOUS},
    ("agent", "POST", "/api/chat/submit"): {"cls": T.ROUTINE_TURN},
    ("agent", "GET", "/api/chat/pending"): {"cls": T.ROUTINE_TURN},
    ("agent", "GET", "/api/chat/turn-outcome"): {"cls": T.ROUTINE_TURN},
    ("identity", "POST", "/internal/principals/workspace"): {"cls": T.ROOM_BILLS_TO,
                                                             "subject": "bills_to"},
    ("meetings", "POST", "/meetings"): {"cls": T.BOT_DISPATCH, "meeting": "native_body"},
    ("meetings", "POST", "/meetings/{}/share"): {"cls": T.TRANSCRIPT_READ, "meeting": "mid"},
    ("meetings", "GET", "/transcripts/by-id/{}"): {"cls": T.TRANSCRIPT_READ, "meeting": "mid"},
    ("meetings", "GET", "/transcripts/{}/{}"): {"cls": T.TRANSCRIPT_READ, "meeting": "native"},
    ("meetings", "POST", "/bots"): {"cls": T.BOT_DISPATCH, "meeting": "native_body"},
    ("meetings", "DELETE", "/bots/{}/{}"): {"cls": T.BOT_DISPATCH, "meeting": "native"},
}
#: The issuers whose keys the door reads from `VEXA_SUBJECT_TICKET_KEYS_DIR` (`<issuer>.pem`). The
#: gateway issuer is this process's own identity key.
KEYS_DIR_ENV = "VEXA_SUBJECT_TICKET_KEYS_DIR"
FILE_ISSUERS = (T.MEETINGS, T.AGENT, T.MAIL)
#: What the door's own ticket opens for a person who presented their bearer to flows-api, and how
#: long; a session asking for a routine grant gets `GRANT_CLASSES` for `T.MAX_LIFETIME`.
GATEWAY_EVENT_CLASSES = (T.DESK_WRITE, T.ROUTINE_TURN, T.BOT_DISPATCH)
GATEWAY_EVENT_TTL = 600
GRANT_CLASSES = (T.ROUTINE_TURN, T.BOT_DISPATCH)
GRANT_REQUEST_HEADER = "x-vexa-ticket-grant"
GRANT_RESPONSE_HEADER = "X-Vexa-Subject-Grant"
_ROUTINE = re.compile(r"^user:(\d+)/[A-Za-z0-9._-]{1,128}$")

#: The internal-tier calls INTO flows-api, made through the door so flows never sees the secret.
FLOWS_INBOUND: Tuple[Tuple[str, str], ...] = (
    ("POST", "/events/signal"),                          # the signals service's wake
    ("POST", "/mail/sign-in"),                           # the terminal's emailed sign-in link
    ("POST", "/mail/send"),                              # platform mail kinds
)

#: The request headers an outbound call carries across the door. The subject (`x-user-id`) is the
#: only identity fact; the flow headers say which flow and step a turn belongs to.
OUTBOUND_HEADERS = frozenset({"content-type", "accept", "x-user-id", "x-trace-id",
                              "x-vexa-internal-accepts-delegation"})
OUTBOUND_PREFIXES = ("x-vexa-flow", "x-vexa-message-", "x-vexa-concern")
INBOUND_HEADERS = frozenset({"content-type", "accept", "x-trace-id", "x-vexa-service"})
_RESPONSE_DROP = frozenset({"content-length", "transfer-encoding", "connection", "content-encoding"})


def _compile(template: str) -> "re.Pattern[str]":
    parts = [r"[^/]+" if seg == _S else re.escape(seg) for seg in template.strip("/").split("/")]
    return re.compile("^/" + "/".join(parts) + "$")


_OUTBOUND = {domain: tuple((m, _compile(t)) for m, t in rows) for domain, rows in FLOWS_OUTBOUND.items()}
_INBOUND = tuple((m, _compile(t)) for m, t in FLOWS_INBOUND)


def allowed(domain: str, method: str, path: str) -> bool:
    """Is `method path` on the flows allow-list for `domain`? `path` is the re-encoded tail."""
    return any(m == method.upper() and rx.match(path) for m, rx in _OUTBOUND.get(domain, ()))


_BOUND = tuple(((d, m), _compile(t), t, spec) for (d, m, t), spec in BOUND.items())


def bound_row(domain: str, method: str, path: str) -> Optional[Tuple[str, Dict[str, str]]]:
    """(template, spec) of the bound row `method path` hits, or None. The most literal template
    wins (`/transcripts/by-id/{}` over `/transcripts/{}/{}`)."""
    hits = [(t, spec) for (d, m), rx, t, spec in _BOUND
            if d == domain and m == method.upper() and rx.match(path)]
    if not hits:
        return None
    return max(hits, key=lambda h: sum(1 for seg in h[0].split("/") if seg and seg != _S))


def _path_args(template: str, path: str) -> list:
    tsegs, psegs = template.strip("/").split("/"), path.strip("/").split("/")
    return [p for t, p in zip(tsegs, psegs) if t == _S]


def ticket_checks(keys_dir: str = "", identity_key=None) -> dict:
    """`{issuer: check}` from the producers' public keys in `keys_dir`, plus the gateway issuer
    from this process's identity key. A missing or unusable file leaves that issuer out."""
    import os  # noqa: PLC0415
    checks = {}

    def _check_of(pub):
        def check(message: bytes, signature: bytes) -> bool:
            try:
                pub.verify(signature, message)
                return True
            except Exception:  # noqa: BLE001 — InvalidSignature, or a malformed signature
                return False
        return check

    for issuer in FILE_ISSUERS:
        path = os.path.join(keys_dir, f"{issuer}.pem") if keys_dir else ""
        if not path or not os.path.exists(path):
            continue
        try:
            checks[issuer] = _check_of(identity_token.read_verify_key(path))
        except Exception as e:  # noqa: BLE001 — named, the issuer stays out
            log_event("subject_ticket_key_unusable", audience="system", level="warning",
                      span="auth", fields={"issuer": issuer, "error": type(e).__name__})
    if identity_key is not None:
        checks[T.GATEWAY] = _check_of(identity_key.public_key())
    return checks


class SeenTickets:
    """The `once` uses already spent: `jti:class` until the ticket's expiry. In-process; a
    deployment that runs several gateway replicas passes a shared store with the same two methods."""

    def __init__(self):
        self._seen: Dict[str, float] = {}

    def seen(self, key: str, now: float) -> bool:
        exp = self._seen.get(key)
        return exp is not None and exp > now

    def spend(self, key: str, exp: float, now: float) -> None:
        if len(self._seen) > 50000:
            self._seen = {k: v for k, v in self._seen.items() if v > now}
        self._seen[key] = exp


def allowed_inbound(method: str, path: str) -> bool:
    return any(m == method.upper() and rx.match(path) for m, rx in _INBOUND)


def usable_key(value: Optional[str]) -> str:
    """The key as configured, or "" when it is unset or a published placeholder."""
    v = (value or "").strip()
    return "" if not v or v in PLACEHOLDERS else v


def _same(presented: Optional[str], expected: str) -> bool:
    return bool(expected) and bool(presented) and hmac.compare_digest(
        presented.encode("utf-8"), expected.encode("utf-8"))


def _json(status: int, body: Mapping[str, Any]) -> Response:
    return Response(content=json.dumps(body), status_code=status, media_type="application/json")


def _refuse(status: int, reason: str, method: str, path: str, **extra) -> Response:
    log_event("service_door_refused", audience="system", level="warning", span="auth",
              fields={"reason": reason, "method": method, "path": path, **extra})
    return _json(status, {"detail": f"service door: {reason}", "reason": reason,
                          "method": method, "path": path, **extra})


def _pick(headers: Iterable[Tuple[str, str]], names: frozenset, prefixes: Tuple[str, ...] = ()) -> dict:
    out = {}
    for k, v in headers:
        lk = k.lower()
        if lk in names or (prefixes and lk.startswith(prefixes)):
            out[lk] = v
    return out


def create_door_app(*, flows_key: str, internal_secret: str, upstreams: Mapping[str, str],
                    flows_api_url: str = "", client: Optional[httpx.AsyncClient] = None,
                    identity_key=None, checks: Optional[Mapping[str, Any]] = None,
                    seen: Optional[SeenTickets] = None, clock=None) -> FastAPI:
    """The door over its ports. `upstreams` maps a domain (`agent`, `identity`, `meetings`, `crm`)
    to its service's base URL; a domain with no URL is not deployed and answers 404."""
    flows_key = usable_key(flows_key)
    if not flows_key:
        raise RuntimeError(f"{FLOWS_KEY_ENV} is unset or a published placeholder — the service door "
                           "does not open without a real key")
    if not usable_key(internal_secret):
        raise RuntimeError("INTERNAL_API_SECRET is unset — the service door forwards over the "
                           "internal tier and cannot without it")
    bases = {d: (u or "").rstrip("/") for d, u in upstreams.items()}
    flows_base = (flows_api_url or "").rstrip("/")
    http = client or httpx.AsyncClient(timeout=30.0)
    checks = dict(checks) if checks is not None else ticket_checks(identity_key=identity_key)
    seen = seen or SeenTickets()
    now_of = clock or __import__("time").time
    if not any(i in checks for i in FILE_ISSUERS):
        log_event("subject_ticket_no_producer_keys", audience="system", level="warning",
                  span="auth", fields={"env": KEYS_DIR_ENV,
                                       "effect": "every bound flows call is refused"})
    app = FastAPI(title="Vexa gateway — service door", docs_url=None, redoc_url=None,
                  openapi_url=None)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "gateway-service-door"}

    async def _relay(method: str, url: str, request: Request, headers: dict) -> Response:
        try:
            resp = await http.request(method, url, headers=headers,
                                      params=dict(request.query_params) or None,
                                      content=await request.body())
        except httpx.TimeoutException:
            return _json(504, {"detail": "upstream timeout", "reason": "upstream_timeout"})
        except httpx.RequestError as e:
            return _json(502, {"detail": f"upstream unreachable: {type(e).__name__}",
                               "reason": "upstream_unreachable"})
        out = {k: v for k, v in resp.headers.items() if k.lower() not in _RESPONSE_DROP}
        return Response(content=resp.content, status_code=resp.status_code, headers=out)

    async def _subject_identity(uid: str) -> Optional[dict]:
        """The subject's identity as identity answers it for the door, or None (refused)."""
        base = bases.get("identity", "")
        if not base:
            return None
        try:
            r = await http.get(f"{base}/internal/users/{uid}/identity",
                               headers={"X-Internal-Secret": internal_secret})
        except httpx.RequestError:
            return None
        if r.status_code != 200:
            return None
        body = r.json()
        return body if isinstance(body, dict) and body.get("user_id") is not None else None

    async def _address_of(uid: str, cache: dict) -> str:
        if uid in cache:
            return cache[uid]
        addr = ""
        base = bases.get("identity", "")
        if base and uid.isdigit():
            try:
                r = await http.get(f"{base}/internal/users/{uid}/email",
                                   headers={"X-Internal-Secret": internal_secret})
                if r.status_code == 200:
                    body = r.json()
                    addr = str((body or {}).get("email") or "").strip().lower() \
                        if isinstance(body, dict) else ""
            except (httpx.RequestError, ValueError):
                addr = ""
        cache[uid] = addr
        return addr

    def _meeting_of(where: str, template: str, path: str, body: Any) -> Tuple[str, str]:
        """(kind, value) the call addresses: ("mid", id) / ("native", "platform/id"), or ("", "")
        when the call names no meeting it can be held to."""
        args = _path_args(template, path)
        if where == "mid" and args:
            return "mid", args[0]
        if where == "native" and len(args) >= 2:
            return "native", T.native_of(args[0], args[1])
        if where == "native_body" and isinstance(body, dict):
            if body.get("platform") and body.get("native_meeting_id"):
                return "native", T.native_of(body["platform"], body["native_meeting_id"])
            parsed = parse_meeting_url(str(body.get("meeting_url") or ""))
            return ("native", T.native_of(*parsed)) if parsed else ("native", "")
        return "", ""

    async def _admit(domain: str, method: str, template: str, spec: Dict[str, str], path: str,
                     request: Request) -> Tuple[Optional[Response], Optional[str]]:
        """(refusal, once_key): a refusal when no presented ticket opens this call; else the
        `jti:class` to spend on success when the ticket that opened it is `once` for the class."""
        cls = spec["cls"]
        raw = await request.body()
        body: Any = None
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
        header_subject = str(request.headers.get("x-user-id") or "").strip()
        field = spec.get("subject")
        if field:
            subject = str((body or {}).get(field) or "").strip() if isinstance(body, dict) else ""
            if header_subject and header_subject != subject:
                return _refuse(403, "subject_ticket_refused", method, path, domain=domain,
                               cls=cls, why=["subject_header_disagrees"]), None
        else:
            subject = header_subject
        if not subject:
            return _refuse(400, "subject_required", method, path, domain=domain), None
        tickets = T.split(request.headers.get(T.HEADER.lower()))
        if not tickets:
            return _refuse(403, "subject_ticket_required", method, path, domain=domain,
                           cls=cls), None
        kind, value = _meeting_of(spec.get("meeting", ""), template, path, body)
        now = now_of()
        why, cache, replayed = [], {}, False
        for token in tickets:
            try:
                c = T.read(token, checks, now=int(now))
            except T.TicketError as e:
                why.append(e.reason)
                continue
            if cls not in c["cls"]:
                why.append("class_not_granted")
                continue
            ok = bool(c["sub"]) and c["sub"] == subject
            if not ok and (c.get("addr") or (cls in T.PEER_CLASSES and c.get("peers"))):
                addr = await _address_of(subject, cache)
                ok = bool(addr) and (addr == c.get("addr")
                                     or (cls in T.PEER_CLASSES and addr in (c.get("peers") or ())))
            if not ok:
                why.append("other_subject")
                continue
            if kind:
                claim = c.get(kind)
                if claim:
                    if not value or claim != value:
                        why.append("other_meeting")
                        continue
                elif c.get("mid") or c.get("native") or cls == T.TRANSCRIPT_READ:
                    # held to a meeting this call does not name the way the ticket does, or a
                    # transcript read with no meeting at all: nothing to hold it to
                    why.append("meeting_unchecked")
                    continue
            once_key = f"{c['jti']}:{cls}" if cls in (c.get("once") or ()) else None
            if once_key and seen.seen(once_key, now):
                replayed = True
                why.append("replayed")
                continue
            log_event("subject_ticket_admitted", audience="system", level="debug", span="auth",
                      fields={"domain": domain, "method": method, "path": path, "cls": cls,
                              "iss": c["iss"], "evt": c.get("evt", "")})
            return None, (once_key, c["exp"]) if once_key else None
        if replayed and set(why) <= {"replayed", "other_meeting", "class_not_granted"}:
            return _refuse(403, "ticket_replayed", method, path, domain=domain, cls=cls), None
        return _refuse(403, "subject_ticket_refused", method, path, domain=domain, cls=cls,
                       why=sorted(set(why))), None

    async def _issue_on_validate(request: Request, resp: Response) -> None:
        """A person's bearer flows-api validated through this door: answer the door's own ticket
        for them (and, to a session arming a routine, a grant)."""
        if resp.status_code != 200 or identity_key is None:
            return
        try:
            user = json.loads(resp.body or b"{}")
        except ValueError:
            return
        if not isinstance(user, dict) or user.get("user_id") is None:
            return
        dlg = user.get("delegation") if isinstance(user.get("delegation"), dict) else {}
        if dlg.get("shared") or str(user.get("principal_kind") or "") == "workspace":
            return
        uid = str(user["user_id"]).strip()
        sign = identity_key.sign
        resp.headers[T.HEADER] = T.mint(sign, iss=T.GATEWAY, sub=uid, cls=GATEWAY_EVENT_CLASSES,
                                        evt="validate", ttl=GATEWAY_EVENT_TTL)
        want = str(request.headers.get(GRANT_REQUEST_HEADER) or "").strip()
        m = _ROUTINE.match(want)
        if m and m.group(1) == uid and user.get("credential_class") == "session" and not dlg:
            resp.headers[GRANT_RESPONSE_HEADER] = T.mint(
                sign, iss=T.GATEWAY, kind=T.GRANT, sub=uid, cls=GRANT_CLASSES, evt=want,
                ttl=T.MAX_LIFETIME[(T.GATEWAY, T.GRANT)])

    @app.api_route("/to-flows/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def inbound(path: str, request: Request):
        method = request.method
        tail, error = tail_path(path, request)
        if error is not None:
            return error
        target = "/" + tail
        if not _same(request.headers.get(INTERNAL_SECRET_HEADER), internal_secret):
            return _refuse(403, "internal_secret_required", method, target)
        if not flows_base:
            return _refuse(404, "flows_not_deployed", method, target)
        if not allowed_inbound(method, target):
            return _refuse(403, "not_on_the_allow_list", method, target, domain="flows")
        headers = _pick(request.headers.items(), INBOUND_HEADERS)
        headers[SERVICE_KEY_HEADER] = flows_key
        return await _relay(method, flows_base + target, request, headers)

    @app.api_route("/{domain}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def outbound(domain: str, path: str, request: Request):
        method = request.method
        tail, error = tail_path(path, request)
        if error is not None:
            return error
        target = "/" + tail
        if not _same(request.headers.get(SERVICE_KEY_HEADER), flows_key):
            return _refuse(403, "service_key_required", method, target, domain=domain)
        if domain not in FLOWS_OUTBOUND:
            return _refuse(404, "unknown_domain", method, target, domain=domain)
        if not allowed(domain, method, target):
            return _refuse(403, "not_on_the_allow_list", method, target, domain=domain)
        base = bases.get(domain, "")
        if not base:
            return _refuse(404, "domain_not_deployed", method, target, domain=domain)
        row = bound_row(domain, method, target)
        once = None
        if row is not None:
            refusal, once = await _admit(domain, method, row[0], row[1], target, request)
            if refusal is not None:
                return refusal
        headers = _pick(request.headers.items(), OUTBOUND_HEADERS, OUTBOUND_PREFIXES)
        if domain != "identity":
            headers.pop("x-vexa-internal-accepts-delegation", None)
        if domain == "meetings":
            uid = str(headers.pop("x-user-id", "") or "").strip()
            if not uid.isdigit():
                return _refuse(400, "subject_required", method, target, domain=domain)
            user = await _subject_identity(uid)
            if user is None:
                return _refuse(403, "subject_unknown", method, target, domain=domain)
            user = {**user, "scopes": list(DOOR_SCOPES)}
            user.pop("credential_class", None)
            if identity_key is not None:
                headers.update(identity_token.signed_headers(identity_key, user))
            else:
                headers.update(identity_token.headers_from_claims(
                    identity_token.claims_from_validation(user)))
        else:
            headers[INTERNAL_SECRET_HEADER] = internal_secret
        log_event("service_door_forwarded", audience="system", level="debug", span="proxy",
                  fields={"domain": domain, "method": method, "path": target})
        resp = await _relay(method, base + target, request, headers)
        if once is not None and 200 <= resp.status_code < 300:
            seen.spend(once[0], once[1], now_of())
        if domain == "identity" and method == "POST" and target == "/internal/validate":
            await _issue_on_validate(request, resp)
        return resp

    return app


def build_door_app(env: Optional[Mapping[str, str]] = None, *, identity_key=None) -> Optional[FastAPI]:
    """The door from the environment, or None where this deployment runs no flows tier."""
    import os  # noqa: PLC0415
    env = os.environ if env is None else env
    key = usable_key(env.get(FLOWS_KEY_ENV))
    if not (env.get(FLOWS_KEY_ENV) or "").strip():
        return None
    if not key:
        raise RuntimeError(f"{FLOWS_KEY_ENV} is a published placeholder — refusing to open the door")
    upstreams = {
        "agent": env.get("AGENT_API_URL", ""),
        "identity": env.get("ADMIN_API_URL", "http://admin-api:8001"),
        "meetings": env.get("MEETING_API_URL", "http://meeting-api:8080"),
        "crm": env.get("CRM_API_URL", ""),
    }
    checks = ticket_checks(env.get(KEYS_DIR_ENV, ""), identity_key)
    return create_door_app(flows_key=key, internal_secret=env.get("INTERNAL_API_SECRET", ""),
                           upstreams=upstreams, flows_api_url=env.get("FLOWS_API_URL", ""),
                           identity_key=identity_key, checks=checks)


def door_port(env: Optional[Mapping[str, str]] = None) -> int:
    import os  # noqa: PLC0415
    env = os.environ if env is None else env
    try:
        return int((env.get(DOOR_PORT_ENV) or str(DEFAULT_DOOR_PORT)).strip())
    except ValueError:
        return DEFAULT_DOOR_PORT

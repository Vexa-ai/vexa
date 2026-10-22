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

from . import identity_token
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
                          "method": method, "path": path})


def _pick(headers: Iterable[Tuple[str, str]], names: frozenset, prefixes: Tuple[str, ...] = ()) -> dict:
    out = {}
    for k, v in headers:
        lk = k.lower()
        if lk in names or (prefixes and lk.startswith(prefixes)):
            out[lk] = v
    return out


def create_door_app(*, flows_key: str, internal_secret: str, upstreams: Mapping[str, str],
                    flows_api_url: str = "", client: Optional[httpx.AsyncClient] = None,
                    identity_key=None) -> FastAPI:
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
        return await _relay(method, base + target, request, headers)

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
    return create_door_app(flows_key=key, internal_secret=env.get("INTERNAL_API_SECRET", ""),
                           upstreams=upstreams, flows_api_url=env.get("FLOWS_API_URL", ""),
                           identity_key=identity_key)


def door_port(env: Optional[Mapping[str, str]] = None) -> int:
    import os  # noqa: PLC0415
    env = os.environ if env is None else env
    try:
        return int((env.get(DOOR_PORT_ENV) or str(DEFAULT_DOOR_PORT)).strip())
    except ValueError:
        return DEFAULT_DOOR_PORT

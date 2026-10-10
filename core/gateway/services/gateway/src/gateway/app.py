"""``create_app(authorizer, downstream, redis, ...) -> FastAPI`` — the PRODUCTION gateway.

This is the single source of the proxy + multiplex logic for the v0.12 gateway lane. Its
behavior is the v0.12 carve of the deployed ``services/api-gateway/main.py``:

  * the ``forward_request`` auth middleware — ``x-api-key`` resolved via the ``Authorizer``
    port (admin-api ``/internal/validate``); fail-closed 401 when missing/invalid; scope 403
    via ``ROUTE_SCOPES`` (main.py:287-369, 59-65), now DENY-BY-DEFAULT: the table is keyed by
    (method, route template) and is exhaustive over the router, so an undeclared route is
    refused at request time and refuses to build at all,
  * the CORE proxy routes — each forwards its method to the matching downstream URL and returns
    the downstream body + status VERBATIM (main.py:450-831, 367),
  * the ``/ws`` multiplex control loop + redis pub/sub fan-in (``multiplex.py``) — subscribe → Subscribed ack;
    unsubscribe → Unsubscribed ack AND stop the fan-in; ping → pong; the invalid_json /
    unknown_action / invalid_subscribe_payload / invalid_unsubscribe_payload / missing_api_key
    error vocabulary; raw redis payloads forwarded over ``tc:…:mutable`` / ``bm:…:status`` /
    ``va:…:chat`` (main.py:2165-2340),
  * ``/health`` — liveness ``{status:"ok", service:"gateway"}`` (gate:health discovers it).

The collaborators (admin-api, downstream services, redis) are injected as PORTS (``ports.py``)
so the same app runs with real adapters in prod (``adapters.py``) and in-process fakes in the
conformance harness — the conformance assertions therefore drive SHIPPED code.

The edge threads ``logevent.v1`` trace_id: ``TraceMiddleware`` mints/reads ``X-Trace-Id`` and
forwards it to the downstream hop; user/system ``log_event``s are emitted on the auth + proxy
spans (preserved from the carve so gate:tracing stays green).
"""
from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import Dict, FrozenSet, List, Literal, Optional, Tuple

import httpx  # the downstream adapter's transport errors are mapped to 502/504 (not leaked as a 500)

from fastapi import FastAPI, Request, Response, WebSocket

from . import identity_token, routes_manifest
from .delegation import McpReentry, delegated_route_response, is_delegated, reported_admin
from .multiplex import run_multiplex
from .paths import (forwarded_param, forwarded_target_error, invalid_path_param_response,
                    path_segment, tail_path)
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute

from .obs import TRACE_HEADER, TraceMiddleware, get_trace_id, log_event, set_user_id
from .ports import Authorizer, AuthUnavailable, DownstreamClient, RedisBus

# #495: the honest answer when the auth path itself is unreachable — a retryable 503, never a
# 401 that blames the caller's (valid) key. Shared by /auth/me and the proxy authorizer. Also
# emits a TYPED, non-empty auth-infra log line (FM02: the old path swallowed the failure in a
# silent `except`, erasing the one signal that would have named this incident for what it was).
def _auth_unavailable_response(exc: Exception, *, span: str) -> Response:
    log_event(
        "auth_infra_unavailable",
        audience="system",
        level="error",
        span=span,
        fields={"reason": type(exc).__name__, "detail": str(exc)},
    )
    return Response(
        content=json.dumps({"detail": "Authentication temporarily unavailable, retry"}),
        status_code=503,
        media_type="application/json",
        headers={"Retry-After": "1"},
    )

# --- the scope model -------------------------------------------------------------------------
# The three key scopes, as ``docs/docs/authentication.mdx`` defines them:
#   bot     — "send and manage meeting bots, read transcripts"
#   tx      — "transcription / transcript access"
#   browser — "browser-tool capabilities"
#
# Read as capabilities, that gives one rule per domain:
#   BOT       anything that can make a bot EXIST, stop existing, or change how it behaves — the
#             whole /bots surface, and the calendar connections, because auto-join turns a feed
#             into bot spawns (the same cost vector as POST /bots, reached by another door).
#   TX        the transcript/meeting data plane: meeting records, transcripts.
#   BOT_OR_TX account-level config and reads that belong to either service domain (recordings,
#             webhook config + delivery history, model/transcription prefs, agent, MCP). Neither
#             scope alone is privileged over the other here; what matters is that a key with
#             NEITHER — a browser-only key — is refused.
#   browser   grants NO gateway route. v0.12 serves no browser-tool surface at this edge, so a
#             browser-only key can authenticate (GET /auth/me) and do nothing else.
# ── THE TABLE IS ASSEMBLED, NOT WRITTEN HERE (PRD decisions 40.5 + 40.7) ─────────────────────
#
# This used to be a 92-line literal holding all 69 rows, seven of them the agent domain's. That
# made the EDGE the place a domain's route list was written down, which 40.5 forbids in as many
# words — *"the gateway still owns nothing: it composes, strips authority, re-stamps, forwards"* —
# and which 40.7 makes concretely wrong rather than untidy: agents are OPTIONAL, and a table that
# names `/agent/*` unconditionally cannot describe a deployment that has none.
#
# Each domain now declares its own routes and their scopes in a `routes.v1.json` beside its
# service, the same shape as the `mcp.tools.v1` manifests and for the same reason. The rules the
# assembly refuses to boot on — duplicate ownership, an unknown scope, a manifest for a domain
# that is not deployed — are in `routes_manifest.py`.
#
# THE MODULE-LEVEL PAIR IS THE FULL PROFILE, kept because it is this package's published surface
# (`gateway/__init__.py`) and because every existing reader means "what does a complete deployment
# serve". An app built without a domain carries its OWN narrower table; see `create_app`.
# CANDIDATES, not an assertion that all five manifests are on disk. `load_carried` assembles over
# the ones THIS CUT ships: the open-core cut is generated by dropping the agent surface, and the
# strict `load` here made `import gateway` raise ManifestError there — killing every test module in
# this package at collection, an import-time hard requirement on optional substrate. `create_app`
# below still uses the strict `load`: a domain a running deployment NAMES and cannot describe is a
# real fault, and that is where the refusal belongs.
_FULL_PROFILE: FrozenSet[str] = frozenset({"gateway", "meetings", "identity", "mcp", "agent"})
_ASSEMBLED = routes_manifest.load_carried(_FULL_PROFILE)
#: The domains THIS BUILD carries a manifest for. `_FULL_PROFILE` is the candidate list; this is
#: what is on disk. An unspecified door defaults to it (see `create_app`), and the package's own
#: tests read it to know which profile they are asserting against.
CARRIED_DOMAINS: FrozenSet[str] = frozenset(_ASSEMBLED.domains)

ROUTE_SCOPES: Dict[Tuple[str, str], FrozenSet[str]] = dict(_ASSEMBLED.scopes)

# The routes that carry NO scope requirement, declared explicitly so "no entry" can mean "denied"
# everywhere else. Both are identity-only and forward nothing downstream: /health is the LB probe
# (unauthenticated by design) and /auth/me is "who is this key" — a browser-only key must be able
# to learn that it is a browser-only key. They are the EDGE's own two routes, and the only ones it
# declares for itself (`core/gateway/services/gateway/routes.v1.json`).
UNSCOPED_ROUTES: FrozenSet[Tuple[str, str]] = frozenset(_ASSEMBLED.unscoped)


def undeclared_routes(app: FastAPI, table=None, unscoped=None) -> List[Tuple[str, str]]:
    """Every (method, route-template) on ``app`` with no entry in ROUTE_SCOPES or UNSCOPED_ROUTES.

    The executable form of the deny-by-default invariant, used by ``create_app``'s build-time
    assertion and by the guard test. WebSocket routes are out of frame: ``/ws`` is not an
    ``APIRoute``, it does not pass through ``_authorize``, and its subscribe leg authorizes each
    meeting against the caller's ownership downstream.
    """
    missing: List[Tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue  # FastAPI's own /openapi.json + /docs + /redoc, and the /ws socket
        for method in sorted(route.methods or ()):
            if method == "HEAD":
                continue  # never registered by FastAPI itself; mirrors its GET sibling if it is
            key = (method, route.path)
            if key not in (ROUTE_SCOPES if table is None else table) and \
                    key not in (UNSCOPED_ROUTES if unscoped is None else unscoped):
                missing.append(key)
    return missing

#: The meeting platforms a `{platform}` path segment may name: api.v1 `components/schemas/Platform`,
#: held equal to it by tests/test_meeting_paths.py. A handler types its `platform` with it, so any
#: other value is refused with the 422 api.v1 declares for these routes, before the caller is
#: authorized and before anything is forwarded.
MeetingPlatform = Literal["google_meet", "zoom", "teams", "jitsi", "browser_session"]

# Default sentinel base URL. The DownstreamClient (real httpx or the fake ASGI transport) resolves
# it; what matters is the PATH the gateway forwards to (verbatim from the route). v0.12 P2 folded
# the transcription-collector INTO meeting-api (one modular monolith), so /transcripts + /meetings
# now forward to the SAME target as /bots — the standalone collector URL is gone.
_DEFAULT_MEETING_API_URL = "http://meeting-api"
# The AGENT control plane (agent-api): chat · sessions · routines · workspace · models · history.
# The gateway fronts it under /api/* so the SAME edge resolves the key → user and injects X-User-Id;
# agent-api (Stage 1) derives its `subject` from that header (never from the client). Sentinel base —
# the DownstreamClient resolves it; what matters is the /api/<path> the gateway forwards verbatim.
_DEFAULT_AGENT_API_URL = "http://agent-api"
# The identity control plane (admin-api): the self-serve /user/webhook config lives there
# (writes to user.data JSONB — the same blob /internal/validate reads the webhook config from).
_DEFAULT_ADMIN_API_URL = "http://admin-api"
# The MCP service (streamable-HTTP transport at ``/mcp``). Fronted at the gateway edge so an MCP
# client points at the SAME authenticated front door as every other Vexa client.
_DEFAULT_MCP_URL = "http://mcp:8010"

# Response headers the MCP streamable-HTTP transport is CARRIED BY — not hop-by-hop, not optional.
# ``mcp-session-id`` is minted by the server on initialize and echoed by the client on every later
# request; drop it at the edge and the session can never be bound. Passed through on BOTH legs.
_MCP_HEADERS = ("mcp-session-id", "mcp-protocol-version")


# Every hop's path is built from what the request MATCHED, never interpolated raw: a meetings row's
# target is its manifest row's (`_meeting_target`), a forwarded domain's literal row and catch-all
# are filled by `_register_forwarded`, and identity's calendar id by `path_segment`. Each parameter
# is one opaque segment, or a 400 (`paths.py`).


def _route_key(request: Request) -> Optional[Tuple[str, str]]:
    """(method, route TEMPLATE) of the route this request matched — the key every manifest row uses.

    The matched template (``request.scope["route"].path``), not the request path, so
    ``/user/calendars/{id}`` is one declaration instead of a prefix that also swallows whatever
    route is added beside it next."""
    path = getattr(request.scope.get("route"), "path", None)
    return (request.method.upper(), path) if path else None


def _required_scopes(request: Request, table=None) -> Optional[FrozenSet[str]]:
    """The scopes declared for the route this request MATCHED, or ``None`` when it declares none.

    ``None`` means DENY: the caller of this function fails closed, so a route that reaches
    ``_authorize`` without a declaration answers 403 instead of forwarding.
    """
    key = _route_key(request)
    return (ROUTE_SCOPES if table is None else table).get(key) if key else None


# ── the authority-header strip (F95) ─────────────────────────────────────────────
# Downstream services trust a small vocabulary of headers as AUTHORITY: ``x-user-*`` is the identity
# the gateway resolved from the api-key, ``x-vexa-identity`` is the signature that makes it believable
# (gateway-identity.v1 — agent-api and meeting-api refuse an ``x-user-*`` header without it),
# ``x-internal-secret`` is the internal service tier (agent-api ``_internal_caller``, admin-api
# ``_check_internal`` — the gate the meeting room calls its own trust boundary), and
# ``x-admin-api-key`` is admin-api's privileged surface.
#
# NONE of them may arrive from a client. The strip used to be an eight-name list of ``x-user-*``
# spellings, so ``x-internal-secret`` from the public edge reached agent-api and was believed — and
# with the shipped compose default (a literal in a public repo) that was the internal tier, open to
# any api-key holder. A LIST rots the moment a new authority header is added; a PREFIX rule does not,
# which is why this matches by family and why every new internal header must be spelled into one.
_AUTHORITY_HEADER_PREFIXES = ("x-user-", "x-internal-", "x-vexa-internal-")
_AUTHORITY_HEADER_EXACT = frozenset({"x-admin-api-key", identity_token.HEADER})


def _is_authority_header(name: str) -> bool:
    """True for any header a downstream service reads as AUTHORITY — never forwarded from a client."""
    name = name.lower()
    return name in _AUTHORITY_HEADER_EXACT or name.startswith(_AUTHORITY_HEADER_PREFIXES)


def _insufficient_scope_response() -> Response:
    return Response(
        content=json.dumps({"detail": "Insufficient scope for this endpoint"}),
        status_code=403,
        media_type="application/json",
    )


# A worker's delegation token is an MCP credential: where it is admitted is `delegation.py`.


def create_app(
    authorizer: Authorizer,
    downstream: DownstreamClient,
    redis: RedisBus,
    *,
    meeting_api_url: str = _DEFAULT_MEETING_API_URL,
    agent_api_url: Optional[str] = None,
    admin_api_url: str = _DEFAULT_ADMIN_API_URL,
    mcp_url: str = _DEFAULT_MCP_URL,
    identity_key=None,
    rate_limiter=None,
) -> FastAPI:
    """Build the gateway FastAPI app over the injected ports.

    ``authorizer``  — resolves ``x-api-key`` → user/scopes (admin-api ``/internal/validate``).
    ``downstream``  — forwards proxied HTTP requests to meeting-api (the unified control plane:
                      /bots + /transcripts + /meetings + /recordings all live there now, P2).
    ``redis``       — pub/sub bus for the ``/ws`` fan-in.
    ``identity_key`` — the Ed25519 PRIVATE key that signs the resolved identity onto every forward
                      (gateway-identity.v1, ``X-Vexa-Identity``). The gateway is its only holder. The
                      production builder loads it from ``VEXA_GATEWAY_IDENTITY_SIGNING_KEY_FILE``,
                      which the boot requires; a harness that injects fakes downstream may leave it
                      ``None`` and forward plain headers.
    """
    # ── WHICH DOMAINS THIS DEPLOYMENT FRONTS (PRD decisions 40.6 + 40.7) ─────────────────────
    #
    # Presence is a CONFIGURATION FACT — whether the deployment named the door — never a probe. A
    # probe makes "agent-api is restarting" and "there is no agent-api" the same answer, and the
    # second is a shipped product: `no-agents` is gateway + meetings + flows + identity (40.6).
    #
    # An absent domain's routes are not registered AND not in the table, and that pairing is the
    # whole point. Declared-but-unregistered would be a dead row; registered-but-undeclared would
    # 403 — "you may not", which is false. Absent on both sides answers **404**: this deployment
    # does not serve it, which is the truth and the only answer a client can act on.
    #
    # UNSPECIFIED IS NOT THE SAME AS "ALL FIVE". `agent_api_url=None` means the caller said
    # nothing, and what a build fronts when nobody says otherwise is what it CARRIES: a build
    # generated without the agent surface must not name a door it cannot describe. Production
    # never reaches this default — `adapters.build_production_app` reads AGENT_API_URL and passes
    # "" when it is unset, which has always meant no agent. An EXPLICIT url still names the
    # domain, and a named domain with no manifest behind it still refuses to boot, below.
    _present = {"gateway", "meetings", "identity", "mcp"}
    if agent_api_url is None:
        agent_api_url = _DEFAULT_AGENT_API_URL if "agent" in CARRIED_DOMAINS else ""
    _agent_present = bool((agent_api_url or "").strip())
    if _agent_present:
        _present.add("agent")
    _assembly = routes_manifest.load(_present)
    _route_scopes = _assembly.scopes
    _unscoped = _assembly.unscoped

    app = FastAPI(title="Vexa API Gateway (v0.12)")
    # The edge: mint/read X-Trace-Id and bind it for the request (logevent.v1 trace_id).
    app.add_middleware(TraceMiddleware)

    # --- liveness probe (gate:health): the edge is up. No auth (mirrors a real LB health
    # check), no downstream call. 200 + {status:"ok", service:"gateway"} = process is up.
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "gateway"}

    # --- /auth/me — caller identity from the API key (GET /auth/me with x-api-key →
    # user_id/email/scopes); the dashboard's login + session-validation resolve the user via this.
    # Uses the SAME authorizer (admin-api /internal/validate) as the proxy — no new dependency.
    @app.get("/auth/me")
    async def auth_me(request: Request):
        api_key = request.headers.get("x-api-key")
        if not api_key:
            return Response(content=json.dumps({"detail": "Missing API key"}),
                            status_code=401, media_type="application/json")
        try:
            user_data = await authorizer.resolve(api_key)
        except AuthUnavailable as e:
            return _auth_unavailable_response(e, span="auth")  # #495: infra down → 503, not 401
        if not user_data:
            return Response(content=json.dumps({"detail": "Invalid API key"}),
                            status_code=401, media_type="application/json")
        # A worker's delegation token is answered here only on the MCP's own re-entry, and only
        # because the edge's manifest declares this an MCP callback route (`"mcp_reentry"`).
        delegated = is_delegated(api_key, user_data)
        if delegated and not _admits_mcp_reentry(request, user_data):
            return delegated_route_response()
        is_admin = reported_admin(user_data, delegated=delegated)
        set_user_id(user_data["user_id"])
        return {
            "user_id": user_data["user_id"],
            "email": user_data.get("email", ""),
            "scopes": user_data.get("scopes", []),
            "max_concurrent": user_data.get("max_concurrent", 3),
            "is_admin": is_admin,
        }

    # --- auth + identity prep, shared by the buffered REST proxy (_forward) and the streaming proxy
    # (agent chat SSE). Returns (downstream_headers, None) on success, or (None, error_Response) when
    # the caller is rejected (fail-closed). This is the ONE place the key → user resolution and the
    # anti-spoof identity injection live, so REST and SSE scope a request identically.
    reentry = McpReentry(identity_key)

    def _admits_mcp_reentry(request: Request, user_data) -> bool:
        """Is this a worker's call the MCP makes back into a route its tools call?

        Both halves, always: the re-entry identity proves the MCP is acting on an `/mcp` request
        this edge admitted, and the row's `"mcp_reentry": true` says this is a route the MCP's
        tools call. Either alone is not enough — a valid re-entry on any other route is refused."""
        return _route_key(request) in _assembly.mcp_reentry and reentry.admits(request, user_data)

    async def _authorize(method: str, request: Request, *, api_key: Optional[str] = None):
        # ``api_key`` overrides the header lookup for a route whose CLIENT protocol carries the key
        # somewhere else (the MCP transport uses ``Authorization: Bearer``). The resolution, the
        # scope check and the identity injection below are the same for every route.
        # A worker's delegation token (`vxd_…`) is resolved like any other bearer: identity verifies
        # it (signature, audience, expiry, the account still existing) and answers with the person it
        # acts for plus the dispatch's ceiling, which rides the signed identity below as
        # `delegation`. It is ADMITTED only on a row whose manifest says `"delegation": true`, or on
        # a row that says `"mcp_reentry": true` when the request is the MCP's own re-entry — see
        # `delegation.py`.
        client_key = api_key if api_key is not None else request.headers.get("x-api-key")
        # Fail-closed: a client route with no key is rejected before any downstream call.
        if not client_key:
            return None, Response(
                content=json.dumps({"detail": "Missing API key"}),
                status_code=401,
                media_type="application/json",
            )

        try:
            user_data = await authorizer.resolve(client_key)
        except AuthUnavailable as e:
            # #495: the validation hop is unreachable/faulted — tell the caller the truth (503,
            # retry), never 401. A valid key must not be reported as invalid because we are slow.
            return None, _auth_unavailable_response(e, span="auth")
        if not user_data:
            return None, Response(
                content=json.dumps({"detail": "Invalid API key"}),
                status_code=401,
                media_type="application/json",
            )

        # Bind the resolved user to the trace context so every later line carries user_id.
        user_id = user_data["user_id"]
        set_user_id(user_id)

        # Per-user request rate limit (WS-6) — a valid key could otherwise fire unlimited requests at
        # the control plane (the max_concurrent_bots cap bounds active bots, not request rate). 429 when
        # the per-user token bucket is empty; the bucket refills continuously (Retry-After: 1s).
        if rate_limiter is not None and not rate_limiter.allow(str(user_id)):
            return None, Response(
                content=json.dumps({"detail": "Rate limit exceeded"}),
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": "1"},
            )

        if (is_delegated(client_key, user_data)
                and _route_key(request) not in _assembly.delegation
                and not _admits_mcp_reentry(request, user_data)):
            log_event(
                "request_denied_delegated_route",
                audience="user",
                level="warning",
                span="auth",
                user_id=user_id,
                fields={"method": method, "path": request.url.path},
            )
            return None, delegated_route_response()

        # Scope enforcement — DENY BY DEFAULT. Every proxied route declares its scopes in
        # ROUTE_SCOPES; an undeclared route is refused here rather than forwarded, so the failure
        # mode of forgetting a declaration is a 403 the author trips over, not an open door. (The
        # build-time check in create_app means an undeclared route cannot normally reach this at
        # all — this is the second wall, for a route registered onto the app after construction.)
        #
        # Per-RESOURCE authorization remains separate and still open on the agent domain: this
        # decides WHICH KEYS reach a route, not which of the owner's objects they may touch. See
        # the strict-xfail on GET /agent/meeting/stream in tests/test_proxy.py.
        required = _required_scopes(request, _route_scopes)
        if required is None:
            log_event(
                "request_denied_undeclared_route",
                audience="system",
                level="error",
                span="auth",
                user_id=user_id,
                fields={"method": method, "path": request.url.path},
            )
            return None, _insufficient_scope_response()
        user_scopes = set(user_data.get("scopes", []))
        if not user_scopes & required:
            log_event(
                "request_denied_scope",
                audience="user",
                level="warning",
                span="auth",
                user_id=user_id,
                fields={"method": method, "path": request.url.path, "required": sorted(required)},
            )
            return None, _insufficient_scope_response()

        # USER-facing event: the request was accepted on behalf of this user.
        log_event(
            "request_accepted",
            audience="user",
            span="auth",
            user_id=user_id,
            fields={"method": method, "path": request.url.path},
        )

        # Inject identity headers + forward the SAME trace_id downstream (main.py:322-326, 365).
        # Strip any client-supplied identity/authority headers first (anti-spoofing, main.py:294-296).
        excluded = {"host", "content-length", "transfer-encoding"}
        headers = {k.lower(): v for k, v in request.headers.items() if k.lower() not in excluded}
        for h in list(headers):
            if _is_authority_header(h):
                headers.pop(h, None)
        headers["x-api-key"] = client_key
        # THE RESOLVED IDENTITY, RE-STAMPED (gateway-identity.v1). Every value comes from /internal/validate,
        # never from the client: the user id; the verified email agent-api's membership redeem checks
        # for RESTRICTED invites (Lane M); the scopes and the bot limit `POST /bots` enforces; the
        # shared-workspace memberships meeting-api authorizes a member's transcript subscribe against
        # (Lane A); the per-user webhook bot_spawn persists into meeting.data (identity owns it); and,
        # for a worker's delegation token, the dispatch's ceiling. The services behind this edge
        # believe these headers only with the signature beside them — `X-Vexa-Identity`, an Ed25519
        # signature over the same claims with a short expiry, made with a private key only this edge
        # holds — so a process that reaches them past this edge cannot name a user.
        if identity_key is not None:
            headers.update(identity_token.signed_headers(identity_key, user_data))
        else:
            headers.update(identity_token.headers_from_claims(
                identity_token.claims_from_validation(user_data)))
        headers[TRACE_HEADER] = get_trace_id() or ""
        return headers, None

    # --- the REST proxy: faithful carve of main.forward_request for client (non-admin) routes.
    async def _forward(method: str, url: str, request: Request, *, api_key: Optional[str] = None
                       ) -> Response:
        headers, error = await _authorize(method, request, api_key=api_key)
        if error is not None:
            return error

        content = await request.body()
        # A public gateway must not LEAK its own 500 for an UPSTREAM fault: map a slow upstream → 504 and
        # an unreachable/transport-failed upstream → 502, so a client can tell "backend down" from
        # "gateway broke" (and get a retryable signal). Timeout is a subclass of RequestError → catch it first.
        try:
            resp = await downstream.request(
                method,
                url,
                headers=headers,
                params=dict(request.query_params) or None,
                content=content,
            )
        except httpx.InvalidURL:
            # Not a RequestError: without this arm an unparseable hop URL surfaces as a gateway 500
            # even though the fault is in what the CALLER put in the path.
            return invalid_path_param_response()
        except httpx.TimeoutException:
            return Response(content=json.dumps({"detail": "upstream timeout"}),
                            status_code=504, media_type="application/json")
        except httpx.RequestError as e:
            return Response(content=json.dumps({"detail": f"upstream unreachable: {type(e).__name__}"}),
                            status_code=502, media_type="application/json")

        # SYSTEM/debug event: the proxy hop completed.
        log_event(
            "downstream_forwarded",
            audience="system",
            level="debug",
            span="proxy",
            fields={"method": method, "path": url, "downstream_status": resp.status_code},
        )

        # Return downstream body + status VERBATIM (drop hop-by-hop headers; main.py:367).
        resp_headers = resp.headers
        media_type = "application/json"
        try:
            media_type = resp_headers.get("content-type", "application/json")
        except Exception:
            pass
        # Preserve the END-TO-END headers a media/range response needs. The buffered proxy
        # otherwise returns a 206 with no Content-Range, which browsers treat as a protocol
        # violation and abort — breaking recording playback (<audio>/<video> Range streaming).
        # Length/encoding headers are intentionally NOT copied: Starlette recomputes
        # Content-Length from the (already httpx-decoded) body; a stale one would corrupt it.
        # ``mcp-session-id``/``mcp-protocol-version`` join them for the same reason: they are the
        # MCP transport's session binding, and a forward that eats them breaks the handshake.
        passthrough = {
            k: resp_headers[k]
            for k in ("content-range", "accept-ranges", "content-disposition") + _MCP_HEADERS
            if k in resp_headers
        }
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=media_type,
            headers=passthrough,
        )

    def _meeting(path: str) -> str:
        return f"{meeting_api_url}{path}"

    # A MEETINGS ROW'S HOP IS ITS MANIFEST ROW'S, FILLED ONE OPAQUE SEGMENT AT A TIME. The target is
    # the row's own path, or the `upstream` it declares (`/user/webhook/deliveries` is meeting-api's
    # `/webhooks/deliveries`) — the same field meeting-api reads to check, on each of its routes, the
    # scopes this edge checked (`meeting_api/route_scopes.py`), so the two cannot disagree about
    # which row reaches which route. Every `{name}` is filled from what the request matched under the
    # forwarded-row rule (`paths.forwarded_param`): a `.`/`..` value, or an encoded `/` or `\`
    # anywhere in the target, is a 400 before the caller is authorized; anything else, `?` and `#`
    # included, is percent-encoded into the one segment it arrived in. Starlette hands a handler its
    # parameters DECODED, so a raw interpolation would let `%2E%2E` or `%23` reshape the hop and land
    # it on a meeting-api route other than the one whose scope was checked here.
    def _meeting_target(request: Request) -> Tuple[Optional[str], Optional[Response]]:
        key = _route_key(request)
        if key is None or _assembly.owner_of.get(key) != "meetings":
            return None, _insufficient_scope_response()  # not a meetings row: nothing to forward
        error = forwarded_target_error(request)
        if error is not None:
            return None, error
        target = _assembly.upstream.get(key, key[1])
        for name in routes_manifest.params_of(target):
            segment, error = forwarded_param(str(request.path_params.get(name, "")), request)
            if error is not None:
                return None, error
            target = target.replace("{" + name + "}", segment, 1)
        return _meeting(target), None

    async def _forward_meeting(request: Request) -> Response:
        url, error = _meeting_target(request)
        if error is not None:
            return error
        return await _forward(request.method, url, request)

    # ---- CORE routes (each forwards to the matching downstream path, per main's route table) ----
    @app.get("/bots")
    async def list_bots(request: Request):
        return await _forward_meeting(request)

    @app.post("/bots", status_code=201)
    async def create_bot(request: Request):
        return await _forward_meeting(request)

    @app.get("/bots/status")
    async def bots_status(request: Request):
        return await _forward_meeting(request)

    @app.delete("/bots/{platform}/{native_meeting_id}")
    async def stop_bot(platform: MeetingPlatform, native_meeting_id: str, request: Request):
        return await _forward_meeting(request)

    @app.put("/bots/{platform}/{native_meeting_id}/config", status_code=202)
    async def update_config(platform: MeetingPlatform, native_meeting_id: str, request: Request):
        return await _forward_meeting(request)

    @app.post("/bots/{platform}/{native_meeting_id}/speak")
    async def speak(platform: MeetingPlatform, native_meeting_id: str, request: Request):
        return await _forward_meeting(request)

    # P0 (cross-tenant leak fix): the by-ROW-id transcript read the terminal uses to fetch EXACTLY the
    # row it displays (owner-scoped downstream). Registered BEFORE the native route so `by-id` is not
    # matched as a {platform}. Forwarded verbatim; the auth/identity prep (X-User-Id) is shared.
    @app.get("/transcripts/by-id/{meeting_id}")
    async def transcript_by_id(meeting_id: int, request: Request):
        return await _forward_meeting(request)

    # Redeem an INDEPENDENT transcript share token (Lane A / M0). Declared BEFORE the {platform}/{native}
    # GET so `share/accept` is not matched as a 2-segment transcript path.
    @app.post("/transcripts/share/accept")
    async def accept_transcript_share(request: Request):
        return await _forward_meeting(request)

    # native-keyed share MINT alias (#579 C3): the 0.10 api.v1 share path. The mint MOVED to
    # POST /meetings/{platform}/{native}/share in 0.12; alias the old transcripts path to it so a
    # 0.10 client no longer 404s. NOTE (signed): the 0.12 mint returns the capability-token share
    # shape ({id, token, mode, expires_at}), NOT the sealed TranscriptShareResponse public-URL shape
    # ({share_id, url, expires_at, expires_in_seconds}) — the public-URL-share backend is gone; only
    # the capability-token share exists. See the PR's "signed gaps" section.
    # by-ROW-id share alias, mirroring the `transcripts/by-id` read above so a client that knows a
    # meeting only as a row id has one path shape for both. Declared BEFORE the {platform}/{native}
    # form so `by-id` is not captured as a platform name.
    @app.post("/transcripts/by-id/{meeting_id}/share")
    async def mint_transcript_share_by_id_alias(meeting_id: int, request: Request):
        return await _forward_meeting(request)

    @app.post("/transcripts/{platform}/{native_meeting_id}/share")
    async def mint_transcript_share_alias(platform: MeetingPlatform, native_meeting_id: str, request: Request):
        return await _forward_meeting(request)

    # Declared BEFORE /transcripts/{platform}/... so `search` is matched as a literal, not
    # captured as a platform name.
    @app.get("/transcripts/search")
    async def search_transcripts(request: Request):
        return await _forward_meeting(request)

    @app.get("/transcripts/{platform}/{native_meeting_id}")
    async def transcript(platform: MeetingPlatform, native_meeting_id: str, request: Request):
        return await _forward_meeting(request)

    @app.get("/recordings")
    async def list_recordings(request: Request):
        return await _forward_meeting(request)

    @app.get("/recordings/{recording_id}")
    async def get_recording(recording_id: int, request: Request):
        return await _forward_meeting(request)

    @app.delete("/recordings/{recording_id}")
    async def delete_recording(recording_id: int, request: Request):
        return await _forward_meeting(request)

    # finalize-on-read master metadata (audio|video); the recording player fetches this, then the
    # raw_url it returns. ?type= is preserved by _forward.
    @app.get("/recordings/{recording_id}/master")
    async def get_recording_master(recording_id: int, request: Request):
        return await _forward_meeting(request)

    # The master byte stream the recording player loads (the master metadata's raw_url points here).
    @app.get("/recordings/{recording_id}/media/{media_file_id}/raw")
    async def get_recording_media_raw(recording_id: int, media_file_id: int, request: Request):
        return await _forward_meeting(request)

    # native download alias (#579 C3): the sealed api.v1 media-download path a 0.10 client calls.
    # 0.12 renamed the media byte route to .../raw (finalize-on-read master stream); alias .../download
    # to it so recording playback no longer 404s. Forwarded verbatim (Range headers preserved).
    @app.get("/recordings/{recording_id}/media/{media_file_id}/download")
    async def get_recording_media_download(recording_id: int, media_file_id: int, request: Request):
        return await _forward_meeting(request)

    @app.get("/meetings")
    async def meetings(request: Request):
        return await _forward_meeting(request)

    # Create a PLANNED meeting (intent status, no bot) — the Meetings surface's "Plan a meeting".
    @app.post("/meetings", status_code=201)
    async def create_planned_meeting(request: Request):
        return await _forward_meeting(request)

    # Import a meeting-bundle.v1 archive as a new meeting the caller owns (`?dry_run=true` previews
    # it and writes nothing). A literal segment, so it is never matched as a row id.
    @app.post("/meetings/import")
    async def import_meeting_bundle(request: Request):
        return await _forward_meeting(request)

    # Single meeting — forwards to meeting-api's GET /meetings/{id} (the meeting-detail page reads it).
    @app.get("/meetings/{meeting_id}")
    async def meeting(meeting_id: int, request: Request):
        return await _forward_meeting(request)

    # The meeting as one portable meeting-bundle.v1 file, for its owner. The zip and its
    # Content-Disposition pass through verbatim.
    @app.get("/meetings/{meeting_id}/export")
    async def export_meeting_bundle(meeting_id: int, request: Request):
        return await _forward_meeting(request)

    # Edit / delete a PLANNED meeting by ROW id (owner-scoped; meeting-api refuses FSM rows with 409).
    @app.patch("/meetings/{meeting_id}")
    async def patch_planned_meeting(meeting_id: int, request: Request):
        return await _forward_meeting(request)

    @app.delete("/meetings/{meeting_id}", status_code=204)
    async def delete_planned_meeting(meeting_id: int, request: Request):
        return await _forward_meeting(request)

    # User-owned scheduling intent (schedule/cancel) — the Meetings surface's Schedule/Cancel action
    # PUTs here; forwards to meeting-api's PUT /meetings/{platform}/{native}/intent (owner-scoped).
    # Mint an INDEPENDENT transcript share link for a meeting (owner) — Lane A / M0.
    # The caller's own description of a meeting — title + arbitrary metadata — writable in ANY
    # status (meeting-api refuses nothing here; nothing in the dispatch pipeline reads it).
    @app.post("/meetings/{platform}/{native_meeting_id}/annotate")
    async def annotate_meeting(platform: MeetingPlatform, native_meeting_id: str, request: Request):
        return await _forward_meeting(request)

    # Annotate by ROW id — the identity a meeting always has. The (platform, native) pair is not
    # one: a Google Meet room code is reused across sessions and downstream resolves it to the
    # caller's NEWEST row, so an older meeting on a recurring link could be READ (that is what
    # /transcripts/by-id above is for) but never written back to. Three segments against the pair
    # route's four, so neither shadows the other on segment count — the same property
    # POST /meetings/{meeting_id}/share below already relies on.
    @app.post("/meetings/{meeting_id}/annotate")
    async def annotate_meeting_by_id(meeting_id: int, request: Request):
        return await _forward_meeting(request)

    # Mint by ROW id — the identity a meeting always has. The (platform, native) pair is not one: a
    # row planned from an invite whose url matched no platform is platform='unknown' with an empty
    # native, so the pair route below 404s on it and the caller (the attendee-mail fan-out) shipped
    # links with no capability. Three segments against the pair route's four, so on segment count
    # alone neither shadows the other — the same property the by-ROW-id notes above rely on. Same
    # _forward, so the same auth/identity header prep (X-User-Id, X-User-Email, X-User-Workspaces).
    @app.post("/meetings/{meeting_id}/share")
    async def mint_transcript_share_by_id(meeting_id: int, request: Request):
        return await _forward_meeting(request)

    # Import a transcript into a meeting the caller owns — "this already happened, here are its
    # words" — and complete it. Row-id addressed like the mint above; same _forward, so the same
    # key→identity resolution (X-User-Id) the meeting-api route scopes on.
    @app.post("/meetings/{meeting_id}/transcript-import")
    async def import_meeting_transcript(meeting_id: int, request: Request):
        return await _forward_meeting(request)

    @app.post("/meetings/{platform}/{native_meeting_id}/share")
    async def mint_transcript_share(platform: MeetingPlatform, native_meeting_id: str, request: Request):
        return await _forward_meeting(request)

    # Bind a meeting to a shared workspace (owner) — Lane A (optional convenience).
    @app.post("/meetings/{platform}/{native_meeting_id}/workspace")
    async def bind_meeting_workspace(platform: MeetingPlatform, native_meeting_id: str, request: Request):
        return await _forward_meeting(request)

    # Who was in this meeting, as far as the core actually knows — invitation attendees + heard
    # speakers, each row labelled with its source (Vexa-ai/vexa#451). Owner-scoped downstream.
    @app.get("/meetings/{platform}/{native_meeting_id}/participants")
    async def get_meeting_participants(platform: MeetingPlatform, native_meeting_id: str, request: Request):
        return await _forward_meeting(request)

    @app.put("/meetings/{platform}/{native_meeting_id}/intent")
    async def set_meeting_intent(platform: MeetingPlatform, native_meeting_id: str, request: Request):
        return await _forward_meeting(request)

    # native-keyed mutate (#579 C1): the sealed api.v1 PATCH/DELETE a 0.10 client (incl. the shipped
    # dashboard) calls by (platform, native_meeting_id). Thin passthrough — meeting-api resolves
    # (platform, native) → the caller's newest OWNED row and forwards to the same row-id handler
    # (unknown/unowned native → 404, FSM-owned row → 409). Additive: the by-ROW-id int routes above
    # are unchanged; these 2-segment paths never shadow them (FastAPI matches on segment count).
    @app.patch("/meetings/{platform}/{native_meeting_id}")
    async def patch_native_meeting(platform: MeetingPlatform, native_meeting_id: str, request: Request):
        return await _forward_meeting(request)

    @app.delete("/meetings/{platform}/{native_meeting_id}")
    async def delete_native_meeting(platform: MeetingPlatform, native_meeting_id: str, request: Request):
        return await _forward_meeting(request)

    # native-keyed chat READ (#579 C3): the sealed api.v1 GET the 0.10 dashboard's chat panel calls.
    # Thin passthrough to meeting-api's honest empty-list restore (0.12 does not persist in-meeting
    # chat server-side). The POST (send) half is a SIGNED GAP — no bot-command backend in 0.12.
    @app.get("/bots/{platform}/{native_meeting_id}/chat")
    async def read_meeting_chat(platform: MeetingPlatform, native_meeting_id: str, request: Request):
        return await _forward_meeting(request)

    # ---- user self-serve webhook config (main.py:1080 set_user_webhook_proxy) ----
    # Identity OWNS the config (user.data JSONB via admin-api); the gateway is the public edge for
    # it, exactly like the meeting routes: _forward resolves the key via /internal/validate (the
    # Authorizer), injects identity headers, and returns the downstream body + status verbatim.
    # Scoped BOT_OR_TX: the webhook is account config for the two service domains, so a key that
    # owns either may manage it — but a key that owns NEITHER may not. Until rc.18 this route had
    # no entry at all, and "no entry" meant "no check" (sweep finding B2).
    def _admin(path: str) -> str:
        return f"{admin_api_url}{path}"

    @app.put("/user/webhook")
    async def set_user_webhook(request: Request):
        return await _forward("PUT", _admin("/user/webhook"), request)

    # Read-back for the self-serve config (admin-api masks the secret before it ships).
    @app.get("/user/webhook")
    async def get_user_webhook(request: Request):
        return await _forward("GET", _admin("/user/webhook"), request)

    # #841: the per-user webhook DELIVERY HISTORY — the queryable record of real delivery outcomes
    # (the user-facing completion of #815→#817). Unlike the config above (identity-owned, admin-api),
    # the deliveries live in meeting-api (the dispatcher that records each outcome), so this forwards
    # THERE. Scoped BOT_OR_TX like the config above; the shared auth prep injects X-User-Id so
    # meeting-api scopes the read to the owner.
    @app.get("/user/webhook/deliveries")
    async def get_user_webhook_deliveries(request: Request):
        return await _forward_meeting(request)

    # ---- user self-serve calendar-sync config (identity owns it, same shape as /user/webhook).
    # The ICS URL is a secret — admin-api masks it on every read-back. Scoped BOT, not BOT_OR_TX:
    # a connection with auto_join on turns every event in the feed into a bot spawn, so managing
    # one is bot-lifecycle power reached through a different door. ----
    # calendar-sync feedback edges live in MEETING-api (they run the sync), unlike the config
    # (identity). Registered before the config routes only for reading clarity - paths are exact.
    @app.get("/user/calendar/sync")
    async def get_user_calendar_sync(request: Request):
        return await _forward_meeting(request)

    @app.post("/user/calendar/sync")
    async def run_user_calendar_sync(request: Request):
        return await _forward_meeting(request)

    @app.put("/user/calendar")
    async def set_user_calendar(request: Request):
        return await _forward("PUT", _admin("/user/calendar"), request)

    @app.get("/user/calendar")
    async def get_user_calendar(request: Request):
        return await _forward("GET", _admin("/user/calendar"), request)

    @app.get("/user/calendars")
    async def list_user_calendars(request: Request):
        return await _forward("GET", _admin("/user/calendars"), request)

    @app.post("/user/calendars")
    async def create_user_calendar(request: Request):
        return await _forward("POST", _admin("/user/calendars"), request)

    @app.patch("/user/calendars/{calendar_id}")
    async def update_user_calendar(calendar_id: str, request: Request):
        segment, error = path_segment(calendar_id)
        if error is not None:
            return error
        return await _forward("PATCH", _admin(f"/user/calendars/{segment}"), request)

    @app.delete("/user/calendars/{calendar_id}")
    async def delete_user_calendar(calendar_id: str, request: Request):
        segment, error = path_segment(calendar_id)
        if error is not None:
            return error
        return await _forward("DELETE", _admin(f"/user/calendars/{segment}"), request)

    @app.get("/user/calendars/{calendar_id}/sync")
    async def get_calendar_connection_sync(calendar_id: str, request: Request):
        return await _forward_meeting(request)

    @app.post("/user/calendars/{calendar_id}/sync")
    async def run_calendar_connection_sync(calendar_id: str, request: Request):
        return await _forward_meeting(request)

    # ---- user self-serve model + transcription prefs (identity owns them, same shape as
    # /user/webhook: secrets masked by admin-api on every read-back, scoped BOT_OR_TX). ----
    @app.put("/user/models")
    async def set_user_models(request: Request):
        return await _forward("PUT", _admin("/user/models"), request)

    @app.get("/user/models")
    async def get_user_models(request: Request):
        return await _forward("GET", _admin("/user/models"), request)

    @app.put("/user/transcription")
    async def set_user_transcription(request: Request):
        return await _forward("PUT", _admin("/user/transcription"), request)

    @app.get("/user/transcription")
    async def get_user_transcription(request: Request):
        return await _forward("GET", _admin("/user/transcription"), request)

    # ---- the AGENT domain (P20·Stage 2): fronted wholesale under the prefix its manifest declares
    # (`forward`), so the SAME edge resolves key → user and injects X-User-Id; agent-api derives
    # `subject` from it (never the client). The terminal therefore talks ONLY to the gateway.
    # The agent SSE routes (chat turn · live meeting feed) must be STREAMED, not buffered like the JSON
    # routes — so they get their own forward, declared BEFORE the catch-all so they win. Identity is
    # injected by the SAME _authorize the buffered proxy uses (so the streamed turn is scoped identically).
    SSE_HEADERS = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

    async def _forward_stream(method: str, url: str, request: Request) -> Response:
        headers, error = await _authorize(method, request)
        if error is not None:
            return error
        content = await request.body()
        params = dict(request.query_params) or None

        # THE HEAD DECIDES, NOT THE ROUTE. Only an upstream that answered a 2xx event stream is
        # relayed as SSE; a refusal (403 from a person-only verb, 501, 404) or any other non-stream
        # answer reaches the caller with the upstream's own status and body, instead of a 200 SSE
        # envelope wrapped around an error. Transport failures map to 504/502 as the buffered
        # forward maps them.
        stack = AsyncExitStack()
        try:
            upstream = await stack.enter_async_context(
                downstream.open_stream(method, url, headers=headers, params=params, content=content))
        except httpx.TimeoutException:
            await stack.aclose()
            return Response(content=json.dumps({"detail": "upstream timeout"}),
                            status_code=504, media_type="application/json")
        except httpx.RequestError as e:
            await stack.aclose()
            return Response(content=json.dumps({"detail": f"upstream unreachable: {type(e).__name__}"}),
                            status_code=502, media_type="application/json")

        media_type = upstream.headers.get("content-type") or "application/json"
        if not (200 <= upstream.status_code < 300 and media_type.startswith("text/event-stream")):
            try:
                answer = b"".join([chunk async for chunk in upstream.aiter_bytes()])
            finally:
                await stack.aclose()
            return Response(content=answer, status_code=upstream.status_code, media_type=media_type)

        async def body():
            async with stack:  # closes the downstream stream when the client goes away
                async for chunk in upstream.aiter_bytes():
                    yield chunk

        return StreamingResponse(body(), media_type="text/event-stream", headers=SSE_HEADERS)

    # The RELAY forward: like _forward_stream, the body is streamed and never buffered — but the
    # UPSTREAM's own head is carried through (status + content-type + the transport headers named
    # in _MCP_HEADERS) instead of the gateway minting an SSE envelope of its own. A route uses this
    # when the upstream, not the gateway, decides what the answer is: the MCP streamable-HTTP leg
    # answers an SSE stream, a JSON error, or a session-handshake refusal over the SAME GET, and
    # the gateway must not decide which by rewriting it (#698 fail-loud: no laundered statuses).
    #
    # The open is awaited BEFORE the response is returned so a transport failure is typed exactly
    # as the buffered forward types it (504 slow / 502 unreachable) — never a blanket 503. Only
    # after a head is in hand does the body iterator take over; the exit stack keeps the downstream
    # stream open for its life and closes it when the client goes away.
    async def _forward_stream_verbatim(
        method: str, url: str, request: Request, *, api_key: Optional[str] = None,
    ) -> Response:
        headers, error = await _authorize(method, request, api_key=api_key)
        if error is not None:
            return error
        content = await request.body()
        params = dict(request.query_params) or None

        stack = AsyncExitStack()
        try:
            upstream = await stack.enter_async_context(
                downstream.open_stream(method, url, headers=headers, params=params, content=content)
            )
        except httpx.TimeoutException:
            await stack.aclose()
            return Response(content=json.dumps({"detail": "upstream timeout"}),
                            status_code=504, media_type="application/json")
        except httpx.RequestError as e:
            await stack.aclose()
            return Response(content=json.dumps({"detail": f"upstream unreachable: {type(e).__name__}"}),
                            status_code=502, media_type="application/json")

        log_event(
            "downstream_stream_opened",
            audience="system",
            level="debug",
            span="proxy",
            fields={"method": method, "path": url, "downstream_status": upstream.status_code},
        )

        async def body():
            async with stack:  # closes the downstream stream when the client disconnects
                async for chunk in upstream.aiter_bytes():
                    yield chunk

        up_headers = upstream.headers
        media_type = up_headers.get("content-type") or "application/json"
        relayed = {k: up_headers[k] for k in _MCP_HEADERS if k in up_headers}
        # Never let an intermediary buffer a relayed stream into uselessness.
        relayed.setdefault("Cache-Control", "no-cache")
        relayed.setdefault("X-Accel-Buffering", "no")
        return StreamingResponse(
            body(), status_code=upstream.status_code, media_type=media_type, headers=relayed
        )

    # A FORWARDED DOMAIN IS REGISTERED FROM ITS MANIFEST, and this code names none of its routes.
    # `forward` maps `<edge_prefix><path>` onto `<upstream_prefix><path>` at the domain's door; a
    # row that is not the catch-all is a route of its own — its `{name}` segments re-encoded
    # (`paths.py`), relayed as server-sent events when it says `stream` — registered BEFORE the
    # prefix's `{path:path}` catch-all so it wins; the catch-all carries the path/method/query/body
    # verbatim, its tail re-encoded too. What a row admits (its scopes, a worker's delegation token,
    # the MCP's re-entry) is read from the same row by `_authorize`.
    #
    # REGISTERED ONLY WHEN THE AGENT DOMAIN IS DEPLOYED: its manifest is loaded on the same
    # condition, so in a no-agents deployment the routes and their declarations are absent together
    # and `/agent/anything` is a 404.
    def _register_forwarded(domain: str, base_url: str) -> None:
        if domain not in _assembly.forwards:
            raise routes_manifest.ManifestError(
                f"{domain} is fronted wholesale but its routes.v1.json declares no forward")
        edge, upstream = _assembly.forwards[domain]
        rows = sorted(k for k, d in _assembly.owner_of.items() if d == domain)

        def literal(method: str, path: str):
            template = f"{base_url}{upstream}{path[len(edge):]}"
            names = routes_manifest.params_of(path)
            relay = _forward_stream if (method, path) in _assembly.stream else _forward

            async def forward_literal(request: Request):
                # Held to the catch-all's rule (`paths.py`): an encoded separator in the target is a
                # 400, and a `{name}` segment is filled with what the caller sent, re-encoded as ONE
                # opaque segment (a `.`/`..` value refused, as the catch-all refuses it).
                error = forwarded_target_error(request)
                if error is not None:
                    return error
                url = template
                for name in names:
                    segment, error = forwarded_param(str(request.path_params.get(name, "")), request)
                    if error is not None:
                        return error
                    url = url.replace("{" + name + "}", segment, 1)
                return await relay(method, url, request)
            app.add_api_route(path, forward_literal, methods=[method])

        for method, path in rows:
            if not path.endswith(routes_manifest.CATCH_ALL):
                literal(method, path)
        catch_all = sorted(m for m, p in rows if p.endswith(routes_manifest.CATCH_ALL))
        if catch_all:
            async def forward_tail(path: str, request: Request):
                tail, error = tail_path(path, request)
                if error is not None:
                    return error
                return await _forward(request.method, f"{base_url}{upstream}{tail}", request)
            app.add_api_route(edge + routes_manifest.CATCH_ALL, forward_tail, methods=catch_all)

    if _agent_present:
        _register_forwarded("agent", agent_api_url)

    # ---- the MCP front door (#795): the streamable-HTTP transport, fronted at the edge ----
    # MCP streamable-HTTP is ONE endpoint driven by two methods with opposite lifetimes:
    #   POST /mcp — a message. Short request/response JSON → the buffered forward, verbatim.
    #   GET  /mcp — the server→client SSE stream. It sends its headers and then IDLES until the
    #               server has something to push, so it MUST be relayed, never buffered: a buffered
    #               forward waits on the next body read of a healthy-but-silent stream, hits its
    #               read timeout, and answers a gateway-manufactured 5xx the MCP service never sees
    #               (#795 — 8 × 503 on GET at the edge, 0 at the service, POST 116/116 fine).
    # The two legs are therefore declared separately: GET streams, everything else buffers.
    # ONE MCP server (ADR-0037 §2): every bearer — a person's API key, a worker's delegation token —
    # reaches the same assembled surface, authenticated by the same `_authorize`.
    def _mcp(path: str, request: Request) -> str:
        return f"{mcp_url.rstrip('/')}{path}"

    def _mcp_key(request: Request) -> Optional[str]:
        """The caller's Vexa API key, from whichever carrier the MCP transport used.

        MCP clients send the credential as ``Authorization: Bearer <key>`` (mcp-remote, the desktop
        connectors) — that is the transport's contract, and adapting it belongs at the client
        boundary, i.e. here at the edge. ``x-api-key`` (every other Vexa client) wins when both are
        present; a raw ``Authorization: <key>`` with no scheme is the key itself (0.10 parity, and
        the same three carriers the MCP service's own parser accepts). Beyond this point the key is
        the SAME string every other forward resolves, through the SAME authorizer.
        """
        header_key = (request.headers.get("x-api-key") or "").strip()
        if header_key:
            return header_key
        auth = (request.headers.get("authorization") or "").strip()
        if not auth:
            return None
        scheme, _, token = auth.partition(" ")
        if scheme.lower() == "bearer":
            return token.strip() or None
        return auth

    # The MCP manifest declares every row `"delegation": true`: these are the routes a worker's
    # delegation token is FOR.
    @app.get("/mcp")
    async def mcp_stream(request: Request):
        return await _forward_stream_verbatim("GET", _mcp("/mcp", request), request,
                                              api_key=_mcp_key(request))

    @app.get("/mcp/{path:path}")
    async def mcp_stream_path(path: str, request: Request):
        tail, error = tail_path(path, request)
        if error is not None:
            return error
        return await _forward_stream_verbatim(
            "GET", _mcp(f"/mcp/{tail}", request), request, api_key=_mcp_key(request))

    @app.api_route("/mcp", methods=["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def mcp_message(request: Request):
        return await _forward(request.method, _mcp("/mcp", request), request,
                              api_key=_mcp_key(request))

    @app.api_route("/mcp/{path:path}", methods=["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def mcp_message_path(path: str, request: Request):
        tail, error = tail_path(path, request)
        if error is not None:
            return error
        return await _forward(
            request.method, _mcp(f"/mcp/{tail}", request), request, api_key=_mcp_key(request))

    # ---- the /ws multiplex (carve of main.websocket_multiplex, main.py:2165-2340) ----
    @app.websocket("/ws")
    async def websocket_multiplex(ws: WebSocket):
        await run_multiplex(ws, authorizer, redis, route_scopes=_route_scopes)

    # ---- deny by default, at BUILD time ----
    # The route table is now complete, so every route must have declared its scopes. Refusing to
    # BUILD is the point: a 403 at request time only tells whoever hits the route, and only after
    # it ships, whereas this fires in every unit test, every conformance run and the container's
    # own start-up. An under-declared gateway does not boot.
    missing = undeclared_routes(app, _route_scopes, _unscoped)
    if missing:
        raise RuntimeError(
            "gateway routes with no scope declaration: "
            + ", ".join(f"{m} {p}" for m, p in missing)
            + " — declare each in the OWNING DOMAIN's routes.v1.json (an identity-only route that "
            "needs no scope is declared there with an empty `scopes`). The edge composes the table "
            f"from the domains it fronts and owns none of it; this build fronts {sorted(_present)}."
        )

    return app

"""``create_app(authorizer, downstream, redis, ...) -> FastAPI`` — the PRODUCTION gateway.

This is the single source of the proxy + multiplex logic for the v0.12 gateway lane. Its
behavior is the v0.12 carve of the deployed ``services/api-gateway/main.py``:

  * the ``forward_request`` auth middleware — ``x-api-key`` resolved via the ``Authorizer``
    port (admin-api ``/internal/validate``); fail-closed 401 when missing/invalid; scope 403
    via ``ROUTE_SCOPES`` (main.py:287-369, 59-65),
  * the CORE proxy routes — each forwards its method to the matching downstream URL and returns
    the downstream body + status VERBATIM (main.py:450-831, 367),
  * the ``/ws`` multiplex control loop + redis pub/sub fan-in — subscribe → Subscribed ack;
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

import asyncio
import json
import os
from typing import Dict, List, Optional, Set, Tuple

import httpx  # the downstream adapter's transport errors are mapped to 502/504 (not leaked as a 500)

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

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

# Route-prefix → required scope set. Mirrors main.py ROUTE_SCOPES (main.py:59-65) for the CORE
# surface the gateway lane carves; multi-scope tokens pass for any of their domains.
ROUTE_SCOPES: Dict[str, Set[str]] = {
    "/bots": {"bot", "browser"},
    "/transcripts": {"tx"},
    "/meetings": {"tx"},
    "/recordings": {"tx", "bot"},
}

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


def _required_scopes(path: str) -> Optional[Set[str]]:
    for prefix, scopes in ROUTE_SCOPES.items():
        if path.startswith(prefix):
            return scopes
    return None


def create_app(
    authorizer: Authorizer,
    downstream: DownstreamClient,
    redis: RedisBus,
    *,
    meeting_api_url: str = _DEFAULT_MEETING_API_URL,
    agent_api_url: str = _DEFAULT_AGENT_API_URL,
    admin_api_url: str = _DEFAULT_ADMIN_API_URL,
    rate_limiter=None,
) -> FastAPI:
    """Build the gateway FastAPI app over the injected ports.

    ``authorizer``  — resolves ``x-api-key`` → user/scopes (admin-api ``/internal/validate``).
    ``downstream``  — forwards proxied HTTP requests to meeting-api (the unified control plane:
                      /bots + /transcripts + /meetings + /recordings all live there now, P2).
    ``redis``       — pub/sub bus for the ``/ws`` fan-in.
    """
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
        set_user_id(user_data["user_id"])
        return {
            "user_id": user_data["user_id"],
            "email": user_data.get("email", ""),
            "scopes": user_data.get("scopes", []),
            "max_concurrent": user_data.get("max_concurrent", 3),
        }

    # --- auth + identity prep, shared by the buffered REST proxy (_forward) and the streaming proxy
    # (agent chat SSE). Returns (downstream_headers, None) on success, or (None, error_Response) when
    # the caller is rejected (fail-closed). This is the ONE place the key → user resolution and the
    # anti-spoof identity injection live, so REST and SSE scope a request identically.
    async def _authorize(method: str, request: Request):
        client_key = request.headers.get("x-api-key")
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

        # Scope enforcement (main.py:341-351).
        # Stage 3 SCAFFOLD (not delivered): the agent domain (/api/*) carries NO scope today, so a valid
        # key reaches any agent route. When Stage 3 lands, add agent-route scopes here (or a canAccess
        # default-deny resolved per (user_id, resource owner)) so a key can read ONLY its owner's
        # workspace/sessions/routines — see core/agent canAccess + the capability-token seam.
        required = _required_scopes(request.url.path)
        if required is not None:
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
                return None, Response(
                    content=json.dumps({"detail": "Insufficient scope for this endpoint"}),
                    status_code=403,
                    media_type="application/json",
                )

        # USER-facing event: the request was accepted on behalf of this user.
        log_event(
            "request_accepted",
            audience="user",
            span="auth",
            user_id=user_id,
            fields={"method": method, "path": request.url.path},
        )

        # Inject identity headers + forward the SAME trace_id downstream (main.py:322-326, 365).
        # Strip any client-supplied identity headers first (anti-spoofing, main.py:294-296).
        excluded = {"host", "content-length", "transfer-encoding"}
        headers = {k.lower(): v for k, v in request.headers.items() if k.lower() not in excluded}
        for h in ("x-user-id", "x-user-email", "x-user-scopes", "x-user-limits", "x-user-workspaces",
                  "x-user-webhook-url", "x-user-webhook-secret", "x-user-webhook-events"):
            headers.pop(h, None)
        headers["x-api-key"] = client_key
        headers["x-user-id"] = str(user_id)
        # The RESOLVED verified email (never client-declared; /internal/validate returns it). agent-api's
        # membership redeem (Lane M) checks it for RESTRICTED invites (allowed_emails).
        if user_data.get("email"):
            headers["x-user-email"] = str(user_data["email"])
        headers["x-user-scopes"] = ",".join(user_data.get("scopes", []))
        headers["x-user-limits"] = str(user_data.get("max_concurrent", 3))
        # Lane A: the RESOLVED shared-workspace membership ids (never client-declared; /internal/validate
        # returns them). meeting-api authorizes a member's live-transcript subscribe against this set.
        if user_data.get("workspaces"):
            headers["x-user-workspaces"] = ",".join(str(w) for w in user_data["workspaces"])
        # Per-user webhook config (identity owns it; /internal/validate returns it from user.data).
        # Forwarded so bot_spawn persists it into meeting.data → the lifecycle callback delivers from
        # there, with NO cross-domain users-table read (the carve's principled path; main read the user
        # row inline as a monolith).
        if user_data.get("webhook_url"):
            headers["x-user-webhook-url"] = str(user_data["webhook_url"])
            if user_data.get("webhook_secret"):
                headers["x-user-webhook-secret"] = str(user_data["webhook_secret"])
            if user_data.get("webhook_events"):
                headers["x-user-webhook-events"] = json.dumps(user_data["webhook_events"])
        headers[TRACE_HEADER] = get_trace_id() or ""
        return headers, None

    # --- the REST proxy: faithful carve of main.forward_request for client (non-admin) routes.
    async def _forward(method: str, url: str, request: Request) -> Response:
        headers, error = await _authorize(method, request)
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
        passthrough = {
            k: resp_headers[k]
            for k in ("content-range", "accept-ranges", "content-disposition")
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

    # ---- CORE routes (each forwards to the matching downstream path, per main's route table) ----
    @app.get("/bots")
    async def list_bots(request: Request):
        return await _forward("GET", _meeting("/bots"), request)

    @app.post("/bots", status_code=201)
    async def create_bot(request: Request):
        return await _forward("POST", _meeting("/bots"), request)

    @app.get("/bots/status")
    async def bots_status(request: Request):
        return await _forward("GET", _meeting("/bots/status"), request)

    @app.delete("/bots/{platform}/{native_meeting_id}")
    async def stop_bot(platform: str, native_meeting_id: str, request: Request):
        return await _forward("DELETE", _meeting(f"/bots/{platform}/{native_meeting_id}"), request)

    @app.put("/bots/{platform}/{native_meeting_id}/config", status_code=202)
    async def update_config(platform: str, native_meeting_id: str, request: Request):
        return await _forward("PUT", _meeting(f"/bots/{platform}/{native_meeting_id}/config"), request)

    @app.post("/bots/{platform}/{native_meeting_id}/speak")
    async def speak(platform: str, native_meeting_id: str, request: Request):
        return await _forward("POST", _meeting(f"/bots/{platform}/{native_meeting_id}/speak"), request)

    # P0 (cross-tenant leak fix): the by-ROW-id transcript read the terminal uses to fetch EXACTLY the
    # row it displays (owner-scoped downstream). Registered BEFORE the native route so `by-id` is not
    # matched as a {platform}. Forwarded verbatim; the auth/identity prep (X-User-Id) is shared.
    @app.get("/transcripts/by-id/{meeting_id}")
    async def transcript_by_id(meeting_id: int, request: Request):
        return await _forward("GET", _meeting(f"/transcripts/by-id/{meeting_id}"), request)

    # Redeem an INDEPENDENT transcript share token (Lane A / M0). Declared BEFORE the {platform}/{native}
    # GET so `share/accept` is not matched as a 2-segment transcript path.
    @app.post("/transcripts/share/accept")
    async def accept_transcript_share(request: Request):
        return await _forward("POST", _meeting("/transcripts/share/accept"), request)

    # native-keyed share MINT alias (#579 C3): the 0.10 api.v1 share path. The mint MOVED to
    # POST /meetings/{platform}/{native}/share in 0.12; alias the old transcripts path to it so a
    # 0.10 client no longer 404s. NOTE (signed): the 0.12 mint returns the capability-token share
    # shape ({id, token, mode, expires_at}), NOT the sealed TranscriptShareResponse public-URL shape
    # ({share_id, url, expires_at, expires_in_seconds}) — the public-URL-share backend is gone; only
    # the capability-token share exists. See the PR's "signed gaps" section.
    @app.post("/transcripts/{platform}/{native_meeting_id}/share")
    async def mint_transcript_share_alias(platform: str, native_meeting_id: str, request: Request):
        return await _forward("POST", _meeting(f"/meetings/{platform}/{native_meeting_id}/share"), request)

    @app.get("/transcripts/{platform}/{native_meeting_id}")
    async def transcript(platform: str, native_meeting_id: str, request: Request):
        return await _forward("GET", _meeting(f"/transcripts/{platform}/{native_meeting_id}"), request)

    @app.get("/recordings")
    async def list_recordings(request: Request):
        return await _forward("GET", _meeting("/recordings"), request)

    @app.get("/recordings/{recording_id}")
    async def get_recording(recording_id: int, request: Request):
        return await _forward("GET", _meeting(f"/recordings/{recording_id}"), request)

    # finalize-on-read master metadata (audio|video); the recording player fetches this, then the
    # raw_url it returns. ?type= is preserved by _forward.
    @app.get("/recordings/{recording_id}/master")
    async def get_recording_master(recording_id: int, request: Request):
        return await _forward("GET", _meeting(f"/recordings/{recording_id}/master"), request)

    # The master byte stream the recording player loads (the master metadata's raw_url points here).
    @app.get("/recordings/{recording_id}/media/{media_file_id}/raw")
    async def get_recording_media_raw(recording_id: int, media_file_id: int, request: Request):
        return await _forward(
            "GET", _meeting(f"/recordings/{recording_id}/media/{media_file_id}/raw"), request
        )

    # native download alias (#579 C3): the sealed api.v1 media-download path a 0.10 client calls.
    # 0.12 renamed the media byte route to .../raw (finalize-on-read master stream); alias .../download
    # to it so recording playback no longer 404s. Forwarded verbatim (Range headers preserved).
    @app.get("/recordings/{recording_id}/media/{media_file_id}/download")
    async def get_recording_media_download(recording_id: int, media_file_id: int, request: Request):
        return await _forward(
            "GET", _meeting(f"/recordings/{recording_id}/media/{media_file_id}/raw"), request
        )

    @app.get("/meetings")
    async def meetings(request: Request):
        return await _forward("GET", _meeting("/meetings"), request)

    # Create a PLANNED meeting (intent status, no bot) — the Meetings surface's "Plan a meeting".
    @app.post("/meetings", status_code=201)
    async def create_planned_meeting(request: Request):
        return await _forward("POST", _meeting("/meetings"), request)

    # Single meeting — forwards to meeting-api's GET /meetings/{id} (the meeting-detail page reads it).
    @app.get("/meetings/{meeting_id}")
    async def meeting(meeting_id: int, request: Request):
        return await _forward("GET", _meeting(f"/meetings/{meeting_id}"), request)

    # Edit / delete a PLANNED meeting by ROW id (owner-scoped; meeting-api refuses FSM rows with 409).
    @app.patch("/meetings/{meeting_id}")
    async def patch_planned_meeting(meeting_id: int, request: Request):
        return await _forward("PATCH", _meeting(f"/meetings/{meeting_id}"), request)

    @app.delete("/meetings/{meeting_id}", status_code=204)
    async def delete_planned_meeting(meeting_id: int, request: Request):
        return await _forward("DELETE", _meeting(f"/meetings/{meeting_id}"), request)

    # User-owned scheduling intent (schedule/cancel) — the Meetings surface's Schedule/Cancel action
    # PUTs here; forwards to meeting-api's PUT /meetings/{platform}/{native}/intent (owner-scoped).
    # Mint an INDEPENDENT transcript share link for a meeting (owner) — Lane A / M0.
    @app.post("/meetings/{platform}/{native_meeting_id}/share")
    async def mint_transcript_share(platform: str, native_meeting_id: str, request: Request):
        return await _forward("POST", _meeting(f"/meetings/{platform}/{native_meeting_id}/share"), request)

    # Bind a meeting to a shared workspace (owner) — Lane A (optional convenience).
    @app.post("/meetings/{platform}/{native_meeting_id}/workspace")
    async def bind_meeting_workspace(platform: str, native_meeting_id: str, request: Request):
        return await _forward("POST", _meeting(f"/meetings/{platform}/{native_meeting_id}/workspace"), request)

    @app.put("/meetings/{platform}/{native_meeting_id}/intent")
    async def set_meeting_intent(platform: str, native_meeting_id: str, request: Request):
        return await _forward(
            "PUT", _meeting(f"/meetings/{platform}/{native_meeting_id}/intent"), request
        )

    # native-keyed mutate (#579 C1): the sealed api.v1 PATCH/DELETE a 0.10 client (incl. the shipped
    # dashboard) calls by (platform, native_meeting_id). Thin passthrough — meeting-api resolves
    # (platform, native) → the caller's newest OWNED row and forwards to the same row-id handler
    # (unknown/unowned native → 404, FSM-owned row → 409). Additive: the by-ROW-id int routes above
    # are unchanged; these 2-segment paths never shadow them (FastAPI matches on segment count).
    @app.patch("/meetings/{platform}/{native_meeting_id}")
    async def patch_native_meeting(platform: str, native_meeting_id: str, request: Request):
        return await _forward("PATCH", _meeting(f"/meetings/{platform}/{native_meeting_id}"), request)

    @app.delete("/meetings/{platform}/{native_meeting_id}")
    async def delete_native_meeting(platform: str, native_meeting_id: str, request: Request):
        return await _forward("DELETE", _meeting(f"/meetings/{platform}/{native_meeting_id}"), request)

    # native-keyed chat READ (#579 C3): the sealed api.v1 GET the 0.10 dashboard's chat panel calls.
    # Thin passthrough to meeting-api's honest empty-list restore (0.12 does not persist in-meeting
    # chat server-side). The POST (send) half is a SIGNED GAP — no bot-command backend in 0.12.
    @app.get("/bots/{platform}/{native_meeting_id}/chat")
    async def read_meeting_chat(platform: str, native_meeting_id: str, request: Request):
        return await _forward("GET", _meeting(f"/bots/{platform}/{native_meeting_id}/chat"), request)

    # ---- user self-serve webhook config (main.py:1080 set_user_webhook_proxy) ----
    # Identity OWNS the config (user.data JSONB via admin-api); the gateway is the public edge for
    # it, exactly like the meeting routes: _forward resolves the key via /internal/validate (the
    # Authorizer), injects identity headers, and returns the downstream body + status verbatim.
    # No ROUTE_SCOPES entry — any valid key may manage its own webhook (parity with 0.10.6, which
    # gated it on api_key_scheme alone).
    def _admin(path: str) -> str:
        return f"{admin_api_url}{path}"

    @app.put("/user/webhook")
    async def set_user_webhook(request: Request):
        return await _forward("PUT", _admin("/user/webhook"), request)

    # Read-back for the self-serve config (admin-api masks the secret before it ships).
    @app.get("/user/webhook")
    async def get_user_webhook(request: Request):
        return await _forward("GET", _admin("/user/webhook"), request)

    # ---- user self-serve calendar-sync config (identity owns it, same shape as /user/webhook).
    # The ICS URL is a secret — admin-api masks it on every read-back. No ROUTE_SCOPES entry
    # (any valid key manages its own calendar), parity with the webhook self-serve. ----
    # calendar-sync feedback edges live in MEETING-api (they run the sync), unlike the config
    # (identity). Registered before the config routes only for reading clarity - paths are exact.
    @app.get("/user/calendar/sync")
    async def get_user_calendar_sync(request: Request):
        return await _forward("GET", _meeting("/user/calendar/sync"), request)

    @app.post("/user/calendar/sync")
    async def run_user_calendar_sync(request: Request):
        return await _forward("POST", _meeting("/user/calendar/sync"), request)

    @app.put("/user/calendar")
    async def set_user_calendar(request: Request):
        return await _forward("PUT", _admin("/user/calendar"), request)

    @app.get("/user/calendar")
    async def get_user_calendar(request: Request):
        return await _forward("GET", _admin("/user/calendar"), request)

    # ---- user self-serve model + transcription prefs (identity owns them, same shape as
    # /user/webhook: secrets masked by admin-api on every read-back, no ROUTE_SCOPES entry). ----
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

    # ---- the AGENT domain (P20·Stage 2): the gateway fronts agent-api under the canonical /agent/*
    # prefix so the SAME edge resolves key → user and injects X-User-Id; agent-api derives `subject`
    # from it (never the client). The terminal therefore talks ONLY to the gateway (one authenticated
    # edge, clean SoC). _agent() maps the public /agent/<path> to agent-api's internal /api/<path>.
    def _agent(path: str) -> str:
        return f"{agent_api_url}/api/{path}"

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

        async def body():
            async for chunk in downstream.stream(method, url, headers=headers, params=params, content=content):
                yield chunk

        return StreamingResponse(body(), media_type="text/event-stream", headers=SSE_HEADERS)

    # The agent domain lives under the canonical /agent/* prefix (peer to the meetings domain). The SSE
    # routes (chat turn · live meeting feed) are STREAMED and declared BEFORE the catch-all so they win;
    # everything else (sessions · history · routines · workspace tree/file/git/upload · models) is
    # request/response JSON → the buffered _forward, with X-User-Id injected. All carry the path/method/
    # query/body verbatim to agent-api's matching /api/<path> via _agent().
    @app.post("/agent/chat")
    async def agent_chat(request: Request):
        return await _forward_stream("POST", _agent("chat"), request)

    @app.get("/agent/meeting/stream")
    async def agent_meeting_stream(request: Request):
        return await _forward_stream("GET", _agent("meeting/stream"), request)

    @app.api_route("/agent/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def agent_proxy(path: str, request: Request):
        return await _forward(request.method, _agent(path), request)

    # ---- the /ws multiplex (carve of main.websocket_multiplex, main.py:2165-2340) ----
    @app.websocket("/ws")
    async def websocket_multiplex(ws: WebSocket):
        await run_multiplex(ws, authorizer, redis)

    return app


async def run_multiplex(ws: WebSocket, authorizer: Authorizer, redis: RedisBus) -> None:
    """The ``/ws`` control loop + fan-in, carved verbatim from main.websocket_multiplex.

    PUBLIC (P2 follow-up): the conformance ws-harness drives this directly to exercise the SHIPPED
    multiplex against its fakes — exposed on the front door (``gateway.run_multiplex``) so the
    harness no longer reaches for a private (the P1-flagged smell).

    guard (PRE-accept, opt-in) → accept → authenticate (missing key → error + close 4401) →
      loop over client frames:
      subscribe   → authorize, register a redis fan-in per meeting, ack ``subscribed``;
      unsubscribe → cancel the fan-in task(s), ack ``unsubscribed`` (stops forwarding);
      ping        → ``pong``;
      otherwise   → an ``error`` frame (invalid_json / unknown_action / invalid_*_payload).
    Each subscription fans in ``tc:meeting:{id}:mutable`` / ``bm:meeting:{id}:status`` /
    ``va:meeting:{id}:chat`` and forwards every raw payload to the socket (main.py:2204).
    """
    # --- optional WS guard hook (GUARD_WS_ENABLED, default false) ---
    # HTTP SecurityMiddleware does not intercept /ws (Starlette middleware is HTTP-only).
    # When the toggle is on, resolve the client IP via the same trusted-proxies XFF logic
    # as guard's HTTP path and deny over-limit/banned IPs at connect. Opt-in: the default
    # (false) leaves the WS path unchanged so the conformance harness observes zero change.
    #
    # PRE-ACCEPT: the full guard check (whitelist/blacklist/ban/rate-limit) runs BEFORE
    # ``ws.accept()`` so a banned IP never gets a WebSocket upgrade. On denial, close with
    # 4401 BEFORE accept — Starlette forwards the pre-accept ``websocket.close`` unchanged
    # (its state machine accepts ``websocket.close`` while CONNECTING); uvicorn (0.51 here)
    # turns that into an HTTP 403 to the upgrade request (no upgrade, no frames). A data
    # frame (send_text) cannot be sent before accept, so the rejection is the close alone —
    # the client sees the 403, not an ip_blocked JSON frame.
    from .ratelimit import env_truthy

    if env_truthy(os.getenv("GUARD_WS_ENABLED")):
        from .edge_guard import ws_guard_check

        if not ws_guard_check(ws):
            await ws.close(code=4401)  # pre-accept reject → HTTP 403 to the upgrade
            return

    await ws.accept()

    api_key = ws.headers.get("x-api-key") or ws.query_params.get("api_key")
    if not api_key:
        try:
            await ws.send_text(json.dumps({"type": "error", "error": "missing_api_key"}))
        finally:
            await ws.close(code=4401)  # Unauthorized
        return

    # Connect-time identity resolve (Track G — meeting-status-ws §C.2). Today connect only checked
    # the key was PRESENT and resolved user_id per-subscribe; now we resolve the key to a user up
    # front (the SAME resolver /auth/me + the proxy use — ports.py resolve / app.py:96-99,119-125)
    # so we can auto-subscribe the socket to its USER-SCOPED channel. Fail-closed like the proxy:
    # a present-but-invalid key → invalid_api_key + close 4401, not a silently half-open socket.
    try:
        user_data = await authorizer.resolve(api_key)
    except AuthUnavailable as e:
        # #495: resolve() now RAISES when the validation hop is unreachable/faulted. On the REST
        # surface that becomes a 503; on this already-accepted socket the truthful equivalent is a
        # typed error frame + a distinct retryable close code (4503 ≈ HTTP 503), NOT 4401 (which
        # asserts the key is bad) and NOT an uncaught raise (which drops the socket 1006/1011 with
        # no signal). A valid key must not be told it is invalid because our auth path is down.
        log_event("auth_infra_unavailable", audience="system", level="error", span="ws",
                  fields={"reason": type(e).__name__, "detail": str(e)})
        try:
            await ws.send_text(json.dumps({"type": "error", "error": "auth_unavailable"}))
        finally:
            await ws.close(code=4503)  # retry later — auth infrastructure unavailable
        return
    if not user_data:
        try:
            await ws.send_text(json.dumps({"type": "error", "error": "invalid_api_key"}))
        finally:
            await ws.close(code=4401)  # Unauthorized
        return
    user_id = user_data["user_id"]
    set_user_id(user_id)

    sub_tasks: Dict[Tuple, asyncio.Task] = {}
    subscribed_meetings: Set[Tuple] = set()

    async def fan_in(channels: List[str]):
        pubsub = redis.pubsub()
        await pubsub.subscribe(*channels)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                try:
                    await ws.send_text(data)  # forward the raw redis payload (main.py:2204)
                except Exception:
                    break
        finally:
            try:
                await pubsub.unsubscribe(*channels)
                await pubsub.close()
            except Exception:
                pass

    async def subscribe_meeting(platform: str, native_id: str, user_id, meeting_id):
        key = (platform, native_id, user_id)
        if key in subscribed_meetings:
            return
        subscribed_meetings.add(key)
        channels = [
            f"tc:meeting:{meeting_id}:mutable",
            f"bm:meeting:{meeting_id}:status",
            f"va:meeting:{meeting_id}:chat",
        ]
        sub_tasks[key] = asyncio.create_task(fan_in(channels))

    async def unsubscribe_meeting(platform: str, native_id: str, user_id):
        key = (platform, native_id, user_id)
        task = sub_tasks.pop(key, None)
        if task:
            task.cancel()
        subscribed_meetings.discard(key)

    # Auto-subscribe the authed socket to its USER scope (Track G — meeting-status-ws §C.2). The
    # user-scoped redis channel `u:{user_id}:meetings` carries every meeting.status frame for this
    # user (the publisher mirrors each bm:meeting:{id}:status onto it — §C.3). No client `subscribe`
    # frame is needed: the identity is resolved at connect. This reuses the SAME verbatim `fan_in`
    # path as the per-meeting channels — the gateway is a thin raw forwarder for the user channel
    # exactly as it is for tc:/bm:/va:. Per-meeting subscriptions below are unchanged.
    user_channel = f"u:{user_id}:meetings"
    user_sub_task = asyncio.create_task(fan_in([user_channel]))

    try:
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                break

            try:
                msg = json.loads(raw)
            except Exception:
                await ws.send_text(json.dumps({"type": "error", "error": "invalid_json"}))
                continue
            # Syntactically-valid but NON-OBJECT JSON ([1,2,3], 42, "x", null): guard before `.get()`,
            # else AttributeError escapes run_multiplex and KILLS the socket — a trivial public-edge DoS.
            if not isinstance(msg, dict):
                await ws.send_text(json.dumps({"type": "error", "error": "invalid_json"}))
                continue

            action = msg.get("action")
            if action == "subscribe":
                meetings = msg.get("meetings", None)
                if not isinstance(meetings, list):
                    await ws.send_text(json.dumps({
                        "type": "error", "error": "invalid_subscribe_payload",
                        "details": "'meetings' must be a non-empty list"}))
                    continue
                if len(meetings) == 0:
                    await ws.send_text(json.dumps({
                        "type": "error", "error": "invalid_subscribe_payload",
                        "details": "'meetings' list cannot be empty"}))
                    continue
                payload_meetings = []
                for m in meetings:
                    if isinstance(m, dict):
                        plat = str(m.get("platform", "")).strip()
                        nid = str(m.get("native_id", "")).strip()
                        if plat and nid:
                            payload_meetings.append({"platform": plat, "native_meeting_id": nid})
                if not payload_meetings:
                    await ws.send_text(json.dumps({
                        "type": "error", "error": "invalid_subscribe_payload",
                        "details": "no valid meeting objects"}))
                    continue

                # The downstream authorize hop must never crash the socket: a RAISE → authorization_call_failed
                # frame + continue; a non-200 (errors carried, nothing authorized) → authorization_service_error
                # frame, NOT a misleading empty `subscribed` ack that hides the auth backend being down.
                try:
                    result = await authorizer.authorize_subscribe(api_key, payload_meetings)
                except Exception as e:  # noqa: BLE001 — surface as a protocol error, keep the socket alive
                    await ws.send_text(json.dumps({
                        "type": "error", "error": "authorization_call_failed", "details": str(e)}))
                    continue
                authorized = result.get("authorized") or []
                auth_errors = result.get("errors") or []
                if not authorized and auth_errors:
                    first = str(auth_errors[0])
                    code = ("authorization_call_failed"
                            if first.startswith("authorization_call_failed")
                            else "authorization_service_error")
                    await ws.send_text(json.dumps({"type": "error", "error": code, "details": first}))
                    continue
                subscribed: List[Dict[str, str]] = []
                for item in authorized:
                    plat = item.get("platform"); nid = item.get("native_id")
                    user_id = item.get("user_id"); meeting_id = item.get("meeting_id")
                    if plat and nid and user_id and meeting_id:
                        await subscribe_meeting(plat, nid, user_id, meeting_id)
                        subscribed.append({"platform": plat, "native_id": nid})
                await ws.send_text(json.dumps({"type": "subscribed", "meetings": subscribed}))

            elif action == "unsubscribe":
                meetings = msg.get("meetings", None)
                if not isinstance(meetings, list):
                    await ws.send_text(json.dumps({
                        "type": "error", "error": "invalid_unsubscribe_payload",
                        "details": "'meetings' must be a list"}))
                    continue
                unsubscribed: List[Dict[str, str]] = []
                errors: List[str] = []
                for idx, m in enumerate(meetings):
                    if not isinstance(m, dict):
                        errors.append(f"meetings[{idx}] must be an object")
                        continue
                    plat = str(m.get("platform", "")).strip()
                    nid = str(m.get("native_id", "")).strip()
                    if not plat or not nid:
                        errors.append(f"meetings[{idx}] missing 'platform' or 'native_id'")
                        continue
                    matching_key = None
                    for key in subscribed_meetings:
                        if key[0] == plat and key[1] == nid:
                            matching_key = key
                            break
                    if matching_key:
                        await unsubscribe_meeting(plat, nid, matching_key[2])
                        unsubscribed.append({"platform": plat, "native_id": nid})
                    else:
                        errors.append(f"meetings[{idx}] not currently subscribed")
                if errors and not unsubscribed:
                    await ws.send_text(json.dumps({
                        "type": "error", "error": "invalid_unsubscribe_payload", "details": errors}))
                    continue
                await ws.send_text(json.dumps({"type": "unsubscribed", "meetings": unsubscribed}))

            elif action == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
            else:
                await ws.send_text(json.dumps({"type": "error", "error": "unknown_action"}))
    except WebSocketDisconnect:
        pass
    finally:
        user_sub_task.cancel()  # Track G — tear down the user-scope fan-in on disconnect.
        for task in sub_tasks.values():
            task.cancel()


# Backward-compatible private alias (kept so any existing internal reference still resolves; the
# public name ``run_multiplex`` is the front door the conformance harness now imports).
_run_multiplex = run_multiplex

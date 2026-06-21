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
from typing import Dict, List, Optional, Set, Tuple

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect

from .obs import TRACE_HEADER, TraceMiddleware, get_trace_id, log_event, set_user_id
from .ports import Authorizer, DownstreamClient, RedisBus

# Route-prefix → required scope set. Mirrors main.py ROUTE_SCOPES (main.py:59-65) for the CORE
# surface the gateway lane carves; multi-scope tokens pass for any of their domains.
ROUTE_SCOPES: Dict[str, Set[str]] = {
    "/bots": {"bot", "browser"},
    "/transcripts": {"tx"},
    "/meetings": {"tx"},
    "/recordings": {"tx", "bot"},
}

# Default sentinel base URLs. The DownstreamClient (real httpx or the fake ASGI transport)
# resolves them; what matters is the PATH the gateway forwards to (verbatim from the route).
_DEFAULT_MEETING_API_URL = "http://meeting-api"
_DEFAULT_TRANSCRIPTION_COLLECTOR_URL = "http://transcription-collector"


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
    transcription_collector_url: str = _DEFAULT_TRANSCRIPTION_COLLECTOR_URL,
) -> FastAPI:
    """Build the gateway FastAPI app over the injected ports.

    ``authorizer``  — resolves ``x-api-key`` → user/scopes (admin-api ``/internal/validate``).
    ``downstream``  — forwards proxied HTTP requests to meeting-api / transcription-collector.
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

    # --- the REST proxy: faithful carve of main.forward_request for client (non-admin) routes.
    async def _forward(method: str, url: str, request: Request) -> Response:
        client_key = request.headers.get("x-api-key")
        # Fail-closed: a client route with no key is rejected before any downstream call.
        if not client_key:
            return Response(
                content=json.dumps({"detail": "Missing API key"}),
                status_code=401,
                media_type="application/json",
            )

        user_data = await authorizer.resolve(client_key)
        if not user_data:
            return Response(
                content=json.dumps({"detail": "Invalid API key"}),
                status_code=401,
                media_type="application/json",
            )

        # Bind the resolved user to the trace context so every later line carries user_id.
        user_id = user_data["user_id"]
        set_user_id(user_id)

        # Scope enforcement (main.py:341-351).
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
                return Response(
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
        for h in ("x-user-id", "x-user-scopes", "x-user-limits"):
            headers.pop(h, None)
        headers["x-api-key"] = client_key
        headers["x-user-id"] = str(user_id)
        headers["x-user-scopes"] = ",".join(user_data.get("scopes", []))
        headers["x-user-limits"] = str(user_data.get("max_concurrent", 1))
        headers[TRACE_HEADER] = get_trace_id() or ""

        content = await request.body()
        resp = await downstream.request(
            method,
            url,
            headers=headers,
            params=dict(request.query_params) or None,
            content=content,
        )

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
        return Response(content=resp.content, status_code=resp.status_code, media_type=media_type)

    def _meeting(path: str) -> str:
        return f"{meeting_api_url}{path}"

    def _tc(path: str) -> str:
        return f"{transcription_collector_url}{path}"

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

    @app.get("/transcripts/{platform}/{native_meeting_id}")
    async def transcript(platform: str, native_meeting_id: str, request: Request):
        return await _forward("GET", _tc(f"/transcripts/{platform}/{native_meeting_id}"), request)

    @app.get("/recordings")
    async def list_recordings(request: Request):
        return await _forward("GET", _meeting("/recordings"), request)

    @app.get("/recordings/{recording_id}")
    async def get_recording(recording_id: int, request: Request):
        return await _forward("GET", _meeting(f"/recordings/{recording_id}"), request)

    @app.get("/meetings")
    async def meetings(request: Request):
        return await _forward("GET", _tc("/meetings"), request)

    # ---- the /ws multiplex (carve of main.websocket_multiplex, main.py:2165-2340) ----
    @app.websocket("/ws")
    async def websocket_multiplex(ws: WebSocket):
        await _run_multiplex(ws, authorizer, redis)

    return app


async def _run_multiplex(ws: WebSocket, authorizer: Authorizer, redis: RedisBus) -> None:
    """The ``/ws`` control loop + fan-in, carved verbatim from main.websocket_multiplex.

    accept → authenticate (missing key → error + close 4401) → loop over client frames:
      subscribe   → authorize, register a redis fan-in per meeting, ack ``subscribed``;
      unsubscribe → cancel the fan-in task(s), ack ``unsubscribed`` (stops forwarding);
      ping        → ``pong``;
      otherwise   → an ``error`` frame (invalid_json / unknown_action / invalid_*_payload).
    Each subscription fans in ``tc:meeting:{id}:mutable`` / ``bm:meeting:{id}:status`` /
    ``va:meeting:{id}:chat`` and forwards every raw payload to the socket (main.py:2204).
    """
    await ws.accept()
    api_key = ws.headers.get("x-api-key") or ws.query_params.get("api_key")
    if not api_key:
        try:
            await ws.send_text(json.dumps({"type": "error", "error": "missing_api_key"}))
        finally:
            await ws.close(code=4401)  # Unauthorized
        return

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

                result = await authorizer.authorize_subscribe(api_key, payload_meetings)
                authorized = result.get("authorized") or []
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
        for task in sub_tasks.values():
            task.cancel()

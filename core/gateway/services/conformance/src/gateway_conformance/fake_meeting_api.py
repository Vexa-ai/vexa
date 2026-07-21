"""The downstream the gateway proxies to — drives the REAL, UNIFIED meeting-api + a faked /bots.

The real gateway (`services/api-gateway/main.py`) is a thin proxy: it authenticates `x-api-key`
then forwards to `MEETING_API_URL` and returns the downstream body verbatim. v0.12 P2 folded the
transcription-collector INTO meeting-api (one modular monolith), so there is now ONE downstream.
This harness stands up ONE in-process FastAPI app the gateway-under-test talks to over an ASGI
transport (no sockets), built in two halves:

  * **`/transcripts/{platform}/{native_meeting_id}` + `/meetings` (+ the `/ws/authorize-subscribe`
    hop the ws_harness uses)** are served by the REAL, SHIPPED, UNIFIED meeting-api app
    (`meeting_api.create_app`) — its folded-in collector module, injected with an in-memory store
    seeded to the api.v1 goldens. Those conformance assertions therefore drive shipped meeting-api
    code, one hop downstream of the gateway carve. meeting-api imports nothing from here.
  * **`/bots*`** stay FAKED (golden replay): meeting-api's `/bots` flow IS carved into v0.12
    (`meeting_api.bot_spawn`), but the conformance asserts the FROZEN api.v1 `MeetingResponse`
    goldens (and needs no ADMIN_TOKEN / runtime kernel), so `/bots*` remains a golden port-fake —
    explicitly noted, not an oversight. The `bot_spawn` flow is covered by meeting-api's own tests.
  * **`/internal/validate`** (the admin-api token endpoint the gateway calls) and **`/recordings`**
    (proxied to meeting-api; no sealed api.v1 component) are faked here too.

The collector reads the caller from the `x-user-id` header the gateway injects after it resolves
`x-api-key` — so the seeded store's `user_id` must match the fake admin-api's `VALID_USER`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response

from meeting_api import create_app as create_meeting_api_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

from .contracts import CONTRACTS_DIR
from .downstream_obs import TraceMiddleware as DownstreamTraceMiddleware
from .downstream_obs import log_event as downstream_log_event

_GOLDEN_DIR = CONTRACTS_DIR / "api.v1" / "golden"

# A valid token the fake admin-api will accept (mirrors a vxa_bot_/vxa_tx_ scoped token).
# Granting BOTH scopes so a single key passes the gateway's scope gate on every CORE route
# (/bots needs bot|browser, /transcripts|/meetings need tx).
VALID_API_KEY = "vxa_test_conformance_key"
VALID_USER = {
    "user_id": 7,
    "scopes": ["bot", "tx", "browser"],
    "max_concurrent": 3,
    "max_concurrent_bots": 3,
}

# The meeting the api.v1 goldens describe (TranscriptionResponse / MeetingListResponse): owned by
# VALID_USER (user_id 7), google_meet / abc-defg-hij. The collector serves /transcripts + /meetings
# from this store, so the proxied bodies conform to the sealed components BY their own production
# code path (not a golden file replay).
GOLDEN_MEETING = {
    "user_id": VALID_USER["user_id"],
    "platform": "google_meet",
    "native_meeting_id": "abc-defg-hij",
    "meeting_id": 42,
    "status": "active",
    "constructed_meeting_url": "https://meet.google.com/abc-defg-hij",
    "bot_container_id": "mtg-abc-defg-hij-bot",
    "start_time": "2026-06-20T09:00:00Z",
    "end_time": None,
    "created_at": "2026-06-20T08:59:00Z",
    "updated_at": "2026-06-20T09:00:05Z",
}
GOLDEN_SEGMENTS = [
    {"segment_id": "ch-0:1:a", "start": 1.0, "end": 2.5, "text": "This is Anna.",
     "language": "en", "speaker": "spk-Anna", "completed": True},
]


def _golden(name: str) -> Any:
    return json.loads((_GOLDEN_DIR / name).read_text(encoding="utf-8"))


def build_collector_store() -> InMemoryTranscriptStore:
    """The in-memory store the REAL collector serves /transcripts + /meetings + the
    authorize-subscribe hop from — seeded to the api.v1 golden meeting (owned by VALID_USER)."""
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        user_id=GOLDEN_MEETING["user_id"],
        platform=GOLDEN_MEETING["platform"],
        native_meeting_id=GOLDEN_MEETING["native_meeting_id"],
        meeting_id=GOLDEN_MEETING["meeting_id"],
        status=GOLDEN_MEETING["status"],
        constructed_meeting_url=GOLDEN_MEETING["constructed_meeting_url"],
        bot_container_id=GOLDEN_MEETING["bot_container_id"],
        start_time=GOLDEN_MEETING["start_time"],
        end_time=GOLDEN_MEETING["end_time"],
        created_at=GOLDEN_MEETING["created_at"],
        updated_at=GOLDEN_MEETING["updated_at"],
        segments=list(GOLDEN_SEGMENTS),
    )
    return store


def build_fake_downstream() -> FastAPI:
    """The downstream app the gateway forwards to: the REAL, UNIFIED meeting-api (`/transcripts`,
    `/meetings`, `/ws/authorize-subscribe`) PLUS faked `/bots*` + `/recordings` and the admin-api
    `/internal/validate`. One app, in-process, no sockets.

    Shape: a parent ``FastAPI`` carries the faked routes + admin-api; the meeting-api routes are
    PROXIED in-process to the SHIPPED ``meeting_api.create_app`` (its folded-in collector module)
    over an httpx ASGI transport — so the gateway's forward to ``/transcripts`` / ``/meetings``
    reaches the REAL unified meeting-api's production code, while ``/bots*`` stay golden fakes."""
    app = FastAPI(title="downstream (REAL unified meeting-api + faked /bots)")
    # This hop's trace middleware/emitter (service=meeting-api) binds the SAME contextvars, so the
    # faked /bots lines correlate on the gateway's forwarded trace_id.
    app.add_middleware(DownstreamTraceMiddleware)

    # Build the SHIPPED, UNIFIED meeting-api app seeded to the api.v1 golden meeting. Its own
    # TraceMiddleware reads the forwarded X-Trace-Id and binds it for the meeting-api hop — the
    # cross-process X-Trace-Id propagation, modelled in one process. The proxy below forwards the
    # X-Trace-Id header so the meeting-api hop joins the same trace.
    meeting_api_app = create_meeting_api_app(transcript_store=build_collector_store())
    meeting_api_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meeting_api_app),
        base_url="http://meeting-api",
    )

    async def _proxy_to_meeting_api(request: Request, path: str) -> Response:
        """Forward a request verbatim to the REAL unified meeting-api app (in-process), returning
        its response body + status unchanged — the gateway already injected x-user-id + x-trace-id."""
        upstream = await meeting_api_client.request(
            request.method,
            path,
            params=dict(request.query_params) or None,
            headers={k: v for k, v in request.headers.items()
                     if k.lower() not in ("host", "content-length")},
            content=await request.body(),
        )
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    # --- meeting-api collector module (REAL, SHIPPED, UNIFIED) — proxied in-process ---
    @app.get("/transcripts/{platform}/{native_meeting_id}")
    async def transcript(platform: str, native_meeting_id: str, request: Request):
        return await _proxy_to_meeting_api(request, f"/transcripts/{platform}/{native_meeting_id}")

    @app.get("/meetings")
    async def meetings(request: Request):
        return await _proxy_to_meeting_api(request, "/meetings")

    @app.post("/ws/authorize-subscribe")
    async def ws_authorize_subscribe(request: Request):
        return await _proxy_to_meeting_api(request, "/ws/authorize-subscribe")

    # --- admin-api token validation (gateway calls this to resolve x-api-key) ---
    @app.post("/internal/validate")
    async def internal_validate(request: Request):
        body = await request.json()
        if body.get("token") == VALID_API_KEY:
            return VALID_USER
        raise HTTPException(status_code=401, detail="invalid token")

    # --- meeting-api: bots (FAKED — meeting-api's /bots serving is not carved yet) ---
    @app.get("/bots")
    async def list_bots():
        # GET /bots → list of meetings (MeetingListResponse-shaped: {"meetings": [...]}).
        return _golden("MeetingListResponse.example.json")

    @app.post("/bots", status_code=201)
    async def create_bot():
        # POST /bots → the created meeting (MeetingResponse). USER-facing: a bot was created
        # for this user — emitted with the trace_id forwarded from the gateway hop.
        downstream_log_event(
            "bot_join_requested",
            audience="user",
            span="bots.create",
            meeting_id="google_meet/abc-defg-hij",
            fields={"platform": "google_meet", "status": "requested"},
        )
        return _golden("MeetingResponse.example.json")

    @app.get("/bots/status")
    async def bots_status():
        return _golden("BotStatusResponse.example.json")

    @app.delete("/bots/{platform}/{native_meeting_id}")
    async def stop_bot(platform: str, native_meeting_id: str):
        # DELETE → the stopped meeting (MeetingResponse).
        return _golden("MeetingResponse.example.json")

    @app.put("/bots/{platform}/{native_meeting_id}/config", status_code=202)
    async def update_config(platform: str, native_meeting_id: str):
        return _golden("MeetingResponse.example.json")

    @app.post("/bots/{platform}/{native_meeting_id}/speak")
    async def speak(platform: str, native_meeting_id: str):
        # The real bot-manager returns the meeting it acted on.
        return _golden("MeetingResponse.example.json")

    @app.post("/meetings/{meeting_id}/transcribe")
    async def transcribe_meeting(meeting_id: int):
        # Deferred transcription from the recording (#525) — the module's honest result shape
        # (the sealed 200 schema is unconstrained).
        return {"meeting_id": meeting_id, "segments_stored": 0, "language": None}

    # --- recordings (gateway forwards /recordings to meeting-api; no sealed api.v1 component) ---
    @app.get("/recordings")
    async def list_recordings():
        return {"recordings": []}

    @app.get("/recordings/{recording_id}")
    async def get_recording(recording_id: int):
        return {"id": recording_id, "media_files": []}

    return app

"""routers/health.py — Liveness and build identity — the two answers that must work when nothing else does.

Extracted from `api.py`'s `create_app` VERBATIM: the handler bodies below are the same
bytes, with `@app.` rewritten to `@router.` and nothing else. Everything they close over
is handed in by `build()` and rebound to the name it already had, so no body needed a
single identifier changed.
"""
from __future__ import annotations

import json
import pathlib

from control_plane import version as version_mod
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

#: ADR-0037 / PRD decision 40.5 — the agent domain's MCP tool manifest, SERVED not baked into the
#: assembler (mirrors core/flows/src/flows_integrations/flows_api.py's own `/.well-known/mcp-tools.json`
#: route byte-for-byte): the version that answers is the version this build actually runs. Committed
#: at `core/agent/mcp.tools.v1.json`; `parents[2]` from this file (routers/ -> control_plane/ ->
#: core/agent/) lands on it, same distance flows-api's route sits from its own manifest.
_MANIFEST_PATH = pathlib.Path(__file__).resolve().parents[2] / "mcp.tools.v1.json"


def build(**d) -> APIRouter:
    """The health routes, bound to one app's dependencies."""
    router = APIRouter()
    dispatcher = d['dispatcher']

    @router.get("/health")
    def health():
        ok = dispatcher is not None
        # ADDITIVE config.v1 rows (ADR-0026): the agent plane's capability tri-states (bot_gateway ·
        # model_inference). They never affect `status`/`checks` or the status code — an unconfigured
        # capability degrades a FEATURE (e.g. 'add bot from URL', worker model credentials), not the
        # process; the runtime's /health carries the credentials-file probe for the mount mechanics.
        from control_plane.config_preflight import capability_health

        return JSONResponse(
            {"status": "ok" if ok else "degraded", "service": "agent-api", "checks": {"dispatcher": ok},
             "capabilities": capability_health()},
            status_code=200 if ok else 503,
        )
    @router.get("/api/version")
    def version():
        """What is serving — unauthenticated, cheap, and polled (PRD decision 39).

        No identity gate on purpose: the blue/green swap probes this from the HOST before any
        traffic is switched onto the container, and an open browser tab polls it to notice that
        the service underneath it moved. Both are outside a session. What it discloses is a build
        stamp and a contract integer — the same facts the running image announces in its tag.

        `api` is the pairing number the swap enforces (F55/F77): a terminal image whose baked
        `ai.vexa.terminal.agent_api` label is not this number is REFUSED before it can be put in
        front of a person, because that is the failure where the client leads the server and every
        click 422s."""
        return version_mod.version_payload()
    @router.get("/.well-known/mcp-tools.json")
    def mcp_tools_manifest():
        """This domain's MCP tool manifest — what the assembled edge (core/meetings/services/mcp)
        discovers and binds against agent-api's own OpenAPI. OPEN, like `/health`: it names routes
        and argument names, never data and never a credential."""
        try:
            return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503,
                                detail=f"this build carries no tool manifest: {e}") from e

    return router

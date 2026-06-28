# syntax=docker/dockerfile:1
# agent-api (v0.12) — the unit control plane's HTTP front door: POST /invocations (the dispatcher
# sink → a runtime.v1 agent spawn), POST /api/chat (SSE), /api/sessions, /health. The unit WORKER
# itself is a separate runtime.v1 `agent` workload (claude-in-container) — this image is the control
# plane only (no claude here).
#
# Build context: the v0.12 carve root (compose `context: ../..`) — wider than its own dir because
# agent_api validates AT THE SEAM (P8): contracts.py loads the sealed transcript.v1 / workspace.v1 /
# invoke.v1 / unit.v1 schemas BY PATH at import (walking up to find `meetings/contracts/...` and
# `agent/contracts/...`), so those frozen files must be vendored into the image alongside src/.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.9.22

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY core/agent/pyproject.toml core/agent/uv.lock ./
# Control-plane image installs ONLY base + the `control-plane` dep group (fastapi/uvicorn/multipart);
# NOT the worker deps, NOT dev/test (D1 — real per-image trimming).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-default-groups --group control-plane

# git only (the control plane reads workspace git state via workspace_reader). NO claude-code / node here:
# agents never run in the control plane — the claude turn runs in the separate `vexa-agent` WORKER image
# (core/agent/worker/Dockerfile). This is the payoff of the control-plane ⊥ worker split (a lean image).
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# The control-plane packages (NO worker): control_plane + shared + contracts, copied to /app so
# `PYTHONPATH=/app` resolves them as top-level imports.
COPY core/agent/shared ./shared
COPY core/agent/control_plane ./control_plane
COPY core/agent/contracts ./contracts
# The registry of workspace templates (the control plane validates/seeds from one — Phase 6).
# workspace-seeds/<name>/ — one validated seed per subdir (today: default).
COPY core/agent/workspace-seeds ./workspace-seeds

# The sealed contracts loaded BY PATH at import. In-image layout placed under /app/ so the module
# walk-up (Path(__file__).resolve().parents → /app/contracts/loader.py → /app) resolves
# `meetings/contracts/...` and `agent/contracts/...` (repo_root == /app).
COPY core/meetings/contracts/transcript.v1/transcript.schema.json  ./meetings/contracts/transcript.v1/transcript.schema.json
COPY core/agent/contracts/workspace.v1/workspace.schema.json       ./agent/contracts/workspace.v1/workspace.schema.json
COPY core/agent/contracts/invoke.v1/invoke.schema.json             ./agent/contracts/invoke.v1/invoke.schema.json
COPY core/agent/contracts/unit.v1/unit.schema.json                 ./agent/contracts/unit.v1/unit.schema.json
COPY core/agent/contracts/routine.v1/routine.schema.json           ./agent/contracts/routine.v1/routine.schema.json
COPY core/agent/contracts/event.v1/event.schema.json               ./agent/contracts/event.v1/event.schema.json
COPY core/agent/contracts/tool.v1/tool.schema.json                 ./agent/contracts/tool.v1/tool.schema.json

EXPOSE 8100
# uvicorn resolves the lazy PEP-562 `app` (control_plane.api:app), which wires the real RuntimeHttpClient
# + Dispatcher at startup.
CMD ["uvicorn", "control_plane.api:app", "--host", "0.0.0.0", "--port", "8100"]

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
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY core/agent/services/agent-api/pyproject.toml core/agent/services/agent-api/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# Production ASGI server (fastapi is already pinned in pyproject).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "uvicorn[standard]==0.34.0"

# Claude Code (the unit's model+tooling runtime) + git (the governance git ops). node 20 via nodesource
# so claude-code resolves; the subscription credentials are bind-mounted read-only at runtime (compose),
# never baked in (P15). MVP0 runs the turn in this container; per-person container isolation is MVP1.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g @anthropic-ai/claude-code && \
    apt-get purge -y curl gnupg && apt-get autoremove -y && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY core/agent/services/agent-api/src ./src
# The per-person workspace template (CLAUDE.md + conventions) the chat runner seeds new workspaces from.
COPY core/agent/services/agent-api/workspace-seed ./workspace-seed
# The unit toolbelt registry (tool.v1 + MCP launch specs) the ToolRegistry loads — the generic tool mechanism.
COPY core/agent/services/agent-api/tools-seed ./tools-seed

# The sealed contracts loaded BY PATH at import. In-image layout placed under /app/ so the module
# walk-up (Path(__file__).resolve().parents → .../src/agent_api/... → /app) resolves
# `meetings/contracts/...` and `agent/contracts/...` (repo_root == /app).
COPY core/meetings/contracts/transcript.v1/transcript.schema.json  ./meetings/contracts/transcript.v1/transcript.schema.json
COPY core/agent/contracts/workspace.v1/workspace.schema.json       ./agent/contracts/workspace.v1/workspace.schema.json
COPY core/agent/contracts/invoke.v1/invoke.schema.json             ./agent/contracts/invoke.v1/invoke.schema.json
COPY core/agent/contracts/unit.v1/unit.schema.json                 ./agent/contracts/unit.v1/unit.schema.json
COPY core/agent/contracts/routine.v1/routine.schema.json           ./agent/contracts/routine.v1/routine.schema.json
COPY core/agent/contracts/event.v1/event.schema.json               ./agent/contracts/event.v1/event.schema.json
COPY core/agent/contracts/tool.v1/tool.schema.json                 ./agent/contracts/tool.v1/tool.schema.json

EXPOSE 8100
# uvicorn resolves the lazy PEP-562 `app` (agent_api.api:app), which wires the real RuntimeHttpClient
# + Dispatcher at startup.
CMD ["uvicorn", "agent_api.api:app", "--host", "0.0.0.0", "--port", "8100"]

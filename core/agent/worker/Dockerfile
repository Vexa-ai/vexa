# syntax=docker/dockerfile:1
# vexa-agent WORKER (v0.12) — the per-dispatch claude-in-container harness (`python -m worker`), spawned
# by the runtime.v1 `agent` profile. Distinct from the agent-api CONTROL PLANE image: the worker runs the
# governed claude turn over the mounted workspace and XADDs UnitEvents to unit:<id>:out. It needs
# claude-code + node + git; it does NOT ship the control_plane package.
#
# Build context: the v0.12 carve root (compose `context: ../..`) — wider than its own dir because the
# sealed contracts (transcript.v1 / workspace.v1 / unit.v1 / …) are loaded BY PATH at import; those frozen
# files are vendored under /app so the walk-up resolves repo_root == /app.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.9.22

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY core/agent/pyproject.toml core/agent/uv.lock ./
# Worker image installs ONLY base (pydantic/jsonschema/PyYAML/redis) — NO fastapi/uvicorn, NO dev/test
# (D1). claude-code is an npm global installed below, not a pip dep.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-default-groups

# Claude Code (the unit's model+tooling runtime) + git (the governance git ops). node 20 via nodesource
# so claude-code resolves; the subscription credentials are bind-mounted read-only at runtime (P15).
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g @anthropic-ai/claude-code && \
    apt-get purge -y curl gnupg && apt-get autoremove -y && apt-get clean && rm -rf /var/lib/apt/lists/*

# The worker's own packages (NO control_plane): worker + llm + shared + contracts, copied to /app so
# `PYTHONPATH=/app` resolves them as top-level imports. llm/ is the provider-agnostic ports module
# (completion adapters + the claude-code harness adapter the npm install above serves).
COPY core/agent/worker ./worker
COPY core/agent/llm ./llm
COPY core/agent/shared ./shared
COPY core/agent/contracts ./contracts
# The registry of per-person workspace templates the worker seeds new workspaces from (Phase 3) +
# the tool registry. workspace-seeds/<name>/ — one validated seed per subdir (today: default, finos).
COPY core/agent/workspace-seeds ./workspace-seeds
COPY core/agent/tools-seed ./tools-seed

# The sealed contracts loaded BY PATH at import (walk-up resolves repo_root == /app).
COPY core/meetings/contracts/transcript.v1/transcript.schema.json  ./meetings/contracts/transcript.v1/transcript.schema.json
COPY core/agent/contracts/workspace.v1/workspace.schema.json       ./agent/contracts/workspace.v1/workspace.schema.json
COPY core/agent/contracts/invoke.v1/invoke.schema.json             ./agent/contracts/invoke.v1/invoke.schema.json
COPY core/agent/contracts/unit.v1/unit.schema.json                 ./agent/contracts/unit.v1/unit.schema.json
COPY core/agent/contracts/routine.v1/routine.schema.json           ./agent/contracts/routine.v1/routine.schema.json
COPY core/agent/contracts/event.v1/event.schema.json               ./agent/contracts/event.v1/event.schema.json
COPY core/agent/contracts/tool.v1/tool.schema.json                 ./agent/contracts/tool.v1/tool.schema.json

# The runtime kernel spawns this image with this command (profiles.py "agent" profile).
CMD ["python", "-m", "worker"]

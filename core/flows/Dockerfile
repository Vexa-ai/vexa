# syntax=docker/dockerfile:1
# core/flows — one image, three entrypoints (worker · mailbox · api), selected by command.
# The engine is stdlib-pure; the deps here are the production edges only: pyproject.toml's
# [project].dependencies, installed from uv.lock exactly as gate:licenses classifies them, and none
# of the dev group (pytest).
FROM python:3.12-slim
# uv via PyPI (ghcr.io is unreachable in this build env) — pinned for deterministic resolves.
RUN pip install --no-cache-dir uv==0.9.22
ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH
WORKDIR /app
COPY core/flows/pyproject.toml core/flows/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
COPY core/flows/schema.sql ./schema.sql
# THE MCP TOOL MANIFEST — what the gateway assembly fetches from
# `/.well-known/mcp-tools.json`. `flows_api` reads it at `parents[2]`, i.e. beside `src/`, so it has
# to be IN the image: without it the route answers 503 and the assembled MCP surface silently loses
# this domain's four tools. It is not under `src/` because it is the DOMAIN's declaration, read by
# the gateway's tests from the repo as well as by this service at runtime — one copy, two readers.
COPY core/flows/mcp.tools.v1.json ./mcp.tools.v1.json
COPY core/flows/src ./src
COPY behavior /behavior
ENV PYTHONPATH=/app/src \
    VEXA_BEHAVIOR_DIR=/behavior-private
# default: the worker; override command for the other two processes:
#   python -m flows_integrations.mailbox   ·   python -m flows_integrations.flows_api
WORKDIR /app/src
CMD ["python", "-m", "flows_worker"]

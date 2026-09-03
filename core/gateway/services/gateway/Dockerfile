# syntax=docker/dockerfile:1
# Gateway (v0.12 P4) — the production edge: auth · routing · /ws fan-out.
#
# BUILD CONTEXT: THE REPOSITORY ROOT (root .dockerignore keeps it light), like meeting-api and
# agent-api. It was this service's own directory until the edge stopped owning its route table:
# `routes_manifest.py` assembles the table from each domain's `routes.v1.json`, read BY PATH at
# import, so the image must carry the manifests of the domains it fronts — and a narrow context
# cannot reach them. Same reason meeting-api builds from the root and COPYs the five sealed
# contract files it loads. A missing manifest is not a degraded gateway: the process refuses to
# start, which is correct and is exactly how this was found (compose stack, gateway unhealthy).
FROM python:3.12-slim

# uv via PyPI (ghcr.io is unreachable in this build env) — pinned for deterministic resolves.
RUN pip install --no-cache-dir uv==0.9.22

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# 1) Resolve + install the pinned dependency set from the lockfile (no project — package=false).
COPY core/gateway/services/gateway/pyproject.toml core/gateway/services/gateway/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# 2) Production-only runtime deps NOT in the (lean) test pyproject: the ASGI server + the real
#    redis fan-in client (httpx is already pinned). Kept out of pyproject so the offline gate venv
#    stays minimal; pinned here for a deterministic image.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "uvicorn[standard]==0.34.0" "redis==5.2.1"

# 3) The shipped source (src/ is on PYTHONPATH; the package is not pip-installed).
COPY core/gateway/services/gateway/src ./src

# 4) THE ROUTE MANIFESTS OF THE DOMAINS THIS EDGE FRONTS. Kept at their repo-relative paths so
#    `routes_manifest._repo_root()` — which walks up for core/gateway + core/meetings rather than
#    counting parents — resolves them exactly as it does in a checkout. One file per domain, listed
#    rather than globbed, for the same reason `manifest_paths()` is a map: a glob would silently
#    miss one whose service moved. A domain that adds a route changes only its own file here.
COPY core/gateway/services/gateway/routes.v1.json ./core/gateway/services/gateway/routes.v1.json
COPY core/meetings/routes.v1.json                 ./core/meetings/routes.v1.json
COPY core/meetings/services/mcp/routes.v1.json    ./core/meetings/services/mcp/routes.v1.json
COPY core/identity/routes.v1.json                 ./core/identity/routes.v1.json
COPY core/agent/routes.v1.json                    ./core/agent/routes.v1.json

EXPOSE 8000
# python -m gateway → uvicorn gateway.adapters:app (HOST/PORT from env).
CMD ["python", "-m", "gateway"]

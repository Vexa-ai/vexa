# syntax=docker/dockerfile:1
# MCP service (v0.12) — the Model Context Protocol front for the Vexa PUBLIC API.
# Stateless: it only calls the gateway (GATEWAY_URL) with the caller's key; no DB, no redis.
# Build context: meetings/services/mcp (pyproject.toml + uv.lock + src/), mirroring the gateway image.
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
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# 2) Production-only runtime dep NOT in the (lean) test pyproject: the ASGI server.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "uvicorn[standard]==0.34.0"

# 3) The shipped source (src/ is on PYTHONPATH; the package is not pip-installed).
COPY src ./src

EXPOSE 8010
# python -m vexa_mcp → uvicorn over create_app() (GATEWAY_URL / HOST / PORT from env).
CMD ["python", "-m", "vexa_mcp"]

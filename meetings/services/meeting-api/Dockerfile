# syntax=docker/dockerfile:1
# Meeting-api (v0.12 P4) — the unified modular-monolith control plane (lifecycle · bot_spawn ·
# collector · recordings). The entrypoint ALSO starts the background loops (segment consumer ·
# webhook retry-drain · scheduler tick / join-retry) alongside the HTTP app via the lifespan.
#
# Build context: the v0.12 carve root (compose `context: ../..`). Wider than the other three
# services because meeting_api validates AT THE SEAM (P8): it loads the sealed invocation.v1 /
# lifecycle.v1 / webhook.v1 / runtime.v1 / schedule.v1 schemas BY PATH at import (walking up from
# the module to find `meetings/contracts/...` and `runtime/contracts/...`), so those frozen
# contract files must be vendored into the image alongside src/.
FROM python:3.12-slim

# uv via PyPI (ghcr.io is unreachable in this build env) — pinned for deterministic resolves.
RUN pip install --no-cache-dir uv==0.9.22

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY meetings/services/meeting-api/pyproject.toml meetings/services/meeting-api/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# Production-only runtime deps NOT in the (lean) test pyproject (redis/httpx are already pinned):
# the ASGI server + the async Postgres stack (collector/bot_spawn/recordings SQLAlchemy adapters)
# + boto3 (the recordings S3/MinIO storage adapter).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "uvicorn[standard]==0.34.0" "sqlalchemy[asyncio]==2.0.36" "asyncpg==0.30.0" "boto3==1.35.0"

# The shipped source (src/ on PYTHONPATH).
COPY meetings/services/meeting-api/src ./src

# The sealed contracts loaded BY PATH at import. Placed under /app/ so the module walk-up
# (Path(__file__).resolve().parents → .../src/meeting_api/... → /app) resolves
# `meetings/contracts/...` and `runtime/contracts/...`.
COPY meetings/contracts/invocation.v1/invocation.schema.json   ./meetings/contracts/invocation.v1/invocation.schema.json
COPY meetings/contracts/lifecycle.v1/lifecycle.schema.json     ./meetings/contracts/lifecycle.v1/lifecycle.schema.json
COPY meetings/contracts/webhook.v1/webhook.schema.json         ./meetings/contracts/webhook.v1/webhook.schema.json
COPY runtime/contracts/runtime.v1/runtime.schema.json          ./runtime/contracts/runtime.v1/runtime.schema.json
COPY runtime/contracts/schedule.v1/schedule.schema.json        ./runtime/contracts/schedule.v1/schedule.schema.json

EXPOSE 8080
# python -m meeting_api → create_app(real adapters) + the lifespan-driven background loops.
CMD ["python", "-m", "meeting_api"]

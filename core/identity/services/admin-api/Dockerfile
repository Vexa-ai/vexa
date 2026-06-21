# syntax=docker/dockerfile:1
# Admin-api (v0.12 P4) — the identity service: user/token CRUD + /internal/validate (the gateway's
# authz oracle). Runs ensure_schema() on startup to converge the identity + meeting tables.
# Build context: identity/services/admin-api (pyproject.toml + uv.lock + src/).
FROM python:3.12-slim

# uv via PyPI (ghcr.io is unreachable in this build env) — pinned for deterministic resolves.
RUN pip install --no-cache-dir uv==0.9.22

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# Production-only runtime deps NOT in the (lean) test pyproject: the ASGI server + the async
# Postgres stack (SQLAlchemy async engine + asyncpg driver) the app binds to the compose Postgres.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "uvicorn[standard]==0.34.0" "sqlalchemy[asyncio]==2.0.36" "asyncpg==0.30.0"

COPY src ./src

EXPOSE 8001
# python -m admin_api → create_app() + DB-from-DB_* env + ensure_schema() on startup.
CMD ["python", "-m", "admin_api"]

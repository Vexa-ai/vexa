# syntax=docker/dockerfile:1
# Admin-api (v0.12 P4) — the identity service: user/token CRUD + /internal/validate (the gateway's
# authz oracle). Runs ensure_schema() on startup to converge the identity + meeting tables.
# Build context: identity/services/admin-api (pyproject.toml + uv.lock + src/).
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f

# uv via PyPI (ghcr.io is unreachable in this build env) — pinned for deterministic resolves.
RUN pip install --no-cache-dir uv==0.11.33

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --group production

COPY src ./src

# An unprivileged user runs the service: it writes nothing outside /tmp, and reads its mounted keys
# through the group the deploy surfaces give it (compose's key one-shot, the chart's fsGroup).
RUN groupadd --system --gid 10001 vexa \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin vexa
USER 10001:10001

EXPOSE 8001
# python -m admin_api → create_app() + DB-from-DB_* env + ensure_schema() on startup.
CMD ["python", "-m", "admin_api"]

# syntax=docker/dockerfile:1
# Runtime kernel (v0.12 P5.1) — realizes runtime.v1; spawns bot/agent containers via the host
# Docker socket. The DockerBackend talks to /var/run/docker.sock over the SOCKET API
# (requests_unixsocket), exactly like main's runtime-api — so the image needs NO docker CLI and
# NO daemon. The compose mounts /var/run/docker.sock + sets DOCKER_NETWORK.
# Build context: runtime/ (pyproject.toml + uv.lock + src/).
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

# Production-only: the ASGI server (fastapi/httpx/redis are already pinned in pyproject).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "uvicorn[standard]==0.34.0"

COPY src ./src

EXPOSE 8090
# python -m runtime_kernel → uvicorn over create_app(Runtime(DockerBackend(), default_registry())).
CMD ["python", "-m", "runtime_kernel"]

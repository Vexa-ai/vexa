# syntax=docker/dockerfile:1
# Runtime kernel (v0.12 P5.1) — realizes runtime.v1; spawns bot/agent workloads. The DockerBackend
# (default) talks to /var/run/docker.sock over the SOCKET API (requests_unixsocket), exactly like
# main's runtime-api — so it needs NO docker CLI and NO daemon; the compose mounts the socket + sets
# DOCKER_NETWORK. The K8sBackend (RUNTIME_BACKEND=k8s, used by deploy/helm) shells out to `kubectl`,
# which is why the static binary below is bundled — inert for the docker/process paths.
# Build context: runtime/ (pyproject.toml + uv.lock + src/).
FROM python:3.12-slim

# kubectl — only invoked by the K8sBackend (RUNTIME_BACKEND=k8s). Pinned static binary; harmless for
# the default docker/process backends. Arch-aware so the image builds on amd64 and arm64 hosts.
ARG KUBECTL_VERSION=v1.31.4
RUN set -eux; \
    apt-get update && apt-get install -y --no-install-recommends ca-certificates curl; \
    arch="$(dpkg --print-architecture)"; case "$arch" in amd64) karch=amd64;; arm64) karch=arm64;; *) karch="$arch";; esac; \
    curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${karch}/kubectl" -o /usr/local/bin/kubectl; \
    chmod +x /usr/local/bin/kubectl; \
    apt-get purge -y curl; apt-get autoremove -y; rm -rf /var/lib/apt/lists/*

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

# syntax=docker/dockerfile:1
# Runtime kernel (v0.12 P5.1) — realizes runtime.v1; spawns bot/agent workloads. The DockerBackend
# (default) talks to /var/run/docker.sock over the SOCKET API (requests_unixsocket), exactly like
# main's runtime-api — so it needs NO docker CLI and NO daemon; the compose mounts the socket + sets
# DOCKER_NETWORK. The K8sBackend (RUNTIME_BACKEND=k8s, used by deploy/helm) shells out to `kubectl`,
# which is why the static binary below is bundled — inert for the docker/process paths.
# Build context: runtime/ (pyproject.toml + uv.lock + src/).
FROM python:3.12-slim

# kubectl — only invoked by the K8sBackend (RUNTIME_BACKEND=k8s). Pinned static binary, verified against
# the release's published SHA-256; harmless for the default docker/process backends. Arch-aware so the
# image builds on amd64 and arm64 hosts. A supported release built with a supported Go (1.35.9: Go 1.26):
# the K8sBackend uses only `create -f -`, `get pod` and `delete pod`, core v1 calls every cluster serves.
ARG KUBECTL_VERSION=v1.35.9
RUN set -eux; \
    apt-get update && apt-get install -y --no-install-recommends ca-certificates curl; \
    arch="$(dpkg --print-architecture)"; case "$arch" in amd64) karch=amd64;; arm64) karch=arm64;; *) karch="$arch";; esac; \
    base="https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${karch}/kubectl"; \
    curl -fsSL "$base" -o /usr/local/bin/kubectl; \
    echo "$(curl -fsSL "$base.sha256")  /usr/local/bin/kubectl" | sha256sum -c -; \
    chmod +x /usr/local/bin/kubectl; \
    apt-get purge -y curl; apt-get autoremove -y; rm -rf /var/lib/apt/lists/*

# uv via PyPI (ghcr.io is unreachable in this build env) — pinned for deterministic resolves, at a
# release past the advisories fixed in 0.11.15.
RUN pip install --no-cache-dir uv==0.11.33

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY pyproject.toml uv.lock ./
# --no-dev: the dev group (pytest) is host-side test tooling, not a runtime dep. The production group is
# the ASGI server, locked like every other package: nothing is installed outside uv.lock.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --group production

COPY src ./src

EXPOSE 8090
# python -m runtime_kernel → uvicorn over create_app(Runtime(DockerBackend(), default_registry())).
CMD ["python", "-m", "runtime_kernel"]

# syntax=docker/dockerfile:1
# Runtime kernel (v0.12 P5.1) — realizes runtime.v1; spawns bot/agent workloads. The DockerBackend
# (default) talks to /var/run/docker.sock over the SOCKET API (requests_unixsocket), exactly like
# main's runtime-api — so it needs NO docker CLI and NO daemon; the compose mounts the socket + sets
# DOCKER_NETWORK. The K8sBackend (RUNTIME_BACKEND=k8s, used by deploy/helm) shells out to `kubectl`,
# which is why the static binary below is bundled — inert for the docker/process paths.
# Build context: runtime/ (pyproject.toml + uv.lock + src/).
# uv is a build tool only: it resolves the venv in this stage (uv via PyPI, ghcr.io is unreachable in this
# build env; 0.13.4, past GHSA-hq85-5cpj-mr62) and never enters the final image.
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS build
RUN pip install --no-cache-dir uv==0.13.4
ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# --no-dev: the dev group (pytest) is host-side test tooling, not a runtime dep. The production group is
# the ASGI server, locked like every other package: nothing is installed outside uv.lock.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --group production

FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f

# kubectl — only invoked by the K8sBackend (RUNTIME_BACKEND=k8s). Pinned static binary, verified against
# the release's published SHA-256; harmless for the default docker/process backends. Arch-aware so the
# image builds on amd64 and arm64 hosts. A supported release built with a supported Go (1.35.9: Go 1.26):
# the K8sBackend uses only `create -f -`, `get pod` and `delete pod`, core v1 calls every cluster serves.
# 1.35.10 (Go 1.26.10) clears the Go stdlib advisories of 1.35.9. The SHA-256 per arch is pinned here,
# copied from dl.k8s.io/release/v1.35.10/bin/linux/<arch>/kubectl.sha256. The same RUN upgrades the
# base's Debian packages to their current security fixes (apt, libapt-pkg, zlib1g).
ARG KUBECTL_VERSION=v1.35.10
ARG KUBECTL_SHA256_AMD64=c49a28f1f4efc4d475ed05f47f2ce8e563bf7bef69ed77fe1b924ea6d83b6327
ARG KUBECTL_SHA256_ARM64=283f1be6a46b3695b625e25030106b8ffe18bd0d3a900f5b65c259ca407f04f5
RUN set -eux; \
    apt-get update && apt-get upgrade -y --no-install-recommends; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    arch="$(dpkg --print-architecture)"; case "$arch" in \
      amd64) karch=amd64; ksum="$KUBECTL_SHA256_AMD64";; \
      arm64) karch=arm64; ksum="$KUBECTL_SHA256_ARM64";; \
      *) echo "no pinned kubectl checksum for $arch" >&2; exit 1;; esac; \
    curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${karch}/kubectl" -o /usr/local/bin/kubectl; \
    echo "${ksum}  /usr/local/bin/kubectl" | sha256sum -c -; \
    chmod +x /usr/local/bin/kubectl; \
    apt-get purge -y curl; apt-get autoremove -y; rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY --from=build /opt/venv /opt/venv
COPY pyproject.toml uv.lock ./
COPY src ./src

EXPOSE 8090
# python -m runtime_kernel → uvicorn over create_app(Runtime(DockerBackend(), default_registry())).
CMD ["python", "-m", "runtime_kernel"]

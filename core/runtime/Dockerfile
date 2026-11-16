# syntax=docker/dockerfile:1.27@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# Runtime kernel (v0.12 P5.1) — realizes runtime.v1; spawns bot/agent workloads. The DockerBackend
# (default) talks to /var/run/docker.sock over the SOCKET API (requests_unixsocket), exactly like
# main's runtime-api — so it needs NO docker CLI and NO daemon; the compose mounts the socket + sets
# DOCKER_NETWORK. The K8sBackend (RUNTIME_BACKEND=k8s, used by deploy/helm) calls the API server itself
# over HTTPS with its ServiceAccount (requests, from uv.lock): no kubectl binary ships in this image.
# Build context: runtime/ (pyproject.toml + uv.lock + src/).
# Every stage starts from the one python base on bases.lock (security/bases/check_bases.py).
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

# The base's Debian packages that have security fixes, at the exact versions (checked 16 Nov, Debian 13
# security and updates). Pinned rather than `apt-get upgrade`, so the image changes only when this list
# does. Debian keeps only the newest build of a package in its archive: when one of these is superseded
# the build fails at this line, and the fix is to bump that pin here.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends --only-upgrade \
      apt=3.0.4 libapt-pkg7.0=3.0.4 \
      ca-certificates=20250419+deb13u1 \
      liblzma5=5.8.1-1+deb13u2 \
      tzdata=2026f-0+deb13u1 \
      zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1; \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY --from=build /opt/venv /opt/venv
COPY pyproject.toml uv.lock ./
COPY src ./src

# An unprivileged user runs the runtime: the uid Compose and the chart's k8s backend already run it as
# (Compose reaches the engine socket through the socket's group). The chart's docker backend, which
# holds the engine as root, asks for uid 0 in its own pod securityContext; Lite runs the runtime from
# its own image.
RUN groupadd --system --gid 10001 vexa \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin vexa
USER 10001:10001

EXPOSE 8090
# python -m runtime_kernel → uvicorn over create_app(Runtime(DockerBackend(), default_registry())).
CMD ["python", "-m", "runtime_kernel"]

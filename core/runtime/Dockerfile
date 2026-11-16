# syntax=docker/dockerfile:1.27@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# Runtime kernel (v0.12 P5.1) — realizes runtime.v1; spawns bot/agent workloads. The DockerBackend
# (default) talks to /var/run/docker.sock over the SOCKET API (requests_unixsocket), exactly like
# main's runtime-api — so it needs NO docker CLI and NO daemon; the compose mounts the socket + sets
# DOCKER_NETWORK. The K8sBackend (RUNTIME_BACKEND=k8s, used by deploy/helm) shells out to `kubectl`,
# which is why the static binary below is bundled — inert for the docker/process paths.
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

# kubectl — only invoked by the K8sBackend (RUNTIME_BACKEND=k8s), which uses only `create -f -`,
# `get pod` and `delete pod`, core v1 calls every cluster serves. Built here from the official
# v1.35.10 source release, because no kubectl release yet carries golang.org/x/net >= 0.62.0 and
# golang.org/x/text >= 0.41.0 (CVE-2026-78661, CVE-2026-78669, CVE-2026-56851; checked 16 Nov:
# v1.35.10, v1.36.6 and v1.37.2 ship x/net 0.56-0.57 and x/text 0.39-0.40; only master has the fix).
# The source is the release's kubernetes-src.tar.gz, verified against dl.k8s.io's published SHA-256;
# the Go toolchain is go.dev's go1.26.11 archive, verified against go.dev's published SHA-256 per arch;
# every module is verified by go.sum and the Go checksum database. The only change to the release is
# the x/net and x/text bump (and what MVS raises with them: x/crypto, x/sys, x/term, x/sync, x/mod,
# x/tools). The version is stamped as upstream does, so scanners still match the kubectl release.
# EXCEPTION (P9, recorded 2026-11-16, expires 2026-11-23): this in-house kubectl build is a dated
# exception, not a standing way to ship kubectl. It ends at the first of: a kubectl release ships
# golang.org/x/net >= 0.62 and golang.org/x/text >= 0.43 (this stage then goes back to the release
# binary), or the runtime stops shipping kubectl (its K8sBackend calls the API server itself). Past the
# expiry with neither done, the image counts as failing the security gate. Its labels name what was bumped.
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS kubectl
ARG KUBECTL_VERSION=v1.35.10
ARG KUBECTL_SRC_SHA256=3049a97c6be1aed0cefe5db304d6a6ff1a440c95bcd031a7a9b6976049f40730
ARG GO_VERSION=1.26.11
ARG GO_SHA256_AMD64=9849f4e04ee35303714e34fbc3ac8ab44ea3e448a95aa182f91e3b9b79c710ec
ARG GO_SHA256_ARM64=6c2f29b458b5add0835f18b776f69c8881a6d88428f1484a6da55755e620757e
ARG X_NET_VERSION=v0.62.0
ARG X_TEXT_VERSION=v0.43.0
ENV GOWORK=off GOFLAGS=-mod=mod GOTOOLCHAIN=local CGO_ENABLED=0 GOPATH=/go PATH=/usr/local/go/bin:$PATH
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; case "$arch" in \
      amd64) gsum="$GO_SHA256_AMD64";; \
      arm64) gsum="$GO_SHA256_ARM64";; \
      *) echo "no pinned Go checksum for $arch" >&2; exit 1;; esac; \
    fetch() { python3 -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' "$1" "$2"; }; \
    fetch "https://go.dev/dl/go${GO_VERSION}.linux-${arch}.tar.gz" /tmp/go.tgz; \
    echo "${gsum}  /tmp/go.tgz" | sha256sum -c -; \
    tar -C /usr/local -xzf /tmp/go.tgz; rm /tmp/go.tgz; \
    fetch "https://dl.k8s.io/release/${KUBECTL_VERSION}/kubernetes-src.tar.gz" /tmp/k8s.tgz; \
    echo "${KUBECTL_SRC_SHA256}  /tmp/k8s.tgz" | sha256sum -c -; \
    mkdir /src; tar -C /src -xzf /tmp/k8s.tgz; rm /tmp/k8s.tgz
WORKDIR /src
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    set -eux; \
    go get "golang.org/x/net@${X_NET_VERSION}" "golang.org/x/text@${X_TEXT_VERSION}"; \
    mods="$(go list -m golang.org/x/net golang.org/x/text golang.org/x/crypto golang.org/x/sys golang.org/x/term golang.org/x/sync golang.org/x/mod golang.org/x/tools | tr '\n' ' ')"; \
    test "$mods" = "golang.org/x/net v0.62.0 golang.org/x/text v0.43.0 golang.org/x/crypto v0.58.0 golang.org/x/sys v0.49.0 golang.org/x/term v0.47.0 golang.org/x/sync v0.24.0 golang.org/x/mod v0.41.0 golang.org/x/tools v0.51.0 " \
      || { echo "the bumped modules differ from the image label ai.vexa.kubectl.bumped-modules: $mods" >&2; exit 1; }; \
    minor="${KUBECTL_VERSION#v1.}"; minor="${minor%%.*}"; ld="-s -w"; \
    for p in k8s.io/client-go/pkg/version k8s.io/component-base/version; do \
      ld="$ld -X $p.gitVersion=${KUBECTL_VERSION} -X $p.gitMajor=1 -X $p.gitMinor=${minor} -X $p.gitTreeState=dirty"; \
    done; \
    go build -trimpath -ldflags "$ld" -o /out/kubectl ./cmd/kubectl; \
    /out/kubectl version --client | grep -qx "Client Version: ${KUBECTL_VERSION}"; \
    go version -m /out/kubectl | grep -E "golang.org/x/(net|text)\s" | tee /out/x-modules.txt >&2; \
    grep -q "golang.org/x/net\s${X_NET_VERSION}\s" /out/x-modules.txt; \
    grep -q "golang.org/x/text\s${X_TEXT_VERSION}\s" /out/x-modules.txt

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

COPY --from=kubectl /out/kubectl /usr/local/bin/kubectl
# What the kubectl in this image is: the v1.35.10 source release, not its binary (the exception above).
LABEL ai.vexa.kubectl.source="kubernetes v1.35.10 kubernetes-src.tar.gz (sha256 3049a97c6be1aed0cefe5db304d6a6ff1a440c95bcd031a7a9b6976049f40730), go1.26.11, gitTreeState=dirty" \
      ai.vexa.kubectl.bumped-modules="golang.org/x/net v0.56.0=>v0.62.0, golang.org/x/text v0.39.0=>v0.43.0, golang.org/x/crypto v0.53.0=>v0.58.0, golang.org/x/sys v0.46.0=>v0.49.0, golang.org/x/term v0.44.0=>v0.47.0, golang.org/x/sync v0.21.0=>v0.24.0, golang.org/x/mod v0.37.0=>v0.41.0, golang.org/x/tools v0.47.0=>v0.51.0" \
      ai.vexa.kubectl.exception="P9 in-house build, recorded 2026-11-16, expires 2026-11-23; ends when a kubectl release ships x/net >= 0.62 and x/text >= 0.43"

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

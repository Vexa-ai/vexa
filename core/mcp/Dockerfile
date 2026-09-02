# syntax=docker/dockerfile:1
# mcp-control (v0.12) — THE EDGE a person's own agent connects to.
#
# Like the gateway it exposes the domains with the caller's identity attached and owns no state of
# its own: no database, no redis, no docker socket. Its whole world is four sibling URLs and a
# volume at VEXA_HOME for the small amount it does hold (durable tokens, pending sign-in codes, the
# one gateway key per person it minted).
#
# Build context: core/mcp (pyproject.toml + src/), mirroring the gateway image.
FROM python:3.12-slim

# uv via PyPI (ghcr.io is unreachable in this build env) — pinned for deterministic resolves.
RUN pip install --no-cache-dir uv==0.9.22

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    VEXA_HOME=/var/lib/vexa-mcp \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# 1) The pinned dependency set (no project — package=false, src/ rides PYTHONPATH).
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "mcp>=2.1,<3" "uvicorn[standard]==0.34.0"

# 2) The shipped source.
COPY src ./src

RUN mkdir -p /var/lib/vexa-mcp
VOLUME ["/var/lib/vexa-mcp"]
EXPOSE 18310
# `vexa-mcp` speaks streamable HTTP. `--stdio` exists for the offline suites and is not a way to run
# Vexa on a laptop: this is a cloud edge, fronted by the gateway at /mcp.
CMD ["python", "-m", "vexa_mcp"]

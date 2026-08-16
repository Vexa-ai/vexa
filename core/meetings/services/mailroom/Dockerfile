# syntax=docker/dockerfile:1
# mailroom (Stage-0 dev) — the invitation mailbox: poll an inbound mailbox, parse the .ics, and
# create/re-schedule/cancel planned meetings on the PUBLIC meeting API. One process: the poll loop
# runs alongside the liveness app via the startup hook in __main__.
#
# Build context: this service directory (compose `context: ../../core/meetings/services/mailroom`).
# Narrow like the mcp service's — the mailroom loads no sealed contract by path; it consumes the
# published REST surface, so nothing outside src/ needs vendoring.
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

# The ASGI server is a production-only dep (the test pyproject stays lean).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "uvicorn[standard]==0.34.0"

COPY src ./src

# Bindings + cursor + notices live here; mount a volume or the state resets with the container
# (which is safe — re-reading the mailbox is idempotent — but re-creates nothing already planned).
ENV MAILROOM_STATE_PATH=/data/mailroom-state.json
RUN mkdir -p /data

EXPOSE 8030
CMD ["python", "-m", "vexa_mailroom"]

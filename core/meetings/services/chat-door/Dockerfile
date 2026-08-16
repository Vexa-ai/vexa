# syntax=docker/dockerfile:1
# chat-door (dev v0) — magic-link entry from an artifact email into the meeting record.
# Build context: core/meetings/services/chat-door (pyproject.toml + uv.lock + src/).
#
# Stateful in one place only: CHAT_DOOR_STORE_DIR holds user rows + personal-instructions
# docs, so mount a volume there or the personal layer dies with the container.
# CHAT_DOOR_SIGNING_KEY must be supplied (a secret) — unset, the process generates an
# ephemeral key and says so, and links stop working across restarts.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.9.22

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH \
    CHAT_DOOR_STORE_DIR=/data/chat-door \
    CHAT_DOOR_PORT=8080

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# The ASGI server is a runtime-only dep, not part of the (lean) test pyproject — same split
# as core/identity/services/admin-api.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "uvicorn[standard]==0.34.0"

COPY src ./src

EXPOSE 8080
CMD ["python", "-m", "chat_door"]

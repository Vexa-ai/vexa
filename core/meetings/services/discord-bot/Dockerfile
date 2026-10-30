# syntax=docker/dockerfile:1
# @vexa/discord-bot — the Discord platform-lane meeting-bot (#875).
#
# Boots from VEXA_BOT_CONFIG (invocation.v1, platform="discord"), joins a Discord voice channel
# over the DAVE/E2EE voice-gateway receive path (dave_voice/), receives per-speaker audio,
# transcribes, and reports lifecycle.v1 / transcript.v1 / acts.v1 — the runtime.v1 `discord-bot`
# profile execs this image with no command (see core/runtime/src/runtime_kernel/profiles.py).
#
# Pinned to python:3.11 — dave_voice/opus_decode.py and discord_bot/audio.py use the stdlib
# `audioop` module, removed in 3.13. Do NOT bump past 3.11.
#
# Build context: the v0.12 carve root (compose `context: ../..`) — wider than its own dir because
# the sealed invocation.v1 / transcript.v1 / lifecycle.v1 / acts.v1 schemas are loaded BY PATH at
# import (discord_bot/contracts.py walks up from the module to find `meetings/contracts/...`), so
# those frozen contract files must be vendored into the image alongside src/.
FROM python:3.11-slim

# libopus is needed to DECODE incoming Discord voice (dave_voice/opus_decode.py, via py-cord).
RUN apt-get update && apt-get install -y --no-install-recommends libopus0 \
    && rm -rf /var/lib/apt/lists/*

# uv via PyPI (ghcr.io is unreachable in this build env) — pinned for deterministic resolves.
RUN pip install --no-cache-dir uv==0.9.22

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY core/meetings/services/discord-bot/pyproject.toml core/meetings/services/discord-bot/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-default-groups

# The shipped source (src/ on PYTHONPATH).
COPY core/meetings/services/discord-bot/src ./src

# The sealed contracts loaded BY PATH at import. Sources live under core/ — the in-image layout
# mirrors meeting-api's Dockerfile: placed under /app/ so the module walk-up
# (Path(__file__).resolve().parents → .../src/discord_bot/... → /app) resolves `meetings/contracts/...`.
COPY core/meetings/contracts/invocation.v1/invocation.schema.json ./meetings/contracts/invocation.v1/invocation.schema.json
COPY core/meetings/contracts/transcript.v1/transcript.schema.json ./meetings/contracts/transcript.v1/transcript.schema.json
COPY core/meetings/contracts/lifecycle.v1/lifecycle.schema.json   ./meetings/contracts/lifecycle.v1/lifecycle.schema.json
COPY core/meetings/contracts/acts.v1/acts.schema.json             ./meetings/contracts/acts.v1/acts.schema.json

# python -m discord_bot → discord_bot.bot.run(): validate config, join, receive, report, die.
CMD ["python", "-m", "discord_bot"]

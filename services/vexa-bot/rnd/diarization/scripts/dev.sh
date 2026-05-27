#!/usr/bin/env bash
# Hot-reload dev wrapper for the MVP0 diarization harness.
#
# Usage:
#   PORT=43500 TRANSCRIPTION_URL=http://localhost:8083 NUM_SPEAKERS=2 ./scripts/dev.sh
#
# Defaults:
#   PORT=43500              (pack-msteams-local-diarization-rnd slot 125 compose_dashboard)
#   TRANSCRIPTION_URL=""    (unset → harness emits placeholder transcripts)
#   NUM_SPEAKERS=2

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/.."

# Load local secrets if present (gitignored). HF_TOKEN lives here for MVP1.
if [ -f .env.local ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env.local
  set +a
fi

if [ ! -d node_modules ]; then
  echo "[dev.sh] installing dependencies..."
  npm install
fi

# MVP1: if DIARIZER=pyannote, make sure the Python sidecar venv exists.
if [ "${DIARIZER:-stub}" = "pyannote" ] && [ ! -d sidecar/.venv ]; then
  echo "[dev.sh] DIARIZER=pyannote — creating sidecar venv (one-time, ~5min)"
  ( cd sidecar && uv venv --python 3.11 && uv pip install -e . )
fi

# tsx watch picks up src/**/*.ts changes and reloads the whole node process.
# Silero VAD (~2MB) reloads with it — acceptable at MVP0. MVP1's pyannote
# sidecar will live in a separate process to survive harness reloads.
exec npm run dev

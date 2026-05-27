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

if [ ! -d node_modules ]; then
  echo "[dev.sh] installing dependencies..."
  npm install
fi

# tsx watch picks up src/**/*.ts changes and reloads the whole node process.
# Silero VAD (~2MB) reloads with it — acceptable at MVP0. MVP1's pyannote
# sidecar will live in a separate process to survive harness reloads.
exec npm run dev

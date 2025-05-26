#!/usr/bin/env bash
# This script is executed inside the container by Docker's HEALTHCHECK instruction.

# Exit immediately if a command exits with a non-zero status.
set -e

# Ping the internal health endpoint of the WhisperLive application.
# The -f option makes curl fail silently (no output) on HTTP errors but return an appropriate exit code.
# The -s option makes curl silent (no progress meter or error messages).
if curl -sf http://localhost:9091/health > /dev/null; then
  exit 0 # Healthy
else
  exit 1 # Unhealthy
fi 
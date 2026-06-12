#!/usr/bin/env bash
# Containerized extension build — no host Node/npm required, no node_modules
# on the host, identical output on any OS. The toolchain runs in node:20
# (Linux); dist/ is the only thing written back.
#
# The extension bundles browser sources from services/vexa-bot/core (the
# bot's capture loop), so the build context is services/, not this dir.
set -euo pipefail

SERVICES_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$SERVICES_DIR/vexa-extension/dist"
mkdir -p "$DIST_DIR"

docker run --rm \
  -v "$SERVICES_DIR:/src:ro" \
  -v "$DIST_DIR:/out" \
  node:20 bash -c '
    mkdir -p /build/services/vexa-bot/core
    cp -r /src/vexa-extension /build/services/vexa-extension
    cp -r /src/vexa-bot/core/src /build/services/vexa-bot/core/src
    cd /build/services/vexa-extension
    rm -rf dist node_modules
    npm install --no-audit --no-fund >/dev/null
    npm run build
    rm -rf /out/* && cp -r dist/* /out/
  '

echo "dist/ ready: $DIST_DIR"

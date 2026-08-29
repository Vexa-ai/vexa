#!/usr/bin/env bash
# Xvfb + capture-page-dom.ts. Sibling of docker-entrypoint.sh, but it captures a
# page instead of joining a meeting (see capture-page-dom.ts / #857).
set -e
: "${CAPTURE_URL:?set CAPTURE_URL}"
Xvfb :99 -screen 0 1920x1080x24 -ac +extension RANDR >/tmp/xvfb.log 2>&1 &
export DISPLAY=:99
for i in $(seq 1 20); do xdpyinfo -display :99 >/dev/null 2>&1 && break; sleep 0.25; done
echo "[capture-entrypoint] DISPLAY :99 up — capturing ${CAPTURE_URL}"
exec npx tsx scripts/capture-page-dom.ts

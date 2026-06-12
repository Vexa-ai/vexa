#!/usr/bin/env bash
# Start a virtual display, then run the join-layer debug runner on it.
# The runner's startDebugView() auto-spawns x11vnc + websockify(noVNC:6080)
# because DISPLAY is set on Linux — no extra wiring here.
set -e

: "${MEETING_URL:?set MEETING_URL to a meet.google.com link}"

Xvfb :99 -screen 0 1920x1080x24 -ac +extension RANDR >/tmp/xvfb.log 2>&1 &
export DISPLAY=:99
# fluxbox gives the headed Chromium a window manager so it maximizes cleanly
( command -v fluxbox >/dev/null && fluxbox >/tmp/fluxbox.log 2>&1 & ) || true

# wait for the display to accept connections
for i in $(seq 1 20); do xdpyinfo -display :99 >/dev/null 2>&1 && break; sleep 0.25; done

echo "[entrypoint] DISPLAY :99 up — launching join-layer debug runner"
exec node dist/scripts/debug-join.js "$MEETING_URL"

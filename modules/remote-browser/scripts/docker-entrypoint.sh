#!/usr/bin/env bash
# Start a virtual display + a VNC lens, then run a remote-browser flow (login|validate)
# FROM SOURCE via tsx. The PROFILE dir is mounted from the host so the saved session
# survives the container and is reusable by the Linux bot (same --password-store=basic).
set -e

: "${PLATFORM:?set PLATFORM=google|zoom|teams}"
: "${RB_CMD:=login}"   # login | validate

Xvfb :99 -screen 0 1920x1080x24 -ac +extension RANDR >/tmp/xvfb.log 2>&1 &
export DISPLAY=:99
( command -v fluxbox >/dev/null && fluxbox >/tmp/fluxbox.log 2>&1 & ) || true
for i in $(seq 1 20); do xdpyinfo -display :99 >/dev/null 2>&1 && break; sleep 0.25; done

# Live lens: x11vnc on :99 → websockify/noVNC on :6080 (same setup the join harness uses).
x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -bg -o /tmp/x11vnc.log >/dev/null 2>&1 || true
NOVNC_WEB=/usr/share/novnc
[ -d "$NOVNC_WEB" ] || NOVNC_WEB=/usr/share/webapps/novnc
( websockify --web="$NOVNC_WEB" 6080 localhost:5900 >/tmp/novnc.log 2>&1 & ) || true

echo "[entrypoint] DISPLAY :99 up — noVNC at http://localhost:6080/vnc.html"
echo "[entrypoint] flow=${RB_CMD} platform=${PLATFORM} profile=${PROFILE:-/tmp/profiles/$PLATFORM}"
exec npx tsx "scripts/${RB_CMD}.ts"

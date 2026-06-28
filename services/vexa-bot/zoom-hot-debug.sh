#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Zoom LFX hot-debug loop — faithful container + noVNC + CDP + real transcription
# ─────────────────────────────────────────────────────────────────────────────
# Debugs the two failure surfaces we care about: (1) the bot JOINING the meeting
# (the LFX portal needs a human to click through — watch + click via noVNC), and
# (2) the bot LISTENING (audio → real whisperlive via transcription-lb → segments).
#
# Faithful: same prod image (vexaai/vexa-bot:dev), real PulseAudio/Xvfb pipeline,
# real transcription backend. Hot: core/dist is mounted, so `npm run build` on the
# host + `./zoom-hot-debug.sh restart` reloads your TypeScript changes in seconds —
# no image rebuild.
#
# Wiring (all on docker network `vexa-network`):
#   redis          → vexa-hot-redis        (dedicated, command + segment bus)
#   transcription  → http://transcription-lb/v1/audio/transcriptions (live GPU whisperlive)
#   noVNC          → http://localhost:16080/vnc.html  (watch the browser, click the LFX portal)
#   CDP            → in-container (Chromium binds 127.0.0.1:9222, so hot-debug.js runs
#                    via `docker exec` inside the bot container): inspect/chat/speaker/eval
#
# Commands:
#   ./zoom-hot-debug.sh run        # join the meeting (explicit — bot enters the room)
#   ./zoom-hot-debug.sh logs       # follow bot logs
#   ./zoom-hot-debug.sh inspect    # CDP DOM/chat/speaker snapshot (via hot-debug.js)
#   ./zoom-hot-debug.sh speaker    # current active speaker
#   ./zoom-hot-debug.sh shot       # screenshot → /tmp/bot-debug-screenshot.jpg
#   ./zoom-hot-debug.sh segments   # live-tail transcription segments (proof of LISTENING)
#   ./zoom-hot-debug.sh leave      # graceful leave via redis command
#   ./zoom-hot-debug.sh restart    # rebuild dist + relaunch (hot-reload your edits)
#   ./zoom-hot-debug.sh stop       # force-kill the container
#   ./zoom-hot-debug.sh selfcheck  # validate plumbing against a DUMMY url (never the LFX room)
#
# Override anything via env, e.g.:
#   ZOOM_MEETING_URL=... BOT_NAME="My Bot" ./zoom-hot-debug.sh run
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$SCRIPT_DIR/core"

# ── config (override via env) ────────────────────────────────────────────────
MEETING_URL="${ZOOM_MEETING_URL:-https://zoom-lfx.platform.linuxfoundation.org/meeting/92446951537?password=68dbd899-2ada-41cf-800d-98249f2f8567}"
BOT_NAME="${BOT_NAME:-Vexa HotDebug}"
CONTAINER_NAME="${CONTAINER_NAME:-vexa-bot-hot}"
NETWORK="${DOCKER_NETWORK:-vexa-network}"
IMAGE="${BOT_IMAGE:-vexaai/vexa-bot:dev}"
REDIS_CONTAINER="${REDIS_CONTAINER:-vexa-hot-redis}"
NOVNC_PORT="${NOVNC_PORT:-16080}"   # host port (6080 is taken host-side); → container 6080
# CDP is NOT published: recent Chromium ignores --remote-debugging-address and binds
# 127.0.0.1:9222, so we attach hot-debug.js from INSIDE the container via `docker exec`.
MEETING_DB_ID="${MEETING_DB_ID:-999999}"   # internal numeric id → redis command channel
CONNECTION_ID="${CONNECTION_ID:-hot-debug}"
REDIS_URL="redis://${REDIS_CONTAINER}:6379/0"
TRANSCRIPTION_URL="http://transcription-lb/v1/audio/transcriptions"
LEAVE_CHANNEL="bot_commands:meeting:${MEETING_DB_ID}"

# Native meeting id: the /meeting/<id> segment for LFX, or /j/<id> for canonical zoom.
NATIVE_ID="$(printf '%s' "$MEETING_URL" | sed -n -E 's#.*/(meeting|j)/([0-9]+).*#\2#p')"
[ -z "$NATIVE_ID" ] && NATIVE_ID="zoom-hot-debug"

bot_config() {
  local url="$1"
  cat <<JSON | python3 -c 'import sys,json;print(json.dumps(json.load(sys.stdin)))'
{
  "platform": "zoom",
  "meetingUrl": "$url",
  "botName": "$BOT_NAME",
  "token": "debug-token",
  "connectionId": "$CONNECTION_ID",
  "nativeMeetingId": "$NATIVE_ID",
  "meeting_id": $MEETING_DB_ID,
  "redisUrl": "$REDIS_URL",
  "transcriptionServiceUrl": "$TRANSCRIPTION_URL",
  "automaticLeave": { "waitingRoomTimeout": 3600000, "noOneJoinedTimeout": 3600000, "everyoneLeftTimeout": 3600000 }
}
JSON
}

ensure_redis() {
  if ! docker ps --format '{{.Names}}' | grep -qx "$REDIS_CONTAINER"; then
    echo "↻ starting $REDIS_CONTAINER on $NETWORK"
    docker rm -f "$REDIS_CONTAINER" >/dev/null 2>&1 || true
    docker run -d --name "$REDIS_CONTAINER" --network "$NETWORK" redis:alpine >/dev/null
  fi
}

launch() {
  local url="$1"
  ensure_redis
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  echo "🤖 $CONTAINER_NAME → $url"
  echo "   noVNC : http://localhost:${NOVNC_PORT}/vnc.html   (watch + click the LFX portal)"
  echo "   CDP   : ./zoom-hot-debug.sh inspect|speaker|chat|eval   (runs in-container)"
  docker run -d --name "$CONTAINER_NAME" \
    --platform linux/amd64 \
    --network "$NETWORK" \
    -p "${NOVNC_PORT}:6080" \
    -v "$CORE_DIR/dist:/app/vexa-bot/core/dist" \
    -v "$SCRIPT_DIR/hot-debug.js:/app/vexa-bot/core/hot-debug.js:ro" \
    -e "BOT_CONFIG=$(bot_config "$url")" \
    -e BOT_DEBUG_CDP=true \
    -e LOG_LEVEL=DEBUG \
    --cap-add=SYS_ADMIN \
    --shm-size=2g \
    "$IMAGE" >/dev/null
  echo "✅ up. Follow logs:  ./zoom-hot-debug.sh logs"
}

# Chromium's CDP is loopback-only inside the container, so run hot-debug.js there.
cdp() { docker exec "$CONTAINER_NAME" node /app/vexa-bot/core/hot-debug.js 9222 "$@"; }

case "${1:-run}" in
  run)       launch "$MEETING_URL" ;;
  logs)      docker logs -f "$CONTAINER_NAME" ;;
  inspect)   cdp inspect ;;
  speaker)   cdp speaker ;;
  shot)      cdp screenshot "${2:-/tmp/bot-debug-screenshot.jpg}" ;;
  chat)      shift; cdp chat-send "$@" ;;
  eval)      shift; cdp eval "$@" ;;
  segments)
    echo "🎧 live transcription segments → meeting:${MEETING_DB_ID}:segments  (Ctrl-C to stop)"
    docker run --rm --network "$NETWORK" redis:alpine \
      redis-cli -h "$REDIS_CONTAINER" SUBSCRIBE "meeting:${MEETING_DB_ID}:segments"
    ;;
  leave)
    echo "📡 leave → $LEAVE_CHANNEL"
    docker run --rm --network "$NETWORK" redis:alpine \
      redis-cli -h "$REDIS_CONTAINER" PUBLISH "$LEAVE_CHANNEL" '{"action":"leave"}'
    ;;
  restart)
    echo "🔁 rebuild dist + relaunch"
    ( cd "$CORE_DIR" && (npx tsc || true) \
        && cp -r src/services/hallucinations dist/services/hallucinations 2>/dev/null \
        && node build-browser-utils.js >/dev/null )
    launch "$MEETING_URL"
    ;;
  stop)      docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 && echo "🛑 stopped" || echo "(not running)" ;;
  selfcheck)
    # Validate the WHOLE rig without touching the real LFX room: point at an
    # invalid canonical-zoom URL so the bot stands up Xvfb/noVNC/CDP and sits in
    # the "meeting link invalid / host not started" retry loop. Then probe.
    DUMMY="https://zoom.us/j/0000000000?pwd=selfcheck"
    echo "🧪 selfcheck against DUMMY (not the LFX meeting): $DUMMY"
    launch "$DUMMY"
    echo "⏳ waiting for browser + services…"; sleep 22
    echo "─ container:"; docker ps --filter name="$CONTAINER_NAME" --format '   {{.Names}} {{.Status}}'
    echo "─ noVNC :${NOVNC_PORT}:"; curl -s -o /dev/null -w '   HTTP %{http_code}\n' "http://localhost:${NOVNC_PORT}/vnc.html" || echo "   unreachable"
    echo "─ CDP (in-container /json/version):"; docker exec "$CONTAINER_NAME" sh -c 'curl -s http://localhost:9222/json/version 2>/dev/null || wget -qO- http://localhost:9222/json/version' | python3 -c 'import sys,json;d=json.load(sys.stdin);print("   ",d.get("Browser","?"))' 2>/dev/null || echo "   unreachable"
    echo "─ redis from container net:"; docker run --rm --network "$NETWORK" redis:alpine redis-cli -h "$REDIS_CONTAINER" ping | sed 's/^/   /'
    echo "─ transcription-lb from container net:"; docker run --rm --network "$NETWORK" redis:alpine sh -c 'wget -qO- --timeout=4 http://transcription-lb/health' | python3 -c 'import sys,json;d=json.load(sys.stdin);print("   healthy:",d.get("status"),d.get("model"),d.get("device"))' 2>/dev/null || echo "   unreachable"
    echo "─ CDP hot-debug.js attach (in-container):"; cdp inspect 2>&1 | head -3 | sed 's/^/   /' || echo "   attach failed"
    echo "🧹 tearing down selfcheck container"; docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    echo "✅ selfcheck done"
    ;;
  *) echo "unknown command: $1"; grep -E '^#   \./' "$0" | sed 's/^# //'; exit 1 ;;
esac

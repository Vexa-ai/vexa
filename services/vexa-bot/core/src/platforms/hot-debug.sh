#!/bin/bash

# Unified Platform Hot-Reload Debug Script (URL-only)
# Usage:
#   ./hot-debug.sh <meeting-url>   # auto-detects platform from URL

set -e

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../../.." && pwd))"
DEBUG_DIR="$REPO_ROOT/debug"
mkdir -p "$DEBUG_DIR"

echo "📍 REPO_ROOT: $REPO_ROOT"
echo "📍 DEBUG_DIR: $DEBUG_DIR"

# Require URL and auto-detect platform
if [[ -z "$1" ]]; then
  echo "❌ Usage: $0 <meeting-url>"
  exit 1
fi

MEETING_URL="$1"
case "$MEETING_URL" in
  *"meet.google.com"*) PLATFORM="google" ;;
  *"teams.live.com"*|*"microsoft.com"*) PLATFORM="teams" ;;
  *)
    echo "❌ Cannot detect platform from URL:"
    echo "    $MEETING_URL"
    echo "   Expected domains: meet.google.com or teams.live.com"
    exit 1
    ;;
esac

# Single hot-bot identity (assumes one hot bot at a time)
CONTAINER_NAME="${CONTAINER_NAME:-vexa-bot-hot}"
CONNECTION_ID="${CONNECTION_ID:-hot-debug}"
REDIS_CHANNEL="${REDIS_CHANNEL:-bot_commands:hot-debug}"
BOT_NAME="${BOT_NAME:-HotDebugBot}"

# Platform-specific minor details and extract meeting ID from URL
if [[ "$PLATFORM" == "google" ]]; then
  PLATFORM_CONFIG="google_meet"
  ADMISSION_SCREENSHOT="bot-checkpoint-2-admitted.png"
  # Extract Google Meet code (e.g., abc-defg-hij from meet.google.com/abc-defg-hij)
  MEETING_ID=$(echo "$MEETING_URL" | sed -n 's|.*meet.google.com/\([^?]*\).*|\1|p')
  [ -z "$MEETING_ID" ] && MEETING_ID="google-hot-debug-$(date +%s)"
else
  PLATFORM_CONFIG="teams"
  ADMISSION_SCREENSHOT="teams-status-startup.png"
  # Extract Teams meeting ID (e.g., 9367932910098 from teams.live.com/meet/9367932910098)
  MEETING_ID=$(echo "$MEETING_URL" | sed -n 's|.*meet/\([0-9]*\).*|\1|p')
  [ -z "$MEETING_ID" ] && MEETING_ID="teams-hot-debug-$(date +%s)"
fi

# Configuration
IMAGE_NAME="vexa-bot:test"
DOCKER_NETWORK="${DOCKER_NETWORK:-vexa_dev_vexa_default}"

# Resolve core/dist for bind mount
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"  # core root
DIST_DIR="$ROOT_DIR/dist"                    # core/dist (built output)

# Run directory (repo-relative debug/)
RUN_DIR="$DEBUG_DIR/screenshots/run-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"
SCREENSHOTS_DIR="$RUN_DIR"

echo "🔥 Starting $PLATFORM Hot-Reload Debug"
echo "📸 Screenshots: $SCREENSHOTS_DIR"

# Clean up any existing container
echo "🧹 Cleaning up existing container if present..."
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

# Make sure the image exists
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  echo "❌ Image $IMAGE_NAME not found. Build it once first: make build"
  exit 1
fi

# Check if dist exists (rebuild manually if needed: make rebuild)
if [ ! -d "$DIST_DIR" ]; then
  echo "❌ Dist directory not found at $DIST_DIR"
  echo "    Run 'make rebuild' or 'cd core && npm run build' first"
  exit 1
fi

echo "🤖 Running $PLATFORM bot container with bind mounts (hot-reload)..."
docker run --rm --name "$CONTAINER_NAME" \
  --network "$DOCKER_NETWORK" \
  -v "$SCREENSHOTS_DIR:/app/storage/screenshots" \
  -v "$DIST_DIR:/app/dist" \
  -e BOT_CONFIG='{
    "platform":"'"$PLATFORM_CONFIG"'",
    "meetingUrl":"'"$MEETING_URL"'",
    "botName":"'"$BOT_NAME"'",
    "connectionId":"'"$CONNECTION_ID"'",
    "nativeMeetingId":"'"$MEETING_ID"'",
    "meeting_id":999,
    "token":"debug-token",
    "redisUrl":"redis://redis:6379/0",
    "container_name":"'"$CONTAINER_NAME"'",
    "automaticLeave":{
      "waitingRoomTimeout":300000,
      "noOneJoinedTimeout":600000,
      "everyoneLeftTimeout":120000
    }
  }' \
  -e WHISPER_LIVE_URL="ws://whisperlive-remote:9090" \
  -e WL_MAX_CLIENTS="10" \
  -e LOG_LEVEL="DEBUG" \
  --cap-add=SYS_ADMIN \
  --shm-size=2g \
  "$IMAGE_NAME" &

BOT_PID=$!

echo "🚀 Bot container started with PID: $BOT_PID"
echo "⏳ Waiting for bot to join and be admitted to the meeting..."
echo "📸 Monitoring for bot admission..."

ADMISSION_TIMEOUT=30
ADMISSION_CHECK_INTERVAL=5
elapsed=0

while [ $elapsed -lt $ADMISSION_TIMEOUT ]; do
  if [ -f "$SCREENSHOTS_DIR/$ADMISSION_SCREENSHOT" ]; then
    echo "✅ Bot admitted to meeting! Found admission screenshot."
    break
  fi

  if ! docker ps --format "table {{.Names}}" | grep -q "$CONTAINER_NAME"; then
    echo "❌ Bot container stopped unexpectedly before admission"
    echo "📋 Bot logs:"
    docker logs "$CONTAINER_NAME" 2>&1 || echo "(Container already removed)"
    echo ""
    echo "📸 Screenshots directory: $SCREENSHOTS_DIR"
    ls -la "$SCREENSHOTS_DIR" 2>/dev/null || echo "  (empty or not accessible)"
    wait $BOT_PID
    exit 1
  fi

  echo "⏳ Still waiting for admission... (${elapsed}s elapsed)"
  sleep $ADMISSION_CHECK_INTERVAL
  elapsed=$((elapsed + ADMISSION_CHECK_INTERVAL))
done

if [ $elapsed -ge $ADMISSION_TIMEOUT ]; then
  echo "⏰ Timeout waiting for bot admission. Proceeding with Redis command test anyway..."
fi

# Persist state for convenience commands
STATE_FILE="$DEBUG_DIR/current.json"
cat > "$STATE_FILE" <<EOF
{ "platform": "$PLATFORM", "meetingUrl": "$MEETING_URL", "connectionId": "$CONNECTION_ID", "channel": "$REDIS_CHANNEL", "container": "$CONTAINER_NAME", "network": "$DOCKER_NETWORK", "screenshots": "$SCREENSHOTS_DIR" }
EOF

echo ""
echo "🎯 Bot is now active! Testing automatic graceful leave..."
echo "⏳ Waiting 5 seconds then triggering graceful leave for testing..."
sleep 5

echo "📡 Sending Redis leave command for testing..."
docker run --rm --network "$DOCKER_NETWORK" \
  redis:alpine redis-cli -h redis -p 6379 \
  PUBLISH "$REDIS_CHANNEL" '{"action":"leave"}'

echo "⏳ Monitoring for graceful shutdown..."
SHUTDOWN_TIMEOUT=30
shutdown_elapsed=0
while [ $shutdown_elapsed -lt $SHUTDOWN_TIMEOUT ]; do
  if ! docker ps --format "table {{.Names}}" | grep -q "$CONTAINER_NAME"; then
    echo "✅ Bot container gracefully stopped after ${shutdown_elapsed} seconds!"
    break
  else
    echo "⏳ Still running... (${shutdown_elapsed}s elapsed)"
    sleep 2
    shutdown_elapsed=$((shutdown_elapsed + 2))
  fi
done

if [ $shutdown_elapsed -ge $SHUTDOWN_TIMEOUT ]; then
  echo "❌ Bot did not stop within ${SHUTDOWN_TIMEOUT} seconds"
  echo "🔍 Checking bot logs..."
  docker logs "$CONTAINER_NAME" --tail 100 | grep -E "leave|shutdown|graceful" || true
fi

echo "🎉 Automatic graceful leave test completed!"

# Cleanup function
cleanup_and_exit() {
    echo "🧹 Cleaning up..."
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    exit ${1:-0}
}

# Set up signal handler for Ctrl+C
cleanup_on_interrupt() {
    echo ""
    echo "🛑 Interrupt received! Sending Redis leave command..."
    
    echo "📡 Sending 'leave' command via Redis..."
    docker run --rm --network "$DOCKER_NETWORK" \
      redis:alpine redis-cli -h redis -p 6379 \
      PUBLISH "$REDIS_CHANNEL" '{"action":"leave"}'
    
    echo "⏳ Monitoring for graceful shutdown..."
    SHUTDOWN_TIMEOUT=30
    shutdown_elapsed=0
    while [ $shutdown_elapsed -lt $SHUTDOWN_TIMEOUT ]; do
      if ! docker ps --format "table {{.Names}}" | grep -q "$CONTAINER_NAME"; then
        echo "✅ Bot container gracefully stopped after ${shutdown_elapsed} seconds!"
        break
      else
        echo "⏳ Still running... (${shutdown_elapsed}s elapsed)"
        sleep 2
        shutdown_elapsed=$((shutdown_elapsed + 2))
      fi
    done
    
    if [ $shutdown_elapsed -ge $SHUTDOWN_TIMEOUT ]; then
      echo "❌ Bot did not stop within ${SHUTDOWN_TIMEOUT} seconds"
      echo "🔍 Checking bot logs..."
      docker logs "$CONTAINER_NAME" --tail 100 | grep -E "leave|shutdown|graceful" || true
    fi
    
    echo "🎉 Manual stop completed!"
    cleanup_and_exit 0
}

# Register signal handler
trap cleanup_on_interrupt INT

echo "🧪 Verifying Redis connectivity..."
docker run --rm --network "$DOCKER_NETWORK" redis:alpine redis-cli -h redis -p 6379 PING

echo "🔎 Checking for subscriber on channel: $REDIS_CHANNEL"
NUMSUB=$(docker run --rm --network "$DOCKER_NETWORK" redis:alpine redis-cli -h redis -p 6379 PUBSUB NUMSUB "$REDIS_CHANNEL" | awk 'NR==2{print $2}')
echo "🔎 PUBSUB NUMSUB $REDIS_CHANNEL => $NUMSUB"

if [ "${NUMSUB:-0}" -ge 1 ]; then
  echo "✅ Subscriber present - Redis command ready!"
else
  echo "❌ No subscriber detected - Redis command may not work"
fi

echo ""
echo "🤖 Bot is running and ready for manual control"
echo "📊 Bot logs (press Ctrl+C to stop):"
echo "----------------------------------------"

# Follow bot logs until interrupted
docker logs -f "$CONTAINER_NAME"

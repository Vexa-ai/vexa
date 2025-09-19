#!/bin/bash

# Unified Platform Hot-Reload Debug Script
# Runs bot containers with bind mounts so you can live-edit code
# without rebuilding the image. Supports both Google Meet and Teams.
#
# Usage:
#   ./hot-debug.sh google [meeting-url]
#   ./hot-debug.sh teams [meeting-url]

set -e

# Check platform argument
PLATFORM="${1}"
if [[ "$PLATFORM" != "google" && "$PLATFORM" != "teams" ]]; then
  echo "❌ Usage: $0 <google|teams> [meeting-url]"
  echo "   Examples:"
  echo "     $0 google https://meet.google.com/abc-defg-hij"
  echo "     $0 teams https://teams.live.com/meet/123456789"
  exit 1
fi

# Platform-specific configuration
if [[ "$PLATFORM" == "google" ]]; then
  CONTAINER_NAME="vexa-bot-google-hot"
  PLATFORM_CONFIG="google_meet"
  BOT_NAME="GoogleDebugBot"
  CONNECTION_ID="google-hot-debug"
  MEETING_ID="google-debug-meeting"
  DEFAULT_URL="https://meet.google.com/kba-qqag-vpq"
  ADMISSION_SCREENSHOT="bot-checkpoint-2-admitted.png"
  REDIS_CHANNEL="bot_commands:google-hot-debug"
else
  CONTAINER_NAME="vexa-bot-teams-hot"
  PLATFORM_CONFIG="teams"
  BOT_NAME="TeamsDebugBot"
  CONNECTION_ID="teams-hot-debug"
  MEETING_ID="9327884808517"
  DEFAULT_URL="https://teams.live.com/meet/9342205715849?p=1Tw4SOPN4ZfYgCKRcQ"
  ADMISSION_SCREENSHOT="teams-status-startup.png"
  REDIS_CHANNEL="bot_commands:teams-hot-debug"
fi

# Configuration
IMAGE_NAME="vexa-bot:test"
SCREENSHOTS_DIR="/home/dima/dev/bot-storage/screenshots/run-$(date +%Y%m%d-%H%M%S)"
MEETING_URL="${2:-$DEFAULT_URL}"

echo "🔥 Starting $PLATFORM Hot-Reload Debug"

# Create screenshots directory for this run
echo "📁 Creating screenshots directory: $SCREENSHOTS_DIR"
mkdir -p "$SCREENSHOTS_DIR"

# Clean up any existing container
echo "🧹 Cleaning up existing container if present..."
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

# Make sure the image exists
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  echo "❌ Image $IMAGE_NAME not found. Build it once first."
  exit 1
fi

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"  # core root
DIST_DIR="$ROOT_DIR/dist"                    # core/dist (built output)

# Ensure fresh code by rebuilding dist files
echo "🔄 Rebuilding dist files to ensure fresh code..."
echo "📍 ROOT_DIR: $ROOT_DIR"
cd "$ROOT_DIR"
npm run build
echo "✅ Dist files rebuilt"

if [ ! -d "$DIST_DIR" ]; then
  echo "❌ Dist directory not found at $DIST_DIR after rebuild."
  exit 1
fi

echo "🤖 Running $PLATFORM bot container with bind mounts (hot-reload)..."

# Start the bot container in the background
docker run --rm --name "$CONTAINER_NAME" \
  --network vexa_dev_vexa_default \
  -v "$SCREENSHOTS_DIR:/app/storage/screenshots" \
  -v "$DIST_DIR:/app/dist" \
  -e BOT_CONFIG='{
    "platform":"'$PLATFORM_CONFIG'",
    "meetingUrl":"'$MEETING_URL'",
    "botName":"'$BOT_NAME'",
    "connectionId":"'$CONNECTION_ID'",
    "nativeMeetingId":"'$MEETING_ID'",
    "token":"debug-token",
    "redisUrl":"redis://redis:6379/0",
    "container_name":"'$CONTAINER_NAME'",
    "automaticLeave":{
      "waitingRoomTimeout":300000,
      "noOneJoinedTimeout":600000,
      "everyoneLeftTimeout":120000
    }
  }' \
  -e WHISPER_LIVE_URL="ws://whisperlive:9090" \
  -e WL_MAX_CLIENTS="10" \
  --cap-add=SYS_ADMIN \
  --shm-size=2g \
  "$IMAGE_NAME" &

BOT_PID=$!

echo "🚀 Bot container started with PID: $BOT_PID"
echo "⏳ Waiting for bot to join and be admitted to the meeting..."

# Wait for bot to be admitted (check for startup callback or screenshots)
echo "📸 Monitoring for bot admission..."
ADMISSION_TIMEOUT=30  # 30 seconds timeout
ADMISSION_CHECK_INTERVAL=5  # Check every 5 seconds
elapsed=0

while [ $elapsed -lt $ADMISSION_TIMEOUT ]; do
  # Check if startup screenshot exists (indicates bot is admitted)
  if [ -f "$SCREENSHOTS_DIR/$ADMISSION_SCREENSHOT" ]; then
    echo "✅ Bot admitted to meeting! Found admission screenshot."
    break
  fi
  
  # Check if container is still running
  if ! docker ps --format "table {{.Names}}" | grep -q "$CONTAINER_NAME"; then
    echo "❌ Bot container stopped unexpectedly before admission"
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

echo ""
echo "🎯 Bot is now active! Testing automatic graceful leave..."
echo "⏳ Waiting 5 seconds then triggering graceful leave for testing..."
sleep 5

echo "📡 Sending Redis leave command for testing..."
docker run --rm --network vexa_dev_vexa_default \
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
cleanup_and_exit 0

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
    
    # Send Redis leave command
    echo "📡 Sending 'leave' command via Redis..."
    docker run --rm --network vexa_dev_vexa_default \
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
docker run --rm --network vexa_dev_vexa_default redis:alpine redis-cli -h redis -p 6379 PING

echo "🔎 Checking for subscriber on channel: $REDIS_CHANNEL"
NUMSUB=$(docker run --rm --network vexa_dev_vexa_default redis:alpine redis-cli -h redis -p 6379 PUBSUB NUMSUB "$REDIS_CHANNEL" | awk 'NR==2{print $2}')
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

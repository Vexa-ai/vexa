#!/bin/bash

# Teams Hot-Reload Debug Script
# Runs the Teams bot container with bind mounts so you can live-edit code
# without rebuilding the image. Pair this with `node dev-watch.js` to rebuild
# dist/browser-utils.global.js on the host automatically.

set -e

# Configuration
CONTAINER_NAME="vexa-bot-teams-hot"
IMAGE_NAME="vexa-bot:test"
SCREENSHOTS_DIR="/home/dima/dev/bot-storage/screenshots/run-$(date +%Y%m%d-%H%M%S)"
MEETING_URL="https://teams.live.com/meet/9327884808517?p=zCmPHnrCLiXtY5atOp"

echo "🔥 Starting Teams Hot-Reload Debug"

# Create screenshots directory for this run
echo "📁 Creating screenshots directory: $SCREENSHOTS_DIR"
mkdir -p "$SCREENSHOTS_DIR"

# Clean up any existing container
echo "🧹 Cleaning up existing container if present..."
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

# NOTE: We do NOT rebuild the image here to avoid slow cycles.
# Make sure the image exists (built once via teams-debug-test.sh)
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  echo "❌ Image $IMAGE_NAME not found. Build it once via teams-debug-test.sh first."
  exit 1
fi

# Resolve paths
ROOT_DIR="$(cd ../../.. && pwd)"                 # core root
TESTS_DIR="$ROOT_DIR/src/tests"                   # core/src/tests
DIST_DIR="$ROOT_DIR/dist"                         # core/dist (built output)

# Ensure fresh code by rebuilding dist files
echo "🔄 Rebuilding dist files to ensure fresh code..."
cd "$ROOT_DIR"
npm run build
echo "✅ Dist files rebuilt"

if [ ! -d "$DIST_DIR" ]; then
  echo "❌ Dist directory not found at $DIST_DIR after rebuild."
  exit 1
fi

echo "🤖 Running Teams bot container with bind mounts (hot-reload)..."
cd "$TESTS_DIR"

# Start the bot container in the background
docker run --rm --name "$CONTAINER_NAME" \
  --network vexa_dev_vexa_default \
  -v "$SCREENSHOTS_DIR:/app/storage/screenshots" \
  -v "$DIST_DIR:/app/dist" \
  -e BOT_CONFIG='{
    "platform":"teams",
    "meetingUrl":"'$MEETING_URL'",
    "botName":"TeamsDebugBot",
    "connectionId":"teams-hot-debug",
    "nativeMeetingId":"9327884808517",
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
  if [ -f "$SCREENSHOTS_DIR/teams-status-startup.png" ]; then
    echo "✅ Bot admitted to meeting! Found startup screenshot."
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
  PUBLISH "bot_commands:teams-hot-debug" '{"action":"leave"}'

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

# Set up signal handler for Ctrl+C
cleanup_on_interrupt() {
    echo ""
    echo "🛑 Interrupt received! Sending Redis leave command..."
    
    # Send Redis leave command
    echo "📡 Sending 'leave' command via Redis..."
    docker run --rm --network vexa_dev_vexa_default \
      redis:alpine redis-cli -h redis -p 6379 \
      PUBLISH "bot_commands:teams-hot-debug" '{"action":"leave"}'
    
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

echo "🔎 Checking for subscriber on channel: bot_commands:teams-hot-debug"
NUMSUB=$(docker run --rm --network vexa_dev_vexa_default redis:alpine redis-cli -h redis -p 6379 PUBSUB NUMSUB "bot_commands:teams-hot-debug" | awk 'NR==2{print $2}')
echo "🔎 PUBSUB NUMSUB bot_commands:teams-hot-debug => $NUMSUB"

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



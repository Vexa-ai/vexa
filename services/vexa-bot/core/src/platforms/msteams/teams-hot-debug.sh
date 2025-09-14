#!/bin/bash

# Teams Hot-Reload Debug Script
# Runs the Teams bot container with bind mounts so you can live-edit code
# without rebuilding the image. Pair this with `node dev-watch.js` to rebuild
# dist/browser-utils.global.js on the host automatically.

set -e

# Configuration
CONTAINER_NAME="vexa-bot-teams-hot"
IMAGE_NAME="vexa-bot:test"
SCREENSHOTS_DIR="../teams_docs/screenshots"
MEETING_URL="https://teams.live.com/meet/9370706962617?p=VT7gUvkrKTQTPgPcns"

echo "🔥 Starting Teams Hot-Reload Debug"

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

if [ ! -d "$DIST_DIR" ]; then
  echo "❌ Dist directory not found at $DIST_DIR. Build the bundle at least once (e.g., ./quick-test.sh)."
  exit 1
fi

echo "🤖 Running Teams bot container with bind mounts (hot-reload)..."
cd "$TESTS_DIR"
docker run --rm --name "$CONTAINER_NAME" \
  --network vexa_dev_vexa_default \
  -v "$(pwd)/$SCREENSHOTS_DIR:/app/screenshots" \
  -v "$DIST_DIR:/app/dist" \
  -e BOT_CONFIG='{
    "platform":"teams",
    "meetingUrl":"'$MEETING_URL'",
    "botName":"TeamsDebugBot",
    "connectionId":"teams-hot-debug",
    "nativeMeetingId":"9370706962617",
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
  "$IMAGE_NAME"

echo "✅ Container exited"



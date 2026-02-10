#!/bin/bash
# Set up Zoom SDK library paths
SDK_LIB_DIR="/app/vexa-bot/core/src/platforms/zoom/native/zoom_meeting_sdk"
if [ -f "${SDK_LIB_DIR}/libmeetingsdk.so" ]; then
  export LD_LIBRARY_PATH="${SDK_LIB_DIR}:${SDK_LIB_DIR}/qt_libs:${SDK_LIB_DIR}/qt_libs/Qt/lib:${LD_LIBRARY_PATH}"
fi

# Start a virtual framebuffer in the background
Xvfb :99 -screen 0 1920x1080x24 &

# Ensure browser utils bundle exists (defensive in case of stale layer pulls)
if [ ! -f "/app/dist/browser-utils.global.js" ]; then
  echo "[Entrypoint] browser-utils.global.js missing; regenerating..."
  node /app/build-browser-utils.js || echo "[Entrypoint] Failed to regenerate browser-utils.global.js"
fi

# Finally, run the bot using the built production wrapper
# This wrapper (e.g., docker.js generated from docker.ts) will read the BOT_CONFIG env variable.
node dist/docker.js

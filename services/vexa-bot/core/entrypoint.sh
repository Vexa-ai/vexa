#!/bin/bash
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

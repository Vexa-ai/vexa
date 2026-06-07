#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# LITE-ONLY per-bot display wrapper.
#
# vexa-lite runs every bot as an in-process child inside one container (the
# `process` orchestrator backend), so all bots otherwise share ONE X display
# (:99). That is fine for CDP-driven clicks, but our humanized join uses
# OS-level XTEST input, which is screen-GLOBAL: with two bots on :99 the second
# bot's pointer/clicks land on the first bot's window, so it can never click its
# own "Ask to join" → it stalls in unknown_blocking_state / needs_human_help.
# (compose/helm don't hit this: each bot is its own container with its own :99.)
#
# Fix, contained entirely to the lite image: give each in-process bot its OWN
# Xvfb display, then hand off to the UNMODIFIED core entrypoint. The core bot
# already honours $DISPLAY (`export DISPLAY="${DISPLAY:-:99}"`), so no core code
# changes — this wrapper is baked over /app/vexa-bot/entrypoint.sh by
# deploy/lite/Dockerfile.lite ONLY. compose/helm bot images are untouched.
#
# Display claim is race-safe: Xvfb refuses to start on an in-use display (atomic
# /tmp/.X<n>-lock), so concurrent bots each win a distinct display. The per-bot
# Xvfb is a child of the bot's process group, so it dies on stop (killpg).
# ─────────────────────────────────────────────────────────────────────────────
set -u

LOG_DIR="${VEXA_BOT_LOG_DIR:-/var/log/vexa-bots}"
mkdir -p "$LOG_DIR" 2>/dev/null || true

# Only self-provision a display if we're on the shared default (:99) or unset.
# If something upstream already handed us a dedicated display, respect it.
if [ "${DISPLAY:-:99}" = ":99" ]; then
  claimed=""
  for n in $(seq 101 199); do
    Xvfb ":$n" -screen 0 1920x1080x24 -ac -nolisten tcp >"$LOG_DIR/xvfb-$n.log" 2>&1 &
    xpid=$!
    sleep 1
    if kill -0 "$xpid" 2>/dev/null && [ -e "/tmp/.X$n-lock" ]; then
      export DISPLAY=":$n"
      claimed=":$n"
      # Derive a per-bot CDP + relay port from the slot so concurrent bots don't
      # collide on Chrome's --remote-debugging-port (9222) — that collision is FATAL
      # (Chrome: "Cannot start http server for devtools" → launch timeout → bot dies).
      # constans.js/index.js in the lite image are patched (Dockerfile.lite) to read
      # these env vars; defaults (9222/9223) keep container mode unchanged.
      slot=$(( n - 100 ))
      export VEXA_CDP_PORT=$(( 9222 + slot * 10 ))
      export VEXA_RELAY_PORT=$(( 9223 + slot * 10 ))
      echo "[lite-slot] bot DISPLAY=:$n CDP=$VEXA_CDP_PORT RELAY=$VEXA_RELAY_PORT (xvfb pid $xpid)"
      break
    fi
    kill "$xpid" 2>/dev/null
  done
  if [ -z "$claimed" ]; then
    echo "[lite-slot] WARNING: no free display in :101-:199; falling back to shared ${DISPLAY:-:99}"
  fi
fi

# Hand off to the real, unmodified core entrypoint (renamed at image build time).
exec /app/vexa-bot/entrypoint.real.sh "$@"

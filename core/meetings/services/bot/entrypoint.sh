#!/bin/bash
# @vexa/bot entrypoint — bring up the X11 + PulseAudio environment the browser /
# capture / speak path expects, then run the worker (boot → join → ... → die).
#
# Mirrors the production bot's meeting-mode bringup (services/vexa-bot/core/
# entrypoint.sh), trimmed to what the v0.12 carved bot needs:
#   • Xvfb on :99           — a display for the headful Chromium @vexa/remote-browser launches.
#   • fluxbox               — a WM so the browser window manages cleanly.
#   • PulseAudio            — the audio graph: tts_sink → virtual_mic (speak) + a null sink.
# The worker itself reads VEXA_BOT_CONFIG (invocation.v1) and drives the rest.
set -u

export DISPLAY="${DISPLAY:-:99}"

echo "[entrypoint] Starting Xvfb on ${DISPLAY}..."
Xvfb "${DISPLAY}" -screen 0 1920x1080x24 >/tmp/xvfb.log 2>&1 &
# Give Xvfb a moment to create the socket before anything attaches.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ -e "/tmp/.X11-unix/X${DISPLAY#:}" ] && break
  sleep 0.3
done

echo "[entrypoint] Starting fluxbox..."
fluxbox >/tmp/fluxbox.log 2>&1 &

echo "[entrypoint] Starting PulseAudio (no idle exit)..."
pulseaudio --start --exit-idle-time=-1 --log-target=syslog 2>/dev/null || true
sleep 1
# Voice/capture audio graph (best-effort; only the speak path strictly needs it).
pactl load-module module-null-sink sink_name=tts_sink \
  sink_properties=device.description="TTSAudioSink" 2>/dev/null || true
pactl load-module module-remap-source master=tts_sink.monitor source_name=virtual_mic \
  source_properties=device.description="VirtualMicrophone" 2>/dev/null || true
pactl set-default-source virtual_mic 2>/dev/null || true
pactl set-sink-mute tts_sink 1 2>/dev/null || true
pactl set-source-mute virtual_mic 1 2>/dev/null || true

# Run the worker from its package dir so the schema path (src→../../../contracts)
# and the pnpm-linked workspace deps resolve. Always emit start + exit breadcrumbs
# so an instant crash is never silent in container stdout.
cd /app/core/meetings/services/bot
echo "[entrypoint] Starting @vexa/bot worker (node dist/index.js, DISPLAY=${DISPLAY})..."
node dist/index.js
EXIT_CODE=$?
echo "[entrypoint] @vexa/bot worker exited with code ${EXIT_CODE}"
exit ${EXIT_CODE}

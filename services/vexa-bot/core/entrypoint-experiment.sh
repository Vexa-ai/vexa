#!/bin/bash
# Experiment entrypoint: same as production + agent-only mode.
# If BOT_CONFIG is set → run the bot (identical to entrypoint.sh).
# If BOT_CONFIG is absent → start Xvfb/PulseAudio and wait for agent.

# Set up Zoom SDK library paths
SDK_LIB_DIR="/app/vexa-bot/core/src/platforms/zoom/native/zoom_meeting_sdk"
if [ -f "${SDK_LIB_DIR}/libmeetingsdk.so" ]; then
  export LD_LIBRARY_PATH="${SDK_LIB_DIR}:${SDK_LIB_DIR}/qt_libs:${SDK_LIB_DIR}/qt_libs/Qt/lib:${LD_LIBRARY_PATH}"
fi

# Start a virtual framebuffer in the background
Xvfb :99 -screen 0 1920x1080x24 &

# Set up PulseAudio
echo "[Entrypoint] Starting PulseAudio daemon..."
pulseaudio --start --log-target=syslog 2>/dev/null || true
sleep 1

echo "[Entrypoint] Creating PulseAudio sinks..."
pactl load-module module-null-sink sink_name=zoom_sink sink_properties=device.description="ZoomAudioSink" 2>/dev/null || true
pactl load-module module-null-sink sink_name=tts_sink sink_properties=device.description="TTSAudioSink" 2>/dev/null || true
pactl load-module module-remap-source master=tts_sink.monitor source_name=virtual_mic source_properties=device.description="VirtualMicrophone" 2>/dev/null || true
pactl set-default-source virtual_mic 2>/dev/null || true

# Configure ALSA
mkdir -p /root
cat > /root/.asoundrc <<'ALSA_EOF'
pcm.!default {
    type pulse
}
ctl.!default {
    type pulse
}
ALSA_EOF

# Ensure browser utils bundle exists
BROWSER_UTILS="/app/vexa-bot/core/dist/browser-utils.global.js"
if [ ! -f "$BROWSER_UTILS" ]; then
  echo "[Entrypoint] browser-utils.global.js missing; regenerating..."
  (cd /app/vexa-bot/core && node build-browser-utils.js) || echo "[Entrypoint] Failed to regenerate browser-utils.global.js"
fi

# Fork: bot mode vs agent-only mode
if [ -n "$BOT_CONFIG" ]; then
  echo "[Entrypoint] BOT_CONFIG set — running bot..."
  node dist/docker.js
else
  echo "[Entrypoint] No BOT_CONFIG — agent-only mode."
  echo "[Entrypoint] Xvfb + PulseAudio ready. Attach with: docker exec -it <container> claude -c"
  echo "[Entrypoint] Waiting..."
  exec sleep infinity
fi

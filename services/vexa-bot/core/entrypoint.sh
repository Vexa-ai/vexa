#!/bin/bash
# Set up Zoom SDK library paths
SDK_LIB_DIR="/app/vexa-bot/core/src/platforms/zoom/native/zoom_meeting_sdk"
if [ -f "${SDK_LIB_DIR}/libmeetingsdk.so" ]; then
  export LD_LIBRARY_PATH="${SDK_LIB_DIR}:${SDK_LIB_DIR}/qt_libs:${SDK_LIB_DIR}/qt_libs/Qt/lib:${LD_LIBRARY_PATH}"
fi

# Start a virtual framebuffer in the background
Xvfb :99 -screen 0 1920x1080x24 &

# Set up PulseAudio for Zoom SDK audio capture
echo "[Entrypoint] Starting PulseAudio daemon..."
pulseaudio --start --log-target=syslog 2>/dev/null || true
sleep 1

# Create a null sink for Zoom SDK audio output
echo "[Entrypoint] Creating PulseAudio null sink for audio capture..."
pactl load-module module-null-sink sink_name=zoom_sink sink_properties=device.description="ZoomAudioSink" 2>/dev/null || true

# Create a dedicated TTS sink for voice agent audio injection
# Audio played to tts_sink will be picked up by tts_sink.monitor (the virtual mic)
echo "[Entrypoint] Creating PulseAudio TTS sink for voice agent..."
pactl load-module module-null-sink sink_name=tts_sink sink_properties=device.description="TTSAudioSink" 2>/dev/null || true

# Create a remap source from tts_sink.monitor — this creates a proper capture device
# that Chromium can discover and use as microphone input for WebRTC / getUserMedia().
# Without this, Chromium only sees monitor sources (which it ignores for mic input).
echo "[Entrypoint] Creating virtual microphone from TTS sink monitor..."
pactl load-module module-remap-source master=tts_sink.monitor source_name=virtual_mic source_properties=device.description="VirtualMicrophone" 2>/dev/null || true
pactl set-default-source virtual_mic 2>/dev/null || true

# Configure ALSA to route through PulseAudio
echo "[Entrypoint] Configuring ALSA to use PulseAudio..."
mkdir -p /root
cat > /root/.asoundrc <<'ALSA_EOF'
pcm.!default {
    type pulse
}
ctl.!default {
    type pulse
}
ALSA_EOF

# Ensure browser utils bundle exists (defensive in case of stale layer pulls)
BROWSER_UTILS="/app/vexa-bot/core/dist/browser-utils.global.js"
if [ ! -f "$BROWSER_UTILS" ]; then
  echo "[Entrypoint] browser-utils.global.js missing; regenerating..."
  (cd /app/vexa-bot/core && node build-browser-utils.js) || echo "[Entrypoint] Failed to regenerate browser-utils.global.js"
fi

# Start socat to forward 0.0.0.0:9223 -> 127.0.0.1:9222 so CDP is reachable
# from outside the container (Playwright ignores --remote-debugging-address=0.0.0.0).
# We retry in a loop because Chromium takes a few seconds to bind CDP.
(
  echo "[Entrypoint] Starting socat CDP forwarder (0.0.0.0:9223 -> 127.0.0.1:9222)..."
  for i in $(seq 1 30); do
    if socat TCP-LISTEN:9223,fork,bind=0.0.0.0,reuseaddr TCP:127.0.0.1:9222 2>/dev/null; then
      break
    fi
    sleep 2
  done
) &

# Finally, run the bot using the built production wrapper
# This wrapper (e.g., docker.js generated from docker.ts) will read the BOT_CONFIG env variable.
node dist/docker.js

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

# Mute TTS sink AND virtual_mic source — silent until /speak explicitly unmutes.
# Muting only the sink is not enough: the remap source still passes a low-level signal
# to WebRTC, which Teams' VAD interprets as speech. Muting the source cuts it at capture level.
pactl set-sink-mute tts_sink 1 2>/dev/null || true
pactl set-source-mute virtual_mic 1 2>/dev/null || true


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

# --- Remote Browser: VNC stack for browser session mode ---
# Extract mode from BOT_CONFIG JSON (defaults to "meeting" if not set)
BOT_MODE=$(echo "$BOT_CONFIG" | node -e "try{const c=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));console.log(c.mode||'meeting')}catch{console.log('meeting')}" 2>/dev/null || echo "meeting")

if [ "$BOT_MODE" = "browser_session" ]; then
  echo "[entrypoint] Browser session mode — starting VNC stack"
  mkdir -p /root/.fluxbox
  cat > /root/.fluxbox/apps <<'FBAPPS'
[app] (name=.*) (class=.*)
  [Maximized]  {yes}
[end]
FBAPPS
  fluxbox &
  x11vnc -display :99 -forever -nopw -shared -rfbport 5900 &
  autocutsel -s PRIMARY &
  autocutsel -s CLIPBOARD &

  # websockify bridges VNC (port 5900) to web (port 6080) for noVNC
  # Use --web only if novnc is installed, otherwise plain WebSocket proxy
  if [ -d /usr/share/novnc ]; then
    websockify --web /usr/share/novnc 6080 localhost:5900 &
  else
    websockify 6080 localhost:5900 &
  fi
  echo "[entrypoint] websockify started on port 6080"

  # CDP proxy: Chromium binds CDP to 127.0.0.1 only. Socat exposes it on 0.0.0.0:9223
  # so the api-gateway can reach it from the Docker network.
  (while ! curl -s http://localhost:9222/json/version > /dev/null 2>&1; do sleep 1; done
  echo "[entrypoint] CDP ready, starting socat proxy on 0.0.0.0:9223"
  socat TCP-LISTEN:9223,fork,reuseaddr,bind=0.0.0.0 TCP:localhost:9222) &

  # SSH server — password is the session token (unique per session)
  SSH_PASS=$(echo "$BOT_CONFIG" | node -e "try{const c=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));console.log(c.session_token||'vexa')}catch{console.log('vexa')}" 2>/dev/null || echo "vexa")
  echo "root:$SSH_PASS" | chpasswd
  echo "[entrypoint] Starting SSH server on port 22"
  /usr/sbin/sshd

  # Run node and keep container alive even if node crashes
  echo "[entrypoint] Starting browser session node process..."
  node dist/docker.js
  EXIT_CODE=$?
  echo "[entrypoint] node dist/docker.js exited with code $EXIT_CODE — keeping container alive for VNC access"
  # Keep alive so VNC + websockify remain accessible
  wait
else
  # Meeting mode — start VNC for browser view on dashboard
  echo "[entrypoint] Meeting mode — starting VNC for browser view"
  mkdir -p /root/.fluxbox
  cat > /root/.fluxbox/apps <<'FBAPPS'
[app] (name=.*) (class=.*)
  [Maximized]  {yes}
[end]
FBAPPS
  fluxbox &
  x11vnc -display :99 -forever -nopw -shared -rfbport 5900 &
  if [ -d /usr/share/novnc ]; then
    websockify --web /usr/share/novnc 6080 localhost:5900 &
  else
    websockify 6080 localhost:5900 &
  fi

  # Run the bot
  node dist/docker.js
fi

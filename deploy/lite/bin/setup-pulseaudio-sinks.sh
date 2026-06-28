#!/bin/bash
# =============================================================================
# Vexa Lite — PulseAudio graph setup (runs once, after PulseAudio is up)
# =============================================================================
# Builds the audio graph the bot's capture/speak path expects: a null sink (tts_sink) whose
# monitor is remapped into a virtual microphone (virtual_mic). In the per-meeting bot
# container the bot's own entrypoint creates these; in lite the bot runs as a child process
# sharing this one system PulseAudio, so we create the graph once here. Best-effort + idempotent.
set -u

for _ in $(seq 1 30); do
  pactl info >/dev/null 2>&1 && break
  sleep 1
done

pactl load-module module-null-sink sink_name=tts_sink \
  sink_properties=device.description="TTSAudioSink" 2>/dev/null || true
pactl load-module module-remap-source master=tts_sink.monitor source_name=virtual_mic \
  source_properties=device.description="VirtualMicrophone" 2>/dev/null || true
pactl set-default-source virtual_mic 2>/dev/null || true

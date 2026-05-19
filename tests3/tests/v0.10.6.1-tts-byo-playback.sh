#!/usr/bin/env bash
# v0.10.6.1-tts-byo-playback — bot tts-playback dispatches by Content-Type.
#
# Steps:
#   handles_wav_and_mp3   — static-grep the bot source: tts-playback.ts
#                           must dispatch on response Content-Type, support
#                           audio/wav, audio/mpeg, audio/pcm; reject other
#                           types with explicit error (no silent guess).

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
TTS="$ROOT_DIR/services/vexa-bot/core/src/services/tts-playback.ts"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-tts-byo-playback :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-tts-byo-playback-$step"

case "$step" in
  handles_wav_and_mp3)
    failed=0
    # Dispatches on Content-Type.
    if ! grep -q "res.headers\['content-type'\]" "$TTS"; then
      echo "    FAIL: tts-playback.ts no longer reads response.headers['content-type']"
      failed=1
    fi
    # Supports audio/wav, audio/mpeg, audio/pcm.
    for ct in "audio/wav" "audio/mpeg" "audio/pcm"; do
      if ! grep -q "$ct" "$TTS"; then
        echo "    FAIL: tts-playback.ts does not handle $ct"
        failed=1
      fi
    done
    # Default voice is 'auto' (so auto-language detection actually fires).
    if ! grep -q "voice: string = 'auto'" "$TTS"; then
      echo "    FAIL: synthesizeAndPlay default voice should be 'auto'"
      failed=1
    fi
    # Default provider is 'piper'.
    if ! grep -q "provider: string = 'piper'" "$TTS"; then
      echo "    FAIL: synthesizeAndPlay default provider should be 'piper'"
      failed=1
    fi
    # Unknown content-type rejects with explicit error (no silent guess).
    if ! grep -q "unsupported upstream Content-Type" "$TTS"; then
      echo "    FAIL: tts-playback.ts must reject unknown Content-Type explicitly, not guess"
      failed=1
    fi
    if (( failed == 0 )); then
      step_pass TTS_PLAYBACK_HANDLES_WAV_AND_MP3_RESPONSES "Content-Type dispatch + WAV/MP3/PCM + voice='auto' default + unknown-type rejection"
    else
      step_fail TTS_PLAYBACK_HANDLES_WAV_AND_MP3_RESPONSES "one or more static-grep checks failed"
    fi
    ;;
  *)
    echo "  unknown step: $step" >&2
    exit 64
    ;;
esac

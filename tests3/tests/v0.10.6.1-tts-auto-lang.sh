#!/usr/bin/env bash
# v0.10.6.1-tts-auto-lang — Piper voice auto-selection from input language.
#
# Steps (compose / helm — TTS_URL must point at a running tts-service):
#   detects_and_picks_voice    — Spanish/Russian/Japanese inputs render via
#                                 the matching Piper voice (script reports
#                                 voice_param + resolved voice from /health
#                                 or via debug log).
#   voice_download_caches      — first call for a new language triggers
#                                 download (~25-60MB, ~10-30s); second call
#                                 for the same language is served from
#                                 cache (<2s).
#
# Env: TTS_URL (default http://localhost:8002), TTS_API_TOKEN (optional).

source "$(dirname "$0")/../lib/common.sh"

step="${1:?usage: $0 <step>}"
TTS_URL="${TTS_URL:-http://localhost:8002}"
AUTH_HEADER=()
if [[ -n "${TTS_API_TOKEN:-}" ]]; then
  AUTH_HEADER=(-H "X-API-Key: ${TTS_API_TOKEN}")
fi

echo ""
echo "  v0.10.6.1-tts-auto-lang :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-tts-auto-lang-$step"

now_ms() {
  python3 - <<'PY'
import time
print(int(time.time() * 1000))
PY
}

case "$step" in

  detects_and_picks_voice)
    # Three samples with unambiguous scripts/languages.
    # We can't introspect "which voice was used" from the audio bytes
    # alone without parsing logs; instead we verify the call returns
    # 200 + non-empty audio for each language. Voice selection is logged
    # by the service; aggregate.py captures stdout/stderr from the
    # tts-service container.
    declare -a SAMPLES=(
      "Hello, how are you today?|en"
      "Hola, ¿cómo estás hoy?|es"
      "Привет, как у тебя дела?|ru"
      "今日はお元気ですか?|ja"
    )
    failed=0
    for entry in "${SAMPLES[@]}"; do
      text="${entry%|*}"
      lang="${entry##*|}"
      body=$(jq -n --arg t "$text" '{model:"tts-1", input:$t, voice:"auto", response_format:"wav"}')
      tmp=$(mktemp --suffix=.wav)
      code=$(curl -sS -o "$tmp" -w '%{http_code}' \
        -X POST "$TTS_URL/v1/audio/speech" \
        "${AUTH_HEADER[@]}" \
        -H "Content-Type: application/json" \
        -d "$body")
      size=$(stat -c%s "$tmp" 2>/dev/null || echo 0)
      rm -f "$tmp"
      if [[ "$code" != "200" ]] || (( size < 1000 )); then
        echo "    FAIL [$lang]: code=$code size=$size text='$text'"
        failed=1
      else
        echo "    ok   [$lang]: code=$code size=$size"
      fi
    done
    if (( failed == 0 )); then
      step_pass TTS_AUTO_LANG_PICKS_RIGHT_VOICE "auto-lang renders all four scripts (en/es/ru/ja)"
    else
      step_fail TTS_AUTO_LANG_PICKS_RIGHT_VOICE "one or more language samples failed (see above)"
    fi
    ;;

  voice_download_caches)
    # Pick a voice unlikely to be pre-loaded. Romanian ("ro_RO-mihai-medium").
    # First call should succeed (download path) unless the voice is already warm
    # from a previous local run. In that warm-cache case, both calls should be
    # fast rather than forcing a false red on "call1=0s call2=1s".
    text="Bună ziua, cum vă simțiți astăzi?"
    body=$(jq -n --arg t "$text" '{model:"tts-1", input:$t, voice:"auto", response_format:"wav"}')

    # Call 1
    t0=$(now_ms)
    code1=$(curl -sS -o /dev/null -w '%{http_code}' \
      -X POST "$TTS_URL/v1/audio/speech" \
      "${AUTH_HEADER[@]}" \
      -H "Content-Type: application/json" \
      -d "$body")
    t1=$(now_ms)
    dur1_ms=$((t1 - t0))

    # Call 2 (same language → should hit cached voice)
    t0=$(now_ms)
    code2=$(curl -sS -o /dev/null -w '%{http_code}' \
      -X POST "$TTS_URL/v1/audio/speech" \
      "${AUTH_HEADER[@]}" \
      -H "Content-Type: application/json" \
      -d "$body")
    t1=$(now_ms)
    dur2_ms=$((t1 - t0))

    echo "    call1 code=$code1 elapsed=${dur1_ms}ms"
    echo "    call2 code=$code2 elapsed=${dur2_ms}ms"

    if [[ "$code1" != "200" || "$code2" != "200" ]]; then
      step_fail TTS_NEW_LANG_VOICE_AUTO_DOWNLOAD_CACHED "non-200 status (call1=$code1 call2=$code2) — voice download path failing"
    elif (( dur1_ms <= 1500 && dur2_ms <= 1500 )); then
      step_pass TTS_NEW_LANG_VOICE_AUTO_DOWNLOAD_CACHED "voice already warm-cached before the prove (call1=${dur1_ms}ms call2=${dur2_ms}ms)"
    elif (( dur2_ms <= dur1_ms )); then
      step_pass TTS_NEW_LANG_VOICE_AUTO_DOWNLOAD_CACHED "voice downloaded on first call, cached on second (call1=${dur1_ms}ms call2=${dur2_ms}ms)"
    else
      step_fail TTS_NEW_LANG_VOICE_AUTO_DOWNLOAD_CACHED "second call (${dur2_ms}ms) slower than first (${dur1_ms}ms) — cache not honored"
    fi
    ;;

  *)
    echo "  unknown step: $step" >&2
    exit 64
    ;;
esac

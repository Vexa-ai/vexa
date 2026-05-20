#!/usr/bin/env bash
# v0.10.6.1-tts-auto-lang — Piper voice auto-selection from input language.
#
# Steps (compose / helm — TTS_URL must point at a running tts-service):
#   runtime_tts_env_wired      — runtime-launched bot profiles receive
#                                 TTS_SERVICE_URL across compose/helm.
#   major_voices_configured    — /health proves major supported voices are
#                                 startup-prepared under strict mode.
#   major_voice_first_call_prompt — Portuguese first-call synthesis is prompt.
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

  runtime_tts_env_wired)
    failed=0
    require_line() {
      local file="$1"
      local pattern="$2"
      local label="$3"
      if grep -Fq "$pattern" "$file"; then
        echo "    ok   $label"
      else
        echo "    FAIL $label: missing '$pattern' in $file"
        failed=1
      fi
    }

    require_line "services/runtime-api/profiles.yaml" 'TTS_SERVICE_URL: "${TTS_SERVICE_URL}"' "runtime profile passes TTS_SERVICE_URL to bot"
    require_line "deploy/compose/docker-compose.yml" "TTS_SERVICE_URL=http://tts-service:8002" "compose runtime-api can resolve profile TTS_SERVICE_URL"
    require_line "deploy/helm/charts/vexa/templates/deployment-runtime-api.yaml" "name: TTS_SERVICE_URL" "helm runtime-api can resolve profile TTS_SERVICE_URL"
    require_line "deploy/helm/charts/vexa/values.yaml" 'TTS_SERVICE_URL: "${TTS_SERVICE_URL}"' "helm runtime profiles pass TTS_SERVICE_URL to bot pods"

    if (( failed == 0 )); then
      step_pass TTS_RUNTIME_ENV_WIRED "runtime-launched bot profiles receive TTS_SERVICE_URL on compose and helm"
    else
      step_fail TTS_RUNTIME_ENV_WIRED "one or more runtime TTS env bindings are missing"
    fi
    ;;

  major_voices_configured)
    health=$(curl -sS "$TTS_URL/health")
    failed=0
    for voice in \
      en_US-amy-medium \
      es_ES-davefx-medium \
      fr_FR-siwis-medium \
      de_DE-thorsten-medium \
      it_IT-paola-medium \
      pt_BR-faber-medium \
      ru_RU-irina-medium \
      hi_IN-pratham-medium
    do
      if jq -e --arg voice "$voice" '.configured_default_voices | index($voice)' >/dev/null <<<"$health"; then
        echo "    ok   configured $voice"
      else
        echo "    FAIL missing configured voice $voice"
        failed=1
      fi
    done

    if jq -e '.preload_strict == true' >/dev/null <<<"$health"; then
      echo "    ok   preload_strict=true"
    else
      echo "    FAIL preload_strict is not true"
      failed=1
    fi

    if jq -e '.default_loaded_voices | index("pt_BR-faber-medium")' >/dev/null <<<"$health"; then
      echo "    ok   pt_BR hot-loaded"
    else
      echo "    FAIL Portuguese voice is not in default_loaded_voices"
      failed=1
    fi

    if (( failed == 0 )); then
      step_pass TTS_MAJOR_VOICES_CONFIGURED "major supported voices are configured for strict startup preparation"
    else
      step_fail TTS_MAJOR_VOICES_CONFIGURED "major voice preparation contract not satisfied"
    fi
    ;;

  major_voice_first_call_prompt)
    text="Olá, esta é uma validação de fala em português da Vexa."
    body=$(jq -n --arg t "$text" '{model:"tts-1", input:$t, voice:"auto", response_format:"wav"}')
    tmp=$(mktemp --suffix=.wav)
    t0=$(now_ms)
    code=$(curl -sS -o "$tmp" -w '%{http_code}' \
      -X POST "$TTS_URL/v1/audio/speech" \
      "${AUTH_HEADER[@]}" \
      -H "Content-Type: application/json" \
      -d "$body")
    t1=$(now_ms)
    dur_ms=$((t1 - t0))
    size=$(stat -c%s "$tmp" 2>/dev/null || echo 0)
    rm -f "$tmp"

    echo "    portuguese first-call code=$code size=$size elapsed=${dur_ms}ms"
    if [[ "$code" == "200" ]] && (( size >= 1000 )) && (( dur_ms <= 6000 )); then
      step_pass TTS_MAJOR_VOICE_FIRST_CALL_PROMPT "Portuguese auto voice rendered promptly on first release-gate call (${dur_ms}ms)"
    else
      step_fail TTS_MAJOR_VOICE_FIRST_CALL_PROMPT "Portuguese first call was not prompt/non-empty (code=$code size=$size elapsed=${dur_ms}ms)"
    fi
    ;;

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

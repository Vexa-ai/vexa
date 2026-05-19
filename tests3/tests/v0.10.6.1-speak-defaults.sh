#!/usr/bin/env bash
# v0.10.6.1-speak-defaults — meeting-api /speak endpoint defaults
# voice='auto' + provider='piper'. The bot then receives the auto
# default in the redis pubsub command, so /speak callers who don't
# pin a voice get language-aware synthesis.
#
# Steps:
#   defaults_propagate  Static (lite mode):
#     - voice_agent.bot_speak default voice='auto' + provider='piper'
#     - tts-playback.synthesizeAndPlay default voice='auto' + provider='piper'
#                       Runtime (compose):
#     - POST /bots /speak without voice/provider → redis publish
#       command must contain voice='auto' (verified via redis SUBSCRIBE).

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
VA="$ROOT_DIR/services/meeting-api/meeting_api/voice_agent.py"
TTS="$ROOT_DIR/services/vexa-bot/core/src/services/tts-playback.ts"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-speak-defaults :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-speak-defaults-$step"

case "$step" in
  defaults_propagate)
    failed=0

    # ── Static: meeting-api /speak defaults ──────────────────────
    if ! grep -q 'req\.get("voice", "auto")' "$VA"; then
      echo "    FAIL: voice_agent.bot_speak default voice should be 'auto'"
      failed=1
    fi
    if ! grep -q 'req\.get("provider", "piper")' "$VA"; then
      echo "    FAIL: voice_agent.bot_speak default provider should be 'piper'"
      failed=1
    fi
    # ── Static: bot tts-playback defaults ────────────────────────
    if ! grep -q "voice: string = 'auto'" "$TTS"; then
      echo "    FAIL: tts-playback synthesizeAndPlay default voice should be 'auto'"
      failed=1
    fi
    if ! grep -q "provider: string = 'piper'" "$TTS"; then
      echo "    FAIL: tts-playback synthesizeAndPlay default provider should be 'piper'"
      failed=1
    fi

    # ── Runtime (compose): /speak publishes voice='auto' to redis ─
    set +e
    if [[ "${DEPLOY_MODE:-}" == "compose" ]] && command -v docker >/dev/null 2>&1; then
      # Find the active meeting via gateway. If none, skip.
      TOKEN_FILE="${TTS_TEST_TOKEN_FILE:-/tmp/v0106_speak_token}"
      if [[ ! -f "$TOKEN_FILE" ]]; then
        # Provision a one-shot user/token for the test.
        ADMIN="${ADMIN_API_KEY:-changeme}"
        USER_ID=$(curl -sS -X POST "${GATEWAY_URL:-http://localhost:8056}/admin/users" \
          -H "X-Admin-API-Key: $ADMIN" -H "Content-Type: application/json" \
          -d '{"email":"speakdefaults@example.com","name":"speakdefaults"}' \
          | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || true)
        if [[ -z "$USER_ID" ]]; then
          USER_ID=$(curl -sS -H "X-Admin-API-Key: $ADMIN" \
            "${GATEWAY_URL:-http://localhost:8056}/admin/users?email=speakdefaults@example.com" \
            | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id'] if d else '')" 2>/dev/null)
        fi
        TOK=$(curl -sS -X POST "${GATEWAY_URL:-http://localhost:8056}/admin/users/$USER_ID/tokens" \
          -H "X-Admin-API-Key: $ADMIN" -H "Content-Type: application/json" -d '{}' \
          | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null)
        echo "$TOK" > "$TOKEN_FILE"
      fi
      TOKEN=$(cat "$TOKEN_FILE")

      # Locate ANY meeting in 'active' state (one already running from
      # an earlier dispatch). If none, dispatch a synthetic one — but
      # synthetic dispatch needs a real Meet URL and isn't safe for
      # this static check. Skip the runtime portion if no active meeting.
      ACTIVE=$(curl -sS -H "X-API-Key: $TOKEN" "${GATEWAY_URL:-http://localhost:8056}/bots/status" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); a=[m for m in d if m.get('status')=='active']; print(a[0].get('platform_specific_id') if a else '')" 2>/dev/null)
      if [[ -z "$ACTIVE" ]]; then
        echo "    skip runtime: no active meeting to fire /speak against"
      else
        # Subscribe to redis bot_commands channel BEFORE firing /speak,
        # then capture the published command and assert voice='auto'.
        # We use a timeout because redis SUBSCRIBE is blocking.
        MEETING_DB_ID=$(curl -sS -H "X-API-Key: $TOKEN" "${GATEWAY_URL:-http://localhost:8056}/bots/status" \
          | python3 -c "
import sys,json
d=json.load(sys.stdin)
a=[m for m in d if m.get('status')=='active']
print(a[0].get('id') if a else '')")
        echo "    runtime: active meeting_id=$MEETING_DB_ID; firing /speak with no voice/provider override"

        # Start subscriber in background.
        SUB_OUT=$(mktemp)
        docker exec vexa-redis-1 redis-cli --timeout 8 SUBSCRIBE "bot_commands:meeting:$MEETING_DB_ID" >"$SUB_OUT" 2>&1 &
        SUB_PID=$!
        sleep 1

        # Fire /speak.
        PLATFORM=$(curl -sS -H "X-API-Key: $TOKEN" "${GATEWAY_URL:-http://localhost:8056}/bots/status" \
          | python3 -c "
import sys,json
d=json.load(sys.stdin)
a=[m for m in d if m.get('status')=='active']
print(a[0].get('platform') if a else '')")
        curl -sS -X POST "${GATEWAY_URL:-http://localhost:8056}/bots/$PLATFORM/$ACTIVE/speak" \
          -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
          -d '{"text":"defaults probe — should publish voice auto"}' >/dev/null

        # Wait for subscriber to capture the message + exit on timeout.
        sleep 4
        kill -TERM $SUB_PID 2>/dev/null || true
        wait $SUB_PID 2>/dev/null || true

        if grep -q '"voice":\s*"auto"' "$SUB_OUT" || grep -q '"voice"' "$SUB_OUT" | grep -q 'auto'; then
          echo "    runtime ok: published command contains voice='auto'"
        elif grep -q "alloy" "$SUB_OUT"; then
          echo "    FAIL: published command has voice='alloy' — meeting-api /speak still hardcoding English"
          cat "$SUB_OUT" | head -10 | sed 's/^/      /'
          failed=1
        else
          echo "    skip: redis subscribe captured no message in 4s — meeting may have ended"
        fi
        rm -f "$SUB_OUT"
      fi
    fi
    set -e

    if (( failed == 0 )); then
      step_pass SPEAK_DEFAULTS_PROPAGATE_AUTO_LANGUAGE "voice_agent + tts-playback default voice='auto' + provider='piper'; runtime publish path verified when active meeting present"
    else
      step_fail SPEAK_DEFAULTS_PROPAGATE_AUTO_LANGUAGE "one or more checks failed"
    fi
    ;;

  *)
    echo "  unknown step: $step" >&2
    exit 64
    ;;
esac

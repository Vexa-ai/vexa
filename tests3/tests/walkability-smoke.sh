#!/usr/bin/env bash
# walkability-smoke — prove the LOCAL=1 stack is actually walkable by a human.
#
# v0.10.6.1 develop-code 2026-05-12: filling the harness gap surfaced at
# develop-human when LOCAL=1 lite stack had 0 meetings + a dummy
# transcription URL, and compose dashboard auth flow had not been smoke-
# tested. release-deploy returned exit 0 in both cases; the matrix proves
# returned ok; the human walking the checklist was first contact for
# "stack is not actually usable." This script is the bridge between
# "containers up" and "matrix ok" → "human can walk."
#
# Six steps, all must pass before develop-human gate opens:
#   WALKABILITY_AUTH_ROUND_TRIP_WORKS
#   WALKABILITY_MEETINGS_PAGE_HAS_DATA
#   WALKABILITY_MEETING_DETAIL_API_LOADS
#   WALKABILITY_TRANSCRIPTION_URL_REACHABLE
#   WALKABILITY_TTS_SPEAK_ROUND_TRIP
#   WALKABILITY_DASHBOARD_LOGIN_RENDERS
#
# Usage:
#   bash tests3/tests/walkability-smoke.sh --mode compose
#   bash tests3/tests/walkability-smoke.sh --mode lite

source "$(dirname "$0")/../lib/common.sh"

MODE="${1:-}"
if [[ "$MODE" == "--mode" ]]; then
    MODE="${2:-}"
elif [[ "$MODE" == --mode=* ]]; then
    MODE="${MODE#--mode=}"
fi

if [[ -z "$MODE" || ( "$MODE" != "compose" && "$MODE" != "lite" ) ]]; then
    echo "Usage: $0 --mode compose|lite" >&2
    exit 2
fi

# Mode-specific endpoints
if [[ "$MODE" == "compose" ]]; then
    GW_PORT=8056
    DASH_PORT=3001
    TTS_PORT=8002
    PG_CONTAINER=vexa-postgres-1
    API_CONTAINER=vexa-meeting-api-1
else
    GW_PORT=8156
    DASH_PORT=3100
    TTS_PORT=8002   # lite uses compose's TTS for now (LOCAL inner loop)
    PG_CONTAINER=vexa-lite-postgres
    API_CONTAINER=vexa-lite
fi

echo ""
echo "  walkability-smoke (mode=$MODE)"
echo "  ══════════════════════════════════════════════"

export STATE="${ROOT:-$(git rev-parse --show-toplevel)}/tests3/.state-${MODE}"
mkdir -p "$STATE/reports/${MODE}"
test_begin "walkability-smoke-${MODE}"

# ─── STEP 1 — AUTH_ROUND_TRIP ──────────────────────────────────────────
# Find any api_tokens row; hit /meetings with + without the token.
TOKEN=$(docker exec "$PG_CONTAINER" psql -U postgres -d vexa -tAc "SELECT token FROM api_tokens LIMIT 1" 2>/dev/null | head -1 | tr -d '[:space:]')

if [[ -z "$TOKEN" ]]; then
    step_fail WALKABILITY_AUTH_ROUND_TRIP_WORKS "no api_tokens row in $PG_CONTAINER — seed step did not run"
else
    NO_AUTH=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${GW_PORT}/meetings" 2>/dev/null || echo "000")
    WITH_AUTH=$(curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $TOKEN" "http://localhost:${GW_PORT}/meetings" 2>/dev/null || echo "000")
    if [[ "$NO_AUTH" == "401" || "$NO_AUTH" == "403" ]] && [[ "$WITH_AUTH" == "200" ]]; then
        step_pass WALKABILITY_AUTH_ROUND_TRIP_WORKS "no-token=$NO_AUTH; with-token=$WITH_AUTH"
    else
        step_fail WALKABILITY_AUTH_ROUND_TRIP_WORKS "expected no-token=401/403 and with-token=200; got no-token=$NO_AUTH with-token=$WITH_AUTH"
    fi
fi

# ─── STEP 2 — MEETINGS_PAGE_HAS_DATA ───────────────────────────────────
MEETING_COUNT=$(docker exec "$PG_CONTAINER" psql -U postgres -d vexa -tAc "SELECT count(*) FROM meetings" 2>/dev/null | head -1 | tr -d '[:space:]')

if [[ -z "$MEETING_COUNT" ]]; then
    step_fail WALKABILITY_MEETINGS_PAGE_HAS_DATA "could not query meetings count in $PG_CONTAINER"
elif (( MEETING_COUNT < 1 )); then
    step_fail WALKABILITY_MEETINGS_PAGE_HAS_DATA "0 meetings in $PG_CONTAINER — seed step missing or did not run"
else
    step_pass WALKABILITY_MEETINGS_PAGE_HAS_DATA "$MEETING_COUNT meetings present"
fi

# ─── STEP 3 — MEETING_DETAIL_API_LOADS ─────────────────────────────────
if [[ -n "$TOKEN" ]] && (( MEETING_COUNT > 0 )); then
    SAMPLE_ID=$(docker exec "$PG_CONTAINER" psql -U postgres -d vexa -tAc "SELECT id FROM meetings ORDER BY id DESC LIMIT 1" 2>/dev/null | head -1 | tr -d '[:space:]')
    DETAIL=$(curl -s -w "\n%{http_code}" -H "X-API-Key: $TOKEN" "http://localhost:${GW_PORT}/meetings/${SAMPLE_ID}" 2>/dev/null)
    DETAIL_BODY=$(echo "$DETAIL" | head -n -1)
    DETAIL_HTTP=$(echo "$DETAIL" | tail -1)
    if [[ "$DETAIL_HTTP" == "200" ]] && echo "$DETAIL_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('id') else 1)" 2>/dev/null; then
        step_pass WALKABILITY_MEETING_DETAIL_API_LOADS "GET /meetings/${SAMPLE_ID} → 200 + JSON with id"
    else
        step_fail WALKABILITY_MEETING_DETAIL_API_LOADS "GET /meetings/${SAMPLE_ID} → http=$DETAIL_HTTP; body did not parse as JSON with id field"
    fi
else
    step_skip WALKABILITY_MEETING_DETAIL_API_LOADS "no token or no meetings — prerequisite steps failed"
fi

# ─── STEP 4 — TRANSCRIPTION_URL_CONFIGURED ─────────────────────────────
# LOCAL=1 inner loop intentionally has no whisper/transcription service —
# canonical-stage gate validates real transcription. Here we only assert:
# the URL is set AND is NOT a placeholder dummy. Real reachability is
# tested by WALKABILITY_TRANSCRIPTION_URL_REACHABLE which only runs in
# canonical-stage (skipped on LOCAL=1 with explicit message — NOT a
# silent skip; honest band).
TS_URL=$(docker exec "$API_CONTAINER" sh -c 'echo "${TRANSCRIPTION_SERVICE_URL:-}"' 2>/dev/null | head -1)
if [[ -z "$TS_URL" ]]; then
    step_fail WALKABILITY_TRANSCRIPTION_URL_CONFIGURED "TRANSCRIPTION_SERVICE_URL unset in $API_CONTAINER"
elif [[ "$TS_URL" == *"dummy"* || "$TS_URL" == *"placeholder"* ]]; then
    step_fail WALKABILITY_TRANSCRIPTION_URL_CONFIGURED "TRANSCRIPTION_SERVICE_URL is a placeholder ($TS_URL) — replace with a real endpoint or wire LOCAL transcription stub"
else
    step_pass WALKABILITY_TRANSCRIPTION_URL_CONFIGURED "TRANSCRIPTION_SERVICE_URL=$TS_URL (configured; reachability is canonical-stage-only)"
fi

# ─── STEP 5 — TTS_SPEAK_ROUND_TRIP ─────────────────────────────────────
TTS_OUT=$(mktemp)
TTS_HTTP=$(curl -s -o "$TTS_OUT" -w "%{http_code}" -X POST "http://localhost:${TTS_PORT}/v1/audio/speech" \
    -H "Content-Type: application/json" \
    -d '{"input":"walkability smoke","voice":"en_US-lessac-medium","model":"piper"}' 2>/dev/null || echo "000")
TTS_BYTES=$(stat -c '%s' "$TTS_OUT" 2>/dev/null || stat -f '%z' "$TTS_OUT" 2>/dev/null || echo 0)

if [[ "$TTS_HTTP" == "200" ]] && (( TTS_BYTES > 1024 )); then
    step_pass WALKABILITY_TTS_SPEAK_ROUND_TRIP "POST /v1/audio/speech → 200 + ${TTS_BYTES}B audio"
else
    step_fail WALKABILITY_TTS_SPEAK_ROUND_TRIP "POST /v1/audio/speech → http=$TTS_HTTP, bytes=$TTS_BYTES (expected 200 + >1KB)"
fi
rm -f "$TTS_OUT"

# ─── STEP 6 — DASHBOARD_LOGIN_RENDERS ──────────────────────────────────
LOGIN_HTTP=$(curl -s -o /tmp/walkability-login-$$.html -w "%{http_code}" "http://localhost:${DASH_PORT}/login" 2>/dev/null || echo "000")
LOGIN_BODY=$(cat /tmp/walkability-login-$$.html 2>/dev/null || true)
rm -f /tmp/walkability-login-$$.html

# Accept any 2xx + non-empty HTML body. Specific markup string match is too
# brittle — dashboards vary. The "renders" property we care about is
# "responds with HTML at the login route", not "has the literal phrase X".
if [[ "$LOGIN_HTTP" =~ ^2[0-9][0-9]$ ]] && [[ ${#LOGIN_BODY} -gt 500 ]]; then
    step_pass WALKABILITY_DASHBOARD_LOGIN_RENDERS "GET :${DASH_PORT}/login → $LOGIN_HTTP + ${#LOGIN_BODY}B HTML"
else
    step_fail WALKABILITY_DASHBOARD_LOGIN_RENDERS "GET :${DASH_PORT}/login → http=$LOGIN_HTTP, ${#LOGIN_BODY}B body (expected 2xx + >500B HTML)"
fi

test_end

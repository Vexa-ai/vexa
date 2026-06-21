#!/usr/bin/env bash
# run.sh — one command: validate the STANDALONE carved bot on a live Google Meet, end to end.
#
#   bash run.sh <meeting_id>          # e.g. bash run.sh rvf-kywf-pxb
#   MEETING=<id> bash run.sh
#
# Phases (each prints a grep-able marker so the validator agent can relay them live):
#   EYEBALL: <url>   the local viewer — open it to watch transcript + lifecycle + verdict
#   ADMIT:   <msg>   the ONE human step — admit `vexa-0.12-bot` in the Meet
#   VERDICT  …       the autonomous PASS/FAIL (also posted to the viewer banner)
#
# Bot runs on the controllable bbb host (proven O6 path); synthetic speakers are driven from the
# Vexa cloud (prod tokens in SECRETS); the viewer + feed bridge + scoring run locally. Exits 0/1
# on the verdict. Env knobs: see config.env.example.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$HERE/config.env" ] && { set -a; . "$HERE/config.env"; set +a; }

MEETING="${1:-${MEETING:-}}"
[ -n "$MEETING" ] || { echo "usage: run.sh <meeting_id>   (or MEETING=<id> run.sh)"; exit 2; }

PLATFORM="${PLATFORM:-google_meet}"
NATIVE_ID="$MEETING"
GMEET_URL="${GMEET_URL:-https://meet.google.com/$MEETING}"
BBB_HOST="${BBB_HOST:-bbb}"
VEXA_NET="${VEXA_NET:-vexa_vexa}"
REDIS_CONTAINER="${REDIS_CONTAINER:-vexa-redis-1}"
RUNTIME_CONTAINER="${RUNTIME_CONTAINER:-vexa-runtime-api-1}"
BOT_IMAGE="${BOT_IMAGE:-vexaai/vexa-bot:v012}"
BOT_CONTAINER="${BOT_CONTAINER:-bot-eval}"
MEETING_ID="${MEETING_ID:-999001}"
VIEWER_PORT="${VIEWER_PORT:-8090}"
VIEWER="http://localhost:$VIEWER_PORT"
DURATION_S="${DURATION_S:-180}"
GAP_MEAN="${GAP_MEAN:--0.5}"
WAIT_ADMIT_S="${WAIT_ADMIT_S:-300}"
SECRETS="${SECRETS:-$HOME/vexa-test-rig/secrets.env}"
EVAL_DIR="$HERE/../../../eval"
TRUTH_LOG="${TRUTH_LOG:-$EVAL_DIR/truth.jsonl}"
OUT="/tmp/bot-eval-$MEETING"

cleanup() {
  [ -n "${KEEP_VIEWER:-}" ] || { [ -n "${VIEWER_PID:-}" ] && kill "$VIEWER_PID" 2>/dev/null || true; }
  [ -n "${FEED_PID:-}" ] && kill "$FEED_PID" 2>/dev/null || true
  [ -n "${SEC_OVERLAY:-}" ] && rm -f "$SEC_OVERLAY" 2>/dev/null || true
  [ -n "${KEEP_BOT:-}" ] || ssh "$BBB_HOST" "docker rm -f $BOT_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ── 1. viewer (local, background) — surface the eyeball URL immediately ──
PORT="$VIEWER_PORT" node "$HERE/viewer/server.mjs" & VIEWER_PID=$!
sleep 0.6
echo "EYEBALL: $VIEWER   ← open to watch the bot live (transcript · lifecycle · verdict)"

# ── 2. spawn the carved bot on bbb (NO callback → console-sink lifecycle for feed.mjs) ──
SINCE=$(( $(date +%s) * 1000 ))
echo "[run] spawning $BOT_IMAGE into $GMEET_URL on $BBB_HOST (since=$SINCE) …"
ssh "$BBB_HOST" '
  set -e
  TT=$(docker exec '"$RUNTIME_CONTAINER"' printenv TRANSCRIPTION_SERVICE_TOKEN 2>/dev/null || true)
  TU=$(docker exec '"$RUNTIME_CONTAINER"' printenv TRANSCRIPTION_SERVICE_URL 2>/dev/null || true)
  CFG=$(TT="$TT" TU="$TU" N="'"$NATIVE_ID"'" MID="'"$MEETING_ID"'" python3 -c "import json,os
print(json.dumps({
  \"platform\":\"google_meet\",
  \"meetingUrl\":\"https://meet.google.com/\"+os.environ[\"N\"],
  \"botName\":\"vexa-0.12-bot\",
  \"nativeMeetingId\":os.environ[\"N\"],
  \"meeting_id\":int(os.environ[\"MID\"]),
  \"connectionId\":\"bot-eval\",
  \"redisUrl\":\"redis://redis:6379\",
  \"transcriptionServiceUrl\":os.environ[\"TU\"],
  \"transcriptionServiceToken\":os.environ[\"TT\"],
  \"recordingEnabled\":True,
  \"automaticLeave\":{\"waitingRoomTimeout\":300000,\"everyoneLeftTimeout\":900000}}))")
  docker rm -f '"$BOT_CONTAINER"' >/dev/null 2>&1 || true
  docker run -d --network '"$VEXA_NET"' --name '"$BOT_CONTAINER"' -e VEXA_BOT_CONFIG="$CFG" '"$BOT_IMAGE"' >/dev/null
'
echo "ADMIT: admit 'vexa-0.12-bot' in $GMEET_URL  — I am watching for the bot to reach 'active'"

# ── 3. feed bridge (background): bbb logs+stream → viewer ──
VIEWER="$VIEWER" BBB_HOST="$BBB_HOST" BOT_CONTAINER="$BOT_CONTAINER" REDIS_CONTAINER="$REDIS_CONTAINER" SINCE="$SINCE" \
  node "$HERE/feed.mjs" & FEED_PID=$!

# ── 4. wait for active (the human admits in the Meet UI) ──
echo "[run] waiting up to ${WAIT_ADMIT_S}s for lifecycle 'active' …"
active=
for ((t=0; t<WAIT_ADMIT_S; t+=5)); do
  if ssh "$BBB_HOST" "docker logs $BOT_CONTAINER 2>&1 | grep -qE 'lifecycle.v1 active'"; then active=1; echo "[run] bot is ACTIVE ✓"; break; fi
  if ssh "$BBB_HOST" "docker logs $BOT_CONTAINER 2>&1 | grep -qE 'lifecycle.v1 failed'"; then echo "[run] bot FAILED before active — see attribution below"; break; fi
  sleep 5
done
[ -n "$active" ] || echo "[run] never saw 'active' — continuing; the verdict/attribution will diagnose"

# ── 5. drive synthetic speakers from the cloud (named TTS → truth.jsonl) ──
if [ -f "$SECRETS" ]; then
  # Overlay PLATFORM/NATIVE_ID so THIS run's meeting wins over any stale value in the secrets file
  # (eval.sh sources SECRETS; a later assignment wins). Tokens etc. come from the original, untouched.
  SEC_OVERLAY="$(mktemp)"
  cat "$SECRETS" > "$SEC_OVERLAY"
  { echo "PLATFORM=$PLATFORM"; echo "NATIVE_ID=$NATIVE_ID"; } >> "$SEC_OVERLAY"
  echo "[run] launching + driving synthetic speakers into $NATIVE_ID (${DURATION_S}s, GAP_MEAN=$GAP_MEAN) …"
  ( cd "$EVAL_DIR" && SECRETS="$SEC_OVERLAY" ./bin/eval.sh launch ) || echo "[run] launch errors (continuing)"
  ( cd "$EVAL_DIR" && SECRETS="$SEC_OVERLAY" DURATION_S="$DURATION_S" GAP_MEAN="$GAP_MEAN" ./bin/eval.sh drive ) || echo "[run] drive errors (continuing)"
  rm -f "$SEC_OVERLAY"; SEC_OVERLAY=
else
  echo "[run] no SECRETS ($SECRETS) — skipping synthetic speakers; speak in the Meet yourself to feed the bot."
  sleep "$DURATION_S"
fi

# ── 6. pull transcript.v1 from redis + score + verdict (+ attribution on red) ──
echo "[run] pulling transcript from redis + scoring vs BASELINE …"
ssh "$BBB_HOST" "docker exec $REDIS_CONTAINER redis-cli XRANGE transcription_segments $SINCE +" \
  | node "$EVAL_DIR/src/read-redis-transcript.mjs" > "$OUT.json"

set +e
PLATFORM="$PLATFORM" NATIVE_ID="$NATIVE_ID" TRUTH_LOG="$TRUTH_LOG" VIEWER="$VIEWER" \
  node "$HERE/verdict.mjs" "$OUT.json" "$OUT.flags.json"
RC=$?
set -e
printf '{"meeting":"%s","native_id":"%s","pass":%s,"transcript":"%s.json"}\n' \
  "$MEETING" "$NATIVE_ID" "$([ $RC -eq 0 ] && echo true || echo false)" "$OUT" > "$OUT.verdict.json"
if [ $RC -ne 0 ]; then echo; node "$HERE/attribute.mjs" "$OUT.flags.json" 2>/dev/null || node "$HERE/attribute.mjs" --empty --status "${active:+active}" || true; fi

echo "[run] done · verdict=$([ $RC -eq 0 ] && echo PASS || echo FAIL) · eyeball $VIEWER · artifacts $OUT.*"
[ -n "${KEEP_VIEWER:-}" ] && { echo "[run] viewer kept up (KEEP_VIEWER) — Ctrl-C to stop"; wait "$VIEWER_PID"; }
exit $RC

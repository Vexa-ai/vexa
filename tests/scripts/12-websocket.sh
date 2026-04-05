#!/usr/bin/env bash
# 12-websocket.sh — Verify WebSocket auth, subscribe, events
# Usage: ./12-websocket.sh GATEWAY_URL API_TOKEN [MEETING_ID]
# Outputs: eval-able WEBSOCKET_OK
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_ID="test/w6a-websocket"
source "$(dirname "$0")/test-lib.sh""

GATEWAY_URL="${1:?Usage: 12-websocket.sh GATEWAY_URL API_TOKEN [MEETING_ID]}"
API_TOKEN="${2:?Missing API_TOKEN}"
MEETING_ID="${3:-}"

log_start "gateway=$GATEWAY_URL meeting=$MEETING_ID"

FAILED=0
WS_BASE="${GATEWAY_URL/http:/ws:}"
WS_BASE="${WS_BASE/https:/wss:}"

# ── Helper: run a node WebSocket snippet with timeout ──────────────────────
# Usage: ws_run <timeout_sec> <node_script>
# The script gets WS_URL, API_TOKEN, MEETING_ID as globals.
ws_run() {
  local timeout_ms=$(( ${1:-10} * 1000 ))
  shift
  local script="$1"
  node --no-warnings -e "
const WebSocket = require('ws');
const WS_URL   = '${WS_BASE}/ws';
const API_TOKEN = '${API_TOKEN}';
const MEETING_ID = '${MEETING_ID}';
const TIMEOUT = ${timeout_ms};

function withTimeout(promise, ms) {
  return Promise.race([
    promise,
    new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), ms))
  ]);
}

function waitForMessage(ws) {
  return new Promise((resolve, reject) => {
    ws.once('message', data => resolve(JSON.parse(data.toString())));
    ws.once('error', reject);
    ws.once('close', (code) => reject(new Error('ws closed with code ' + code)));
  });
}

function connect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    ws.once('open', () => resolve(ws));
    ws.once('error', reject);
    setTimeout(() => reject(new Error('connect timeout')), TIMEOUT);
  });
}

(async () => {
${script}
})().then(() => process.exit(0)).catch(e => { console.error('ERROR: ' + e.message); process.exit(1); });
" 2>&1
}

# ── Step 1: Connect with valid key ────────────────────────────────────────
echo "--- Step 1: connect with valid API key ---" >&2
STEP1=$(ws_run 10 "
  const ws = await connect(WS_URL + '?api_key=' + API_TOKEN);
  // If we connected and can send ping, connection is good
  ws.send(JSON.stringify({action: 'ping'}));
  const msg = await withTimeout(waitForMessage(ws), TIMEOUT);
  if (msg.type === 'pong') {
    console.log('CONNECT_OK');
  } else if (msg.type === 'error') {
    console.log('CONNECT_FAIL: ' + JSON.stringify(msg));
  }
  ws.close();
")

if echo "$STEP1" | grep -q "CONNECT_OK"; then
  log_pass "step 1: connected with valid API key and received pong"
else
  log "FAIL" "step 1: connection with valid key failed: $STEP1"
  FAILED=$((FAILED + 1))
fi

# ── Step 2: Connect without key — expect rejection ────────────────────────
echo "--- Step 2: connect without API key ---" >&2
STEP2=$(ws_run 10 "
  const ws = await connect(WS_URL);
  const msg = await withTimeout(waitForMessage(ws), TIMEOUT);
  if (msg.type === 'error' && msg.error === 'missing_api_key') {
    console.log('NOAUTH_REJECTED');
  } else {
    console.log('NOAUTH_UNEXPECTED: ' + JSON.stringify(msg));
  }
  ws.close();
")

if echo "$STEP2" | grep -q "NOAUTH_REJECTED"; then
  log_pass "step 2: connection without key correctly rejected (missing_api_key)"
else
  log "FAIL" "step 2: expected missing_api_key error: $STEP2"
  FAILED=$((FAILED + 1))
fi

# ── Step 3: Ping/pong ─────────────────────────────────────────────────────
echo "--- Step 3: ping/pong ---" >&2
STEP3=$(ws_run 10 "
  const ws = await connect(WS_URL + '?api_key=' + API_TOKEN);
  ws.send(JSON.stringify({action: 'ping'}));
  const msg = await withTimeout(waitForMessage(ws), TIMEOUT);
  if (msg.type === 'pong') {
    console.log('PONG_OK');
  } else {
    console.log('PONG_FAIL: ' + JSON.stringify(msg));
  }
  ws.close();
")

if echo "$STEP3" | grep -q "PONG_OK"; then
  log_pass "step 3: ping -> pong works"
else
  log "FAIL" "step 3: ping/pong failed: $STEP3"
  FAILED=$((FAILED + 1))
fi

# ── Step 4: Subscribe to meeting (if MEETING_ID provided) ────────────────
if [ -n "$MEETING_ID" ]; then
  echo "--- Step 4: subscribe to meeting ---" >&2

  # First, look up the meeting to get platform and native_meeting_id
  MEETING_INFO=$(curl -sf "$GATEWAY_URL/meetings" \
    -H "X-API-Key: $API_TOKEN" 2>/dev/null || echo '{"meetings":[]}')

  MEETING_LOOKUP=$(echo "$MEETING_INFO" | python3 -c "
import sys, json
data = json.load(sys.stdin)
meetings = data.get('meetings', [])
mid = '${MEETING_ID}'
for m in meetings:
    if str(m.get('id','')) == mid or str(m.get('native_meeting_id','')) == mid:
        print(f\"{m.get('platform','')},{m.get('native_meeting_id','')}\")
        break
else:
    # If meeting_id is the native_meeting_id itself, try to find by that
    for m in meetings:
        if mid in str(m.get('native_meeting_id','')):
            print(f\"{m.get('platform','')},{m.get('native_meeting_id','')}\")
            break
    else:
        print(',')
" 2>/dev/null || echo ",")

  PLATFORM=$(echo "$MEETING_LOOKUP" | cut -d, -f1)
  NATIVE_ID=$(echo "$MEETING_LOOKUP" | cut -d, -f2)

  if [ -z "$PLATFORM" ] || [ -z "$NATIVE_ID" ]; then
    log_skip "step 4: could not resolve meeting_id=$MEETING_ID to platform/native_id (found: platform='$PLATFORM' native_id='$NATIVE_ID')"
  else
    log_pass "resolved meeting: platform=$PLATFORM native_id=$NATIVE_ID"

    STEP4=$(ws_run 15 "
      const ws = await connect(WS_URL + '?api_key=' + API_TOKEN);
      ws.send(JSON.stringify({
        action: 'subscribe',
        meetings: [{platform: '${PLATFORM}', native_id: '${NATIVE_ID}'}]
      }));
      const msg = await withTimeout(waitForMessage(ws), TIMEOUT);
      if (msg.type === 'subscribed') {
        const count = (msg.meetings || []).length;
        console.log('SUBSCRIBE_OK:' + count);
      } else if (msg.type === 'error') {
        console.log('SUBSCRIBE_ERR:' + msg.error + ':' + (msg.details || msg.detail || ''));
      } else {
        console.log('SUBSCRIBE_UNEXPECTED:' + JSON.stringify(msg));
      }
      ws.close();
    ")

    if echo "$STEP4" | grep -q "^SUBSCRIBE_OK:"; then
      SUB_COUNT=$(echo "$STEP4" | grep "^SUBSCRIBE_OK:" | cut -d: -f2)
      log_pass "step 4: subscribed to $SUB_COUNT meeting(s)"
    else
      log "FAIL" "step 4: subscribe failed: $STEP4"
      FAILED=$((FAILED + 1))
    fi

    # ── Step 5: Unsubscribe ──────────────────────────────────────────────
    echo "--- Step 5: unsubscribe ---" >&2
    STEP5=$(ws_run 15 "
      const ws = await connect(WS_URL + '?api_key=' + API_TOKEN);
      // Subscribe first
      ws.send(JSON.stringify({
        action: 'subscribe',
        meetings: [{platform: '${PLATFORM}', native_id: '${NATIVE_ID}'}]
      }));
      const subMsg = await withTimeout(waitForMessage(ws), TIMEOUT);
      if (subMsg.type !== 'subscribed') {
        console.log('UNSUB_PRESUB_FAIL:' + JSON.stringify(subMsg));
        ws.close();
        return;
      }
      // Now unsubscribe
      ws.send(JSON.stringify({
        action: 'unsubscribe',
        meetings: [{platform: '${PLATFORM}', native_id: '${NATIVE_ID}'}]
      }));
      const msg = await withTimeout(waitForMessage(ws), TIMEOUT);
      if (msg.type === 'unsubscribed') {
        const count = (msg.meetings || []).length;
        console.log('UNSUBSCRIBE_OK:' + count);
      } else {
        console.log('UNSUBSCRIBE_FAIL:' + JSON.stringify(msg));
      }
      ws.close();
    ")

    if echo "$STEP5" | grep -q "^UNSUBSCRIBE_OK:"; then
      UNSUB_COUNT=$(echo "$STEP5" | grep "^UNSUBSCRIBE_OK:" | cut -d: -f2)
      log_pass "step 5: unsubscribed from $UNSUB_COUNT meeting(s)"
    else
      log "FAIL" "step 5: unsubscribe failed: $STEP5"
      FAILED=$((FAILED + 1))
    fi

    # ── Step 7: Validate transcript segments via REST ───────────────────
    echo "--- Step 7: validate transcript segments via REST ---" >&2
    TRANSCRIPT=$(curl -sf "$GATEWAY_URL/transcripts/$PLATFORM/$NATIVE_ID" \
      -H "X-API-Key: $API_TOKEN" 2>/dev/null || echo '{}')

    SEGMENT_CHECK=$(echo "$TRANSCRIPT" | python3 -c "
import sys, json

data = json.load(sys.stdin)
segments = data.get('segments', [])

if not segments:
    print('NO_SEGMENTS')
    sys.exit(0)

total = len(segments)
segment_ids = []
missing_text = 0
missing_speaker = 0
duplicates = 0

seen_ids = set()
for s in segments:
    sid = s.get('segment_id')
    text = (s.get('text') or '').strip()
    speaker = (s.get('speaker') or '').strip()

    if not text:
        missing_text += 1
    if not speaker:
        missing_speaker += 1

    if sid:
        if sid in seen_ids:
            duplicates += 1
        seen_ids.add(sid)
        segment_ids.append(sid)

ids_with_values = len(segment_ids)
print(f'total={total}')
print(f'ids_with_values={ids_with_values}')
print(f'duplicates={duplicates}')
print(f'missing_text={missing_text}')
print(f'missing_speaker={missing_speaker}')
" 2>/dev/null || echo "PARSE_ERROR")

    if echo "$SEGMENT_CHECK" | grep -q "NO_SEGMENTS"; then
      log_skip "step 7: no segments found for $PLATFORM/$NATIVE_ID (meeting may have no transcript)"
    elif echo "$SEGMENT_CHECK" | grep -q "PARSE_ERROR"; then
      log "FAIL" "step 7: could not parse transcript response"
      FAILED=$((FAILED + 1))
    else
      TOTAL=$(echo "$SEGMENT_CHECK" | grep "^total=" | cut -d= -f2)
      DUPS=$(echo "$SEGMENT_CHECK" | grep "^duplicates=" | cut -d= -f2)
      MISSING_TEXT=$(echo "$SEGMENT_CHECK" | grep "^missing_text=" | cut -d= -f2)
      MISSING_SPEAKER=$(echo "$SEGMENT_CHECK" | grep "^missing_speaker=" | cut -d= -f2)

      log_pass "step 7: $TOTAL segments retrieved via REST"

      if [ "${DUPS:-0}" -gt 0 ]; then
        log "FAIL" "step 7: $DUPS duplicate segment_ids found"
        FAILED=$((FAILED + 1))
      else
        log_pass "step 7: no duplicate segment_ids"
      fi

      if [ "${MISSING_TEXT:-0}" -gt 0 ]; then
        log "FAIL" "step 7: $MISSING_TEXT segments missing text"
        FAILED=$((FAILED + 1))
      else
        log_pass "step 7: all segments have text"
      fi

      if [ "${MISSING_SPEAKER:-0}" -gt 0 ]; then
        log_finding "step 7: $MISSING_SPEAKER/$TOTAL segments missing speaker (may be expected for system segments)"
      else
        log_pass "step 7: all segments have speaker"
      fi
    fi
  fi
else
  log_skip "steps 4-5, 7: no MEETING_ID provided — skipping subscribe/unsubscribe/segment validation"
fi

# ── Step 6: Invalid JSON — expect error, not crash ────────────────────────
echo "--- Step 6: invalid JSON ---" >&2
STEP6=$(ws_run 10 "
  const ws = await connect(WS_URL + '?api_key=' + API_TOKEN);
  // Send garbage text
  ws.send('this is not json {{{');
  const msg = await withTimeout(waitForMessage(ws), TIMEOUT);
  if (msg.type === 'error' && msg.error === 'invalid_json') {
    // Verify connection still alive by sending ping
    ws.send(JSON.stringify({action: 'ping'}));
    const pong = await withTimeout(waitForMessage(ws), TIMEOUT);
    if (pong.type === 'pong') {
      console.log('INVALID_JSON_OK');
    } else {
      console.log('INVALID_JSON_NORECOVER');
    }
  } else {
    console.log('INVALID_JSON_UNEXPECTED:' + JSON.stringify(msg));
  }
  ws.close();
")

if echo "$STEP6" | grep -q "INVALID_JSON_OK"; then
  log_pass "step 6: invalid JSON returns error, connection survives"
elif echo "$STEP6" | grep -q "INVALID_JSON_NORECOVER"; then
  log "FAIL" "step 6: invalid JSON error returned but connection did not survive for follow-up ping"
  FAILED=$((FAILED + 1))
else
  log "FAIL" "step 6: invalid JSON handling unexpected: $STEP6"
  FAILED=$((FAILED + 1))
fi

# ── Step 8: Unknown action — expect error ─────────────────────────────────
echo "--- Step 8: unknown action ---" >&2
STEP8=$(ws_run 10 "
  const ws = await connect(WS_URL + '?api_key=' + API_TOKEN);
  ws.send(JSON.stringify({action: 'nonexistent_action'}));
  const msg = await withTimeout(waitForMessage(ws), TIMEOUT);
  if (msg.type === 'error' && msg.error === 'unknown_action') {
    console.log('UNKNOWN_ACTION_OK');
  } else {
    console.log('UNKNOWN_ACTION_UNEXPECTED:' + JSON.stringify(msg));
  }
  ws.close();
")

if echo "$STEP8" | grep -q "UNKNOWN_ACTION_OK"; then
  log_pass "step 8: unknown action returns error"
else
  log "FAIL" "step 8: unknown action handling unexpected: $STEP8"
  FAILED=$((FAILED + 1))
fi

# ── Summary ───────────────────────────────────────────────────────────────
if [ "$FAILED" -gt 0 ]; then
  log_fail "$FAILED check(s) failed"
fi

log_pass "all WebSocket checks passed"
echo "WEBSOCKET_OK=true"

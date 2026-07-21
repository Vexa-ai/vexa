#!/usr/bin/env bash
# =============================================================================
# run_e2e.sh — the synthetic capture→archive E2E rig (WP-M9 workstream C).
#
# WHY THIS EXISTS: one live session found NINE stacked defects between "capture
# requested" and "archive readable" — each invisible behind the previous,
# because no PR lane ever exercised the path end-to-end (transcripts landed in
# redis but never Postgres; the read index hid every stopped capture; the
# summary writer had no writer; settlement refunded real compute). This script
# drives ONE synthetic meeting through the REAL engine — control plane →
# runtime spawn → lifecycle FSM → transcription_segments stream → collector →
# db-writer → summarizer → read plane → settlement callbacks — and fails LOUD
# with the failing stage named, so that class of silent re-stacking cannot
# merge again.
#
# HOW IT STAYS HONEST: only two instruments are synthetic, both local files —
#   * scripted_bot.py — swapped in via the runtime's documented BOT_COMMAND
#     override (the lite process backend's operator knob); the spawn, the
#     invocation, the callback receiver, the segment stream, the leave command
#     are all production machinery.
#   * stub_llm.py — a loopback OpenAI-compatible backend + Hub callback sink;
#     SUMMARY_SERVICE_URL / TRANSCRIPTION_SERVICE_URL / the Minutes callback
#     URL point at it through their EXISTING declared config keys. No new
#     config.v1 keys exist for this rig.
#
# PRECONDITION: the lite stack is already up (pr-value lite-smoke boots it; a
# local run needs `make -C deploy/lite up`). The script rewrites the rig's
# managed keys in the repo-root .env and re-runs `make up`, which re-creates
# ONLY the app container (postgres/minio are kept), so the boot cost is paid
# once per job, not twice.
#
# Stages (each fails loud by name):
#   env → instruments → control-ready → ensure → capture-spawn →
#   lifecycle-active → live-transcript → stop → terminal → transcript-rows →
#   settlement-active → summary → read-index → callback-settlement →
#   lobby-spawn → lobby-stop → settlement-lobby
#
# WORKSTREAM A CONTRACT (settlement stages): captured_seconds_total must equal
# the scripted ACTIVE duration, and a lobby-only capture must settle 0. The
# lobby assertion is written to A's contract and MAY LEGITIMATELY FAIL against
# pre-A main (there, a never-admitted capture settles wall-time since capture
# creation) — that red run is the defect evidence, not a rig bug.
# E2E_SETTLEMENT_STRICT=0 downgrades ONLY the settlement stages to warnings if
# the coordinator must land C ahead of A.
# =============================================================================
set -uo pipefail

APP="${APP_CONTAINER:-vexa-lite}"
PG="${PG_CONTAINER:-vexa-lite-postgres}"
ROOT="$(git rev-parse --show-toplevel)"
ENV_FILE="$ROOT/.env"
E2E_SRC="$ROOT/deploy/lite/tests/e2e"
E2E_DIR=/tmp/zaki-e2e
STUB_PORT="${STUB_PORT:-8099}"
ACTIVE_S="${ACTIVE_S:-20}"          # the scripted active window (settlement oracle)
LOBBY_HOLD_S="${LOBBY_HOLD_S:-8}"   # how long the lobby capture waits before the stop
STRICT="${E2E_SETTLEMENT_STRICT:-1}"

# CI-only credentials (>=32 chars where the engine demands it). Overridable for local runs.
CONTROL_SECRET="${MINUTES_ENGINE_CONTROL_TOKEN:-e2e-control-signing-secret-0123456789abcdef}"
CALLBACK_HMAC="${MINUTES_ENGINE_CALLBACK_HMAC_KEY:-e2e-callback-hmac-key-0123456789abcdef00}"
READ_TOKEN="${ZAKI_READ_TOKEN_MINUTES:-e2e-minutes-read-token-0123456789abcdef}"

RUN="$(date +%s)"
TENANT="e2e-$RUN"
USER_ID="${E2E_USER_ID:-7042}"

STAGE=preflight

diagnostics() {
  echo "──── e2e diagnostics (stage: $STAGE) ────"
  docker exec "$APP" supervisorctl status 2>/dev/null || true
  docker logs "$APP" --tail 120 2>/dev/null || true
  docker exec "$APP" sh -c 'for f in /tmp/vexa-workloads/*.log; do [ -f "$f" ] && { echo "── $f"; tail -n 30 "$f"; }; done' 2>/dev/null || true
  docker exec "$PG" psql -U postgres -d vexa -c \
    "SELECT id,user_id,status,start_time,end_time,platform_specific_id FROM meetings ORDER BY id DESC LIMIT 6" 2>/dev/null || true
  docker exec "$PG" psql -U postgres -d vexa -c \
    "SELECT capture_id,state,meeting_id,captured_seconds_total FROM zaki_control_captures ORDER BY created_at DESC LIMIT 6" 2>/dev/null || true
  echo "── stub events"
  docker exec "$APP" curl -s -m 5 "http://localhost:$STUB_PORT/_events" 2>/dev/null || true
  echo
}

fail() {
  echo "::error ::E2E FAIL at stage [$STAGE]: $*"
  diagnostics
  exit 1
}

soft_or_fail() { # settlement stages honor E2E_SETTLEMENT_STRICT
  if [ "$STRICT" = "0" ]; then
    echo "::warning ::E2E stage [$STAGE] violated workstream A's contract (non-strict mode): $*"
  else
    fail "$@"
  fi
}

xcurl() { docker exec "$APP" curl -s -m 15 "$@"; }

jget() { # jget '<python expr over d>' — parse stdin JSON, print an expression (empty on miss)
  python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    d=None
try:
    v=eval(sys.argv[1])
except Exception:
    v=''
print('' if v is None else v)" "$1"
}

mint_token() { # fresh Hub HMAC token (aud zaki-control.v1, 240s validity)
  python3 - "$CONTROL_SECRET" "$TENANT" "$USER_ID" <<'PY'
import base64, hashlib, hmac, json, sys, time
secret, tenant, user = sys.argv[1], sys.argv[2], sys.argv[3]
now = int(time.time())
claims = {"aud": "zaki-control.v1", "exp": now + 240, "iat": now,
          "tenant_id": tenant, "user_id": user, "v": 1}
payload = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).rstrip(b"=").decode()
sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
print(f"{payload}.{sig}")
PY
}

control_post() { # control_post <path> <request_id> <idempotency_key> <json-body>
  xcurl -X POST "http://localhost:8080$1" \
    -H "Content-Type: application/json" \
    -H "X-Zaki-Control-Token: $(mint_token)" \
    -H "X-Zaki-Tenant-Id: $TENANT" \
    -H "X-Zaki-User-Id: $USER_ID" \
    -H "X-Request-Id: $2" \
    -H "Idempotency-Key: $3" \
    -d "$4"
}

capture_status() { # capture_status <capture_id>
  xcurl "http://localhost:8080/api/zaki/control/v1/$USER_ID/captures/$1" \
    -H "X-Zaki-Control-Token: $(mint_token)" \
    -H "X-Zaki-Tenant-Id: $TENANT" \
    -H "X-Zaki-User-Id: $USER_ID" \
    -H "X-Request-Id: status-$RUN"
}

poll() { # poll <timeout_s> <interval_s> <fn returning 0 when satisfied>
  local deadline=$(( $(date +%s) + $1 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if "$3"; then return 0; fi
    sleep "$2"
  done
  return 1
}

# ── stage: env — rewrite the rig's managed keys in .env, restart the app container ───────────────
STAGE=env
MANAGED='ZAKI_MINUTES_CONTROL_ENABLED|ZAKI_MINUTES_OPERATOR_ENABLED|MINUTES_ENGINE_CONTROL_TOKEN|MINUTES_ENGINE_CALLBACK_URL|MINUTES_ENGINE_CALLBACK_HMAC_KEY|ZAKI_MINUTES_TTL_ENABLED|ZAKI_READ_TOKEN_MINUTES|SUMMARY_MODEL|SUMMARY_SERVICE_URL|SUMMARY_SERVICE_TOKEN|SUMMARY_INTERVAL_S|TRANSCRIPTION_SERVICE_URL|BOT_COMMAND|ZAKI_CONTROL_CALLBACK_INTERVAL_S|STOP_RECONCILE_GRACE_S|STOP_RECONCILE_INTERVAL_S'
TMP_ENV="$(mktemp)"
[ -f "$ENV_FILE" ] && grep -vE "^($MANAGED)=" "$ENV_FILE" > "$TMP_ENV" || true
# SUMMARY_SERVICE_TOKEN must be non-empty: openai_chat_llm sends Authorization
# unconditionally, and with an empty token the header value becomes b'Bearer '
# which httpx refuses (LocalProtocolError) on every tick — data.summary then never
# appears and the [summary] stage is guaranteed red. The stub ignores the value;
# the instruments test pins this exact line.
cat >> "$TMP_ENV" <<EOF
ZAKI_MINUTES_CONTROL_ENABLED=true
ZAKI_MINUTES_OPERATOR_ENABLED=true
MINUTES_ENGINE_CONTROL_TOKEN=$CONTROL_SECRET
MINUTES_ENGINE_CALLBACK_URL=http://localhost:$STUB_PORT/api/minutes/callback/v1
MINUTES_ENGINE_CALLBACK_HMAC_KEY=$CALLBACK_HMAC
ZAKI_MINUTES_TTL_ENABLED=true
ZAKI_READ_TOKEN_MINUTES=$READ_TOKEN
SUMMARY_MODEL=zaki-e2e-stub
SUMMARY_SERVICE_URL=http://localhost:$STUB_PORT
SUMMARY_SERVICE_TOKEN=e2e-token
SUMMARY_INTERVAL_S=5
TRANSCRIPTION_SERVICE_URL=http://localhost:$STUB_PORT
BOT_COMMAND=/opt/venvs/meeting/bin/python $E2E_DIR/scripted_bot.py
ZAKI_CONTROL_CALLBACK_INTERVAL_S=2
STOP_RECONCILE_GRACE_S=10
STOP_RECONCILE_INTERVAL_S=5
EOF
mv "$TMP_ENV" "$ENV_FILE"
make -C "$ROOT/deploy/lite" up >/dev/null 2>&1 || fail "make up (app restart with the e2e env) failed"

# ── stage: instruments — ship the two local instruments into the container, start the stub ───────
STAGE=instruments
docker exec "$APP" mkdir -p "$E2E_DIR" || fail "cannot mkdir $E2E_DIR in $APP"
docker cp "$E2E_SRC/scripted_bot.py" "$APP:$E2E_DIR/scripted_bot.py" || fail "cp scripted_bot.py"
docker cp "$E2E_SRC/stub_llm.py" "$APP:$E2E_DIR/stub_llm.py" || fail "cp stub_llm.py"
docker exec "$APP" chmod -R a+rx "$E2E_DIR" || true
docker exec -d -e "PORT=$STUB_PORT" "$APP" python3 "$E2E_DIR/stub_llm.py" || fail "stub launch"
stub_up() { xcurl -o /dev/null -w '%{http_code}' "http://localhost:$STUB_PORT/health" | grep -q '^200$'; }
poll 30 2 stub_up || fail "stub /health never answered on :$STUB_PORT"

# ── stage: control-ready — the sealed control plane is mounted and lifecycle-ready ──────────────
STAGE=control-ready
control_ready() {
  local body; body=$(xcurl "http://localhost:8080/api/zaki/control/v1/ready" 2>/dev/null) || return 1
  echo "$body" | grep -q '"state":"ready"' && echo "$body" | grep -q '"operator_enabled":true'
}
poll 120 5 control_ready || fail "/api/zaki/control/v1/ready never reported ready+operator_enabled (are the ZAKI_* env keys reaching meeting-api?)"

# ── stage: ensure — tenant policy: capture on, agent read on, durable retention windows ──────────
STAGE=ensure
NOW_ISO="$(python3 -c 'from datetime import datetime,timezone,timedelta;print((datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat())')"
ENSURE_BODY=$(cat <<EOF
{"api_version":"zaki-control.v1","request_id":"ens-$RUN","idempotency_key":"ens-$RUN",
 "subject":{"tenant_id":"$TENANT","user_id":"$USER_ID"},
 "policy":{"capture_enabled":true,"agent_read_enabled":true,
   "capture_notice_policy_version":"notice-e2e",
   "retention":{"audio_days":7,"transcript_days":30,"summary_days":30}}}
EOF
)
RESP=$(control_post "/api/zaki/control/v1/$USER_ID/ensure" "ens-$RUN" "ens-$RUN" "$ENSURE_BODY")
[ "$(echo "$RESP" | jget "d.get('state')")" = "ready" ] || fail "ensure did not answer state=ready: $RESP"

# ── stage: capture-spawn — the real control-plane capture (spawns the scripted bot) ─────────────
STAGE=capture-spawn
capture_body() { # capture_body <req> <meet-code>
  cat <<EOF
{"api_version":"zaki-control.v1","request_id":"$1","idempotency_key":"$1",
 "subject":{"tenant_id":"$TENANT","user_id":"$USER_ID"},
 "platform":"google_meet","meeting_url":"https://meet.google.com/$2",
 "capture_attestation":{"bot_visible":true,"bot_display_name":"ZAKI Notetaker",
   "policy_version":"notice-e2e","attested_at":"$NOW_ISO","attested_by_user_id":"$USER_ID"},
 "metering":{"reservation_id":"resv-$1","unit":"bot_minute","reserved_units":30}}
EOF
}
RESP=$(control_post "/api/zaki/control/v1/$USER_ID/captures" "cap-$RUN" "cap-$RUN" "$(capture_body "cap-$RUN" 'aaa-actv-xyz')")
CAPTURE_ID=$(echo "$RESP" | jget "d.get('capture_id')")
MEETING_ID=$(echo "$RESP" | jget "d.get('meeting_id')")
[ -n "$CAPTURE_ID" ] && [ -n "$MEETING_ID" ] || fail "capture create returned no capture_id/meeting_id: $RESP"
echo "capture $CAPTURE_ID bound to meeting $MEETING_ID ✓"

# ── stage: lifecycle-active — the spawned bot drives the FSM to active ──────────────────────────
STAGE=lifecycle-active
is_active() { capture_status "$CAPTURE_ID" | jget "d.get('state')" | grep -q '^active$'; }
poll 60 2 is_active || fail "capture never reached active (state: $(capture_status "$CAPTURE_ID"))"
ACTIVE_AT=$(date +%s)
echo "capture active ✓ — holding the scripted active window (${ACTIVE_S}s)"
sleep "$ACTIVE_S"

# ── stage: live-transcript — segments flowed stream→collector while still active ────────────────
STAGE=live-transcript
live_doc() {
  xcurl "http://localhost:8080/transcripts/by-id/$MEETING_ID" -H "X-User-Id: $USER_ID" \
    | jget "len(d.get('segments') or [])" | grep -qE '^[1-9][0-9]*$'
}
poll 20 2 live_doc || fail "no live transcript segments for meeting $MEETING_ID while active (stream→collector broken)"
echo "live transcript has segments ✓"

# ── stage: stop — the sealed stop (leave command + durable ordinary tombstone) ──────────────────
STAGE=stop
STOP_BODY="{\"api_version\":\"zaki-control.v1\",\"request_id\":\"stop-$RUN\",\"idempotency_key\":\"stop-$RUN\",\"subject\":{\"tenant_id\":\"$TENANT\",\"user_id\":\"$USER_ID\"},\"capture_id\":\"$CAPTURE_ID\"}"
RESP=$(control_post "/api/zaki/control/v1/$USER_ID/captures/$CAPTURE_ID/stop" "stop-$RUN" "stop-$RUN" "$STOP_BODY")
STOP_AT=$(date +%s)
echo "$RESP" | jget "d.get('state')" | grep -qE '^(stopping|completed)$' || fail "stop did not answer stopping/completed: $RESP"

# ── stage: terminal — the capture settles terminal through the real callback plumbing ───────────
STAGE=terminal
is_terminal() { capture_status "$CAPTURE_ID" | jget "d.get('state')" | grep -qE '^(completed|failed)$'; }
poll 90 3 is_terminal || fail "capture never reached a terminal state after stop"
FINAL=$(capture_status "$CAPTURE_ID")
[ "$(echo "$FINAL" | jget "d.get('state')")" = "completed" ] || fail "capture terminal state is not completed: $FINAL"
[ "$(echo "$FINAL" | jget "d.get('metering',{}).get('terminal')")" = "True" ] || fail "terminal capture is not metering-terminal: $FINAL"
echo "capture completed ✓"

# ── stage: transcript-rows — the rounds-3/4 class: rows exist in the DURABLE table ──────────────
STAGE=transcript-rows
rows_exist() {
  docker exec "$PG" psql -U postgres -d vexa -tAc \
    "SELECT count(*) FROM transcriptions WHERE meeting_id=$MEETING_ID AND text ~ '[^[:space:]]'" \
    | grep -qE '^[1-9][0-9]*$'
}
poll 30 3 rows_exist || fail "no transcriptions rows for meeting $MEETING_ID after terminal (redis→Postgres flush broken — the rounds-3/4 class)"
echo "transcriptions rows exist ✓"

# ── stage: settlement-active — WORKSTREAM A CONTRACT: settled seconds == scripted active window ─
STAGE=settlement-active
SECONDS_TOTAL=$(echo "$FINAL" | jget "d.get('metering',{}).get('captured_seconds_total')")
ELAPSED=$(( STOP_AT - ACTIVE_AT ))
LOW=$(( ACTIVE_S - 2 )); HIGH=$(( ELAPSED + 30 ))
echo "settlement: captured_seconds_total=$SECONDS_TOTAL (scripted active window ${ACTIVE_S}s, observed active→stop ${ELAPSED}s)"
if ! { [ -n "$SECONDS_TOTAL" ] && [ "$SECONDS_TOTAL" -ge "$LOW" ] && [ "$SECONDS_TOTAL" -le "$HIGH" ]; }; then
  soft_or_fail "captured_seconds_total=$SECONDS_TOTAL outside the scripted active window [$LOW..$HIGH]s (workstream A: settlement must equal true ACTIVE seconds)"
fi

# ── stage: summary — WP-M12: the summarizer writes minutes via the stub backend ─────────────────
STAGE=summary
summary_written() {
  docker exec "$PG" psql -U postgres -d vexa -tAc \
    "SELECT COALESCE(data->'summary'->>'text','') FROM meetings WHERE id=$MEETING_ID" | grep -q '[^[:space:]]'
}
poll 60 5 summary_written || fail "data.summary never appeared for meeting $MEETING_ID (summarizer→SUMMARY_SERVICE_URL stub broken — WP-M12)"
echo "summary written ✓"

# ── stage: read-index — the round-5 class: the archive lists the STOPPED capture's items ────────
STAGE=read-index
INDEX=$(xcurl "http://localhost:8080/api/zaki/read/v1/$USER_ID/index" \
  -H "X-Zaki-Read-Token: $READ_TOKEN" -H "X-Zaki-User-Id: $USER_ID")
for item in "meeting:$MEETING_ID" "transcript:$MEETING_ID" "summary:$MEETING_ID"; do
  # repr-quoted match so meeting:12 can never satisfy a meeting:123 assertion
  echo "$INDEX" | jget "[i.get('id') for i in d.get('items') or []]" | grep -q "'$item'" \
    || fail "read index is missing '$item' after the stop (round-5 class / WP-M12): $INDEX"
done
echo "read index lists meeting + transcript + summary ✓"

# ── stage: callback-settlement — the Hub-visible terminal usage event reached the sink ──────────
# POLLED: the outbox drain sweeps on ZAKI_CONTROL_CALLBACK_INTERVAL_S (5s default) and a slow CI
# runner can put the terminal pair one-or-two sweeps after the stop — a single-shot assert here
# raced it and failed a healthy engine (PR #33's first run). Poll to a bound instead.
STAGE=callback-settlement
DELIVERED=""
for _i in $(seq 1 15); do
  EVENTS=$(xcurl "http://localhost:$STUB_PORT/_events")
  if echo "$EVENTS" | jget "[e for e in d if e.get('event_type')=='minutes.capture.usage' and (e.get('data') or {}).get('capture_id')=='$CAPTURE_ID' and ((e.get('data') or {}).get('metering') or {}).get('terminal') is True]" \
    | grep -q "'capture_id': '$CAPTURE_ID'"; then DELIVERED=1; break; fi
  sleep 3
done
[ -n "$DELIVERED" ] || fail "no terminal minutes.capture.usage callback for $CAPTURE_ID reached the Hub sink within 45s: $EVENTS"
echo "terminal usage callback delivered ✓"

# ── stage: lobby-spawn — the never-admitted capture (workstream A's zero-settlement case) ───────
STAGE=lobby-spawn
RESP=$(control_post "/api/zaki/control/v1/$USER_ID/captures" "lob-$RUN" "lob-$RUN" "$(capture_body "lob-$RUN" 'aaa-lobb-xyz')")
LOBBY_ID=$(echo "$RESP" | jget "d.get('capture_id')")
LOBBY_MID=$(echo "$RESP" | jget "d.get('meeting_id')")
[ -n "$LOBBY_ID" ] || fail "lobby capture create failed: $RESP"
in_lobby() { capture_status "$LOBBY_ID" | jget "d.get('state')" | grep -q '^awaiting_admission$'; }
poll 45 2 in_lobby || fail "lobby capture never reached awaiting_admission"
echo "lobby capture holding in the waiting room ✓ (${LOBBY_HOLD_S}s)"
sleep "$LOBBY_HOLD_S"

# ── stage: lobby-stop ───────────────────────────────────────────────────────────────────────────
STAGE=lobby-stop
STOP_BODY="{\"api_version\":\"zaki-control.v1\",\"request_id\":\"lstop-$RUN\",\"idempotency_key\":\"lstop-$RUN\",\"subject\":{\"tenant_id\":\"$TENANT\",\"user_id\":\"$USER_ID\"},\"capture_id\":\"$LOBBY_ID\"}"
control_post "/api/zaki/control/v1/$USER_ID/captures/$LOBBY_ID/stop" "lstop-$RUN" "lstop-$RUN" "$STOP_BODY" >/dev/null
lobby_terminal() { capture_status "$LOBBY_ID" | jget "d.get('state')" | grep -qE '^(completed|failed)$'; }
poll 120 3 lobby_terminal || fail "lobby capture never reached a terminal state after stop (reconcile path broken)"

# ── stage: settlement-lobby — WORKSTREAM A CONTRACT (expected RED on pre-A main) ────────────────
# A lobby-only capture never went active: it must settle 0 captured seconds. Pre-A engines settle
# wall-time since capture creation here — this stage failing against origin/main is the red-first
# evidence for workstream A, not a rig defect.
STAGE=settlement-lobby
LOBBY_FINAL=$(capture_status "$LOBBY_ID")
LOBBY_SECONDS=$(echo "$LOBBY_FINAL" | jget "d.get('metering',{}).get('captured_seconds_total')")
echo "lobby settlement: captured_seconds_total=$LOBBY_SECONDS (meeting $LOBBY_MID, contract: 0)"
if [ "$LOBBY_SECONDS" != "0" ]; then
  soft_or_fail "lobby-only capture settled $LOBBY_SECONDS seconds; workstream A's contract says 0 (never-active captures bill nothing)"
fi

echo
echo "E2E PASS: capture→lifecycle→segments→collector→transcriptions→stop→settlement→summary→read-index all green"

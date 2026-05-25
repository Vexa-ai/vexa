#!/usr/bin/env bash
# Pack 4 autonomous lifecycle-convergence runner.
#
# Exercises 6 stop/delete scenarios across both bot profiles
# (meeting + browser-session) without any human input — each scenario's
# verdict is computed deterministically from DB state, Docker state,
# Redis state, and meeting-api callback counts.
#
# Output: per-scenario evidence dir + summary.json.

set -uo pipefail

PACK_ROOT="${PACK_ROOT:-/home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence}"
EVIDENCE_ROOT="${EVIDENCE_ROOT:-$PACK_ROOT/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/synthetic/lifecycle-convergence}"
GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:46461}"
RUNTIME_URL="${RUNTIME_URL:-http://127.0.0.1:46465}"
ADMIN_URL="${ADMIN_URL:-http://127.0.0.1:46464}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-vexa_0-10-6x-pack-4-stop-delete-lifecycle-convergence_compose}"
MA_CONTAINER="${COMPOSE_PROJECT}-meeting-api-1"
RA_CONTAINER="${COMPOSE_PROJECT}-runtime-api-1"
PG_CONTAINER="${COMPOSE_PROJECT}-postgres-1"
REDIS_CONTAINER="${COMPOSE_PROJECT}-redis-1"

mkdir -p "$EVIDENCE_ROOT"

# Retrieve listener test@vexa.ai token from postgres
LISTENER_TOKEN="$(docker exec "$PG_CONTAINER" psql -U postgres -d vexa -tA -c \
  "SELECT t.token FROM api_tokens t JOIN users u ON u.id=t.user_id WHERE u.email='test@vexa.ai' ORDER BY t.id DESC LIMIT 1;" 2>/dev/null | tr -d ' \n\r')"
ADMIN_TOKEN="${ADMIN_TOKEN:-changeme}"

if [ -z "$LISTENER_TOKEN" ]; then
  echo "ERROR: could not retrieve listener token from $PG_CONTAINER" >&2
  exit 2
fi

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] $*" >&2; }

# DB helpers
db_meeting_status() {
  local id="$1"
  docker exec "$PG_CONTAINER" psql -U postgres -d vexa -tA -c \
    "SELECT status FROM meetings WHERE id=$id;" 2>/dev/null | tr -d ' \n\r'
}

db_meeting_data_recordings_count() {
  local id="$1"
  docker exec "$PG_CONTAINER" psql -U postgres -d vexa -tA -c \
    "SELECT jsonb_array_length(COALESCE(data->'recordings','[]'::jsonb)) FROM meetings WHERE id=$id;" 2>/dev/null | tr -d ' \n\r'
}

docker_container_exists() {
  local name="$1"
  docker ps -a --format '{{.Names}}' | grep -qx "$name"
}

docker_container_running() {
  local name="$1"
  docker ps --format '{{.Names}}' | grep -qx "$name"
}

ma_callback_count() {
  local meeting_id="$1"
  docker logs "$MA_CONTAINER" --since 5m 2>&1 \
    | grep -E "Exit callback.*meeting=$meeting_id|callback/exited.*meeting_id=$meeting_id" \
    | wc -l | tr -d ' '
}

ra_session_state_key_count() {
  local meeting_id="$1"
  docker exec "$REDIS_CONTAINER" redis-cli --scan --pattern "bm:meeting:${meeting_id}:*" 2>/dev/null | wc -l | tr -d ' '
}

# Poll a DB status until terminal or timeout
poll_db_terminal() {
  local meeting_id="$1"; local max_seconds="${2:-60}"
  local elapsed=0
  while [ "$elapsed" -lt "$max_seconds" ]; do
    local s; s="$(db_meeting_status "$meeting_id")"
    case "$s" in
      completed|failed|cancelled) echo "$s"; return 0 ;;
    esac
    sleep 2
    elapsed=$((elapsed+2))
  done
  echo "TIMEOUT"
  return 1
}

# Scenario runner: each writes verdict.json + supporting evidence
SCENARIOS_PASSED=0
SCENARIOS_FAILED=0
declare -a SCENARIO_LIST=()

record_verdict() {
  local dir="$1"; local pass="$2"; local note="$3"
  mkdir -p "$dir"
  cat > "$dir/verdict.json" <<EOF
{
  "scenario_dir": "$(basename "$dir")",
  "completed_at": "$(ts)",
  "pass": $pass,
  "note": "$note"
}
EOF
  if [ "$pass" = "true" ]; then
    SCENARIOS_PASSED=$((SCENARIOS_PASSED+1)); log "  ✅ PASS: $note"
  else
    SCENARIOS_FAILED=$((SCENARIOS_FAILED+1)); log "  ❌ FAIL: $note"
  fi
}

# ---------------------------------------------------------------
# Scenario 3: meeting bot — no_one_joined_timeout converges to terminal
# ---------------------------------------------------------------
scenario_3() {
  local DIR="$EVIDENCE_ROOT/scenario-03-no-joined-timeout"
  mkdir -p "$DIR"
  log "=== Scenario 3: no_one_joined_timeout ==="
  # Deploy listener bot to a fresh unused Meet code; do NOT admit; let timeout fire.
  # Use a real Meet URL pattern so bot reaches awaiting_admission where
  # the max_wait_for_admission timer fires; tight timeout = 20s.
  local NATIVE="ios-njgt-nnh"
  local payload
  payload=$(printf '{"platform":"google_meet","native_meeting_id":"%s","bot_name":"conv-3-listener","transcribe_enabled":true,"recording_enabled":true,"automatic_leave":{"no_one_joined_timeout":20000,"waiting_room_timeout":20000,"everyone_left_timeout":20000}}' "$NATIVE")
  local resp; resp=$(curl -s -X POST "$GATEWAY_URL/bots" -H "X-API-Key: $LISTENER_TOKEN" -H "Content-Type: application/json" -d "$payload" -w "\n%{http_code}")
  local code; code=$(echo "$resp" | tail -1)
  local body; body=$(echo "$resp" | sed '$d')
  echo "$body" > "$DIR/deploy-response.json"
  if [ "$code" != "201" ]; then
    record_verdict "$DIR" "false" "deploy returned HTTP $code"
    return
  fi
  local mid; mid=$(echo "$body" | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])" 2>/dev/null)
  local cname; cname=$(echo "$body" | python3 -c "import json,sys;print(json.load(sys.stdin)['bot_container_id'])" 2>/dev/null)
  log "  meeting_id=$mid container=$cname; waiting up to 180s for terminal status"
  local final; final=$(poll_db_terminal "$mid" 180)
  # Allow docker reap settling
  local container_exists="true"
  for i in $(seq 1 10); do
    if ! docker_container_exists "$cname"; then container_exists="false"; break; fi
    sleep 2
  done
  > "$DIR/db-states.jsonl"
  for k in status; do
    echo "{\"key\":\"$k\",\"value\":\"$final\"}" >> "$DIR/db-states.jsonl"
  done
  local cnt; cnt=$(ma_callback_count "$mid"); echo "$cnt" > "$DIR/callback-count.txt"
  local rkc; rkc=$(ra_session_state_key_count "$mid"); echo "$rkc" > "$DIR/redis-key-count.txt"
  echo "{\"container\":\"$cname\",\"still_present\":$container_exists}" > "$DIR/docker-state.json"
  # Pass: status is failed/completed, container removed, redis state cleared (<=2 keys)
  if [ "$final" != "TIMEOUT" ] && [ "$container_exists" = "false" ] && [ "$rkc" -le 2 ]; then
    record_verdict "$DIR" "true" "status=$final, container_removed, redis_keys=$rkc, callbacks=$cnt"
  else
    record_verdict "$DIR" "false" "status=$final container_present=$container_exists redis_keys=$rkc"
  fi
  SCENARIO_LIST+=("scenario-03-no-joined-timeout")
}

# ---------------------------------------------------------------
# Scenario 6: browser-session DELETE converges
# ---------------------------------------------------------------
scenario_6() {
  local DIR="$EVIDENCE_ROOT/scenario-06-browser-session-delete"
  mkdir -p "$DIR"
  log "=== Scenario 6: browser-session explicit DELETE ==="
  local NAME="browser-session-conv-6-$(date +%s)"
  local payload
  payload=$(printf '{"profile":"browser-session","user_id":"2","name":"%s","config":{"meeting_id":900006,"redisUrl":"redis://redis:6379/0","mode":"browser_session"}}' "$NAME")
  local resp; resp=$(curl -s -X POST "$RUNTIME_URL/containers" -H "Content-Type: application/json" -d "$payload" -w "\n%{http_code}")
  echo "$resp" | sed '$d' > "$DIR/create-response.json"
  local code; code=$(echo "$resp" | tail -1)
  if [ "$code" != "201" ]; then
    record_verdict "$DIR" "false" "create returned HTTP $code"
    return
  fi
  sleep 3
  local before_running; before_running="false"; if docker_container_running "$NAME"; then before_running="true"; fi
  log "  created container $NAME (running=$before_running); firing DELETE"
  local del; del=$(curl -s -X DELETE "$RUNTIME_URL/containers/$NAME" -w "\n%{http_code}")
  echo "$del" | sed '$d' > "$DIR/delete-response.json"
  local del_code; del_code=$(echo "$del" | tail -1)
  sleep 3
  local after_present; if docker_container_exists "$NAME"; then after_present="true"; else after_present="false"; fi
  echo "{\"name\":\"$NAME\",\"running_before_delete\":$before_running,\"present_after_delete\":$after_present,\"delete_http\":$del_code}" > "$DIR/state-after-delete.json"
  if [ "$del_code" = "200" ] && [ "$after_present" = "false" ]; then
    record_verdict "$DIR" "true" "DELETE 200, container removed in <=3s"
  else
    record_verdict "$DIR" "false" "delete_http=$del_code after_present=$after_present"
  fi
  SCENARIO_LIST+=("scenario-06-browser-session-delete")
}

# ---------------------------------------------------------------
# Scenario 10: stop during JOINING (DELETE within 3s of bot create)
# ---------------------------------------------------------------
scenario_10() {
  local DIR="$EVIDENCE_ROOT/scenario-10-stop-during-joining"
  mkdir -p "$DIR"
  log "=== Scenario 10: stop during JOINING ==="
  local NATIVE="conv-test-10-$(date +%s)"
  local payload
  payload=$(printf '{"platform":"google_meet","native_meeting_id":"%s","bot_name":"conv-10-listener","transcribe_enabled":true,"recording_enabled":true}' "$NATIVE")
  local resp; resp=$(curl -s -X POST "$GATEWAY_URL/bots" -H "X-API-Key: $LISTENER_TOKEN" -H "Content-Type: application/json" -d "$payload" -w "\n%{http_code}")
  local code; code=$(echo "$resp" | tail -1)
  local body; body=$(echo "$resp" | sed '$d')
  echo "$body" > "$DIR/deploy-response.json"
  if [ "$code" != "201" ]; then
    record_verdict "$DIR" "false" "deploy returned HTTP $code"
    return
  fi
  local mid; mid=$(echo "$body" | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])" 2>/dev/null)
  local cname; cname=$(echo "$body" | python3 -c "import json,sys;print(json.load(sys.stdin)['bot_container_id'])" 2>/dev/null)
  log "  bot $mid deployed; firing DELETE within 3s"
  sleep 2  # let bot reach "joining" not "active"
  local del; del=$(curl -s -X DELETE "$GATEWAY_URL/bots/google_meet/$NATIVE" -H "X-API-Key: $LISTENER_TOKEN" -w "\n%{http_code}")
  echo "$del" | sed '$d' > "$DIR/delete-response.json"
  local final; final=$(poll_db_terminal "$mid" 90)
  # Give docker a moment to actually reap after status flipped
  local container_exists="true"
  for i in $(seq 1 10); do
    if ! docker_container_exists "$cname"; then container_exists="false"; break; fi
    sleep 2
  done
  echo "{\"meeting_id\":\"$mid\",\"final_status\":\"$final\"}" > "$DIR/db-states.jsonl"
  local cnt; cnt=$(ma_callback_count "$mid"); echo "$cnt" > "$DIR/callback-count.txt"
  echo "{\"container\":\"$cname\",\"still_present\":$container_exists}" > "$DIR/docker-state.json"
  if [ "$final" != "TIMEOUT" ] && [ "$container_exists" = "false" ]; then
    record_verdict "$DIR" "true" "early-stop converged: status=$final callbacks=$cnt"
  else
    record_verdict "$DIR" "false" "status=$final container_present=$container_exists"
  fi
  SCENARIO_LIST+=("scenario-10-stop-during-joining")
}

# ---------------------------------------------------------------
# Scenario 9: concurrent stops (meeting bot + browser-session)
# ---------------------------------------------------------------
scenario_9() {
  local DIR="$EVIDENCE_ROOT/scenario-09-concurrent-stops"
  mkdir -p "$DIR"
  log "=== Scenario 9: concurrent DELETEs ==="
  # Spawn 1 meeting bot + 1 browser-session
  local NATIVE="conv-test-9-$(date +%s)"
  local BNAME="browser-session-conv-9-$(date +%s)"
  curl -s -X POST "$GATEWAY_URL/bots" -H "X-API-Key: $LISTENER_TOKEN" -H "Content-Type: application/json" \
    -d "{\"platform\":\"google_meet\",\"native_meeting_id\":\"$NATIVE\",\"bot_name\":\"conv-9-listener\",\"transcribe_enabled\":true,\"recording_enabled\":true}" \
    > "$DIR/bot-create.json"
  curl -s -X POST "$RUNTIME_URL/containers" -H "Content-Type: application/json" \
    -d "{\"profile\":\"browser-session\",\"user_id\":\"2\",\"name\":\"$BNAME\",\"config\":{\"meeting_id\":900009,\"redisUrl\":\"redis://redis:6379/0\",\"mode\":\"browser_session\"}}" \
    > "$DIR/browser-create.json"
  local mid; mid=$(python3 -c "import json;print(json.load(open('$DIR/bot-create.json')).get('id',''))" 2>/dev/null)
  log "  bot meeting_id=$mid + browser-session $BNAME deployed; firing parallel DELETEs"
  sleep 2
  (curl -s -X DELETE "$GATEWAY_URL/bots/google_meet/$NATIVE" -H "X-API-Key: $LISTENER_TOKEN" > "$DIR/bot-delete.json") &
  (curl -s -X DELETE "$RUNTIME_URL/containers/$BNAME" > "$DIR/browser-delete.json") &
  wait
  local final; final=$(poll_db_terminal "$mid" 90)
  local browser_present; if docker_container_exists "$BNAME"; then browser_present="true"; else browser_present="false"; fi
  echo "{\"bot_final_status\":\"$final\",\"browser_present_after\":$browser_present}" > "$DIR/state-after.json"
  if [ "$final" != "TIMEOUT" ] && [ "$browser_present" = "false" ]; then
    record_verdict "$DIR" "true" "both converged: bot=$final browser=removed"
  else
    record_verdict "$DIR" "false" "bot=$final browser_present=$browser_present"
  fi
  SCENARIO_LIST+=("scenario-09-concurrent-stops")
}

# ---------------------------------------------------------------
# Scenario 5: meeting bot force-kill (docker rm -f) -> sweep recovers
# ---------------------------------------------------------------
scenario_5() {
  local DIR="$EVIDENCE_ROOT/scenario-05-force-kill"
  mkdir -p "$DIR"
  log "=== Scenario 5: force-kill meeting bot ==="
  local NATIVE="conv-test-5-$(date +%s)"
  local payload
  payload=$(printf '{"platform":"google_meet","native_meeting_id":"%s","bot_name":"conv-5-listener","transcribe_enabled":true,"recording_enabled":true}' "$NATIVE")
  local resp; resp=$(curl -s -X POST "$GATEWAY_URL/bots" -H "X-API-Key: $LISTENER_TOKEN" -H "Content-Type: application/json" -d "$payload" -w "\n%{http_code}")
  local code; code=$(echo "$resp" | tail -1)
  local body; body=$(echo "$resp" | sed '$d')
  echo "$body" > "$DIR/deploy-response.json"
  if [ "$code" != "201" ]; then
    record_verdict "$DIR" "false" "deploy returned HTTP $code"
    return
  fi
  local mid; mid=$(echo "$body" | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])" 2>/dev/null)
  local cname; cname=$(echo "$body" | python3 -c "import json,sys;print(json.load(sys.stdin)['bot_container_id'])" 2>/dev/null)
  log "  meeting_id=$mid container=$cname; waiting 5s then force-killing"
  sleep 5
  if docker_container_running "$cname"; then
    docker rm -f "$cname" 2>&1 | head -1 > "$DIR/force-kill.txt"
  else
    echo "container not running" > "$DIR/force-kill.txt"
  fi
  local final; final=$(poll_db_terminal "$mid" 60)
  echo "{\"meeting_id\":\"$mid\",\"final_status\":\"$final\"}" > "$DIR/db-states.jsonl"
  local cnt; cnt=$(ma_callback_count "$mid"); echo "$cnt" > "$DIR/callback-count.txt"
  if [ "$final" != "TIMEOUT" ]; then
    record_verdict "$DIR" "true" "force-kill converged: status=$final callbacks=$cnt"
  else
    record_verdict "$DIR" "false" "status=$final after force-kill (sweep should have recovered)"
  fi
  SCENARIO_LIST+=("scenario-05-force-kill")
}

# ---------------------------------------------------------------
# Scenario 11: instant start-stop (DELETE within ~100ms of POST)
# ---------------------------------------------------------------
scenario_11() {
  local DIR="$EVIDENCE_ROOT/scenario-11-instant-start-stop"
  mkdir -p "$DIR"
  log "=== Scenario 11: instant start-stop (no sleep) ==="
  local NATIVE="conv-test-11-$(date +%s)"
  local payload
  payload=$(printf '{"platform":"google_meet","native_meeting_id":"%s","bot_name":"conv-11-listener","transcribe_enabled":true,"recording_enabled":true}' "$NATIVE")
  local resp; resp=$(curl -s -X POST "$GATEWAY_URL/bots" -H "X-API-Key: $LISTENER_TOKEN" -H "Content-Type: application/json" -d "$payload" -w "\n%{http_code}")
  local code; code=$(echo "$resp" | tail -1)
  local body; body=$(echo "$resp" | sed '$d')
  echo "$body" > "$DIR/deploy-response.json"
  if [ "$code" != "201" ]; then
    record_verdict "$DIR" "false" "deploy returned HTTP $code"
    return
  fi
  local mid; mid=$(echo "$body" | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])" 2>/dev/null)
  local cname; cname=$(echo "$body" | python3 -c "import json,sys;print(json.load(sys.stdin)['bot_container_id'])" 2>/dev/null)
  # NO SLEEP - fire DELETE immediately
  local del; del=$(curl -s -X DELETE "$GATEWAY_URL/bots/google_meet/$NATIVE" -H "X-API-Key: $LISTENER_TOKEN" -w "\n%{http_code}")
  echo "$del" | sed '$d' > "$DIR/delete-response.json"
  local del_code; del_code=$(echo "$del" | tail -1)
  log "  bot $mid created + DELETEd in same shell tick (HTTP $del_code)"
  local final; final=$(poll_db_terminal "$mid" 120)
  local container_exists="true"
  for i in $(seq 1 10); do
    if ! docker_container_exists "$cname"; then container_exists="false"; break; fi
    sleep 2
  done
  echo "{\"final_status\":\"$final\",\"container_present\":$container_exists,\"delete_http\":$del_code}" > "$DIR/state-after.json"
  if [ "$final" != "TIMEOUT" ] && [ "$container_exists" = "false" ] && [ "$del_code" = "202" -o "$del_code" = "200" ]; then
    record_verdict "$DIR" "true" "instant DELETE converged: status=$final del_http=$del_code"
  else
    record_verdict "$DIR" "false" "status=$final container_present=$container_exists del_http=$del_code"
  fi
  SCENARIO_LIST+=("scenario-11-instant-start-stop")
}

# ---------------------------------------------------------------
# Scenario 12: double-stop idempotency (DELETE already-stopped bot)
# ---------------------------------------------------------------
scenario_12() {
  local DIR="$EVIDENCE_ROOT/scenario-12-double-stop-idempotency"
  mkdir -p "$DIR"
  log "=== Scenario 12: double-stop idempotency ==="
  local NATIVE="conv-test-12-$(date +%s)"
  local payload
  payload=$(printf '{"platform":"google_meet","native_meeting_id":"%s","bot_name":"conv-12-listener","transcribe_enabled":true,"recording_enabled":true}' "$NATIVE")
  local body; body=$(curl -s -X POST "$GATEWAY_URL/bots" -H "X-API-Key: $LISTENER_TOKEN" -H "Content-Type: application/json" -d "$payload")
  echo "$body" > "$DIR/deploy-response.json"
  local mid; mid=$(echo "$body" | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])" 2>/dev/null)
  if [ -z "$mid" ]; then record_verdict "$DIR" "false" "deploy failed"; return; fi
  sleep 3
  # First DELETE
  local d1; d1=$(curl -s -X DELETE "$GATEWAY_URL/bots/google_meet/$NATIVE" -H "X-API-Key: $LISTENER_TOKEN" -w "\n%{http_code}")
  local d1_code; d1_code=$(echo "$d1" | tail -1)
  echo "$d1" | sed '$d' > "$DIR/delete-1.json"
  local final1; final1=$(poll_db_terminal "$mid" 60)
  # Second DELETE on already-terminal meeting
  local d2; d2=$(curl -s -X DELETE "$GATEWAY_URL/bots/google_meet/$NATIVE" -H "X-API-Key: $LISTENER_TOKEN" -w "\n%{http_code}")
  local d2_code; d2_code=$(echo "$d2" | tail -1)
  echo "$d2" | sed '$d' > "$DIR/delete-2.json"
  local final2; final2=$(db_meeting_status "$mid")
  echo "{\"first_delete_http\":$d1_code,\"first_final\":\"$final1\",\"second_delete_http\":$d2_code,\"status_after_second\":\"$final2\"}" > "$DIR/state-after.json"
  # Pass: first DELETE accepted (202/200), bot reaches terminal, second DELETE returns 4xx or no-op (NOT 5xx), terminal status preserved
  if [ "$final1" != "TIMEOUT" ] && [ "$final1" = "$final2" ] && { [ "$d2_code" = "404" ] || [ "$d2_code" = "409" ] || [ "$d2_code" = "200" ] || [ "$d2_code" = "202" ]; }; then
    record_verdict "$DIR" "true" "idempotent: 1st=$d1_code→$final1, 2nd=$d2_code (status preserved)"
  else
    record_verdict "$DIR" "false" "1st=$d1_code→$final1, 2nd=$d2_code→$final2"
  fi
  SCENARIO_LIST+=("scenario-12-double-stop-idempotency")
}

# ---------------------------------------------------------------
# Scenario 13: stop nonexistent bot (404, clean)
# ---------------------------------------------------------------
scenario_13() {
  local DIR="$EVIDENCE_ROOT/scenario-13-stop-nonexistent"
  mkdir -p "$DIR"
  log "=== Scenario 13: stop nonexistent bot ==="
  local NATIVE="nonexistent-$(date +%s)"
  local d; d=$(curl -s -X DELETE "$GATEWAY_URL/bots/google_meet/$NATIVE" -H "X-API-Key: $LISTENER_TOKEN" -w "\n%{http_code}")
  local code; code=$(echo "$d" | tail -1)
  echo "$d" | sed '$d' > "$DIR/delete-response.json"
  echo "{\"http\":$code}" > "$DIR/state-after.json"
  # Pass: clean 404 (not 5xx), no side effects
  if [ "$code" = "404" ]; then
    record_verdict "$DIR" "true" "404 returned for nonexistent bot (no 5xx)"
  else
    record_verdict "$DIR" "false" "HTTP $code (expected 404)"
  fi
  SCENARIO_LIST+=("scenario-13-stop-nonexistent")
}

# ---------------------------------------------------------------
# Scenario 14: rapid start-stop loop (5x same URL, no leaks)
# ---------------------------------------------------------------
scenario_14() {
  local DIR="$EVIDENCE_ROOT/scenario-14-rapid-loop"
  mkdir -p "$DIR"
  log "=== Scenario 14: rapid start-stop loop (5x) ==="
  local NATIVE="conv-test-14-$(date +%s)"
  local payload
  payload=$(printf '{"platform":"google_meet","native_meeting_id":"%s","bot_name":"conv-14-listener","transcribe_enabled":true,"recording_enabled":true}' "$NATIVE")
  local containers_before; containers_before=$(docker ps -a --format '{{.Names}}' | grep -c '^meeting-' || true)
  local all_ids="["
  local i
  for i in 1 2 3 4 5; do
    local body; body=$(curl -s -X POST "$GATEWAY_URL/bots" -H "X-API-Key: $LISTENER_TOKEN" -H "Content-Type: application/json" -d "$payload")
    local mid; mid=$(echo "$body" | python3 -c "import json,sys;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
    if [ -z "$mid" ]; then
      record_verdict "$DIR" "false" "iteration $i deploy failed: $(echo $body | head -c 200)"
      return
    fi
    all_ids="${all_ids}${mid},"
    sleep 2
    curl -s -X DELETE "$GATEWAY_URL/bots/google_meet/$NATIVE" -H "X-API-Key: $LISTENER_TOKEN" >/dev/null
    sleep 3
  done
  all_ids="${all_ids%,}]"
  echo "$all_ids" > "$DIR/all-meeting-ids.json"
  sleep 10  # let cleanups complete
  local containers_after; containers_after=$(docker ps -a --format '{{.Names}}' | grep -c '^meeting-' || true)
  # Check none of the 5 are still in a non-terminal status
  local stuck; stuck=$(docker exec "$PG_CONTAINER" psql -U postgres -d vexa -tA -c \
    "SELECT count(*) FROM meetings WHERE id = ANY($(echo $all_ids | tr -d '[]' | awk -F, '{printf "ARRAY["; for(i=1;i<=NF;i++){if(i>1)printf ","; printf $i} printf "]"}')) AND status NOT IN ('completed','failed','cancelled');" 2>/dev/null | tr -d ' \n')
  echo "{\"containers_before\":$containers_before,\"containers_after\":$containers_after,\"stuck_meetings\":$stuck}" > "$DIR/state-after.json"
  if [ "$stuck" = "0" ] && [ "$containers_after" -le "$((containers_before + 1))" ]; then
    record_verdict "$DIR" "true" "5x loop: 0 stuck meetings, container delta $((containers_after - containers_before))"
  else
    record_verdict "$DIR" "false" "stuck=$stuck containers_before=$containers_before containers_after=$containers_after"
  fi
  SCENARIO_LIST+=("scenario-14-rapid-loop")
}

# ---------------------------------------------------------------
# Run scenarios sequentially
# ---------------------------------------------------------------
log "Lifecycle convergence runner starting"
log "Evidence root: $EVIDENCE_ROOT"

scenario_6   # cheapest: browser-session DELETE
scenario_5   # meeting bot force-kill
scenario_10  # stop during JOINING
scenario_11  # instant start-stop (no sleep at all)
scenario_12  # double-stop idempotency
scenario_13  # stop nonexistent bot
scenario_14  # rapid start-stop loop
scenario_9   # concurrent stops
scenario_3   # max_wait_for_admission timeout (slowest; runs last)

# Write summary
cat > "$EVIDENCE_ROOT/summary.json" <<EOF
{
  "ran_at": "$(ts)",
  "passed": $SCENARIOS_PASSED,
  "failed": $SCENARIOS_FAILED,
  "scenarios": [
$(for s in "${SCENARIO_LIST[@]}"; do printf '    "%s",\n' "$s"; done | sed '$s/,$//')
  ]
}
EOF
log "=========================================="
log "summary: $SCENARIOS_PASSED passed, $SCENARIOS_FAILED failed"
log "see $EVIDENCE_ROOT/summary.json"
exit $SCENARIOS_FAILED

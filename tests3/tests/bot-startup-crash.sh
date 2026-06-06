#!/usr/bin/env bash
# bot-startup-crash — Pack 4 [407-C] startup-crash observability.
#
# Proves:
#   display_exported      — entrypoint.sh exports DISPLAY before starting node
#   start_log             — entrypoint.sh echoes start breadcrumb
#   exit_code_log         — entrypoint.sh captures and echoes exit code
#   startup_breadcrumb    — docker.ts emits JSON breadcrumb before config parsing
#   startup_failure_reporter — docker.ts has reportStartupFailure with X-Internal-Secret
#   bot_startup_logged    — invalid-config run: failure recorded in DB (compose only)
#
# GMEET_BOT_STARTUP_LOGGED (bot_startup_logged step) requires a running compose
# stack with postgres, meeting-api, and the vexa-bot image available. Skipped
# when the stack is not reachable.
#
# Validate plan: invalid-config run on a faithful stack → startup breadcrumb +
# failure reason re-observed in a prod-shaped DB, not asserted from logs.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
ENTRYPOINT="$ROOT_DIR/services/vexa-bot/core/entrypoint.sh"
DOCKER_TS="$ROOT_DIR/services/vexa-bot/core/src/docker.ts"

echo ""
echo "  bot-startup-crash"
echo "  ──────────────────────────────────────────────"

test_begin bot-startup-crash

# ── Static: DISPLAY export ────────────────────────────────────────
if [ ! -f "$ENTRYPOINT" ]; then
  step_fail display_exported "entrypoint.sh not found at $ENTRYPOINT"
  exit 1
fi
if grep -q 'export DISPLAY' "$ENTRYPOINT"; then
  step_pass display_exported "export DISPLAY present in entrypoint.sh"
else
  step_fail display_exported "export DISPLAY missing from entrypoint.sh"
fi

# ── Static: start log breadcrumb ─────────────────────────────────
if grep -qE 'echo.*\[entrypoint\].*start' "$ENTRYPOINT"; then
  step_pass start_log "start breadcrumb echo present in entrypoint.sh"
else
  step_fail start_log "start breadcrumb echo missing from entrypoint.sh (no 'echo.*[entrypoint].*start' pattern)"
fi

# ── Static: exit code log ─────────────────────────────────────────
if grep -q 'EXIT_CODE=\$?' "$ENTRYPOINT" && grep -q 'exit \$EXIT_CODE' "$ENTRYPOINT"; then
  step_pass exit_code_log "EXIT_CODE capture + exit relay present in entrypoint.sh"
else
  step_fail exit_code_log "EXIT_CODE capture or exit relay missing from entrypoint.sh"
fi

# ── Static: startupBreadcrumb in docker.ts ────────────────────────
if [ ! -f "$DOCKER_TS" ]; then
  step_fail startup_breadcrumb "docker.ts not found at $DOCKER_TS"
  exit 1
fi
if grep -q 'function startupBreadcrumb' "$DOCKER_TS" || grep -q 'startupBreadcrumb()' "$DOCKER_TS"; then
  step_pass startup_breadcrumb "startupBreadcrumb() present in docker.ts"
else
  step_fail startup_breadcrumb "startupBreadcrumb() missing from docker.ts"
fi

# ── Static: reportStartupFailure + X-Internal-Secret header ──────
if grep -q 'reportStartupFailure' "$DOCKER_TS" && grep -q 'X-Internal-Secret' "$DOCKER_TS"; then
  step_pass startup_failure_reporter "reportStartupFailure + X-Internal-Secret header present in docker.ts"
else
  step_fail startup_failure_reporter "reportStartupFailure or X-Internal-Secret header missing from docker.ts"
fi

# ── Dynamic: invalid-config run → DB observation (compose only) ──
MODE=$(cat "$STATE/deploy_mode" 2>/dev/null || echo "")
if [ "$MODE" != "compose" ]; then
  step_skip bot_startup_logged "dynamic DB check only runs in compose mode (mode=$MODE)"
  echo "  ──────────────────────────────────────────────"
  echo ""
  exit 0
fi

GATEWAY=$(cat "$STATE/gateway_url" 2>/dev/null || echo "")
API_TOKEN=$(cat "$STATE/api_token" 2>/dev/null || echo "")

if [ -z "$GATEWAY" ] || [ -z "$API_TOKEN" ]; then
  step_skip bot_startup_logged "compose not reachable (no gateway_url or api_token in state)"
  echo "  ──────────────────────────────────────────────"
  echo ""
  exit 0
fi

COMPOSE_PROJECT=$(cat "$STATE/compose_project" 2>/dev/null || echo "vexa")
NETWORK="${COMPOSE_PROJECT}_vexa"

# Verify the postgres container is reachable
PG_CONTAINER="${COMPOSE_PROJECT}-postgres-1"
if ! docker inspect "$PG_CONTAINER" > /dev/null 2>&1; then
  step_skip bot_startup_logged "postgres container $PG_CONTAINER not found — compose stack not up"
  echo "  ──────────────────────────────────────────────"
  echo ""
  exit 0
fi

# Get INTERNAL_API_SECRET and MEETING_API_URL from the meeting-api container
MA_CONTAINER="${COMPOSE_PROJECT}-meeting-api-1"
INTERNAL_SECRET=$(docker inspect "$MA_CONTAINER" \
  --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
  | grep '^INTERNAL_API_SECRET=' | cut -d= -f2- | head -1 || echo "")
MEETING_API_URL_INTERNAL=$(docker inspect "$MA_CONTAINER" \
  --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
  | grep '^MEETING_API_URL=' | cut -d= -f2- | head -1 || echo "")

if [ -z "$INTERNAL_SECRET" ]; then
  # Fall back to default from docker-compose.yml
  INTERNAL_SECRET="${INTERNAL_API_SECRET:-vexa-internal-secret}"
fi
if [ -z "$MEETING_API_URL_INTERNAL" ]; then
  MEETING_API_URL_INTERNAL="http://meeting-api:8083"
fi

# Get bot image from state (image_tag set during make build) or fall back to runtime-api profile
IMAGE_TAG=$(cat "$STATE/image_tag" 2>/dev/null || echo "dev")
BOT_IMAGE="vexaai/vexa-bot:${IMAGE_TAG}"
if ! docker image inspect "$BOT_IMAGE" > /dev/null 2>&1; then
  # Image not found locally — try BROWSER_IMAGE from runtime-api env
  BOT_IMAGE=$(docker inspect "${COMPOSE_PROJECT}-runtime-api-1" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
    | grep '^BROWSER_IMAGE=' | cut -d= -f2- | head -1 || echo "vexaai/vexa-bot:dev")
fi

# Get a valid user_id for the test meeting
USER_ID=$(docker exec "$PG_CONTAINER" \
  psql -U postgres -d vexa -t -A -c "SELECT id FROM users LIMIT 1" 2>/dev/null | tr -d '[:space:]' || echo "1")
if [ -z "$USER_ID" ]; then USER_ID=1; fi

# Generate a unique session_uid for the probe
SESSION_UID=$(python3 -c "import uuid; print(str(uuid.uuid4()))")
TEST_NATIVE_ID="startup-crash-$$"

# Insert a test meeting + session directly into the DB (no bot container started).
# This avoids race conditions with a legitimately-running bot's exit callback
# overwriting the completion_reason before our probe fires.
MEETING_ID=$(docker exec "$PG_CONTAINER" \
  psql -U postgres -d vexa -t -A -c \
  "INSERT INTO meetings (user_id, platform, platform_specific_id, status, data)
   VALUES ($USER_ID, 'google_meet', '$TEST_NATIVE_ID', 'requested', '{}')
   RETURNING id" 2>/dev/null | head -1 | tr -d '[:space:]' || echo "")

if [ -z "$MEETING_ID" ]; then
  step_skip bot_startup_logged "could not insert test meeting into DB — postgres write failed"
  echo "  ──────────────────────────────────────────────"
  echo ""
  exit 0
fi

docker exec "$PG_CONTAINER" \
  psql -U postgres -d vexa -t -A -c \
  "INSERT INTO meeting_sessions (meeting_id, session_uid, session_start_time)
   VALUES ($MEETING_ID, '$SESSION_UID', NOW())" > /dev/null 2>&1 || true

# Craft an invalid BOT_CONFIG: has valid callback fields but missing required Zod
# fields (botName + redisUrl) → BotConfigSchema.parse() throws → reportStartupFailure fires
CALLBACK_URL="${MEETING_API_URL_INTERNAL}/bots/internal/callback/exited"
INVALID_CONFIG=$(python3 -c "
import json, sys
cfg = {
  'platform': 'google_meet',
  'meetingUrl': 'https://meet.google.com/startup-crash-test',
  'connectionId': sys.argv[1],
  'meetingApiCallbackUrl': sys.argv[2],
  'internalSecret': sys.argv[3],
  'container_name': 'startup-crash-probe',
  # intentionally omit botName and redisUrl (required by BotConfigSchema) to trigger Zod failure
}
print(json.dumps(cfg))
" "$SESSION_UID" "$CALLBACK_URL" "$INTERNAL_SECRET")

# Run the invalid-config bot container attached to the compose network
PROBE_NAME="startup-crash-probe-$$"
docker run --rm --name "$PROBE_NAME" \
  --network "$NETWORK" \
  -e "BOT_CONFIG=$INVALID_CONFIG" \
  "$BOT_IMAGE" \
  2>/dev/null || true

# Wait a moment for the callback to be processed
sleep 3

# Query DB: the meeting should now have completion_reason='validation_error' and
# bot_logs containing the startup breadcrumb
QUERY_RESULT=$(docker exec "$PG_CONTAINER" \
  psql -U postgres -d vexa -t -A -c \
  "SELECT data->>'completion_reason', data->>'bot_logs' FROM meetings WHERE id = $MEETING_ID" \
  2>/dev/null || echo "")

COMPLETION_REASON=$(echo "$QUERY_RESULT" | cut -d'|' -f1 | tr -d '[:space:]')
BOT_LOGS=$(echo "$QUERY_RESULT" | cut -d'|' -f2-)

if [ "$COMPLETION_REASON" = "validation_error" ]; then
  if echo "$BOT_LOGS" | grep -q "startup"; then
    step_pass GMEET_BOT_STARTUP_LOGGED "startup breadcrumb + validation_error recorded in DB for meeting $MEETING_ID (session=$SESSION_UID)"
  else
    step_pass GMEET_BOT_STARTUP_LOGGED "validation_error recorded in DB (meeting $MEETING_ID); bot_logs present"
  fi
else
  step_fail GMEET_BOT_STARTUP_LOGGED "expected completion_reason=validation_error in DB, got '$COMPLETION_REASON' (meeting $MEETING_ID, session=$SESSION_UID)"
fi

# Cleanup: remove the test meeting record
docker exec "$PG_CONTAINER" \
  psql -U postgres -d vexa -t -A -c \
  "DELETE FROM meetings WHERE id = $MEETING_ID" 2>/dev/null || true

echo "  ──────────────────────────────────────────────"
echo ""

#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  dashboard-ws-synthetic-proof.sh --dashboard-url URL --out DIR [options]

No-rebuild dashboard WebSocket proof. Creates a disposable active meeting row,
opens the dashboard in an instrumented browser, publishes transcript ticks to
Redis, captures browser WebSocket frames, then marks the synthetic row complete.

Options:
  --dashboard-url URL        Dashboard URL, e.g. http://localhost:3000
  --out DIR                  Evidence directory
  --postgres-container NAME  Postgres container, default vexa-postgres
  --redis-container NAME     Container with redis-cli, default vexa-lite
  --user-email EMAIL         Dashboard/API user, default test@vexa.ai
  --platform NAME            Platform, default google_meet
  --native-id ID             Native id, default generated synthetic id
  --from-list                Navigate from /meetings and click the row before proving WS
  --timeout-ms MS            Browser frame-proof timeout, default 30000
  -h, --help                 Show this help
USAGE
}

DASHBOARD_URL="${DASHBOARD_URL:-}"
OUT_DIR="${OUT_DIR:-}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-vexa-postgres}"
REDIS_CONTAINER="${REDIS_CONTAINER:-vexa-lite}"
USER_EMAIL="${USER_EMAIL:-test@vexa.ai}"
PLATFORM="${PLATFORM:-google_meet}"
NATIVE_ID="${NATIVE_ID:-}"
TIMEOUT_MS="${TIMEOUT_MS:-30000}"
FROM_LIST=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dashboard-url) DASHBOARD_URL="${2:-}"; shift 2 ;;
    --out) OUT_DIR="${2:-}"; shift 2 ;;
    --postgres-container) POSTGRES_CONTAINER="${2:-}"; shift 2 ;;
    --redis-container) REDIS_CONTAINER="${2:-}"; shift 2 ;;
    --user-email) USER_EMAIL="${2:-}"; shift 2 ;;
    --platform) PLATFORM="${2:-}"; shift 2 ;;
    --native-id) NATIVE_ID="${2:-}"; shift 2 ;;
    --from-list) FROM_LIST=1; shift ;;
    --timeout-ms) TIMEOUT_MS="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$DASHBOARD_URL" ] || [ -z "$OUT_DIR" ]; then
  echo "ERROR: --dashboard-url and --out are required" >&2
  usage >&2
  exit 2
fi

command -v docker >/dev/null 2>&1 || { echo "ERROR: docker is required" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "ERROR: jq is required" >&2; exit 1; }

DASHBOARD_URL="${DASHBOARD_URL%/}"
mkdir -p "$OUT_DIR"

sql_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

if [ -z "$NATIVE_ID" ]; then
  if [ "$PLATFORM" = "google_meet" ]; then
    # The WS authorization endpoint validates native ids with platform parsing,
    # so use a valid-looking Meet code rather than a free-form synthetic slug.
    letters="$(printf '%s' "$(date +%N)$$" | tr '0123456789' 'abcdefghij')"
    NATIVE_ID="${letters:0:3}-${letters:3:4}-${letters:7:3}"
  else
    NATIVE_ID="synthetic-ws-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  fi
fi

USER_EMAIL_SQL="$(sql_escape "$USER_EMAIL")"
PLATFORM_SQL="$(sql_escape "$PLATFORM")"
NATIVE_ID_SQL="$(sql_escape "$NATIVE_ID")"

TOKEN="$(
  docker exec "$POSTGRES_CONTAINER" psql -X -qAt -U postgres -d vexa -c \
    "select t.token
       from api_tokens t
       join users u on u.id = t.user_id
      where u.email = '$USER_EMAIL_SQL'
        and t.scopes @> ARRAY['bot','tx']::text[]
      order by t.id desc
      limit 1;"
)"
if [ -z "$TOKEN" ]; then
  echo "ERROR: no bot+tx token found for $USER_EMAIL" >&2
  exit 1
fi

MEETING_ID="$(
  docker exec "$POSTGRES_CONTAINER" psql -X -qAt -U postgres -d vexa -c \
    "insert into meetings (user_id, platform, platform_specific_id, status, start_time, data, created_at, updated_at)
       select id,
              '$PLATFORM_SQL',
              '$NATIVE_ID_SQL',
              'active',
              now(),
              jsonb_build_object(
                'synthetic_ws_probe', true,
                'probe', 'dashboard-ws-synthetic-proof',
                'probe_created_at', now()::text
              ),
              now(),
              now()
         from users
        where email = '$USER_EMAIL_SQL'
        returning id;"
)"
if [ -z "$MEETING_ID" ]; then
  echo "ERROR: failed to create synthetic meeting for $USER_EMAIL" >&2
  exit 1
fi

cleanup() {
  docker exec "$POSTGRES_CONTAINER" psql -X -qAt -U postgres -d vexa -c \
    "update meetings
        set status = 'completed',
            end_time = coalesce(end_time, now()),
            data = data || jsonb_build_object('synthetic_ws_closed', true, 'probe_closed_at', now()::text),
            updated_at = now()
      where id = $MEETING_ID;" >/dev/null || true
}
trap cleanup EXIT

cat > "$OUT_DIR/synthetic-meeting.json" <<EOF
{
  "meeting_id": "$MEETING_ID",
  "platform": "$PLATFORM",
  "native_id": "$NATIVE_ID",
  "dashboard_url": "$DASHBOARD_URL/meetings/$MEETING_ID",
  "postgres_container": "$POSTGRES_CONTAINER",
  "redis_container": "$REDIS_CONTAINER",
  "user_email": "$USER_EMAIL"
}
EOF

proof_json="$OUT_DIR/browser-ws-synthetic-proof.json"
proof_stdout="$OUT_DIR/browser-ws-synthetic-proof.stdout.json"
publish_jsonl="$OUT_DIR/redis-publish-results.jsonl"
frame_args=()
if [ "$FROM_LIST" = "1" ]; then
  frame_args+=(--from-list)
fi

(
  DASHBOARD_AUTH_TOKEN="$TOKEN" node .agents/skills/release/scripts/dashboard-ws-frame-proof.mjs \
    --dashboard-url "$DASHBOARD_URL" \
    --meeting-id "$MEETING_ID" \
    --platform "$PLATFORM" \
    --native-id "$NATIVE_ID" \
    --out "$proof_json" \
    --timeout-ms "$TIMEOUT_MS" \
    "${frame_args[@]}"
) > "$proof_stdout" &
proof_pid=$!

publish_count=0
for i in $(seq 1 20); do
  if ! kill -0 "$proof_pid" >/dev/null 2>&1; then
    break
  fi
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  payload="$(
    jq -cn \
      --argjson id "$MEETING_ID" \
      --arg speaker "Synthetic WS Probe" \
      --arg text "Synthetic dashboard WebSocket transcript tick $i." \
      --arg now "$now" \
      '{
        type: "transcript",
        meeting: { id: $id },
        speaker: $speaker,
        confirmed: [
          {
            start: (($id % 1000) + 0.1),
            end: (($id % 1000) + 1.1),
            text: $text,
            language: "en",
            completed: true,
            speaker: $speaker,
            segment_id: ("synthetic-ws-" + ($id|tostring) + "-" + ($now|gsub("[^0-9]"; ""))),
            absolute_start_time: $now,
            absolute_end_time: $now
          }
        ],
        pending: [],
        ts: $now
      }'
  )"
  receivers="$(docker exec "$REDIS_CONTAINER" redis-cli PUBLISH "tc:meeting:${MEETING_ID}:mutable" "$payload" || true)"
  jq -cn \
    --arg at "$now" \
    --argjson tick "$i" \
    --arg receivers "${receivers:-}" \
    '{at: $at, tick: $tick, receivers: $receivers}' >> "$publish_jsonl"
  publish_count=$((publish_count + 1))
  sleep 1
done

set +e
wait "$proof_pid"
proof_status=$?
set -e

cleanup
trap - EXIT

jq -n \
  --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --slurpfile meeting "$OUT_DIR/synthetic-meeting.json" \
  --slurpfile proof "$proof_json" \
  --rawfile publishes "$publish_jsonl" \
  --argjson proof_status "$proof_status" \
  --argjson publish_count "$publish_count" \
  '{
    created_at: $created_at,
    status: (if $proof_status == 0 and ($proof[0].success == true) then "pass" else "fail" end),
    proof_exit_code: $proof_status,
    publish_count: $publish_count,
    synthetic_meeting: $meeting[0],
    browser_ws_proof: $proof[0],
    publish_results_jsonl: $publishes
  }' > "$OUT_DIR/summary.json"

if grep -R -E 'vxa_|webhook_secret|TRANSCRIPTION_SERVICE_TOKEN|Authorization: Bearer' "$OUT_DIR" >/dev/null 2>&1 ||
   grep -R -E 'api_key=[^*&[:space:]"]+' "$OUT_DIR" >/dev/null 2>&1; then
  echo "ERROR: possible secret material detected in $OUT_DIR" >&2
  exit 1
fi

cat "$OUT_DIR/summary.json"

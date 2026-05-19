#!/usr/bin/env bash
# local-human-mechanical-gate — machine-owned checks that must be green before
# the human spends attention on LOCAL UI validation.

set -euo pipefail

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
test_begin "local-human-mechanical-gate"

failures=0

check_url() {
  local url="$1"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || echo 000)"
  [[ "$code" =~ ^2[0-9][0-9]$ ]]
}

if check_url "http://localhost:3100/login" \
   && check_url "http://localhost:8156/docs" \
   && check_url "http://localhost:3001/login" \
   && check_url "http://localhost:8056/docs" \
   && check_url "http://localhost:8057/docs"; then
  step_pass LOCAL_HUMAN_TARGET_URLS_READY "lite + compose validation URLs return 2xx"
else
  step_fail LOCAL_HUMAN_TARGET_URLS_READY "one or more LOCAL validation URLs did not return 2xx"
  failures=$((failures + 1))
fi

container_healthy() {
  local name="$1"
  local state health
  state="$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || true)"
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name" 2>/dev/null || true)"
  [[ "$state" == "running" && ( "$health" == "healthy" || "$health" == "none" ) ]]
}

if container_healthy vexa-lite \
   && container_healthy vexa-lite-postgres \
   && container_healthy vexa-api-gateway-1 \
   && container_healthy vexa-admin-api-1 \
   && container_healthy vexa-meeting-api-1 \
   && container_healthy vexa-runtime-api-1 \
   && container_healthy vexa-tts-service-1; then
  step_pass LOCAL_HUMAN_CONTAINERS_HEALTHY "LOCAL lite + compose containers are running/healthy"
else
  step_fail LOCAL_HUMAN_CONTAINERS_HEALTHY "one or more LOCAL containers are not running/healthy"
  failures=$((failures + 1))
fi

TX_URL="$(grep -E '^TRANSCRIPTION_SERVICE_URL=' "$ROOT_DIR/deploy/compose/.env" 2>/dev/null | head -1 | cut -d= -f2- || true)"
if echo "$TX_URL" | grep -qE '^https?://transcription-lb([/:]|$)'; then
  CONFIGURED_TX_WORKERS="$(docker exec transcription-lb sh -c "grep -E '^[[:space:]]*server[[:space:]]+transcription-worker-[0-9]+:8000' /etc/nginx/nginx.conf | sed -E 's/^[[:space:]]*server[[:space:]]+([^:]+):.*/\1/'" 2>/dev/null || true)"
  BAD_TX_WORKERS=""
  while IFS= read -r worker; do
    [[ -z "$worker" ]] && continue
    if ! container_healthy "$worker"; then
      BAD_TX_WORKERS+="$worker "
    fi
  done <<< "$CONFIGURED_TX_WORKERS"
  BOT_NETWORK="$(docker exec vexa-runtime-api-1 printenv DOCKER_NETWORK 2>/dev/null || true)"
  [[ -n "$BOT_NETWORK" ]] || BOT_NETWORK="vexa_vexa"
  LB_ON_BOT_NETWORK="$(docker network inspect "$BOT_NETWORK" -f '{{range $id, $c := .Containers}}{{println $c.Name}}{{end}}' 2>/dev/null | grep -Fx 'transcription-lb' || true)"
  RUNTIME_RESOLVES_LB="$(docker exec vexa-runtime-api-1 getent hosts transcription-lb 2>/dev/null || true)"
  MEETING_URL="$(docker exec vexa-meeting-api-1 printenv TRANSCRIPTION_SERVICE_URL 2>/dev/null || true)"
  RUNTIME_URL="$(docker exec vexa-runtime-api-1 printenv TRANSCRIPTION_SERVICE_URL 2>/dev/null || true)"
  if container_healthy transcription-lb \
     && [ -n "$CONFIGURED_TX_WORKERS" ] \
     && [ -z "$BAD_TX_WORKERS" ] \
     && [ -n "$LB_ON_BOT_NETWORK" ] \
     && [ -n "$RUNTIME_RESOLVES_LB" ] \
     && [ "$MEETING_URL" = "$TX_URL" ] \
     && [ "$RUNTIME_URL" = "$TX_URL" ]; then
    step_pass LOCAL_HUMAN_TRANSCRIPTION_LB_READY "transcription-lb upstreams healthy and resolvable from bot network $BOT_NETWORK: $(echo "$CONFIGURED_TX_WORKERS" | tr '\n' ' ')"
  else
    step_fail LOCAL_HUMAN_TRANSCRIPTION_LB_READY "TRANSCRIPTION_SERVICE_URL=$TX_URL but local transcription topology is not ready; bot_network=$BOT_NETWORK lb_on_network=${LB_ON_BOT_NETWORK:-no} runtime_resolves=${RUNTIME_RESOLVES_LB:-no} meeting_url=${MEETING_URL:-unset} runtime_url=${RUNTIME_URL:-unset} configured=[$(echo "$CONFIGURED_TX_WORKERS" | tr '\n' ' ')] bad=[$BAD_TX_WORKERS]"
    failures=$((failures + 1))
  fi
else
  step_pass LOCAL_HUMAN_TRANSCRIPTION_LB_READY "TRANSCRIPTION_SERVICE_URL does not target local transcription-lb ($TX_URL)"
fi

recent_log_errors() {
  local name="$1"
  docker logs --since "${LOCAL_HUMAN_LOG_SINCE:-3m}" "$name" 2>&1 \
    | grep -Ei '(^|[[:space:]])(ERROR|Traceback|Exception|CRITICAL|FATAL)([[:space:]:]|$)' \
    | grep -Evi 'HTTP/1\.1" 503|503 Service Unavailable|(^|[[:space:]])WARNING([[:space:]:]|$)|(^|[[:space:]])WARN([[:space:]:]|$)|(^|[[:space:]])W:' || true
}

LOG_PROBLEMS="$(
  for name in vexa-lite vexa-api-gateway-1 vexa-admin-api-1 vexa-meeting-api-1 vexa-runtime-api-1 vexa-tts-service-1 transcription-lb; do
    docker ps -a --format '{{.Names}}' | grep -qx "$name" || continue
    out="$(recent_log_errors "$name")"
    if [[ -n "$out" ]]; then
      printf '%s: %s\n' "$name" "$(echo "$out" | head -3 | tr '\n' ' ')"
    fi
  done
)"
if [[ -z "$LOG_PROBLEMS" ]]; then
  step_pass LOCAL_HUMAN_RECENT_LOGS_CLEAN "no recent ERROR/TRACEBACK/CRITICAL/FATAL lines in LOCAL service logs"
else
  step_fail LOCAL_HUMAN_RECENT_LOGS_CLEAN "$LOG_PROBLEMS"
  failures=$((failures + 1))
fi

lite_mem_bytes="$(docker stats --no-stream --format '{{.MemUsage}}' vexa-lite 2>/dev/null \
  | awk -F/ '{print $1}' \
  | python3 -c 'import sys; s=sys.stdin.read().strip().lower().replace(" ",""); units={"b":1,"kib":1024,"mib":1024**2,"gib":1024**3}; import re; m=re.match(r"([0-9.]+)([a-z]+)",s); print(int(float(m.group(1))*units[m.group(2)])) if m else print(0)' 2>/dev/null || echo 0)"
if [[ "$lite_mem_bytes" =~ ^[0-9]+$ ]] && (( lite_mem_bytes > 0 && lite_mem_bytes < 2147483648 )); then
  step_pass LOCAL_HUMAN_MEMORY_WITHIN_LIMIT "vexa-lite memory is below 2GiB"
else
  step_fail LOCAL_HUMAN_MEMORY_WITHIN_LIMIT "vexa-lite memory is not below 2GiB or could not be measured"
  failures=$((failures + 1))
fi

LITE_RECORDING_PATHS="$(docker exec -i vexa-lite-postgres psql -U postgres -d vexa -A -t -c \
  "WITH media AS (
     SELECT m.id AS meeting_id, mf->>'storage_path' AS storage_path
     FROM meetings m
     CROSS JOIN LATERAL jsonb_array_elements(COALESCE(m.data->'recordings','[]'::jsonb)) rec
     CROSS JOIN LATERAL jsonb_array_elements(COALESCE(rec->'media_files','[]'::jsonb)) mf
     WHERE mf->>'storage_backend' = 'local'
       AND COALESCE((mf->>'is_final')::boolean, false)
       AND mf->>'storage_path' IS NOT NULL
   )
   SELECT meeting_id || ':' || storage_path
   FROM media
   ORDER BY meeting_id
   LIMIT 25" 2>/dev/null || echo query_failed)"
LITE_MISSING_RECORDINGS=""
if [[ "$LITE_RECORDING_PATHS" == "query_failed" ]]; then
  LITE_MISSING_RECORDINGS="query_failed"
else
  while IFS= read -r row; do
    [[ -z "$row" ]] && continue
    path="${row#*:}"
    if ! docker exec vexa-lite test -f "/var/lib/vexa/recordings/$path" 2>/dev/null; then
      LITE_MISSING_RECORDINGS+="$row"$'\n'
    fi
  done <<< "$LITE_RECORDING_PATHS"
  LITE_MISSING_RECORDINGS="$(echo "$LITE_MISSING_RECORDINGS" | head -5)"
fi
if [[ -z "$LITE_MISSING_RECORDINGS" ]]; then
  step_pass LOCAL_HUMAN_LITE_RECORDING_FILES_PRESENT "lite DB local final recording paths exist on disk"
else
  step_fail LOCAL_HUMAN_LITE_RECORDING_FILES_PRESENT "missing lite recording files: $(echo "$LITE_MISSING_RECORDINGS" | tr '\n' ' ')"
  failures=$((failures + 1))
fi

ENV_DIFF="$(python3 - <<PY
from pathlib import Path
root = Path("$ROOT_DIR")
example = root / "deploy/env-example"
env = root / "deploy/compose/.env"
def keys(path):
    out = {}
    for line in path.read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k] = v
    return out
ex = keys(example)
actual = keys(env)
missing = sorted(set(ex) - set(actual))
required_nonempty = ["IMAGE_TAG", "BROWSER_IMAGE", "MINIO_HOST_PORT", "MINIO_CONSOLE_HOST_PORT", "TRANSCRIPTION_SERVICE_URL"]
empty = [k for k in required_nonempty if not actual.get(k)]
if missing or empty:
    print("missing=" + ",".join(missing) + " empty=" + ",".join(empty))
PY
)"
if [[ -z "$ENV_DIFF" ]]; then
  step_pass LOCAL_HUMAN_COMPOSE_ENV_SSOT "deploy/compose/.env contains env-example keys + non-empty LOCAL overrides"
else
  step_fail LOCAL_HUMAN_COMPOSE_ENV_SSOT "$ENV_DIFF"
  failures=$((failures + 1))
fi

LEFTOVERS="$(docker ps -a --format '{{.Names}}' | grep -E '^(lifecycle-|webhook-test|spoof-test)' || true)"
if [[ -z "$LEFTOVERS" ]]; then
  step_pass LOCAL_HUMAN_NO_TEST_CONTAINERS "no leftover lifecycle/webhook/spoof test containers"
else
  step_fail LOCAL_HUMAN_NO_TEST_CONTAINERS "leftover test containers: $(echo "$LEFTOVERS" | tr '\n' ' ')"
  failures=$((failures + 1))
fi

TABLES_PRESENT="$(docker exec -i vexa-postgres-1 psql -U postgres -d vexa -A -t -c \
  "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('recordings','media_files') ORDER BY table_name" 2>/dev/null || echo query_failed)"
if [[ -z "$TABLES_PRESENT" ]]; then
  step_pass LOCAL_HUMAN_RECORDINGS_TABLES_DROPPED "compose DB has no recordings/media_files relational tables"
else
  step_fail LOCAL_HUMAN_RECORDINGS_TABLES_DROPPED "unexpected table result: $TABLES_PRESENT"
  failures=$((failures + 1))
fi

test_end
exit "$failures"

#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence"
OUT_DIR="${1:-.}"
mkdir -p "$OUT_DIR"

TAG="$(cat "$ROOT/deploy/compose/.pack4-lifecycle-tag")"
API_PORT="$(grep -E "^API_GATEWAY_HOST_PORT=" "$ROOT/.env" | cut -d= -f2)"
RUNTIME_PORT="$(grep -E "^RUNTIME_API_PORT=" "$ROOT/.env" | cut -d= -f2)"
API_KEY="$(grep -E "^VEXA_API_KEY=" "$ROOT/.env" | tail -1 | cut -d= -f2)"

if [ -z "$API_KEY" ]; then
  echo "missing VEXA_API_KEY" >&2
  exit 1
fi

create_body="$OUT_DIR/create-response.json"
delete_body="$OUT_DIR/delete-response.json"

http="$(curl -sS -o "$create_body" -w "%{http_code}" \
  -X POST "http://localhost:${API_PORT}/bots" \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"mode":"browser_session"}')"
if [ "$http" -lt 200 ] || [ "$http" -ge 300 ]; then
  echo "create failed http=$http" >&2
  cat "$create_body" >&2
  exit 1
fi

MEETING_ID="$(jq -r ".id" "$create_body")"
NATIVE_ID="$(jq -r ".native_meeting_id" "$create_body")"
if [ -z "$MEETING_ID" ] || [ "$MEETING_ID" = "null" ] || [ -z "$NATIVE_ID" ] || [ "$NATIVE_ID" = "null" ]; then
  echo "failed to parse create response" >&2
  cat "$create_body" >&2
  exit 1
fi
echo "created browser_session meeting_id=${MEETING_ID} native_id=${NATIVE_ID}"

http="$(curl -sS -o "$delete_body" -w "%{http_code}" \
  -X DELETE "http://localhost:${API_PORT}/bots/browser_session/${NATIVE_ID}" \
  -H "X-API-Key: ${API_KEY}")"
if [ "$http" -lt 200 ] || [ "$http" -ge 300 ]; then
  echo "delete failed http=$http" >&2
  cat "$delete_body" >&2
  exit 1
fi
echo "delete accepted http=$http"

STATUS=""
CONTAINER=""
for i in $(seq 1 90); do
  ROW="$(IMAGE_TAG="$TAG" docker compose --env-file "$ROOT/.env" \
    -f "$ROOT/deploy/compose/docker-compose.yml" \
    exec -T postgres psql -U postgres -d vexa -tA -F $'\t' \
    -c "select status, coalesce(bot_container_id, '') from meetings where id=${MEETING_ID};" \
    | tr -d "\r" || true)"
  STATUS="$(printf "%s" "$ROW" | awk -F $'\t' '{print $1}' | xargs || true)"
  CONTAINER="$(printf "%s" "$ROW" | awk -F $'\t' '{print $2}' | xargs || true)"
  echo "poll=${i} status=${STATUS:-unknown} container=${CONTAINER:-none}"
  if [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ]; then
    break
  fi
  sleep 2
done

if [ "$STATUS" != "completed" ] && [ "$STATUS" != "failed" ]; then
  echo "meeting did not reach terminal status" >&2
  exit 1
fi

if [ -n "$CONTAINER" ]; then
  HTTP="$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${RUNTIME_PORT}/containers/${CONTAINER}" || true)"
  echo "runtime_container_http=${HTTP}"
  if [ "$HTTP" = "200" ]; then
    echo "runtime still reports container" >&2
    exit 1
  fi
fi

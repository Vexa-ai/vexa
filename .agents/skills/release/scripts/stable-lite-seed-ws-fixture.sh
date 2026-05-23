#!/usr/bin/env bash
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-vexa-1063-pg}"
DASHBOARD_URL="${DASHBOARD_URL:-http://localhost:33000}"
GATEWAY_URL="${GATEWAY_URL:-http://localhost:38056}"
EMAIL="${BASELINE_EMAIL:-baseline-ws-1063@vexa.local}"
DISPLAY_NAME="${BASELINE_NAME:-Baseline WS 0.10.6}"
NATIVE_ID="${BASELINE_NATIVE_ID:-abc-defg-hij}"
TOKEN="${BASELINE_AUTH_TOKEN:?BASELINE_AUTH_TOKEN is required}"
OUT="${OUT:-.agents/releases/0.10.6.3/baseline/ws-fixture.json}"

if ! docker inspect "$PG_CONTAINER" >/dev/null 2>&1; then
  echo "Postgres container not found: $PG_CONTAINER" >&2
  exit 1
fi

USER_ID="$(
  docker exec -i "$PG_CONTAINER" psql -U postgres -d vexa -tAc "
    INSERT INTO users (email, name, max_concurrent_bots, data)
    VALUES ('$EMAIL', '$DISPLAY_NAME', 5, '{}'::jsonb)
    ON CONFLICT (email)
    DO UPDATE SET
      name = EXCLUDED.name,
      max_concurrent_bots = EXCLUDED.max_concurrent_bots
    RETURNING id;
  " |
  awk '/^[[:space:]]*[0-9]+[[:space:]]*$/ { print $1; exit }'
)"

docker exec -i "$PG_CONTAINER" psql -U postgres -d vexa -v ON_ERROR_STOP=1 -c "
  INSERT INTO api_tokens (token, user_id, scopes, name)
  VALUES ('$TOKEN', $USER_ID, ARRAY['bot','tx','browser'], 'baseline ws token')
  ON CONFLICT (token)
  DO UPDATE SET
    user_id = EXCLUDED.user_id,
    scopes = EXCLUDED.scopes,
    name = EXCLUDED.name;
" >/dev/null

MEETING_ID="$(
  docker exec -i "$PG_CONTAINER" psql -U postgres -d vexa -tAc "
    INSERT INTO meetings (
      user_id,
      platform,
      platform_specific_id,
      status,
      start_time,
      data,
      created_at,
      updated_at
    )
    VALUES (
      $USER_ID,
      'google_meet',
      '$NATIVE_ID',
      'active',
      now(),
      '{\"name\":\"0.10.6 stable WS baseline\"}'::jsonb,
      now(),
      now()
    )
    RETURNING id;
  " |
  awk '/^[[:space:]]*[0-9]+[[:space:]]*$/ { print $1; exit }'
)"

mkdir -p "$(dirname "$OUT")"
jq -n \
  --arg dashboard_url "$DASHBOARD_URL" \
  --arg gateway_url "$GATEWAY_URL" \
  --arg token "$TOKEN" \
  --arg email "$EMAIL" \
  --arg native_id "$NATIVE_ID" \
  --arg user_id "$USER_ID" \
  --arg meeting_id "$MEETING_ID" \
  '{
    dashboard_url: $dashboard_url,
    gateway_url: $gateway_url,
    token: $token,
    email: $email,
    native_id: $native_id,
    user_id: ($user_id | tonumber),
    meeting_id: ($meeting_id | tonumber)
  }' > "$OUT"

jq '.token="***"' "$OUT"

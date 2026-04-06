#!/usr/bin/env bash
# dashboard-validation.sh — Validate dashboard backends from inside its container
# Usage: ./dashboard-validation.sh [CONTAINER]
# Outputs: eval-able DASHBOARD_URL
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_ID="test/dashboard-validation"
source "$(dirname "$0")/test-lib.sh"

CONTAINER="${1:-}"

# Auto-detect deploy mode: lite (single container) vs compose (separate containers)
if [ -z "$CONTAINER" ]; then
  if docker ps --format '{{.Names}}' | grep -q '^vexa$'; then
    CONTAINER="vexa"
    DEPLOY_MODE="lite"
  else
    CONTAINER="vexa-dashboard-1"
    DEPLOY_MODE="compose"
  fi
fi

log_start "container=$CONTAINER mode=${DEPLOY_MODE:-compose}"

# Resolve host port
if [ "${DEPLOY_MODE:-compose}" = "lite" ]; then
  DASHBOARD_PORT=3000
else
  DASHBOARD_PORT=$(docker port "$CONTAINER" 3000 2>/dev/null | head -1 | cut -d: -f2)
  if [ -z "$DASHBOARD_PORT" ]; then
    log_fail "dashboard container not running or port 3000 not exposed"
  fi
fi

# Step 1: Dashboard serves HTML
HTTP=$(curl -sf -o /dev/null -w '%{http_code}' "http://localhost:$DASHBOARD_PORT" 2>/dev/null || echo "000")
[ "$HTTP" -ge 200 ] && [ "$HTTP" -lt 300 ] || log_fail "dashboard http://localhost:$DASHBOARD_PORT → $HTTP"
log_pass "dashboard serving on :$DASHBOARD_PORT"

# Step 2: Backend calls — exactly as dashboard makes them, from inside the container
# Each entry: "LABEL|URL_EXPR|EXTRA_HEADERS|ACCEPT_CODES"
# URL_EXPR uses env vars available inside the container.
# ACCEPT_CODES: pipe-separated HTTP codes that count as PASS (401 = gateway alive but auth rejected = OK)
# In lite mode, env vars are set per-process by supervisord, not globally.
# Export them for docker exec subshells.
if [ "${DEPLOY_MODE:-compose}" = "lite" ]; then
  LITE_ADMIN_TOKEN=$(docker exec "$CONTAINER" printenv ADMIN_API_TOKEN 2>/dev/null || echo "changeme")
  EXEC_ENV="VEXA_API_URL=http://localhost:8056 VEXA_ADMIN_API_URL=http://localhost:8056 VEXA_ADMIN_API_KEY=$LITE_ADMIN_TOKEN VEXA_PUBLIC_API_URL=http://localhost:8056"
else
  EXEC_ENV=""
fi

CALLS=(
  "gateway root|\$VEXA_API_URL/||200"
  "gateway /meetings|\$VEXA_API_URL/meetings|-H X-API-Key:TEST|200|401"
  "gateway /bots/status|\$VEXA_API_URL/bots/status|-H X-API-Key:TEST|200|401|404"
  "admin /users?limit=1|\$VEXA_ADMIN_API_URL/admin/users?limit=1|-H X-Admin-API-Key:\$VEXA_ADMIN_API_KEY|200"
  "admin /users/email|\$VEXA_ADMIN_API_URL/admin/users/email/staging%40vexa.ai|-H X-Admin-API-Key:\$VEXA_ADMIN_API_KEY|200|404"
  "admin /users/1|\$VEXA_ADMIN_API_URL/admin/users/1|-H X-Admin-API-Key:\$VEXA_ADMIN_API_KEY|200"
  "public API URL (client-side, test from host)|\$VEXA_PUBLIC_API_URL/||200|SKIP_INSIDE"
  "internal auth|http://localhost:3000/api/auth/session||200"
)

FAILED=0
for entry in "${CALLS[@]}"; do
  IFS='|' read -r LABEL URL HEADERS CODES <<< "$entry"

  if [ "${DEPLOY_MODE:-compose}" = "lite" ]; then
    # Lite: host network, resolve env vars locally and curl from host
    RESOLVED_URL=$(echo "$URL" | sed "s|\\\$VEXA_API_URL|http://localhost:8056|g; s|\\\$VEXA_ADMIN_API_URL|http://localhost:8056|g; s|\\\$VEXA_ADMIN_API_KEY|$LITE_ADMIN_TOKEN|g; s|\\\$VEXA_PUBLIC_API_URL|http://localhost:8056|g")
    RESOLVED_HEADERS=$(echo "$HEADERS" | sed "s|\\\$VEXA_API_URL|http://localhost:8056|g; s|\\\$VEXA_ADMIN_API_URL|http://localhost:8056|g; s|\\\$VEXA_ADMIN_API_KEY|$LITE_ADMIN_TOKEN|g")
    CURL_CMD="curl -s -o /dev/null -w '%{http_code}'"
    if [ -n "$RESOLVED_HEADERS" ]; then
      CURL_CMD="$CURL_CMD $RESOLVED_HEADERS"
    fi
    HTTP=$(eval $CURL_CMD "\"$RESOLVED_URL\"" 2>/dev/null || echo "000")
  else
    # Compose: use wget inside the Next.js container (no curl)
    WGET_CMD="wget -q --spider -S -O /dev/null \"$URL\" 2>&1 | grep 'HTTP/' | tail -1 | grep -o '[0-9][0-9][0-9]'"
    if [ -n "$HEADERS" ]; then
      WGET_HEADERS=$(echo "$HEADERS" | sed 's/-H /--header="/g; s/:/: /; s/$/"/')
      WGET_CMD="wget -q --spider -S -O /dev/null $WGET_HEADERS \"$URL\" 2>&1 | grep 'HTTP/' | tail -1 | grep -o '[0-9][0-9][0-9]'"
    fi
    HTTP=$(docker exec "$CONTAINER" sh -c "$WGET_CMD" 2>/dev/null || echo "000")
  fi

  # SKIP_INSIDE = client-side URL, test from host instead
  if echo "$CODES" | grep -q "SKIP_INSIDE"; then
    CODES=$(echo "$CODES" | sed 's/|*SKIP_INSIDE//g')
    if [ "${DEPLOY_MODE:-compose}" = "lite" ]; then
      # Already resolved and tested from host above
      :
    else
      RESOLVED_URL=$(docker exec "$CONTAINER" sh -c "echo $URL" 2>/dev/null)
      HOST_HTTP=$(curl -s -o /dev/null -w '%{http_code}' "$RESOLVED_URL" 2>/dev/null || echo "000")
      HTTP="$HOST_HTTP"
    fi
  fi

  # Check if HTTP code is in accepted list
  PASS=false
  IFS='|' read -ra ACCEPTED <<< "$CODES"
  for code in "${ACCEPTED[@]}"; do
    if [ "$HTTP" = "$code" ]; then
      PASS=true
      break
    fi
  done

  if $PASS; then
    log_pass "$LABEL → $HTTP"
  else
    log "FAIL" "$LABEL → $HTTP (expected: $CODES)"
    FAILED=$((FAILED + 1))
  fi
done

if [ "$FAILED" -gt 0 ]; then
  log_fail "$FAILED backend calls failed — dashboard will show errors to human"
fi

log_pass "all GET backend calls OK"

# Step 3: POST calls & feature backends — need a valid API token
TEST_TOKEN="${TEST_API_TOKEN:-}"
if [ -z "$TEST_TOKEN" ]; then
  # Try to source staging env
  SECRETS_FILE="$SCRIPT_DIR/../secrets/staging.env"
  [ -f "$SECRETS_FILE" ] && source "$SECRETS_FILE" 2>/dev/null
  TEST_TOKEN="${TEST_API_TOKEN:-}"
fi
if [ -z "$TEST_TOKEN" ]; then
  # Fall back to VEXA_API_KEY from root .env (set by make setup-api-key)
  ROOT_ENV="$SCRIPT_DIR/../../.env"
  [ -f "$ROOT_ENV" ] && TEST_TOKEN=$(grep -E '^VEXA_API_KEY=' "$ROOT_ENV" 2>/dev/null | cut -d= -f2)
fi
if [ -z "$TEST_TOKEN" ]; then
  # Last resort: read from dashboard container env
  TEST_TOKEN=$(docker exec "$CONTAINER" printenv VEXA_API_KEY 2>/dev/null || echo "")
fi

# Resolve user email for vexa-user-info cookie (needed by getAuthenticatedUserId)
# Look up the email for this token's user from the admin API
TEST_USER_EMAIL="${TEST_USER_EMAIL:-}"
if [ -z "$TEST_USER_EMAIL" ] && [ -n "$TEST_TOKEN" ]; then
  ADMIN_TK=$(docker exec "$CONTAINER" printenv VEXA_ADMIN_API_KEY 2>/dev/null || echo "")
  ADMIN_URL_INTERNAL=$(docker exec "$CONTAINER" printenv VEXA_ADMIN_API_URL 2>/dev/null || echo "http://admin-api:8001")
  TEST_USER_EMAIL=$(docker exec "$CONTAINER" sh -c "wget -q -O - --header='X-Admin-API-Key: $ADMIN_TK' '$ADMIN_URL_INTERNAL/admin/users?limit=1'" 2>/dev/null \
    | python3 -c "import sys,json; u=json.load(sys.stdin); print(u[0]['email'] if u else '')" 2>/dev/null || echo "")
fi
if [ -z "$TEST_USER_EMAIL" ]; then
  TEST_USER_EMAIL="admin@vexa.ai"
fi
USER_INFO_COOKIE=$(printf '{"email":"%s"}' "$TEST_USER_EMAIL" | python3 -c "import sys,urllib.parse; print(urllib.parse.quote(sys.stdin.read()))" 2>/dev/null || echo "")
COOKIE_HEADER="vexa-token=$TEST_TOKEN; vexa-user-info=$USER_INFO_COOKIE"

if [ -n "$TEST_TOKEN" ]; then
  DASHBOARD_URL_INTERNAL="http://localhost:3000"

  # ── Bot creation tests (existing) ──────────────────────────────────────

  # Test browser session creation — send exactly what the dashboard button sends
  POST_HTTP=$(docker exec "$CONTAINER" sh -c "wget -q -S -O /dev/null \
    --header='Content-Type: application/json' \
    --header='Cookie: vexa-token=$TEST_TOKEN' \
    --post-data='{\"mode\":\"browser_session\",\"workspaceGitRepo\":\"https://github.com/test/validation\",\"workspaceGitToken\":\"ghp_test\",\"workspaceGitBranch\":\"main\"}' \
    '$DASHBOARD_URL_INTERNAL/api/vexa/bots' 2>&1 | grep 'HTTP/' | tail -1 | grep -o '[0-9][0-9][0-9]'" 2>/dev/null || echo "000")

  if [ "$POST_HTTP" = "201" ] || [ "$POST_HTTP" = "200" ]; then
    log_pass "POST /api/vexa/bots (browser_session) → $POST_HTTP"
    # Cleanup: stop the test browser session
    BS_NATIVE=$(docker exec "$CONTAINER" sh -c "wget -q -O - \
      --header='Content-Type: application/json' \
      --header='Cookie: vexa-token=$TEST_TOKEN' \
      --post-data='{\"mode\":\"browser_session\"}' \
      '$DASHBOARD_URL_INTERNAL/api/vexa/bots' 2>/dev/null" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('native_meeting_id',''))" 2>/dev/null || true)
    # The create above already ran, use last response. Clean up via gateway directly
    curl -sf -X DELETE "http://localhost:8066/bots/browser_session/$BS_NATIVE" -H "X-API-Key: $TEST_TOKEN" >/dev/null 2>&1 || true
  elif [ "$POST_HTTP" = "403" ]; then
    log "FAIL" "POST /api/vexa/bots (browser_session) → 403 — max concurrent bots reached, clean up stale sessions"
    FAILED=$((FAILED + 1))
  else
    log "FAIL" "POST /api/vexa/bots (browser_session) → $POST_HTTP (expected: 201)"
    FAILED=$((FAILED + 1))
  fi

  # Test meeting join — the field the dashboard sends when user enters a URL
  POST_HTTP=$(docker exec "$CONTAINER" sh -c "wget -q -S -O /dev/null \
    --header='Content-Type: application/json' \
    --header='Cookie: vexa-token=$TEST_TOKEN' \
    --post-data='{\"platform\":\"google_meet\",\"native_meeting_id\":\"test-dashboard-validation\",\"bot_name\":\"validation-bot\"}' \
    '$DASHBOARD_URL_INTERNAL/api/vexa/bots' 2>&1 | grep 'HTTP/' | tail -1 | grep -o '[0-9][0-9][0-9]'" 2>/dev/null || echo "000")

  if [ "$POST_HTTP" = "201" ] || [ "$POST_HTTP" = "200" ]; then
    log_pass "POST /api/vexa/bots (meeting join) → $POST_HTTP"
    # Cleanup: stop the validation bot
    curl -sf -X DELETE "http://localhost:8066/bots/google_meet/test-dashboard-validation" -H "X-API-Key: $TEST_TOKEN" >/dev/null 2>&1 || true
  elif [ "$POST_HTTP" = "422" ]; then
    log "FAIL" "POST /api/vexa/bots (meeting join) → 422 — gateway rejects dashboard payload"
    FAILED=$((FAILED + 1))
  else
    log "FAIL" "POST /api/vexa/bots (meeting join) → $POST_HTTP (expected: 201)"
    FAILED=$((FAILED + 1))
  fi

  # ── Webhook backend tests ──────────────────────────────────────────────
  # Tests the dashboard's Next.js API routes exactly as the frontend calls them

  # GET /api/webhooks/config — fetch webhook configuration
  WH_CONFIG_HTTP=$(docker exec "$CONTAINER" sh -c "wget -q -S -O /dev/null \
    --header='Cookie: $COOKIE_HEADER' \
    '$DASHBOARD_URL_INTERNAL/api/webhooks/config' 2>&1 | grep 'HTTP/' | tail -1 | grep -o '[0-9][0-9][0-9]'" 2>/dev/null || echo "000")

  if [ "$WH_CONFIG_HTTP" = "200" ]; then
    log_pass "GET /api/webhooks/config → $WH_CONFIG_HTTP"
  elif [ "$WH_CONFIG_HTTP" = "401" ]; then
    log "FAIL" "GET /api/webhooks/config → 401 — auth cookies not working (needs vexa-token + vexa-user-info)"
    FAILED=$((FAILED + 1))
  elif [ "$WH_CONFIG_HTTP" = "404" ]; then
    log_pass "GET /api/webhooks/config → 404 (no config yet, acceptable)"
  else
    log "FAIL" "GET /api/webhooks/config → $WH_CONFIG_HTTP (expected: 200 or 404)"
    FAILED=$((FAILED + 1))
  fi

  # PUT /api/webhooks/config — save webhook configuration
  WH_SAVE_HTTP=$(docker exec "$CONTAINER" sh -c "wget -q -S -O /dev/null \
    --header='Content-Type: application/json' \
    --header='Cookie: $COOKIE_HEADER' \
    --post-data='{\"endpoint_url\":\"https://httpbin.org/post\",\"events\":{\"meeting.completed\":true,\"meeting.started\":false,\"bot.failed\":false,\"meeting.status_change\":false}}' \
    --method=PUT \
    '$DASHBOARD_URL_INTERNAL/api/webhooks/config' 2>&1 | grep 'HTTP/' | tail -1 | grep -o '[0-9][0-9][0-9]'" 2>/dev/null || echo "000")

  # wget may not support --method=PUT, fall back to node fetch
  if [ "$WH_SAVE_HTTP" = "000" ] || [ "$WH_SAVE_HTTP" = "405" ]; then
    WH_SAVE_HTTP=$(docker exec "$CONTAINER" node -e "
      fetch('$DASHBOARD_URL_INTERNAL/api/webhooks/config', {
        method: 'PUT',
        headers: {'Content-Type':'application/json','Cookie':'$COOKIE_HEADER'},
        body: JSON.stringify({endpoint_url:'https://httpbin.org/post',events:{'meeting.completed':true}})
      }).then(r => console.log(r.status)).catch(() => console.log('000'))
    " 2>/dev/null || echo "000")
  fi

  if [ "$WH_SAVE_HTTP" = "200" ]; then
    log_pass "PUT /api/webhooks/config → $WH_SAVE_HTTP"
  elif [ "$WH_SAVE_HTTP" = "401" ]; then
    log "FAIL" "PUT /api/webhooks/config → 401 — auth cookies not working"
    FAILED=$((FAILED + 1))
  else
    log "FAIL" "PUT /api/webhooks/config → $WH_SAVE_HTTP (expected: 200)"
    FAILED=$((FAILED + 1))
  fi

  # POST /api/webhooks/test — send test webhook (uses httpbin as safe target)
  WH_TEST_BODY=$(docker exec "$CONTAINER" sh -c "wget -q -O - \
    --header='Content-Type: application/json' \
    --header='Cookie: $COOKIE_HEADER' \
    --post-data='{\"url\":\"https://httpbin.org/post\"}' \
    '$DASHBOARD_URL_INTERNAL/api/webhooks/test' 2>/dev/null" 2>/dev/null || echo "{}")

  WH_TEST_SUCCESS=$(echo "$WH_TEST_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if d.get('success') else 'false')" 2>/dev/null || echo "error")

  if [ "$WH_TEST_SUCCESS" = "true" ]; then
    log_pass "POST /api/webhooks/test → delivered to httpbin"
  elif [ "$WH_TEST_SUCCESS" = "false" ]; then
    WH_TEST_ERR=$(echo "$WH_TEST_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error','unknown'))" 2>/dev/null || echo "unknown")
    if echo "$WH_TEST_ERR" | grep -qi "authenticated"; then
      log "FAIL" "POST /api/webhooks/test → 401 — auth cookies not working"
    else
      log "FAIL" "POST /api/webhooks/test → failed: $WH_TEST_ERR"
    fi
    FAILED=$((FAILED + 1))
  else
    log "FAIL" "POST /api/webhooks/test → could not parse response: $WH_TEST_BODY"
    FAILED=$((FAILED + 1))
  fi

  # POST /api/webhooks/rotate-secret — rotate signing secret
  WH_ROTATE_BODY=$(docker exec "$CONTAINER" sh -c "wget -q -O - \
    --header='Content-Type: application/json' \
    --header='Cookie: $COOKIE_HEADER' \
    --post-data='{}' \
    '$DASHBOARD_URL_INTERNAL/api/webhooks/rotate-secret' 2>/dev/null" 2>/dev/null || echo "{}")

  WH_ROTATE_SECRET=$(echo "$WH_ROTATE_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('signing_secret',''))" 2>/dev/null || echo "")

  if [ -n "$WH_ROTATE_SECRET" ] && echo "$WH_ROTATE_SECRET" | grep -q "^whsec_"; then
    log_pass "POST /api/webhooks/rotate-secret → new secret generated"
  elif echo "$WH_ROTATE_BODY" | grep -qi "authenticated"; then
    log "FAIL" "POST /api/webhooks/rotate-secret → 401 — auth cookies not working"
    FAILED=$((FAILED + 1))
  else
    log "FAIL" "POST /api/webhooks/rotate-secret → unexpected response: $WH_ROTATE_BODY"
    FAILED=$((FAILED + 1))
  fi

  # GET /api/webhooks/deliveries — fetch delivery history
  WH_DEL_HTTP=$(docker exec "$CONTAINER" sh -c "wget -q -S -O /dev/null \
    --header='Cookie: $COOKIE_HEADER' \
    '$DASHBOARD_URL_INTERNAL/api/webhooks/deliveries?time_range=7d' 2>&1 | grep 'HTTP/' | tail -1 | grep -o '[0-9][0-9][0-9]'" 2>/dev/null || echo "000")

  if [ "$WH_DEL_HTTP" = "200" ]; then
    log_pass "GET /api/webhooks/deliveries → $WH_DEL_HTTP"
  elif [ "$WH_DEL_HTTP" = "401" ]; then
    log "FAIL" "GET /api/webhooks/deliveries → 401 — auth cookies not working"
    FAILED=$((FAILED + 1))
  else
    log "FAIL" "GET /api/webhooks/deliveries → $WH_DEL_HTTP (expected: 200)"
    FAILED=$((FAILED + 1))
  fi

  # ── API Keys backend tests ─────────────────────────────────────────────
  # Tests the dashboard's /api/profile/keys routes exactly as the Profile page calls them

  # GET /api/profile/keys — list user's API keys
  KEYS_BODY=$(docker exec "$CONTAINER" sh -c "wget -q -O - \
    --header='Cookie: $COOKIE_HEADER' \
    '$DASHBOARD_URL_INTERNAL/api/profile/keys' 2>/dev/null" 2>/dev/null || echo "{}")

  KEYS_OK=$(echo "$KEYS_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if 'keys' in d else 'false')" 2>/dev/null || echo "error")

  if [ "$KEYS_OK" = "true" ]; then
    KEYS_COUNT=$(echo "$KEYS_BODY" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('keys',[])))" 2>/dev/null || echo "0")
    log_pass "GET /api/profile/keys → $KEYS_COUNT keys listed"
    # Verify the dashboard's own token appears (it should — user has at least the login token)
    if [ "$KEYS_COUNT" = "0" ]; then
      log "FINDING" "GET /api/profile/keys returned 0 keys — dashboard's own auth token may not be in api_tokens list"
    fi
  elif echo "$KEYS_BODY" | grep -qi "authenticated"; then
    log "FAIL" "GET /api/profile/keys → 401 — auth cookies not working (needs vexa-token + vexa-user-info)"
    FAILED=$((FAILED + 1))
  else
    log "FAIL" "GET /api/profile/keys → unexpected response: $KEYS_BODY"
    FAILED=$((FAILED + 1))
  fi

  # POST /api/profile/keys — create a new API key
  KEY_CREATE_BODY=$(docker exec "$CONTAINER" sh -c "wget -q -O - \
    --header='Content-Type: application/json' \
    --header='Cookie: $COOKIE_HEADER' \
    --post-data='{\"name\":\"test-dashboard-validation\",\"scopes\":\"bot,tx\"}' \
    '$DASHBOARD_URL_INTERNAL/api/profile/keys' 2>/dev/null" 2>/dev/null || echo "{}")

  KEY_TOKEN=$(echo "$KEY_CREATE_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null || echo "")
  KEY_ID=$(echo "$KEY_CREATE_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")

  if [ -n "$KEY_TOKEN" ] && echo "$KEY_TOKEN" | grep -q "^vxa_"; then
    log_pass "POST /api/profile/keys → key created: ${KEY_TOKEN:0:12}..."
  elif echo "$KEY_CREATE_BODY" | grep -qi "authenticated"; then
    log "FAIL" "POST /api/profile/keys → 401 — auth cookies not working"
    FAILED=$((FAILED + 1))
  else
    log "FAIL" "POST /api/profile/keys → unexpected response: $KEY_CREATE_BODY"
    FAILED=$((FAILED + 1))
  fi

  # DELETE /api/profile/keys/:id — revoke the test key we just created
  if [ -n "$KEY_ID" ]; then
    KEY_DEL_HTTP=$(docker exec "$CONTAINER" node -e "
      fetch('$DASHBOARD_URL_INTERNAL/api/profile/keys/$KEY_ID', {
        method: 'DELETE',
        headers: {'Cookie':'$COOKIE_HEADER'}
      }).then(r => console.log(r.status)).catch(() => console.log('000'))
    " 2>/dev/null || echo "000")

    if [ "$KEY_DEL_HTTP" = "200" ]; then
      log_pass "DELETE /api/profile/keys/$KEY_ID → key revoked"
    elif [ "$KEY_DEL_HTTP" = "401" ]; then
      log "FAIL" "DELETE /api/profile/keys/$KEY_ID → 401 — auth cookies not working"
      FAILED=$((FAILED + 1))
    else
      log "FAIL" "DELETE /api/profile/keys/$KEY_ID → $KEY_DEL_HTTP (expected: 200)"
      FAILED=$((FAILED + 1))
    fi
  else
    log_skip "DELETE /api/profile/keys — no key ID from create step"
  fi

else
  log_skip "POST/webhook/keys tests — no TEST_API_TOKEN available"
fi

if [ "$FAILED" -gt 0 ]; then
  log_fail "$FAILED calls failed — dashboard will show errors to human"
fi

log_pass "all backend calls OK"
echo "DASHBOARD_URL=http://localhost:$DASHBOARD_PORT"

#!/usr/bin/env bash
# Validate dashboard deployment before handing to human.
# Run: bash validate.sh [port]
set -u

PORT=${1:-3001}
BASE="http://localhost:$PORT"
PASS=0; FAIL=0; WARN=0

check() {
  local label="$1" url="$2" expect="$3"
  local code body
  code=$(curl -s -o /tmp/_dash_body -w "%{http_code}" "$url" 2>/dev/null) || code="000"
  body=$(cat /tmp/_dash_body 2>/dev/null || echo "")

  if [[ "$code" == "$expect" ]]; then
    echo "  ✓ $label ($code)"
    ((PASS++))
  else
    echo "  ✗ $label — got $code, expected $expect"
    [[ -n "$body" ]] && echo "    $(echo "$body" | head -c 200)"
    ((FAIL++))
  fi
}

check_json() {
  local label="$1" url="$2" field="$3" expect="$4"
  local body val
  body=$(curl -s "$url" 2>/dev/null) || body="{}"
  val=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d$field)" 2>/dev/null) || val="PARSE_ERROR"

  if [[ "$val" == "$expect" ]]; then
    echo "  ✓ $label ($val)"
    ((PASS++))
  else
    echo "  ✗ $label — got '$val', expected '$expect'"
    ((FAIL++))
  fi
}

check_health() {
  local body svc configured reachable error
  body=$(curl -s "$BASE/api/health" 2>/dev/null) || { echo "  ✗ /api/health unreachable"; ((FAIL++)); return; }

  for svc in adminApi vexaApi; do
    configured=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin)['checks']['$svc']['configured'])" 2>/dev/null)
    reachable=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin)['checks']['$svc']['reachable'])" 2>/dev/null)
    error=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin)['checks']['$svc'].get('error',''))" 2>/dev/null)

    if [[ "$configured" == "True" && "$reachable" == "True" ]]; then
      echo "  ✓ $svc: configured + reachable"
      ((PASS++))
    elif [[ "$configured" == "True" ]]; then
      echo "  ✗ $svc: configured but NOT reachable — $error"
      ((FAIL++))
    else
      echo "  ✗ $svc: not configured — $error"
      ((FAIL++))
    fi
  done

  # Optional services (warn only)
  for svc in smtp googleOAuth; do
    configured=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin)['checks']['$svc']['configured'])" 2>/dev/null)
    if [[ "$configured" != "True" ]]; then
      echo "  ~ $svc: not configured (optional)"
      ((WARN++))
    else
      echo "  ✓ $svc: configured"
      ((PASS++))
    fi
  done
}

echo "=== Dashboard Validation: $BASE ==="
echo ""

# 1. Process running?
echo "[Process]"
if curl -s -o /dev/null -w "" "$BASE/" 2>/dev/null; then
  echo "  ✓ Dashboard responding on port $PORT"
  ((PASS++))
else
  echo "  ✗ Dashboard not responding on port $PORT"
  ((FAIL++))
  echo ""
  echo "RESULT: $FAIL FAIL — dashboard not running"
  exit 1
fi

# 2. Pages load
echo "[Pages]"
check "GET /" "$BASE/" "200"
check "GET /login" "$BASE/login" "200"
check "GET /api/config" "$BASE/api/config" "200"
check "GET /api/health" "$BASE/api/health" "200"

# 3. Health checks (env vars + backend connectivity)
echo "[Health]"
check_health

# 4. Config endpoint returns correct values
echo "[Config]"
check_json "wsUrl set" "$BASE/api/config" "['wsUrl']" "ws://localhost:3001/ws"
check_json "apiUrl set" "$BASE/api/config" "['apiUrl']" "http://localhost:8066"

# 5. API proxy works (dashboard proxies to gateway)
echo "[API Proxy]"
# 403 = proxy works, just needs auth token (no cookie in curl)
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/vexa/meetings" 2>/dev/null) || code="000"
if [[ "$code" == "200" || "$code" == "403" ]]; then
  echo "  ✓ API proxy reachable ($code — auth required)"
  ((PASS++))
else
  echo "  ✗ API proxy — got $code, expected 200|403"
  ((FAIL++))
fi

# Summary
echo ""
echo "=== $PASS passed, $FAIL failed, $WARN warnings ==="
[[ $FAIL -eq 0 ]] && echo "READY for human." || echo "FIX $FAIL issues before delivering."
exit $FAIL

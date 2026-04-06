#!/usr/bin/env bash
# 03-url-formats.sh — Test meeting URL parsing for all supported formats
# Usage: ./03-url-formats.sh [API_TOKEN]
# Outputs: eval-able TEAMS_URLS_OK, FORMATS_PASSED
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_ID="test/teams-url-formats"
source "$(dirname "$0")/test-lib.sh"

SECRETS_FILE="$SCRIPT_DIR/../secrets/staging.env"
[ -f "$SECRETS_FILE" ] && source "$SECRETS_FILE"
API_TOKEN="${1:-${TEST_API_TOKEN_FULL:-${TEST_API_TOKEN:-}}}"
[ -z "$API_TOKEN" ] && log_fail "no API_TOKEN"

MCP_URL="http://localhost:18888"

log_start "mcp=$MCP_URL"

PASSED=0
FAILED=0

parse_url() {
  local label="$1" url="$2" expect_platform="$3"
  local result
  result=$(curl -s "$MCP_URL/parse-meeting-link" \
    -H "Authorization: Bearer $API_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"meeting_url\": \"$url\"}" 2>&1)

  local platform
  platform=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('platform',''))" 2>/dev/null || echo "")

  if [ "$platform" = "$expect_platform" ]; then
    local native_id
    native_id=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('native_meeting_id',''))" 2>/dev/null)
    log_pass "$label → platform=$platform id=${native_id:0:20}"
    PASSED=$((PASSED + 1))
  else
    log "FAIL" "$label → expected $expect_platform, got: $(echo "$result" | head -c 100)"
    FAILED=$((FAILED + 1))
  fi
}

# Google Meet
parse_url "GMeet standard" "https://meet.google.com/abc-defg-hij" "google_meet"

# Teams standard join
parse_url "T1 standard join" \
  "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc123%40thread.v2/0?context=%7b%22Tid%22%3a%22tid123%22%7d" \
  "teams"

# Teams short (OeNB)
parse_url "T2 meet shortlink" \
  "https://teams.microsoft.com/meet/1234567890?p=abc456" \
  "teams"

# Teams channel
parse_url "T3 channel meeting" \
  "https://teams.microsoft.com/l/meetup-join/19%3achannel123%40thread.tacv2/1234567890?context=%7b%22Tid%22%3a%22tid123%22%7d" \
  "teams"

# Teams custom domain
parse_url "T4 custom domain" \
  "https://oenb.teams.microsoft.com/meet/9876543210?p=xyz123" \
  "teams"

# Teams personal (teams.live.com)
parse_url "T6 teams.live.com" \
  "https://teams.live.com/meet/1112223334?p=test99" \
  "teams"

if [ "$FAILED" -gt 0 ]; then
  log_fail "$FAILED/$((PASSED + FAILED)) URL formats failed"
fi

log_pass "all $PASSED URL formats parsed correctly"
echo "TEAMS_URLS_OK=true"
echo "FORMATS_PASSED=$PASSED"

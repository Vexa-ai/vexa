#!/usr/bin/env bash
# v0.10.5 Pack X — synthetic test rig (bash + curl + jq).
#
# Provides primitives for "synthetic to us" tests: drive OSS-side meeting
# lifecycle without depending on external platforms (Zoom/Meet/Teams DOM,
# real audio, browser engines). Tests catch OSS-side regressions in
# callback handlers, classifier logic, JSONB invariants, and sweep
# behavior — deterministically, in seconds.
#
# Companion to real-meeting validation: real meetings exercise external
# integration; synthetic tests exercise OSS contracts.
#
# Usage:
#   source rig.sh
#   BASE=http://localhost:8056 ADMIN_TOKEN=changeme
#   token=$(rig_get_user_token)
#   meeting_id=$(rig_spawn_dryrun "$token" "test-$(date +%s)")
#   session_uid=$(rig_session_bootstrap "$meeting_id")
#   rig_callback "$session_uid" started
#   ...
#   rig_assert_state "$meeting_id" status=failed completion_reason=stopped_with_no_audio
#
# Requires: bash, curl, jq (or python3), netcat.
set -uo pipefail

: "${BASE:=http://localhost:8056}"
: "${ADMIN_TOKEN:=changeme}"

# ─── Internal helpers ──────────────────────────────────────────────

_rig_jq() {
    # Use python3 for JSON parsing (jq not always available).
    python3 -c "import sys, json; d=json.load(sys.stdin); print($1)" 2>/dev/null
}

# ─── Public primitives ─────────────────────────────────────────────

rig_get_user_token() {
    # Issue a fresh token for the default test user (bot+browser+tx scopes).
    curl -sf -X POST "$BASE/admin/users/1/tokens?scopes=bot,browser,tx&name=synthetic-rig" \
        -H "X-Admin-API-Key: $ADMIN_TOKEN" | _rig_jq 'd["token"]'
}

rig_spawn_dryrun() {
    # Spawn a meeting record. The bot will fail to launch (we don't care);
    # we hand-roll the lifecycle via callbacks. Returns meeting_id.
    local token=$1
    local native_id=$2
    local platform=${3:-google_meet}
    local body
    body=$(cat <<EOF
{
  "native_meeting_id": "$native_id",
  "platform": "$platform",
  "transcribe_enabled": true,
  "recording_enabled": false
}
EOF
)
    curl -sf -X POST "$BASE/bots" \
        -H "X-API-Key: $token" \
        -H "Content-Type: application/json" \
        -d "$body" | _rig_jq 'd["id"]'
}

rig_session_bootstrap() {
    # Pack X synthetic endpoint: create MeetingSession row directly.
    # Returns session_uid (auto-generated if not provided).
    local meeting_id=$1
    local session_uid=${2:-}
    local body
    if [ -n "$session_uid" ]; then
        body=$(printf '{"meeting_id": %s, "session_uid": "%s"}' "$meeting_id" "$session_uid")
    else
        body=$(printf '{"meeting_id": %s}' "$meeting_id")
    fi
    curl -sf -X POST "$BASE/internal/test/session-bootstrap" \
        -H "Content-Type: application/json" \
        -d "$body" | _rig_jq 'd["session_uid"]'
}

rig_callback() {
    # Fire a callback against /bots/internal/callback/<endpoint>.
    # First arg: connection_id (session_uid). Second arg: endpoint name
    # (started, joining, status_change, exited). Remaining args are
    # JSON key=value pairs added to the payload.
    local session_uid=$1
    local endpoint=$2
    shift 2

    local extra=""
    for kv in "$@"; do
        local k="${kv%%=*}"
        local v="${kv#*=}"
        # Quote string values; leave numbers/bools/null bare.
        case "$v" in
            true|false|null|[0-9]*) extra+=", \"$k\": $v" ;;
            *) extra+=", \"$k\": \"$v\"" ;;
        esac
    done
    local body
    body="{\"connection_id\": \"$session_uid\"$extra}"
    curl -sf -X POST "$BASE/bots/internal/callback/$endpoint" \
        -H "Content-Type: application/json" \
        -d "$body"
}

rig_delete_bot() {
    # User-stop via DELETE — transitions active → stopping.
    local token=$1
    local platform=$2
    local native_id=$3
    curl -sf -X DELETE "$BASE/bots/$platform/$native_id" \
        -H "X-API-Key: $token"
}

rig_get_state() {
    # Returns full meeting JSON. Use rig_assert_state for inline checks.
    local token=$1
    local meeting_id=$2
    curl -sf -H "X-API-Key: $token" "$BASE/bots/id/$meeting_id"
}

rig_assert_state() {
    # Assert key=value pairs against a meeting's state.
    # Each pair is either a top-level field (status=...) or a data field
    # (data.completion_reason=... — written as completion_reason=...).
    # Returns 0 on all-pass, 1 on first mismatch.
    local token=$1
    local meeting_id=$2
    shift 2
    local state
    state=$(rig_get_state "$token" "$meeting_id")
    if [ -z "$state" ]; then
        echo "FAIL: could not fetch state for meeting $meeting_id" >&2
        return 1
    fi

    local fail=0
    for kv in "$@"; do
        local k="${kv%%=*}"
        local v="${kv#*=}"
        local actual
        case "$k" in
            status|id|platform|native_meeting_id)
                actual=$(echo "$state" | python3 -c "import sys,json; print(json.load(sys.stdin).get('$k',''))")
                ;;
            *)
                # Treat as data.<k>
                actual=$(echo "$state" | python3 -c "import sys,json; d=json.load(sys.stdin).get('data') or {}; print(d.get('$k', '') if d.get('$k') is not None else '')")
                ;;
        esac
        if [ "$actual" = "$v" ]; then
            echo "  ✓ $k = $v"
        else
            echo "  ✗ $k: expected $v, got $actual" >&2
            fail=1
        fi
    done
    return $fail
}

# Echo a banner so sourcing this script provides visible feedback.
echo "[rig.sh] loaded; BASE=$BASE"

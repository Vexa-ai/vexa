#!/usr/bin/env bash
# Shared helpers for tests3. Source this, don't execute it.
# Usage: source "$(dirname "$0")/../lib/common.sh"

set -euo pipefail

: "${ROOT:=$(git rev-parse --show-toplevel)}"
: "${STATE:=$ROOT/tests3/.state}"

mkdir -p "$STATE"

# ─── Colors ──────────────────────────────────────────────────────

red()   { printf '\033[31m%s\033[0m' "$*"; }
green() { printf '\033[32m%s\033[0m' "$*"; }
dim()   { printf '\033[90m%s\033[0m' "$*"; }
bold()  { printf '\033[1m%s\033[0m' "$*"; }

LOG_FILE="$STATE/tests3.log"

_log() {
    local level="$1"; shift
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $level $*" >> "$LOG_FILE"
}

pass() { printf '  %s  %s\n' "$(green " ok ")" "$*"; _log "PASS" "$*"; }
fail() { printf '  %s  %s\n' "$(red "FAIL")" "$*"; _log "FAIL" "$*"; }
info() { printf '  %s  %s\n' "$(dim "    ")" "$*"; _log "INFO" "$*"; }

# ─── Structured step reporting (JSON artifacts) ─────────────────
# See /home/dima/.claude/plans/bubbly-foraging-wilkes.md for the design.
#
# Usage in a test script:
#   test_begin my-test
#   step_pass step-id "message"
#   step_fail step-id "message"
#   step_skip step-id "reason"
#   test_end
#
# test_begin sets an EXIT trap, so test_end is optional (it just runs once if
# explicitly called). On exit (clean or errored), a JSON report is flushed to:
#   $STATE/reports/<mode>/<test_name>.json
#
# stdout output from pass/fail/info is UX only and is NOT parsed. JSON is the
# only source of truth.

_TEST_NAME=""
_TEST_STARTED_AT=""
_TEST_START_EPOCH_MS=""
_TEST_STEPS_FILE=""
_TEST_REPORT_PATH=""
_TEST_IMAGE_TAG=""
_TEST_ENDED=0

# Escape a string for JSON (handles \, ", newlines, tabs, control chars).
_json_escape() {
    python3 -c 'import json,sys; sys.stdout.write(json.dumps(sys.argv[1]))' "$1" 2>/dev/null \
        || printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g' | tr '\n' ' ')"
}

_now_ms() {
    # Millisecond epoch, portable (Python avoids bash-version drift).
    python3 -c 'import time; print(int(time.time()*1000))' 2>/dev/null \
        || echo $(($(date +%s)*1000))
}

_now_iso() {
    date -u '+%Y-%m-%dT%H:%M:%SZ'
}

_detect_mode_cached() {
    cat "$STATE/deploy_mode" 2>/dev/null || detect_mode
}

test_begin() {
    _TEST_NAME="${1:?test_begin requires a name}"
    _TEST_STARTED_AT="$(_now_iso)"
    _TEST_START_EPOCH_MS="$(_now_ms)"
    _TEST_IMAGE_TAG="$(cat "$STATE/image_tag" 2>/dev/null || echo '')"
    _TEST_STEPS_FILE="$(mktemp -t "tests3-steps-${_TEST_NAME}.XXXXXX.jsonl")"
    local mode
    mode="$(_detect_mode_cached)"
    mkdir -p "$STATE/reports/$mode"
    _TEST_REPORT_PATH="$STATE/reports/$mode/${_TEST_NAME}.json"
    _TEST_ENDED=0
    # Flush on any exit path (normal, error, signal). set -e doesn't skip traps.
    trap '_flush_test_report' EXIT INT TERM
}

_append_step() {
    # _append_step <status> <id> <message>
    local status="$1" id="$2" msg="$3"
    [ -z "$_TEST_STEPS_FILE" ] && return 0  # test_begin not called — no-op
    local esc_id esc_msg
    esc_id="$(_json_escape "$id")"
    esc_msg="$(_json_escape "$msg")"
    printf '{"id":%s,"status":"%s","message":%s}\n' "$esc_id" "$status" "$esc_msg" >> "$_TEST_STEPS_FILE"
}

step_pass() { _append_step "pass" "$1" "${2:-}"; pass "$1${2:+: $2}"; }
step_fail() { _append_step "fail" "$1" "${2:-}"; fail "$1${2:+: $2}"; }
step_skip() { _append_step "skip" "$1" "${2:-}"; info "$1${2:+ (skipped: $2)}"; }

# check <id> <condition-exit-code> <pass-msg> <fail-msg>
# Convenience: one line for the common pattern "did X succeed? yes|no".
# Usage: SOMETHING=$(...); check "step-id" $? "ok-msg" "failed: $SOMETHING"
check() {
    local id="$1" rc="$2" pass_msg="$3" fail_msg="$4"
    if [ "$rc" = "0" ]; then
        step_pass "$id" "$pass_msg"
    else
        step_fail "$id" "$fail_msg"
    fi
    return "$rc"
}

_flush_test_report() {
    local rc=$?
    # Idempotent: only flush once.
    [ "$_TEST_ENDED" = "1" ] && return $rc
    _TEST_ENDED=1
    [ -z "$_TEST_NAME" ] && return $rc   # test_begin not called

    local ended_at duration_ms status steps_json
    ended_at="$(_now_iso)"
    duration_ms=$(( $(_now_ms) - _TEST_START_EPOCH_MS ))

    # Build steps JSON array
    if [ -s "$_TEST_STEPS_FILE" ]; then
        steps_json="[$(paste -sd, "$_TEST_STEPS_FILE")]"
    else
        steps_json="[]"
    fi

    # Overall test status: fail if any step failed OR the script exited non-zero
    if grep -q '"status":"fail"' "$_TEST_STEPS_FILE" 2>/dev/null || [ "$rc" != "0" ]; then
        status="fail"
    else
        status="pass"
    fi

    local mode
    mode="$(_detect_mode_cached)"
    local esc_name esc_mode esc_image esc_started esc_ended
    esc_name="$(_json_escape "$_TEST_NAME")"
    esc_mode="$(_json_escape "$mode")"
    esc_image="$(_json_escape "$_TEST_IMAGE_TAG")"
    esc_started="$(_json_escape "$_TEST_STARTED_AT")"
    esc_ended="$(_json_escape "$ended_at")"

    cat > "$_TEST_REPORT_PATH" <<EOF
{"test":$esc_name,"mode":$esc_mode,"image_tag":$esc_image,"started_at":$esc_started,"ended_at":$esc_ended,"duration_ms":$duration_ms,"status":"$status","exit_code":$rc,"steps":$steps_json}
EOF

    rm -f "$_TEST_STEPS_FILE"
    _log "REPORT" "$_TEST_NAME → $_TEST_REPORT_PATH ($status, ${duration_ms}ms)"
    return $rc
}

test_end() {
    _flush_test_report
}

# ─── Deploy mode detection ───────────────────────────────────────

detect_mode() {
    if [ "${DEPLOY_MODE:-auto}" != "auto" ]; then
        echo "$DEPLOY_MODE"
        return
    fi

    # Check for compose
    if docker compose ls 2>/dev/null | grep -q vexa 2>/dev/null; then
        echo "compose"
        return
    fi

    # Check for single lite container (named "vexa" or "vexa-lite")
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qxE 'vexa|vexa-lite'; then
        echo "lite"
        return
    fi

    # Check for helm/k8s (deployments use release-prefixed names via helm labels)
    if kubectl get deploy -l app.kubernetes.io/name=vexa --no-headers 2>/dev/null | grep -q .; then
        echo "helm"
        return
    fi

    echo "none"
}

detect_urls() {
    local mode="$1"
    case "$mode" in
        compose)
            : "${GATEWAY_URL:=http://localhost:8056}"
            : "${ADMIN_URL:=http://localhost:8057}"
            : "${DASHBOARD_URL:=http://localhost:3001}"
            ;;
        lite)
            : "${GATEWAY_URL:=http://localhost:8056}"
            : "${ADMIN_URL:=http://localhost:8057}"
            : "${DASHBOARD_URL:=http://localhost:3000}"
            ;;
        helm)
            # Read from state if not set via env
            : "${GATEWAY_URL:=$(cat "$STATE/gateway_url" 2>/dev/null || echo "")}"
            if [ -z "${GATEWAY_URL:-}" ]; then
                echo "ERROR: GATEWAY_URL must be set for helm deployments" >&2
                exit 1
            fi
            : "${ADMIN_URL:=$(cat "$STATE/admin_url" 2>/dev/null || echo "$GATEWAY_URL")}"
            : "${DASHBOARD_URL:=$(cat "$STATE/dashboard_url" 2>/dev/null || echo "$GATEWAY_URL")}"
            ;;
    esac
    export GATEWAY_URL ADMIN_URL DASHBOARD_URL
}

# ─── Container execution ─────────────────────────────────────────

_lite_container() {
    # Return the name of the running lite container ("vexa" or "vexa-lite")
    docker ps --format '{{.Names}}' 2>/dev/null | grep -xE 'vexa|vexa-lite' | head -1
}

svc_exec() {
    # svc_exec <service> <command...>
    # Runs a command inside the container for the given service.
    local svc="$1"; shift
    local mode
    mode=$(cat "$STATE/deploy_mode" 2>/dev/null || detect_mode)

    case "$mode" in
        compose) docker exec "vexa-${svc}-1" "$@" ;;
        lite)    docker exec "$(_lite_container)" "$@" ;;
        helm)
            local release
            release=$(cat "$STATE/helm_release" 2>/dev/null || echo "")
            if [ -n "$release" ]; then
                kubectl exec "deploy/${release}-vexa-${svc}" -- "$@"
            else
                kubectl exec "deploy/${svc}" -- "$@"
            fi
            ;;
        *)       echo "ERROR: unknown deploy mode: $mode" >&2; return 1 ;;
    esac
}

# ─── Pod helpers (individual bot pods, not service deploys) ──────

find_bot_pod() {
    # find_bot_pod [pattern] → first matching bot pod/container name
    local pattern="${1:-}"
    local mode
    mode=$(cat "$STATE/deploy_mode" 2>/dev/null || detect_mode)
    case "$mode" in
        compose) docker ps --filter "name=meeting-" --format '{{.Names}}' | grep -v meeting-api | { grep "$pattern" || true; } | head -1 ;;
        lite)    _lite_container ;;
        helm)    kubectl get pods --no-headers -l app.kubernetes.io/name=vexa 2>/dev/null | grep -v meeting-api | awk '{print $1}' | { grep "$pattern" || true; } | head -1 ;;
    esac
}

pod_exec() {
    # pod_exec <pod_name> <command...>
    local pod="$1"; shift
    local mode
    mode=$(cat "$STATE/deploy_mode" 2>/dev/null || detect_mode)
    case "$mode" in
        compose|lite) docker exec "$pod" "$@" ;;
        helm)         kubectl exec "$pod" -- "$@" ;;
    esac
}

pod_logs() {
    # pod_logs <pod_name> → stdout
    local pod="$1"
    local mode
    mode=$(cat "$STATE/deploy_mode" 2>/dev/null || detect_mode)
    case "$mode" in
        compose|lite) docker logs "$pod" 2>&1 ;;
        helm)         kubectl logs "$pod" 2>&1 ;;
    esac
}

# ─── State helpers ────────────────────────────────────────────────

state_write() {
    # state_write <key> <value>
    echo "$2" > "$STATE/$1"
}

state_read() {
    # state_read <key> → stdout, exits 1 if missing
    local f="$STATE/$1"
    if [ ! -f "$f" ]; then
        echo "ERROR: missing state: $1 (run the target that produces it)" >&2
        return 1
    fi
    cat "$f"
}

state_exists() {
    [ -f "$STATE/$1" ]
}

# ─── HTTP helpers ─────────────────────────────────────────────────

_HTTP_CODE_FILE="$STATE/.http_code"

http_get() {
    # http_get <url> [api_token] → stdout (body), sets HTTP_CODE via file
    local url="$1"
    local token="${2:-}"
    local headers=()
    [ -n "$token" ] && headers+=(-H "X-API-Key: $token")
    local resp
    resp=$(curl -s -w '\n%{http_code}' "${headers[@]}" "$url" 2>/dev/null) || true
    local code
    code=$(echo "$resp" | tail -1)
    echo "${code:-000}" > "$_HTTP_CODE_FILE"
    echo "$resp" | head -n -1
}

http_post() {
    # http_post <url> <data> [api_token] → stdout (body), sets HTTP_CODE via file
    local url="$1" data="$2" token="${3:-}"
    local headers=(-H "Content-Type: application/json")
    [ -n "$token" ] && headers+=(-H "X-API-Key: $token")
    local resp
    resp=$(curl -s -w '\n%{http_code}' "${headers[@]}" -X POST -d "$data" "$url" 2>/dev/null) || true
    local code
    code=$(echo "$resp" | tail -1)
    echo "${code:-000}" > "$_HTTP_CODE_FILE"
    echo "$resp" | head -n -1
}

# Read the HTTP status code from the last http_get/http_post call
http_code() {
    cat "$_HTTP_CODE_FILE" 2>/dev/null || echo "000"
}

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

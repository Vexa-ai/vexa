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

    # Check for single lite container
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx vexa; then
        echo "lite"
        return
    fi

    # Check for helm/k8s
    if kubectl get deploy api-gateway -o name 2>/dev/null | grep -q api-gateway; then
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
            # Must be set explicitly for helm
            if [ -z "${GATEWAY_URL:-}" ]; then
                echo "ERROR: GATEWAY_URL must be set for helm deployments" >&2
                exit 1
            fi
            : "${ADMIN_URL:=$GATEWAY_URL}"
            : "${DASHBOARD_URL:=$GATEWAY_URL}"
            ;;
    esac
    export GATEWAY_URL ADMIN_URL DASHBOARD_URL
}

# ─── Container execution ─────────────────────────────────────────

svc_exec() {
    # svc_exec <service> <command...>
    # Runs a command inside the container for the given service.
    local svc="$1"; shift
    local mode
    mode=$(cat "$STATE/deploy_mode" 2>/dev/null || detect_mode)

    case "$mode" in
        compose) docker exec "vexa-${svc}-1" "$@" ;;
        lite)    docker exec vexa "$@" ;;
        helm)    kubectl exec "deploy/${svc}" -- "$@" ;;
        *)       echo "ERROR: unknown deploy mode: $mode" >&2; return 1 ;;
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

http_get() {
    # http_get <url> [api_token] → stdout (body), sets HTTP_CODE
    local url="$1"
    local token="${2:-}"
    local headers=()
    [ -n "$token" ] && headers+=(-H "X-API-Key: $token")
    local resp
    resp=$(curl -sf -w '\n%{http_code}' "${headers[@]}" "$url" 2>/dev/null) || true
    HTTP_CODE=$(echo "$resp" | tail -1)
    echo "$resp" | head -n -1
}

http_post() {
    # http_post <url> <data> [api_token] → stdout (body), sets HTTP_CODE
    local url="$1" data="$2" token="${3:-}"
    local headers=(-H "Content-Type: application/json")
    [ -n "$token" ] && headers+=(-H "X-API-Key: $token")
    local resp
    resp=$(curl -sf -w '\n%{http_code}' "${headers[@]}" -X POST -d "$data" "$url" 2>/dev/null) || true
    HTTP_CODE=$(echo "$resp" | tail -1)
    echo "$resp" | head -n -1
}

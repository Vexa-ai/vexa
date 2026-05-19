#!/usr/bin/env bash
# local-provision.sh — LOCAL=1 path for release-provision.
#
# Brings up compose + lite stacks on the dev machine (no Linode VMs).
# Assumes images are already built (or will be by release-deploy LOCAL=1).
# Writes the same `.state-<mode>/vm_ip` markers that the canonical VM path
# writes, so downstream stages (deploy / validate) read uniform state.
#
# Usage: local-provision.sh "<modes-space-separated>"
#   e.g. local-provision.sh "compose lite"
#
# Outputs (per mode):
#   tests3/.state-<mode>/vm_ip       = 127.0.0.1
#   tests3/.state-<mode>/deploy_mode = <mode>
#   tests3/.state-<mode>/local       = 1
# For compose:
#   tests3/.state-compose/ports.env  = gateway/admin/dashboard host ports
#
# This script is idempotent: re-running it leaves state files identical.
# It does NOT (re)build images or (re)create containers; that's release-deploy.
# It DOES start sidecar postgres for lite mode (since lite always needs it).

set -euo pipefail

MODES="${1:-}"
if [[ -z "$MODES" ]]; then
    echo "FAIL: usage: local-provision.sh \"<modes-space-separated>\"" >&2
    exit 2
fi

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"

lite_postgres_password() {
    local state="$1"
    local file="$state/postgres_password"
    if [ ! -s "$file" ]; then
        umask 077
        if command -v openssl >/dev/null 2>&1; then
            openssl rand -hex 24 > "$file"
        else
            od -An -N24 -tx1 /dev/urandom | tr -d ' \n' > "$file"
            printf '\n' >> "$file"
        fi
    fi
    tr -d '\n\r' < "$file"
}

echo "=== LOCAL-PROVISION: modes=$MODES ==="
echo ""

for mode in $MODES; do
    case "$mode" in
        compose)
            STATE="$ROOT_DIR/tests3/.state-compose"
            mkdir -p "$STATE"
            echo "127.0.0.1" > "$STATE/vm_ip"
            echo "compose"   > "$STATE/deploy_mode"
            echo "1"         > "$STATE/local"
            # Canonical compose ports (from deploy/compose/docker-compose.yml).
            cat > "$STATE/ports.env" <<'PORTS'
GATEWAY_PORT=8056
ADMIN_PORT=8057
DASHBOARD_PORT=3001
RUNTIME_PORT=8090
MCP_PORT=18888
MINIO_API_PORT=9100
MINIO_CONSOLE_PORT=9101
POSTGRES_PORT=5438
PORTS
            echo "  compose: state seeded at $STATE (127.0.0.1)"
            ;;
        lite)
            STATE="$ROOT_DIR/tests3/.state-lite"
            mkdir -p "$STATE"
            echo "127.0.0.1" > "$STATE/vm_ip"
            echo "lite"      > "$STATE/deploy_mode"
            echo "1"         > "$STATE/local"
            # Canonical lite ports (via --network host in deploy/lite/Makefile).
            cat > "$STATE/ports.env" <<'PORTS'
GATEWAY_PORT=8056
ADMIN_PORT=8057
DASHBOARD_PORT=3000
PORTS
            # Sidecar postgres for lite (always required).
            LITE_POSTGRES_PASSWORD="$(lite_postgres_password "$STATE")"
            if ! docker network inspect vexa-lite-net >/dev/null 2>&1; then
                docker network create vexa-lite-net >/dev/null
                echo "  lite: created docker network vexa-lite-net"
            fi
            if ! docker ps --format '{{.Names}}' | grep -qx 'vexa-lite-postgres'; then
                # If a stopped container exists, recreate.
                docker rm -f vexa-lite-postgres >/dev/null 2>&1 || true
                docker run -d --name vexa-lite-postgres --network vexa-lite-net \
                    -e POSTGRES_DB=vexa -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD="$LITE_POSTGRES_PASSWORD" \
                    postgres:17-alpine >/dev/null
                echo "  lite: started sidecar vexa-lite-postgres"
            fi
            echo "  lite: state seeded at $STATE (127.0.0.1)"
            ;;
        helm)
            echo "  helm: LOCAL=1 not supported for helm mode (requires LKE cluster) — skipping" >&2
            ;;
        *)
            echo "  WARN: unknown mode '$mode' — skipping" >&2
            ;;
    esac
done

echo ""
echo "  LOCAL-PROVISION done. Next: make release-deploy LOCAL=1 SCOPE=<scope-yaml>."
